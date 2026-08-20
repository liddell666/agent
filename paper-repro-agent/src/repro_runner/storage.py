"""Privacy-preserving persistence for experiment summaries."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import secrets
import shutil

from pydantic import ValidationError

from repro_runner.config import Settings
from repro_runner.schemas import ExperimentResult, ExperimentSuiteResult
from repro_runner import __version__


_EXPERIMENT_ID = re.compile(r"exp-[A-Za-z0-9-]+\Z")


class ResultNotFoundError(LookupError):
    """Raised when a requested persisted experiment is unavailable."""


class ResultFormatError(ValueError):
    """Raised when a stored result exists but cannot match a supported contract."""


def create_experiment_id() -> str:
    """Create an opaque, filesystem-safe identifier for one experiment."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"exp-{timestamp}-{secrets.token_hex(8)}"


def save_result(result: ExperimentResult, settings: Settings) -> str:
    """Store only result metadata, configuration, and aggregate dataset profile."""
    experiment_id = _validate_experiment_id(result.experiment_id)
    root = settings.storage_dir.resolve()
    directory = _experiment_directory(experiment_id, settings)
    temporary = root / f".{experiment_id}.{secrets.token_hex(8)}.tmp"
    root.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        raise FileExistsError("experiment result already exists")

    temporary.mkdir()
    try:
        _write_json_atomic(temporary / "result.json", _result_payload(result))
        _write_json_atomic(temporary / "config.json", _stored_config_payload(result))
        _write_json_atomic(
            temporary / "dataset_profile.json", _dataset_profile_payload(result)
        )
        temporary.replace(directory)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return experiment_id


def save_suite_result(result: ExperimentSuiteResult, settings: Settings) -> str:
    """Store only the public suite result contract and aggregate metadata."""
    experiment_id = _validate_experiment_id(result.experiment_id)
    root = settings.storage_dir.resolve()
    directory = _experiment_directory(experiment_id, settings)
    temporary = root / f".{experiment_id}.{secrets.token_hex(8)}.tmp"
    root.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        raise FileExistsError("experiment result already exists")

    temporary.mkdir()
    try:
        _write_json_atomic(temporary / "result.json", _suite_result_payload(result))
        _write_json_atomic(
            temporary / "config.json", _stored_suite_config_payload(result)
        )
        _write_json_atomic(
            temporary / "dataset_profile.json", _suite_dataset_profile_payload(result)
        )
        temporary.replace(directory)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return experiment_id


def update_suite_result(result: ExperimentSuiteResult, settings: Settings) -> str:
    """Atomically update the public suite payload after comparison enrichment."""
    experiment_id = _validate_experiment_id(result.experiment_id)
    directory = _experiment_directory(experiment_id, settings)
    if not directory.is_dir():
        raise ResultNotFoundError("experiment result was not found")
    _write_json_atomic(directory / "result.json", _suite_result_payload(result))
    return experiment_id


def load_result(experiment_id: str, settings: Settings) -> ExperimentResult:
    """Load a persisted result without permitting paths outside the result root."""
    try:
        directory = _experiment_directory(_validate_experiment_id(experiment_id), settings)
        content = (directory / "result.json").read_text(encoding="utf-8")
    except ResultNotFoundError:
        raise
    except OSError as exc:
        raise ResultNotFoundError("experiment result was not found") from exc
    except UnicodeDecodeError as exc:
        raise ResultFormatError(
            "experiment result uses an unsupported or corrupted result format"
        ) from exc
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ResultFormatError(
            "experiment result uses an unsupported or corrupted result format"
        ) from exc
    try:
        return ExperimentResult.model_validate(payload)
    except ValidationError as exc:
        raise ResultFormatError(
            "experiment result uses an unsupported or corrupted result format"
        ) from exc


def load_suite_result(experiment_id: str, settings: Settings) -> ExperimentSuiteResult:
    """Load a persisted suite result without permitting paths outside the root."""
    try:
        directory = _experiment_directory(_validate_experiment_id(experiment_id), settings)
        content = (directory / "result.json").read_text(encoding="utf-8")
    except ResultNotFoundError:
        raise
    except OSError as exc:
        raise ResultNotFoundError("experiment result was not found") from exc
    except UnicodeDecodeError as exc:
        raise ResultFormatError(
            "experiment result uses an unsupported or corrupted result format"
        ) from exc
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ResultFormatError(
            "experiment result uses an unsupported or corrupted result format"
        ) from exc
    try:
        return ExperimentSuiteResult.model_validate(payload)
    except ValidationError as exc:
        raise ResultFormatError(
            "experiment result uses an unsupported or corrupted result format"
        ) from exc


def _validate_experiment_id(experiment_id: str) -> str:
    if not isinstance(experiment_id, str) or not _EXPERIMENT_ID.fullmatch(experiment_id):
        raise ResultNotFoundError("experiment result was not found")
    return experiment_id


def _experiment_directory(experiment_id: str, settings: Settings) -> Path:
    root = settings.storage_dir.resolve()
    candidate = (root / experiment_id).resolve()
    if candidate.parent != root:
        raise ResultNotFoundError("experiment result was not found")
    return candidate


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _result_payload(result: ExperimentResult) -> dict[str, object]:
    """Return the explicit public result contract, excluding arbitrary input context."""
    return {
        "experiment_id": result.experiment_id,
        "status": result.status,
        "config": _config_payload(result),
        "dataset": _dataset_profile_payload(result),
        "metrics": {
            "roc_auc": result.metrics.roc_auc,
            "accuracy": result.metrics.accuracy,
            "balanced_accuracy": result.metrics.balanced_accuracy,
            "precision": result.metrics.precision,
            "recall": result.metrics.recall,
            "f1": result.metrics.f1,
            "confusion_matrix": result.metrics.confusion_matrix,
        },
        "feature_importance": [
            {"feature": item.feature, "importance": item.importance}
            for item in result.feature_importance
        ],
        "split_provenance": {
            "test_size": result.split_provenance.test_size,
            "random_state": result.split_provenance.random_state,
            "train_rows": result.split_provenance.train_rows,
            "test_rows": result.split_provenance.test_rows,
            "test_digest": result.split_provenance.test_digest,
        },
        "reproducibility_status": result.reproducibility_status,
    }


