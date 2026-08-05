"""Privacy-preserving persistence for experiment summaries."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import secrets

from repro_runner.config import Settings
from repro_runner.schemas import ExperimentResult


_EXPERIMENT_ID = re.compile(r"exp-[A-Za-z0-9-]+\Z")


class ResultNotFoundError(LookupError):
    """Raised when a requested persisted experiment is unavailable."""


def create_experiment_id() -> str:
    """Create an opaque, filesystem-safe identifier for one experiment."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"exp-{timestamp}-{secrets.token_hex(8)}"


def save_result(result: ExperimentResult, settings: Settings) -> str:
    """Store only result metadata, configuration, and aggregate dataset profile."""
    experiment_id = _validate_experiment_id(result.experiment_id)
    directory = _experiment_directory(experiment_id, settings)
    directory.mkdir(parents=True, exist_ok=True)

    _write_json_atomic(directory / "result.json", result.model_dump(mode="json"))
    _write_json_atomic(directory / "config.json", result.config.model_dump(mode="json"))
    _write_json_atomic(
        directory / "dataset_profile.json", result.dataset.model_dump(mode="json")
    )
    return experiment_id


def load_result(experiment_id: str, settings: Settings) -> ExperimentResult:
    """Load a persisted result without permitting paths outside the result root."""
    try:
        directory = _experiment_directory(_validate_experiment_id(experiment_id), settings)
        payload = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        return ExperimentResult.model_validate(payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ResultNotFoundError("experiment result was not found") from exc


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
