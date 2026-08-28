from __future__ import annotations

import json
import math
from pathlib import Path
import secrets
import time
from typing import Protocol

from .acquisition import AcquiredCase
from .dify_client import DifyClientError, WorkflowOutcome
from .models import AcceptanceCase, FailureCode, SafeCaseResult


MODELS = (
    "linear_regression",
    "random_forest",
    "gradient_boosting",
    "xgboost",
)
METRICS = ("mae", "rmse", "r2")
OUTPUT_KEYS = {
    "dossier_json",
    "validation_json",
    "experiment_json",
    "comparison_json",
    "assessment_json",
    "markdown_report",
    "protocol_token",
}


class WorkflowClient(Protocol):
    @staticmethod
    def file_input(file_id: str) -> dict[str, str]: ...

    def upload_file(self, path: Path, user: str) -> str: ...

    def run(self, inputs: dict[str, object], user: str) -> WorkflowOutcome: ...


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(
        self,
        *,
        case_id: str,
        phase: str,
        run_id: str,
        terminal_status: str,
    ) -> None:
        payload = {
            "case_id": case_id,
            "phase": phase,
            "run_id": run_id,
            "terminal_status": terminal_status,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _failure(
    case: AcceptanceCase,
    acquired: AcquiredCase,
    code: FailureCode,
    elapsed_seconds: float,
    run_ids: tuple[str, ...] = (),
) -> SafeCaseResult:
    return SafeCaseResult(
        case_id=case.id,
        paper_digest=acquired.paper_digest,
        dataset_digest=acquired.dataset_digest,
        workflow_run_ids=run_ids,
        task_type="regression",
        status="failed",
        failure_code=code,
        elapsed_seconds=max(0.0, elapsed_seconds),
    )


def _ranking(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    safe = [item for item in value if item in MODELS]
    return tuple(dict.fromkeys(safe))


def _safe_result(
    case: AcceptanceCase,
    acquired: AcquiredCase,
    outputs: dict[str, object],
    run_ids: tuple[str, ...],
    elapsed_seconds: float,
) -> SafeCaseResult:
    safe_outputs = {key: outputs[key] for key in OUTPUT_KEYS if key in outputs}
    experiment = _object(safe_outputs.get("experiment_json"))
    assessment = _object(safe_outputs.get("assessment_json"))
    comparison = _object(safe_outputs.get("comparison_json"))
    if experiment.get("task_type") != "regression" or experiment.get("status") not in {
        "succeeded",
        "partial",
    }:
        return _failure(
            case,
            acquired,
            "experiment_failed",
            elapsed_seconds,
            run_ids,
        )

    metrics: dict[str, dict[str, float]] = {}
    statuses: dict[str, str] = {}
    results = experiment.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict) or item.get("model") not in MODELS:
                continue
            model = str(item["model"])
            status = item.get("status")
            if isinstance(status, str) and status in {
                "succeeded",
                "failed",
                "skipped",
            }:
                statuses[model] = status
            raw_metrics = item.get("metrics")
            if not isinstance(raw_metrics, dict):
                continue
            safe_metrics: dict[str, float] = {}
            for name in METRICS:
                value = raw_metrics.get(name)
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    safe_metrics[name] = float(value)
            if safe_metrics:
                metrics[model] = safe_metrics

    split = experiment.get("split_provenance")
    test_digest = split.get("test_digest") if isinstance(split, dict) else None
    if not isinstance(test_digest, str):
        test_digest = None
    strict_reasons = assessment.get("strict_reason_codes")
    if not isinstance(strict_reasons, list):
        strict_reasons = assessment.get("reason_codes")
    strict_reason_codes = (
        tuple(item for item in strict_reasons if isinstance(item, str))
        if isinstance(strict_reasons, list)
        else ()
    )
    strict_status = assessment.get("strict_status")
    approximate_status = assessment.get("approximate_status")
    experiment_id = experiment.get("experiment_id")
    return SafeCaseResult(
        case_id=case.id,
        paper_digest=acquired.paper_digest,
        dataset_digest=acquired.dataset_digest,
        workflow_run_ids=run_ids,
        experiment_id=experiment_id if isinstance(experiment_id, str) else None,
        task_type="regression",
        status="succeeded",
        metrics=metrics,
        model_statuses=statuses,
        performance_ranking=_ranking(experiment.get("performance_ranking")),
        paper_closeness_ranking=_ranking(comparison.get("paper_closeness_ranking")),
        strict_status=(
            strict_status if isinstance(strict_status, str) else "not_comparable"
        ),
        approximate_status=(
            approximate_status
            if isinstance(approximate_status, str)
            else "insufficient_metrics"
        ),
        strict_reason_codes=strict_reason_codes,
        test_digest=test_digest,
        elapsed_seconds=max(0.0, elapsed_seconds),
    )


def _base_inputs(case: AcceptanceCase, training_file: dict[str, str]) -> dict[str, object]:
    return {
        "task_type": "regression",
        "training_csv": training_file,
        "target_column": case.target_column,
        "test_size": 0.2,
        "random_state": 42,
        "models_json": '["linear_regression","random_forest","gradient_boosting","xgboost"]',
        "cv_folds": "5",
        "optimization_metric": "rmse",
        "n_iter": 8,
        "use_gpu": False,
        "drop_duplicates": False,
        "close_threshold": 0.05,
        "partial_threshold": 0.1,
        "protocol_notes": "",
    }


def run_case(
    case: AcceptanceCase,
    acquired: AcquiredCase,
    client: WorkflowClient,
    checkpoint_store: CheckpointStore,
) -> SafeCaseResult:
    started = time.monotonic()
    user = f"acceptance-{case.id}"
    run_ids: list[str] = []
    try:
        paper_id = client.upload_file(acquired.paper_path, user)
        dataset_id = client.upload_file(acquired.csv_path, user)
        training_file = client.file_input(dataset_id)
        prepare_inputs = _base_inputs(case, training_file)
        prepare_inputs.update(
            {
                "run_mode": "prepare",
                "paper_pdf": client.file_input(paper_id),
                "confirm_protocol": False,
                "protocol_token": "",
            }
        )
        prepare = client.run(prepare_inputs, user)
        run_ids.append(prepare.run_id)
        checkpoint_store.write(
            case_id=case.id,
            phase="prepare",
            run_id=prepare.run_id,
            terminal_status=prepare.status,
        )
        validation = _object(prepare.outputs.get("validation_json"))
        preview = _object(prepare.outputs.get("protocol_preview_json"))
        manifest = preview.get("manifest_draft")
        paper_summary = preview.get("paper_summary")
        unresolved = preview.get("unresolved_protocol_fields")
        preview_valid = (
            isinstance(manifest, dict)
            and manifest.get("task_type") == "regression"
            and isinstance(paper_summary, dict)
            and paper_summary.get("task_type") == "regression"
            and unresolved == []
        )
        validation_valid = (
            validation.get("valid") is True
            and validation.get("task_type") == "regression"
        )
        token = prepare.outputs.get("protocol_token")
        if (
            prepare.status != "succeeded"
            or not (preview_valid or validation_valid)
            or not isinstance(token, str)
            or not token
        ):
            result = _failure(
                case,
                acquired,
                "evidence_ambiguous",
                time.monotonic() - started,
                tuple(run_ids),
            )
            checkpoint_store.write(
                case_id=case.id,
                phase="prepare",
                run_id=prepare.run_id,
                terminal_status="failed",
            )
            return result

        confirm_inputs = _base_inputs(case, training_file)
        confirm_inputs.update(
            {
                "run_mode": "run",
                "confirm_protocol": True,
                "protocol_token": token,
            }
        )
        confirmed = client.run(confirm_inputs, user)
        run_ids.append(confirmed.run_id)
        result = _safe_result(
            case,
            acquired,
            confirmed.outputs,
            tuple(run_ids),
            time.monotonic() - started,
        )
        checkpoint_store.write(
            case_id=case.id,
            phase="confirm",
            run_id=confirmed.run_id,
            terminal_status=result.status,
        )
        return result
    except DifyClientError:
        return _failure(
            case,
            acquired,
            "service_unavailable",
            time.monotonic() - started,
            tuple(run_ids),
        )
