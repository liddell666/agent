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
    ModelSuiteConfig,
)
from repro_runner.split import ExperimentError, make_stratified_split


def _bundle(frame: pd.DataFrame):
    return load_dataset(frame.to_csv(index=False).encode(), DatasetOptions(), Settings())


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


def test_run_model_suite_uses_shared_holdout_training_only_cv_and_json_safe_params(
    monkeypatch,
):
    bundle = _bundle(_dataset())
    config = ModelSuiteConfig(
        models=["logistic_regression", "random_forest"],
        cv_folds=3,
        n_iter=1,
        n_jobs=2,
    )
    expected_train, _ = make_stratified_split(
        bundle.frame["Y_cls"].to_numpy(), config.test_size, config.random_state
    )
    expected_train_class_counts = {
        "0": int((bundle.frame.iloc[expected_train]["Y_cls"] == 0).sum()),
        "1": int((bundle.frame.iloc[expected_train]["Y_cls"] == 1).sum()),
    }
    observed_class_counts = []
    init_calls = []
    fit_calls = []

    class SpySearch:
        def __init__(
            self,
            estimator,
            param_distributions,
            n_iter,
            scoring,
            cv,
            random_state,
            n_jobs,
            refit,
            error_score,
        ):
            self.estimator = estimator
            self.param_distributions = param_distributions
            self.cv = cv
            init_calls.append(
                {
                    "cv": cv,
                    "n_iter": n_iter,
                    "scoring": scoring,
                    "random_state": random_state,
                    "n_jobs": n_jobs,
                    "refit": refit,
                    "error_score": error_score,
                }
            )

        def fit(self, x_train, y_train):
            fit_calls.append(
                {
                    "rows": len(y_train),
                    "class_counts": {
                        str(label): int(count)
                        for label, count in zip(*np.unique(y_train, return_counts=True))
                    },
                }
            )
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

    def recording_get_model_spec(name, class_counts, random_state, use_gpu):
        observed_class_counts.append((name, dict(class_counts)))
        return real_get_model_spec(name, class_counts, random_state, use_gpu)

    monkeypatch.setattr(suite_engine, "RandomizedSearchCV", SpySearch)
    monkeypatch.setattr(suite_engine, "get_model_spec", recording_get_model_spec)

    first = suite_engine.run_model_suite(bundle, config)
    second = suite_engine.run_model_suite(bundle, config)

    assert first.status == "succeeded"
    assert [item.status for item in first.results] == ["succeeded", "succeeded"]
    assert first.split_provenance == second.split_provenance
    assert first.split_provenance.train_rows == len(expected_train)
    assert first.split_provenance.test_digest.startswith("sha256:")
    assert first.performance_ranking
    assert set(first.performance_ranking) == {
        "logistic_regression",
        "random_forest",
    }
    assert first.results[0].best_params == {"choice": 2, "weights": [1, 2]}
    assert all(item.metrics is not None for item in first.results)
    assert observed_class_counts == [
        ("logistic_regression", expected_train_class_counts),
        ("random_forest", expected_train_class_counts),
        ("logistic_regression", expected_train_class_counts),
        ("random_forest", expected_train_class_counts),
    ]
    assert len(init_calls) == 4
    assert init_calls[0]["cv"] is init_calls[1]["cv"]
    assert init_calls[2]["cv"] is init_calls[3]["cv"]
    assert init_calls[0]["cv"] is not init_calls[2]["cv"]
    assert init_calls[0]["cv"].n_splits == config.cv_folds
    assert init_calls[0]["cv"].shuffle is True
    assert init_calls[0]["cv"].random_state == config.random_state
    assert all(call["scoring"] == config.optimization_metric for call in init_calls)
    assert all(call["n_jobs"] == config.n_jobs for call in init_calls)
    assert all(call["refit"] is True for call in init_calls)
    assert all(call["error_score"] == "raise" for call in init_calls)
    assert all(call["rows"] == len(expected_train) for call in fit_calls)
    assert all(
        call["class_counts"] == expected_train_class_counts for call in fit_calls
    )


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

    def failing_get_model_spec(name, class_counts, random_state, use_gpu):
        if name == "random_forest":
            raise ImportError("random_forest dependency is not installed")
        return real_get_model_spec(name, class_counts, random_state, use_gpu)

    monkeypatch.setattr(suite_engine, "RandomizedSearchCV", SpySearch)
    monkeypatch.setattr(suite_engine, "get_model_spec", failing_get_model_spec)

    result = suite_engine.run_model_suite(bundle, config)

    assert result.status == "partial"
    assert [item.model for item in result.results] == config.models
    assert result.results[0].status == "unavailable"
    assert result.results[0].error is not None
    assert result.results[0].error.code == "missing_dependency"
    assert result.results[1].status == "succeeded"
    assert result.performance_ranking == ["logistic_regression"]


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

    def fake_get_model_spec(name, class_counts, random_state, use_gpu):
        del class_counts, random_state, use_gpu
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
