import numpy as np
import pandas as pd
import pytest

import repro_runner.suite_engine as suite_engine
from repro_runner.config import Settings
from repro_runner.data import load_dataset
from repro_runner.model_registry import ModelSpec, get_model_spec as real_get_model_spec
from repro_runner.schemas import (
    DatasetOptions,
    ExperimentMetrics,
    ModelRunResult,
    ModelSuiteConfig,
    RegressionMetrics,
)
from repro_runner.split import ExperimentError, make_stratified_split


TEST_DIGEST = "sha256:21d827456c211be096b22e4364e63ce8c05e3167d91ebd8a22846747edf42056"


def _bundle(frame: pd.DataFrame):
    return load_dataset(frame.to_csv(index=False).encode(), DatasetOptions(), Settings())


def _regression_bundle():
    values = np.arange(40, dtype=float)
    frame = pd.DataFrame(
        {
            "x1": values,
            "x2": values % 5,
            "region": ["a", "b", "c", "d"] * 10,
            "target": 3.0 * values + (values % 3),
        }
    )
    return load_dataset(
        frame.to_csv(index=False).encode(),
        DatasetOptions(
            task_type="regression",
            target_column="target",
            target_column_confirmed=True,
        ),
        Settings(),
    )


def _dataset(rows_per_class: int = 60) -> pd.DataFrame:
    values = np.arange(rows_per_class * 2)
    labels = np.array([0, 1] * rows_per_class)
    return pd.DataFrame(
        {
            "signal": labels,
            "secondary": values % 7,
            "noise": (values * 3) % 11,
            "Y_cls": labels,
        }
    )


def test_regression_suite_trains_core_models_with_shared_provenance():
    config = ModelSuiteConfig(
        task_type="regression",
        models=["linear_regression", "random_forest", "gradient_boosting"],
        optimization_metric="rmse",
        cv_folds=3,
        n_iter=1,
        n_jobs=1,
        random_state=13,
    )

    result = suite_engine.run_model_suite(_regression_bundle(), config)

    assert result.status == "succeeded"
    assert result.performance_ranking
    assert all(item.metrics.rmse >= 0 for item in result.results)
    assert len({item.split_provenance.test_digest for item in result.results}) == 1
    assert all(set(item.cv_fold_scores) == {"mae", "rmse", "r2"} for item in result.results)


def test_performance_ranking_minimizes_regression_errors_and_maximizes_r2():
    results = [
        ModelRunResult(
            model="linear_regression",
            status="succeeded",
            metrics=RegressionMetrics(mae=3.0, rmse=4.0, r2=0.8),
        ),
        ModelRunResult(
            model="random_forest",
            status="succeeded",
            metrics=RegressionMetrics(mae=1.0, rmse=2.0, r2=0.5),
        ),
    ]

    assert suite_engine._performance_ranking(results, "rmse") == [
        "random_forest",
        "linear_regression",
    ]
    assert suite_engine._performance_ranking(results, "r2") == [
        "linear_regression",
        "random_forest",
    ]


def test_run_model_suite_reports_repeated_cv_aggregation_and_json_safe_params(
    monkeypatch,
):
    bundle = _bundle(_dataset())
    config = ModelSuiteConfig(
        models=["logistic_regression"],
        workflow_version="multimodel-0.8.0",
        cv_folds=3,
        n_seeds=2,
        n_iter=1,
        n_jobs=2,
    )
    expected_train, _ = make_stratified_split(
        bundle.frame["Y_cls"].to_numpy(), config.test_size, config.random_state
    )

    class SpySearch:
        def __init__(self, estimator, param_distributions, **_kwargs):
            self.estimator = estimator
            self.param_distributions = param_distributions

        def fit(self, x_train, y_train):
            fit_params = {
                name: values[0] for name, values in self.param_distributions.items()
            }
            self.best_estimator_ = self.estimator.set_params(**fit_params)
            self.best_estimator_.fit(x_train, y_train)
            self.best_params_ = {
                "choice": np.int64(2),
                "weights": np.array([1, 2], dtype=np.int64),
            }
            self.best_score_ = 0.812345
            return self

    monkeypatch.setattr(suite_engine, "RandomizedSearchCV", SpySearch)

    first = suite_engine.run_model_suite(bundle, config)
    second = suite_engine.run_model_suite(bundle, config)

    assert first.status == "succeeded"
    assert first.reproducibility_status == "cv_evaluated"
    assert first.runtime is not None
    assert first.runtime.workflow_version == "multimodel-0.8.0"
    assert [item.status for item in first.results] == ["succeeded"]
    assert first.split_provenance == second.split_provenance
    assert first.split_provenance.train_rows == len(expected_train)
    assert first.split_provenance.test_digest.startswith("sha256:")
    item = first.results[0]
    assert item.best_params == {"choice": 2, "weights": [1, 2]}
    assert len(item.cv_fold_scores["roc_auc"]) == config.cv_folds * config.n_seeds
    assert len(item.seed_means["roc_auc"]) == config.n_seeds
    assert item.cv_mean["roc_auc"] == pytest.approx(
        float(np.mean(item.cv_fold_scores["roc_auc"])), abs=1e-5
    )
    assert item.cv_std["roc_auc"] == pytest.approx(
        float(np.std(item.cv_fold_scores["roc_auc"])), abs=1e-5
    )
    assert item.metrics is not None


