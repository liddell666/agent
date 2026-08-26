import pytest

from repro_runner.schemas import (
    DatasetOptions,
    DatasetProfile,
    ExperimentConfig,
    ExperimentMetrics,
    ExperimentResult,
    ExperimentSuiteResult,
    FeatureImportance,
    ModelSuiteConfig,
    RegressionMetrics,
    SplitProvenance,
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


def test_suite_config_keeps_binary_defaults_when_task_type_is_absent():
    config = ModelSuiteConfig()

    assert config.task_type == "binary_classification"
    assert config.models == [
        "logistic_regression", "random_forest", "xgboost", "lightgbm",
        "svm", "knn", "mlp",
    ]
    assert config.optimization_metric == "roc_auc"
    assert config.threshold == 0.5


def test_suite_config_selects_explicit_regression_defaults():
    config = ModelSuiteConfig(task_type="regression")

    assert config.models == [
        "linear_regression", "random_forest", "gradient_boosting", "xgboost"
    ]
    assert config.optimization_metric == "rmse"
    assert config.threshold is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"task_type": "regression", "models": ["logistic_regression"]}, "model"),
        ({"task_type": "regression", "optimization_metric": "roc_auc"}, "metric"),
        ({"task_type": "binary_classification", "models": ["linear_regression"]}, "model"),
        ({"task_type": "binary_classification", "optimization_metric": "rmse"}, "metric"),
    ],
)
def test_suite_config_rejects_cross_task_options(payload, message):
    with pytest.raises(ValueError, match=message):
        ModelSuiteConfig(**payload)


def test_regression_options_reject_classification_sampling():
    with pytest.raises(ValueError, match="sampling"):
        DatasetOptions(task_type="regression", sampling_strategy="class_weight")


def test_regression_metrics_reject_invalid_public_values():
    with pytest.raises(ValueError):
        RegressionMetrics(mae=-0.1, rmse=0.2, r2=0.3)
    with pytest.raises(ValueError):
        RegressionMetrics(mae=0.1, rmse=float("nan"), r2=0.3)

    assert RegressionMetrics(mae=1.0, rmse=2.0, r2=-4.0).r2 == -4.0


def _suite_result_payload(config: ModelSuiteConfig) -> dict[str, object]:
    return {
        "experiment_id": "exp-20260824T010203Z-suite",
        "status": "succeeded",
        "config": config.model_dump(mode="json"),
        "dataset": {
            "rows": 10,
            "effective_rows": 10,
            "features": 1,
            "target": "Y_cls",
            "missing_values": 0,
            "duplicate_rows": 0,
            "dataset_id": "sha256:" + "a" * 64,
        },
        "split_provenance": SplitProvenance(
            test_size=0.2,
            random_state=42,
            train_rows=8,
            test_rows=2,
            test_digest="sha256:" + "b" * 64,
        ).model_dump(mode="json"),
    }


def test_suite_result_derives_omitted_task_type_from_regression_config():
    payload = _suite_result_payload(ModelSuiteConfig(task_type="regression"))

    result = ExperimentSuiteResult.model_validate(payload)

    assert result.task_type == "regression"


def test_suite_result_rejects_task_type_mismatch_with_config():
    payload = _suite_result_payload(ModelSuiteConfig(task_type="regression"))
    payload["task_type"] = "binary_classification"

    with pytest.raises(ValueError, match="task_type"):
        ExperimentSuiteResult.model_validate(payload)


@pytest.mark.parametrize(
    ("config", "metrics", "message"),
    [
        (
            ModelSuiteConfig(),
            RegressionMetrics(mae=1.0, rmse=2.0, r2=0.3).model_dump(mode="json"),
            "ExperimentMetrics",
        ),
        (
            ModelSuiteConfig(task_type="regression"),
            ExperimentMetrics(
                roc_auc=0.8,
                accuracy=0.8,
                balanced_accuracy=0.8,
                precision=0.8,
                recall=0.8,
                f1=0.8,
                confusion_matrix=[[2, 0], [0, 2]],
            ).model_dump(mode="json"),
            "RegressionMetrics",
        ),
    ],
)
def test_suite_result_rejects_metrics_from_the_other_task(config, metrics, message):
    payload = _suite_result_payload(config)
    payload["results"] = [
        {"model": "random_forest", "status": "succeeded", "metrics": metrics}
    ]

    with pytest.raises(ValueError, match=message):
        ExperimentSuiteResult.model_validate(payload)


@pytest.mark.parametrize("config", [ModelSuiteConfig(), ModelSuiteConfig(task_type="regression")])
def test_suite_result_rejects_succeeded_result_without_metrics(config):
    payload = _suite_result_payload(config)
    payload["results"] = [{"model": "random_forest", "status": "succeeded"}]

    with pytest.raises(ValueError, match="succeeded.*metrics"):
        ExperimentSuiteResult.model_validate(payload)


def test_suite_result_accepts_task_appropriate_regression_metrics():
    payload = _suite_result_payload(ModelSuiteConfig(task_type="regression"))
    payload["results"] = [
        {
            "model": "random_forest",
            "status": "succeeded",
            "metrics": RegressionMetrics(mae=1.0, rmse=2.0, r2=0.3).model_dump(mode="json"),
        }
    ]

    result = ExperimentSuiteResult.model_validate(payload)

    assert isinstance(result.results[0].metrics, RegressionMetrics)


def test_legacy_suite_result_omitting_config_and_suite_task_types_is_binary():
    payload = _suite_result_payload(ModelSuiteConfig())
    payload["config"].pop("task_type")
    payload["results"] = [
        {
            "model": "random_forest",
            "status": "succeeded",
            "metrics": ExperimentMetrics(
                roc_auc=0.8,
                accuracy=0.8,
                balanced_accuracy=0.8,
                precision=0.8,
                recall=0.8,
                f1=0.8,
                confusion_matrix=[[2, 0], [0, 2]],
            ).model_dump(mode="json"),
        }
    ]

    result = ExperimentSuiteResult.model_validate(payload)

    assert result.config.task_type == "binary_classification"
    assert result.task_type == "binary_classification"
    assert isinstance(result.results[0].metrics, ExperimentMetrics)
