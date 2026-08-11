import pytest

from repro_runner.protocol import create_manifest
from repro_runner.schemas import (
    ColumnProfile,
    DatasetDiagnosticResponse,
    DatasetDiagnosticSummary,
    DatasetOptions,
    ModelSuiteConfig,
    ValidationErrorItem,
)


def _diagnostic(
    *,
    feature_columns: list[str] | None = None,
    target: str | None = "Y_cls",
) -> DatasetDiagnosticResponse:
    features = ["age", "segment"] if feature_columns is None else feature_columns
    columns = [
        ColumnProfile(
            name="Y_cls",
            inferred_type="categorical",
            missing_count=0,
            unique_count=2,
            is_target_candidate=True,
            risk_flags=[],
        )
    ]
    columns.extend(
        ColumnProfile(
            name=name,
            inferred_type="numeric" if name == "age" else "categorical",
            missing_count=0,
            unique_count=4,
            is_target_candidate=False,
            risk_flags=[],
        )
        for name in features
    )
    return DatasetDiagnosticResponse(
        valid=True,
        dataset=DatasetDiagnosticSummary(
            rows=10,
            effective_rows=10,
            features=len(features),
            target=target,
            missing_values=0,
            duplicate_rows=0,
            class_counts={"0": 5, "1": 5} if target else {},
            class_ratios={"0": 0.5, "1": 0.5} if target else {},
            column_names=[column.name for column in columns],
            column_types={column.name: column.inferred_type for column in columns},
            numeric_ranges={"age": (18.0, 61.0)} if "age" in features else {},
            dataset_id="sha256:" + ("a" * 64),
        ),
        columns=columns,
        target_candidates=["Y_cls"],
        risk_flags=[],
        warnings=[],
        errors=[],
        recommended_options=DatasetOptions(target_column_confirmed=bool(target)),
    )


def test_create_manifest_rejects_invalid_threshold_duplicate_features_and_empty_plan():
    diagnostic = _diagnostic()
    invalid_threshold = ModelSuiteConfig.model_construct(
        models=["random_forest"],
        test_size=0.2,
        random_state=42,
        drop_duplicates=False,
        cv_folds=5,
        optimization_metric="roc_auc",
        threshold=float("nan"),
        n_iter=8,
        use_gpu=False,
        n_jobs=4,
    )

    with pytest.raises(ValueError):
        create_manifest(diagnostic, DatasetOptions(), invalid_threshold)

    with pytest.raises(ValueError):
        create_manifest(
            diagnostic,
            DatasetOptions(feature_columns=["segment", "segment"]),
            ModelSuiteConfig(models=["random_forest"]),
        )

    with pytest.raises(ValueError):
        create_manifest(
            _diagnostic(feature_columns=[]),
            DatasetOptions(feature_columns=[]),
            ModelSuiteConfig(models=["random_forest"]),
        )


def test_create_manifest_is_canonical_for_identical_inputs():
    diagnostic = _diagnostic()
    options = DatasetOptions(
        feature_columns=["segment", "age"],
        target_column_confirmed=True,
    )
    suite = ModelSuiteConfig(models=["random_forest", "svm"], random_state=7)

    first = create_manifest(diagnostic, options, suite)
    second = create_manifest(diagnostic, options, suite)

    assert first.model_dump(exclude={"manifest_id"}) == second.model_dump(
        exclude={"manifest_id"}
    )
    assert first.manifest_id == second.manifest_id


def test_create_manifest_preserves_protocol_choices():
    manifest = create_manifest(
        _diagnostic(),
        DatasetOptions(
            missing_policy="impute",
            sampling_strategy="balanced_undersample",
            comparison_mode="paper_comparable",
            target_column_confirmed=True,
        ),
        ModelSuiteConfig(models=["random_forest"]),
    )

    assert manifest.missing_policy == "impute"
    assert manifest.sampling_strategy == "balanced_undersample"
    assert manifest.comparison_mode == "paper_comparable"


def test_create_manifest_requires_confirmed_target_column():
    with pytest.raises(ValueError, match="confirmed"):
        create_manifest(
            _diagnostic(target=None),
            DatasetOptions(target_column="Y_cls", target_column_confirmed=False),
            ModelSuiteConfig(models=["random_forest"]),
        )