def test_run_model_suite_marks_missing_dependency_unavailable_and_continues(
    monkeypatch,
):
    bundle = _bundle(_dataset())
    config = ModelSuiteConfig(
        models=["random_forest", "logistic_regression"],
        cv_folds=3,
        n_iter=1,
    )

    class SpySearch:
        def __init__(self, estimator, param_distributions, **_kwargs):
            self.estimator = estimator
            self.param_distributions = param_distributions

        def fit(self, x_train, y_train):
            fit_params = {
                name: values[0] for name, values in self.param_distributions.items()
            }
            self.best_estimator_ = self.estimator.set_params(**fit_params)
            self.best_estimator_.fit(x_train, y_train)
            self.best_params_ = fit_params
            self.best_score_ = 0.7
            return self

    def failing_get_model_spec(
        name, class_counts, random_state, use_gpu, **kwargs
    ):
        if name == "random_forest":
            raise ImportError(
                "module missing while reading C:\\sensitive\\private-wheel.whl token=abc123"
            )
        return real_get_model_spec(
            name, class_counts, random_state, use_gpu, **kwargs
        )

    monkeypatch.setattr(suite_engine, "RandomizedSearchCV", SpySearch)
    monkeypatch.setattr(suite_engine, "get_model_spec", failing_get_model_spec)

    result = suite_engine.run_model_suite(bundle, config)
    payload = result.model_dump_json()

    assert result.status == "partial"
    assert [item.model for item in result.results] == config.models
    assert result.results[0].status == "unavailable"
    assert result.results[0].error is not None
    assert result.results[0].error.code == "missing_dependency"
    assert result.results[0].error.message == "random_forest dependency is unavailable"
    assert result.results[1].status == "succeeded"
    assert result.performance_ranking == ["logistic_regression"]
    assert "private-wheel.whl" not in payload
    assert "abc123" not in payload


def test_run_model_suite_keeps_shared_split_provenance_when_one_model_fails():
    result = suite_engine.run_model_suite(
        _bundle(_dataset(rows_per_class=10)),
        ModelSuiteConfig(
            models=["random_forest", "mlp", "logistic_regression"],
            cv_folds=3,
            n_iter=1,
            n_jobs=1,
        ),
    )

    assert result.status == "partial"
    assert [item.status for item in result.results] == [
        "succeeded",
        "failed",
        "succeeded",
    ]
    assert result.performance_ranking == ["random_forest", "logistic_regression"]
    assert {
        item.split_provenance.test_digest
        for item in result.results
        if item.status == "succeeded"
    } == {TEST_DIGEST}


def test_run_model_suite_preserves_safe_registry_dependency_message_and_sanitizes_model_failures(
    monkeypatch,
):
    bundle = _bundle(_dataset())
    config = ModelSuiteConfig(
        models=["xgboost", "svm"],
        cv_folds=3,
        n_iter=1,
    )

    class FailingSearch:
        def __init__(self, estimator, param_distributions, **_kwargs):
            self.estimator = estimator
            self.param_distributions = param_distributions

        def fit(self, _x_train, _y_train):
            raise RuntimeError(
                "rows=[1, 2] feature_value=9.5 path=C:\\sensitive\\train.csv secret=shh"
            )

    def controlled_get_model_spec(
        name, class_counts, random_state, use_gpu, **kwargs
    ):
        if name == "xgboost":
            raise ImportError("xgboost dependency is not installed")
        return real_get_model_spec(
            name, class_counts, random_state, use_gpu, **kwargs
        )

    monkeypatch.setattr(suite_engine, "RandomizedSearchCV", FailingSearch)
    monkeypatch.setattr(suite_engine, "get_model_spec", controlled_get_model_spec)

    result = suite_engine.run_model_suite(bundle, config)
    payload = result.model_dump_json()

    assert result.status == "failed"
    assert result.performance_ranking == []
    assert [item.status for item in result.results] == ["unavailable", "failed"]
    assert result.results[0].error is not None
    assert result.results[0].error.code == "missing_dependency"
    assert result.results[0].error.message == "xgboost dependency is not installed"
    assert result.results[1].error is not None
    assert result.results[1].error.code == "model_training_failed"
    assert result.results[1].error.message == "svm model training failed (RuntimeError)"
    assert "rows=[1, 2]" not in payload
    assert "feature_value=9.5" not in payload
    assert "train.csv" not in payload
    assert "secret=shh" not in payload


