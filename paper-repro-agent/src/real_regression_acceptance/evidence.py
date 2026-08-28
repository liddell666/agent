from __future__ import annotations

from collections import Counter
import json
import math
from typing import Iterable, Mapping

from .models import CorpusEvaluation, SafeCaseResult


FAILURE_CODES = {
    "acquisition_failed",
    "paper_parse_failed",
    "evidence_ambiguous",
    "dataset_invalid",
    "experiment_failed",
    "comparison_failed",
    "privacy_gate_failed",
    "service_unavailable",
}
PROHIBITED_TERMS = (
    "authorization",
    "bearer ",
    "cookie",
    "protocol_token",
    "api_key",
    "paper_text",
    "csv_rows",
)


def scan_for_leaks(
    payload: object,
    *,
    sentinels: Iterable[str] = (),
) -> tuple[str, ...]:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
    findings = {
        f"prohibited_term:{term.strip()}"
        for term in PROHIBITED_TERMS
        if term in serialized
    }
    if any(
        isinstance(sentinel, str)
        and sentinel
        and sentinel.casefold() in serialized
        for sentinel in sentinels
    ):
        findings.add("explicit_sentinel")
    return tuple(sorted(findings))


def evaluate_corpus(
    results: Iterable[SafeCaseResult],
    *,
    expected_graph_digest: str | None = None,
    actual_graph_digest: str | None = None,
    sentinels: Iterable[str] = (),
) -> CorpusEvaluation:
    cases = list(results)
    errors: set[str] = set()
    if len(cases) != 5 or len({case.case_id for case in cases}) != 5:
        errors.add("corpus_size_invalid")

    completed = [case for case in cases if case.status == "succeeded"]
    if len(completed) < 4:
        errors.add("completed_below_threshold")

    false_strict_count = 0
    for case in cases:
        if case.status == "failed" and case.failure_code not in FAILURE_CODES:
            errors.add("unknown_failure_code")
        if case.status != "succeeded":
            continue
        if case.test_digest is None:
            errors.add("completed_test_digest_missing")
        for model_metrics in case.metrics.values():
            if any(
                not isinstance(value, (int, float)) or not math.isfinite(float(value))
                for value in model_metrics.values()
            ):
                errors.add("non_finite_metric")
        if case.strict_status == "not_comparable" and not case.strict_reason_codes:
            errors.add("strict_reason_missing")
        strict_allowed = (
            case.strict_status != "strictly_comparable"
            or (
                case.paper_dataset_digest == case.dataset_digest
                and case.paper_test_digest == case.test_digest
            )
        )
        if not strict_allowed:
            false_strict_count += 1
    if false_strict_count:
        errors.add("false_strict_comparability")

    if (
        expected_graph_digest is not None
        and actual_graph_digest is not None
        and expected_graph_digest != actual_graph_digest
    ):
        errors.add("candidate_graph_drift")
    if scan_for_leaks(
        [case.model_dump(mode="json") for case in cases],
        sentinels=sentinels,
    ):
        errors.add("privacy_gate_failed")

    failures = Counter(
        str(case.failure_code)
        for case in cases
        if case.status == "failed" and case.failure_code is not None
    )
    return CorpusEvaluation(
        passed=not errors,
        completed_count=len(completed),
        total_count=len(cases),
        false_strict_count=false_strict_count,
        failure_counts=dict(sorted(failures.items())),
        gate_errors=tuple(sorted(errors)),
    )


def evidence_payload(
    results: Iterable[SafeCaseResult],
    evaluation: CorpusEvaluation,
    *,
    candidate_app_id: str,
) -> dict[str, object]:
    return {
        "candidate_app_id": candidate_app_id,
        "evaluation": evaluation.model_dump(mode="json"),
        "results": [result.model_dump(mode="json") for result in results],
        "schema_version": 1,
    }
