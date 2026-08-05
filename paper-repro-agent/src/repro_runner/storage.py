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
from repro_runner.schemas import ExperimentResult
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