def test_run_model_suite_rejects_invalid_cv_folds_before_search(monkeypatch):
    bundle = _bundle(pd.DataFrame({"x": [0, 1, 2, 3], "Y_cls": [0, 0, 1, 1]}))
    config = ModelSuiteConfig(
        models=["logistic_regression"],
        test_size=0.5,
        cv_folds=3,
        n_iter=1,
    )
    called = False

    class UnexpectedSearch:
        def __init__(self, *_args, **_kwargs):
            nonlocal called
            called = True

    monkeypatch.setattr(suite_engine, "RandomizedSearchCV", UnexpectedSearch)

    with pytest.raises(ExperimentError) as raised:
        suite_engine.run_model_suite(bundle, config)

    assert raised.value.code == "invalid_cv_folds"
    assert "fold" in raised.value.message
    assert called is False


def test_run_model_suite_rejects_infeasible_nested_cv_before_model_failures():
    bundle = _bundle(
        pd.DataFrame(
            {
                "x": list(range(8)),
                "Y_cls": [0, 0, 0, 0, 1, 1, 1, 1],
            }
        )
    )

    with pytest.raises(ExperimentError) as raised:
        suite_engine.run_model_suite(
            bundle,
            ModelSuiteConfig(
                models=["logistic_regression"],
                test_size=0.25,
                cv_folds=3,
                n_iter=1,
                n_jobs=1,
            ),
        )

    assert raised.value.code == "invalid_nested_cv_folds"
    assert "nested" in raised.value.message


def test_run_model_suite_ranks_by_auc_then_f1_then_recall_with_stable_ties(
    monkeypatch,
):
    bundle = _bundle(_dataset())
    config = ModelSuiteConfig(
        models=["logistic_regression", "random_forest", "svm"],
        cv_folds=3,
        n_iter=1,
    )

    class DummyEstimator:
        def __init__(self, model_name):
            self.model_name = model_name
            self.classes_ = np.array([0, 1])

        def fit(self, _x_train, _y_train):
            return self

        def set_params(self, **_params):
            return self

        def predict_proba(self, x_test):
            return np.tile([0.4, 0.6], (len(x_test), 1))

    class SpySearch:
        def __init__(self, estimator, param_distributions, **_kwargs):
            self.estimator = estimator
            self.param_distributions = param_distributions

        def fit(self, x_train, y_train):
            self.best_estimator_ = self.estimator.fit(x_train, y_train)
            self.best_params_ = {"k": np.int64(1)}
            self.best_score_ = 0.5
            return self

    def fake_get_model_spec(
        name, class_counts, random_state, use_gpu, **kwargs
    ):
        del class_counts, random_state, use_gpu
        del kwargs
        return ModelSpec(
            name=name,
            estimator=DummyEstimator(name),
            search_space={"k": [1]},
            optional_dependency=None,
            feature_importance_kind="none",
        )

    metric_map = {
        "svm": ExperimentMetrics(
            roc_auc=0.97,
            accuracy=0.9,
            balanced_accuracy=0.9,
            precision=0.9,
            recall=0.4,
            f1=0.6,
            confusion_matrix=[[5, 1], [1, 5]],
        ),
        "logistic_regression": ExperimentMetrics(
            roc_auc=0.91,
            accuracy=0.8,
            balanced_accuracy=0.8,
            precision=0.8,
            recall=0.6,
            f1=0.7,
            confusion_matrix=[[4, 2], [1, 5]],
        ),
        "random_forest": ExperimentMetrics(
            roc_auc=0.91,
            accuracy=0.7,
            balanced_accuracy=0.7,
            precision=0.7,
            recall=0.6,
            f1=0.7,
            confusion_matrix=[[4, 2], [1, 5]],
        ),
    }

    def fake_evaluate_classifier(classifier, x_test, y_test, classes, threshold):
        del x_test, y_test, classes, threshold
        return metric_map[classifier.model_name]

    monkeypatch.setattr(suite_engine, "RandomizedSearchCV", SpySearch)
    monkeypatch.setattr(suite_engine, "get_model_spec", fake_get_model_spec)
    monkeypatch.setattr(suite_engine, "evaluate_classifier", fake_evaluate_classifier)

    result = suite_engine.run_model_suite(bundle, config)

    assert result.performance_ranking == [
        "svm",
        "logistic_regression",
        "random_forest",
    ]
    assert result.results[0].best_params == {"k": 1}


