import pytest
from pydantic import ValidationError

from repro_runner.schemas import (
    DEFAULT_MODEL_NAMES,
    DatasetProfile,
    ExperimentSuiteResult,
    ModelSuiteConfig,
    SplitProvenance,
)


def test_model_suite_config_defaults():
    config = ModelSuiteConfig()

    assert config.models == list(DEFAULT_MODEL_NAMES)
    assert config.test_size == 0.2
    assert config.random_state == 42
    assert config.cv_folds == 5
    assert config.optimization_metric == "roc_auc"
    assert config.threshold == 0.5
    assert config.n_iter == 8
    assert config.use_gpu is False


def test_duplicate_models_and_unknown_scorer_raise_validation_error():
    with pytest.raises(ValidationError):
        ModelSuiteConfig(
            models=["random_forest", "random_forest"],
        )

    with pytest.raises(ValidationError):
        ModelSuiteConfig(optimization_metric="accuracy")


def test_minimal_experiment_suite_result_validates():
    result = ExperimentSuiteResult(
        experiment_id="exp-20260811T010203Z-00000000",
        status="partial",
        config=ModelSuiteConfig(),
        dataset=DatasetProfile(
            rows=4,
            effective_rows=4,
            features=1,
            target="Y_cls",
            missing_values=0,
            duplicate_rows=0,
            class_counts={"0": 2, "1": 2},
            dataset_id="sha256:" + "0" * 64,
        ),
        split_provenance=SplitProvenance(
            test_size=0.2,
            random_state=42,
            train_rows=3,
            test_rows=1,
            test_digest="sha256:" + "1" * 64,
        ),
    )

    assert result.status == "partial"
    assert result.dataset.rows == 4
    assert result.split_provenance.test_rows == 1
