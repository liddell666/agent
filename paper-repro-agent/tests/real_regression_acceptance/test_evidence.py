from __future__ import annotations

import math

from real_regression_acceptance.evidence import evaluate_corpus, scan_for_leaks
from real_regression_acceptance.models import SafeCaseResult


DIGEST = "sha256:" + "a" * 64
TEST_DIGEST = "sha256:" + "b" * 64
GRAPH_DIGEST = "sha256:" + "c" * 64


def _success(case_id: str) -> SafeCaseResult:
    return SafeCaseResult(
        case_id=case_id,
        paper_digest=DIGEST,
        dataset_digest=DIGEST,
        workflow_run_ids=("80c9fa4f-2693-4142-8af3-52f15e5aba17",),
        experiment_id=f"exp-{case_id}",
        task_type="regression",
        status="succeeded",
        metrics={"linear_regression": {"mae": 1.0, "rmse": 1.2, "r2": 0.9}},
        model_statuses={"linear_regression": "succeeded"},
        performance_ranking=("linear_regression",),
        strict_status="not_comparable",
        approximate_status="materially_different",
        strict_reason_codes=("paper_dataset_identity_missing",),
        test_digest=TEST_DIGEST,
        elapsed_seconds=1.0,
    )


def _failure(case_id: str) -> SafeCaseResult:
    return SafeCaseResult(
        case_id=case_id,
        paper_digest=DIGEST,
        dataset_digest=DIGEST,
        task_type="regression",
        status="failed",
        failure_code="evidence_ambiguous",
        elapsed_seconds=1.0,
    )


def test_four_of_five_completed_cases_pass_the_corpus_gate() -> None:
    results = [_success(f"case-{index}") for index in range(4)] + [_failure("case-4")]

    evaluation = evaluate_corpus(
        results,
        expected_graph_digest=GRAPH_DIGEST,
        actual_graph_digest=GRAPH_DIGEST,
    )

    assert evaluation.passed is True
    assert evaluation.completed_count == 4
    assert evaluation.false_strict_count == 0
    assert evaluation.failure_counts == {"evidence_ambiguous": 1}
    assert evaluation.gate_errors == ()


def test_fewer_than_four_completed_cases_fail() -> None:
    results = [_success(f"case-{index}") for index in range(3)] + [
        _failure("case-3"),
        _failure("case-4"),
    ]

    evaluation = evaluate_corpus(results)

    assert evaluation.passed is False
    assert "completed_below_threshold" in evaluation.gate_errors


def test_unjustified_strict_comparability_fails() -> None:
    strict = _success("case-0").model_copy(
        update={
            "strict_status": "strictly_comparable",
            "paper_dataset_digest": "sha256:" + "d" * 64,
            "paper_test_digest": "sha256:" + "e" * 64,
        }
    )
    results = [strict] + [_success(f"case-{index}") for index in range(1, 5)]

    evaluation = evaluate_corpus(results)

    assert evaluation.passed is False
    assert evaluation.false_strict_count == 1
    assert "false_strict_comparability" in evaluation.gate_errors


def test_matching_dataset_and_test_identity_allows_strict_comparability() -> None:
    strict = _success("case-0").model_copy(
        update={
            "strict_status": "strictly_comparable",
            "paper_dataset_digest": DIGEST,
            "paper_test_digest": TEST_DIGEST,
        }
    )
    results = [strict] + [_success(f"case-{index}") for index in range(1, 5)]

    evaluation = evaluate_corpus(results)

    assert evaluation.passed is True
    assert evaluation.false_strict_count == 0


def test_completed_case_requires_finite_metrics_and_test_digest() -> None:
    non_finite = _success("case-0").model_copy(
        update={"metrics": {"linear_regression": {"rmse": math.nan}}}
    )
    no_digest = _success("case-1").model_copy(update={"test_digest": None})
    results = [non_finite, no_digest] + [_success(f"case-{index}") for index in range(2, 5)]

    evaluation = evaluate_corpus(results)

    assert evaluation.passed is False
    assert "non_finite_metric" in evaluation.gate_errors
    assert "completed_test_digest_missing" in evaluation.gate_errors


def test_not_comparable_requires_specific_reason() -> None:
    result = _success("case-0").model_copy(update={"strict_reason_codes": ()})

    evaluation = evaluate_corpus([result] + [_success(f"case-{i}") for i in range(1, 5)])

    assert evaluation.passed is False
    assert "strict_reason_missing" in evaluation.gate_errors


def test_unknown_failure_and_candidate_graph_drift_fail() -> None:
    unknown = _failure("case-4").model_copy(update={"failure_code": "unknown"})
    results = [_success(f"case-{index}") for index in range(4)] + [unknown]

    evaluation = evaluate_corpus(
        results,
        expected_graph_digest=GRAPH_DIGEST,
        actual_graph_digest="sha256:" + "f" * 64,
    )

    assert evaluation.passed is False
    assert "unknown_failure_code" in evaluation.gate_errors
    assert "candidate_graph_drift" in evaluation.gate_errors


def test_leak_scan_checks_keys_values_and_explicit_sentinels() -> None:
    assert scan_for_leaks({"safe": "value"}) == ()
    assert "prohibited_term:authorization" in scan_for_leaks(
        {"Authorization": "Bearer secret"}
    )
    assert "explicit_sentinel" in scan_for_leaks(
        {"message": "contains raw-row-sentinel"},
        sentinels=("raw-row-sentinel",),
    )