def test_run_model_suite_preserves_linear_pipeline_feature_importance_without_raw_rows():
    frame = _dataset(rows_per_class=40)
    bundle = _bundle(frame)
    config = ModelSuiteConfig(
        models=["logistic_regression"],
        cv_folds=3,
        n_iter=1,
    )

    result = suite_engine.run_model_suite(bundle, config)

    model_result = result.results[0]
    payload = result.model_dump_json()

    assert model_result.status == "succeeded"
    assert model_result.feature_importance
    assert sum(item.importance for item in model_result.feature_importance) == pytest.approx(1.0)
    assert frame.to_csv(index=False).strip() not in payload


def test_run_model_suite_supports_mixed_features_and_reports_transformed_names():
    labels = np.array([0, 1] * 30)
    frame = pd.DataFrame(
        {
            "slope": np.arange(60, dtype=float),
            "landform": ["secret-alpha", "secret-beta", "secret-gamma"] * 20,
            "Y_cls": labels,
        }
    )
    bundle = load_dataset(
        frame.to_csv(index=False).encode(),
        DatasetOptions(target_column="Y_cls"),
        Settings(),
    )

    result = suite_engine.run_model_suite(
        bundle,
        ModelSuiteConfig(
            models=["logistic_regression", "random_forest"],
            cv_folds=3,
            n_iter=1,
            n_jobs=1,
        ),
    )

    assert result.status == "succeeded"
    assert result.preprocessing is not None
    assert result.preprocessing.numeric_columns == ["slope"]
    assert result.preprocessing.categorical_columns == ["landform"]
    assert set(result.preprocessing.transformed_feature_names) == {
        "slope",
        "landform__category_0",
        "landform__category_1",
        "landform__category_2",
    }
    payload = result.model_dump_json()
    assert "secret-alpha" not in payload
    assert "secret-beta" not in payload
    assert "secret-gamma" not in payload
    for item in result.results:
        assert item.status == "succeeded"
        if item.model == "random_forest":
            assert {importance.feature for importance in item.feature_importance} == set(
                result.preprocessing.transformed_feature_names
            )


def test_run_model_suite_fits_imputation_inside_pipeline_and_hashes_missing_values():
    labels = np.array([0, 1] * 30)
    frame = pd.DataFrame(
        {
            "slope": np.arange(60, dtype=float),
            "landform": ["A", "B", "C"] * 20,
            "Y_cls": labels,
        }
    )
    frame.loc[0, "slope"] = np.nan
    frame.loc[1, "landform"] = None
    bundle = load_dataset(
        frame.to_csv(index=False).encode(),
        DatasetOptions(target_column="Y_cls", missing_policy="impute"),
        Settings(),
    )

    result = suite_engine.run_model_suite(
        bundle,
        ModelSuiteConfig(
            models=["logistic_regression"],
            cv_folds=3,
            n_iter=1,
            n_jobs=1,
        ),
    )

    assert result.status == "succeeded"
    assert result.results[0].status == "succeeded"
    assert result.split_provenance.test_digest.startswith("sha256:")


def test_run_model_suite_accepts_mixed_numeric_and_text_tokens_as_category():
    frame = pd.DataFrame(
        {
            "landform": ["1", "A", "1", "B"] * 15,
            "Y_cls": [0, 0, 1, 1] * 15,
        }
    )
    bundle = load_dataset(
        frame.to_csv(index=False).encode(),
        DatasetOptions(target_column="Y_cls"),
        Settings(),
    )

    assert bundle.feature_columns == ["landform"]
    result = suite_engine.run_model_suite(
        bundle,
        ModelSuiteConfig(
            models=["logistic_regression"],
            cv_folds=3,
            n_iter=1,
            n_jobs=1,
        ),
    )
    assert result.status == "succeeded"


def test_run_model_suite_supports_balanced_undersampling_fold_safely():
    bundle = _bundle(_dataset())
    bundle.sampling_strategy = "balanced_undersample"

    result = suite_engine.run_model_suite(
        bundle,
        ModelSuiteConfig(models=["random_forest"], cv_folds=3, n_iter=1, n_jobs=1),
    )

    assert result.status == "succeeded"
    assert result.results[0].status == "succeeded"
    assert result.preprocessing.sampling_strategy == "balanced_undersample"


def test_resample_training_partition_undersamples_majority_class_only():
    frame = pd.DataFrame({"feature": np.arange(10)})
    target = np.array([0, 0, 0, 0, 0, 0, 0, 1, 1, 1])

    resampled_x, resampled_y = suite_engine._resample_training_partition(
        frame, target, "balanced_undersample", seed=0
    )
    counts = dict(zip(*np.unique(resampled_y, return_counts=True)))
    assert counts == {0: 3, 1: 3}
    assert len(resampled_x) == 6

    unchanged_x, unchanged_y = suite_engine._resample_training_partition(
        frame, target, "original", seed=0
    )
    assert len(unchanged_y) == 10
    assert unchanged_x.equals(frame)