def _config_payload(result: ExperimentResult) -> dict[str, object]:
    return {
        "model": result.config.model,
        "test_size": result.config.test_size,
        "random_state": result.config.random_state,
        "drop_duplicates": result.config.drop_duplicates,
    }


def _stored_config_payload(result: ExperimentResult) -> dict[str, object]:
    """Persist the execution settings together with the runner release."""
    return {**_config_payload(result), "service_version": __version__}


def _dataset_profile_payload(result: ExperimentResult) -> dict[str, object]:
    dataset = result.dataset
    return {
        "rows": dataset.rows,
        "effective_rows": dataset.effective_rows,
        "features": dataset.features,
        "target": dataset.target,
        "missing_values": dataset.missing_values,
        "duplicate_rows": dataset.duplicate_rows,
        "class_counts": dataset.class_counts,
        "class_ratios": dataset.class_ratios,
        "column_names": dataset.column_names,
        "column_types": dataset.column_types,
        "numeric_ranges": dataset.numeric_ranges,
        "dataset_id": dataset.dataset_id,
    }


def _suite_result_payload(result: ExperimentSuiteResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "experiment_id": result.experiment_id,
        "status": result.status,
        "config": _suite_config_payload(result),
        "dataset": _suite_dataset_profile_payload(result),
        "split_provenance": _split_provenance_payload(result),
        "results": [_model_run_payload(item) for item in result.results],
        "performance_ranking": list(result.performance_ranking),
        "paper_closeness_ranking": list(result.paper_closeness_ranking),
        "reproducibility_status": result.reproducibility_status,
    }
    if result.runtime is not None:
        payload["runtime"] = result.runtime.model_dump(mode="json")
    if result.preprocessing is not None:
        payload["preprocessing"] = {
            "numeric_columns": list(result.preprocessing.numeric_columns),
            "categorical_columns": list(result.preprocessing.categorical_columns),
            "transformed_feature_names": list(
                result.preprocessing.transformed_feature_names
            ),
            "sampling_strategy": result.preprocessing.sampling_strategy,
        }
    return payload


def _suite_config_payload(result: ExperimentSuiteResult) -> dict[str, object]:
    config = result.config
    return {
        "models": list(config.models),
        "test_size": config.test_size,
        "random_state": config.random_state,
        "drop_duplicates": config.drop_duplicates,
        "cv_folds": config.cv_folds,
        "n_seeds": config.n_seeds,
        "optimization_metric": config.optimization_metric,
        "threshold": config.threshold,
        "n_iter": config.n_iter,
        "use_gpu": config.use_gpu,
        "n_jobs": config.n_jobs,
        "workflow_version": config.workflow_version,
    }


def _stored_suite_config_payload(result: ExperimentSuiteResult) -> dict[str, object]:
    return {**_suite_config_payload(result), "service_version": __version__}


def _suite_dataset_profile_payload(result: ExperimentSuiteResult) -> dict[str, object]:
    dataset = result.dataset
    return {
        "rows": dataset.rows,
        "effective_rows": dataset.effective_rows,
        "features": dataset.features,
        "target": dataset.target,
        "missing_values": dataset.missing_values,
        "duplicate_rows": dataset.duplicate_rows,
        "class_counts": dataset.class_counts,
        "class_ratios": dataset.class_ratios,
        "column_names": dataset.column_names,
        "column_types": dataset.column_types,
        "numeric_ranges": dataset.numeric_ranges,
        "dataset_id": dataset.dataset_id,
    }


def _split_provenance_payload(result: ExperimentSuiteResult) -> dict[str, object]:
    split = result.split_provenance
    return {
        "test_size": split.test_size,
        "random_state": split.random_state,
        "train_rows": split.train_rows,
        "test_rows": split.test_rows,
        "test_digest": split.test_digest,
    }


def _model_run_payload(item) -> dict[str, object]:
    metrics = None
    if item.metrics is not None:
        metrics = {
            "roc_auc": item.metrics.roc_auc,
            "accuracy": item.metrics.accuracy,
            "balanced_accuracy": item.metrics.balanced_accuracy,
            "precision": item.metrics.precision,
            "recall": item.metrics.recall,
            "f1": item.metrics.f1,
            "confusion_matrix": item.metrics.confusion_matrix,
        }
    error = None
    if item.error is not None:
        error = {"code": item.error.code, "message": item.error.message}
    split_provenance = None
    if item.split_provenance is not None:
        split = item.split_provenance
        split_provenance = {
            "test_size": split.test_size,
            "random_state": split.random_state,
            "train_rows": split.train_rows,
            "test_rows": split.test_rows,
            "test_digest": split.test_digest,
        }
    return {
        "model": item.model,
        "status": item.status,
        "cv_best_score": item.cv_best_score,
        "best_params": item.best_params,
        "metrics": metrics,
        "feature_importance": [
            {"feature": feature.feature, "importance": feature.importance}
            for feature in item.feature_importance
        ],
        "fit_seconds": item.fit_seconds,
        "error": error,
        "split_provenance": split_provenance,
        "cv_mean": item.cv_mean,
        "cv_std": item.cv_std,
        "cv_fold_scores": item.cv_fold_scores,
        "seed_means": item.seed_means,
    }
