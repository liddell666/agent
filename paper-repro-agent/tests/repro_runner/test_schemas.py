from repro_runner.schemas import (
    DatasetProfile,
    ExperimentConfig,
    ExperimentMetrics,
    ExperimentResult,
    FeatureImportance,
)


def test_experiment_config_defaults_are_unchanged():
    config = ExperimentConfig()

    assert config.model == "random_forest"
    assert config.test_size == 0.2
    assert config.random_state == 42
    assert config.drop_duplicates is False


def test_legacy_experiment_result_without_split_provenance_is_still_accepted():
    result = ExperimentResult(
        experiment_id="exp-20260811T010203Z-00000000",
        status="succeeded",
        config=ExperimentConfig(),
        dataset=DatasetProfile(
            rows=4,
            effective_rows=4,
            features=1,
            target="Y_cls",
            missing_values=0,
            duplicate_rows=0,
            dataset_id="sha256:" + "0" * 64,
        ),
        metrics=ExperimentMetrics(
            roc_auc=0.8,
            accuracy=0.8,
            balanced_accuracy=0.8,
            precision=0.8,
            recall=0.8,
            f1=0.8,
            confusion_matrix=[[2, 0], [0, 2]],
        ),
        feature_importance=[FeatureImportance(feature="f1", importance=1.0)],
    )

    assert result.reproducibility_status == "legacy_incomparable"
