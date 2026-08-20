from __future__ import annotations

import hashlib
import json

from repro_runner.preprocessing import build_column_plan
from repro_runner.schemas import (
    DatasetDiagnosticResponse,
    DatasetOptions,
    ExperimentManifest,
    ModelSuiteConfig,
)


def create_manifest(
    diagnostic: DatasetDiagnosticResponse,
    options: DatasetOptions,
    suite_config: ModelSuiteConfig,
    dossier_id: str | None = None,
) -> ExperimentManifest:
    if diagnostic.dataset is None:
        raise ValueError("diagnostic dataset summary is required")
    if not diagnostic.valid:
        raise ValueError("diagnostic must be valid before creating a manifest")

    available_columns = [column.name for column in diagnostic.columns]
    if not available_columns:
        raise ValueError("diagnostic columns are required")

    target_column = _resolve_target_column(available_columns, diagnostic, options)
    plan = build_column_plan(
        available_columns,
        target_column=target_column,
        feature_columns=options.feature_columns,
        exclude_columns=options.exclude_columns,
    )
    if not plan.feature_columns:
        raise ValueError("manifest requires at least one feature column")

    _validate_cv_feasibility(diagnostic, suite_config.cv_folds)
    payload = {
        "comparison_mode": options.comparison_mode,
        "cv_folds": suite_config.cv_folds,
        "n_seeds": suite_config.n_seeds,
        "dataset_id": diagnostic.dataset.dataset_id,
        "dossier_id": dossier_id,
        "feature_columns": plan.feature_columns,
        "missing_policy": options.missing_policy,
        "models": list(suite_config.models),
        "optimization_metric": suite_config.optimization_metric,
        "random_state": suite_config.random_state,
        "sampling_strategy": options.sampling_strategy,
        "target_column": target_column,
        "test_size": suite_config.test_size,
        "threshold": suite_config.threshold,
        "workflow_version": suite_config.workflow_version,
    }
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest_id = f"sha256:{hashlib.sha256(canonical_payload).hexdigest()}"
    return ExperimentManifest(manifest_id=manifest_id, **payload)


def _resolve_target_column(
    available_columns: list[str],
    diagnostic: DatasetDiagnosticResponse,
    options: DatasetOptions,
) -> str:
    if options.target_column_confirmed and options.target_column in available_columns:
        return options.target_column
    raise ValueError("target column must be confirmed before creating a manifest")


def _validate_cv_feasibility(
    diagnostic: DatasetDiagnosticResponse, cv_folds: int
) -> None:
    if diagnostic.dataset is None:
        raise ValueError("diagnostic dataset summary is required")
    if diagnostic.dataset.class_counts:
        minority_class_size = min(diagnostic.dataset.class_counts.values())
        if minority_class_size < cv_folds:
            raise ValueError("cv_folds exceed the available minority-class rows")
