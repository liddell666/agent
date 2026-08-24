import numpy as np
import pytest
from sklearn.linear_model import LinearRegression

import repro_runner.metrics as metrics_module
from repro_runner.metrics import (
    evaluate_classifier,
    feature_importances,
    mean_std_over_metrics,
)
from repro_runner.schemas import RegressionMetrics


class ProbabilityClassifier:
    classes_ = np.array(["alpha", "zeta"])

    def __init__(self, positive_probabilities):
        self._positive_probabilities = np.array(positive_probabilities, dtype=float)

    def predict_proba(self, _features):
        return np.column_stack((1 - self._positive_probabilities, self._positive_probabilities))


class TreeImportanceClassifier:
    feature_importances_ = np.array([0.3333334, 0.3333333, 0.3333333])


class LinearImportanceClassifier:
    coef_ = np.array([[-2.0, 0.5, 1.0]])


class NoImportanceClassifier:
    pass


def test_evaluate_classifier_returns_metrics_and_confusion_matrix():
    classifier = ProbabilityClassifier([0.8, 0.4, 0.7, 0.3])
    x_test = np.array([[0], [1], [2], [3]], dtype=float)
    y_test = np.array(["zeta", "alpha", "zeta", "alpha"])

    metrics = evaluate_classifier(
        classifier,
        x_test,
        y_test,
        classes=np.array(["alpha", "zeta"]),
        threshold=0.5,
    )

    assert metrics.roc_auc == 1.0
    assert metrics.accuracy == 1.0
    assert metrics.balanced_accuracy == 1.0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.confusion_matrix == [[2, 0], [0, 2]]


def test_evaluate_classifier_threshold_changes_predictions():
    classifier = ProbabilityClassifier([0.8, 0.4, 0.7, 0.3])
    x_test = np.array([[0], [1], [2], [3]], dtype=float)
    y_test = np.array(["zeta", "alpha", "zeta", "alpha"])

    low_threshold = evaluate_classifier(
        classifier,
        x_test,
        y_test,
        classes=np.array(["alpha", "zeta"]),
        threshold=0.5,
    )
    high_threshold = evaluate_classifier(
        classifier,
        x_test,
        y_test,
        classes=np.array(["alpha", "zeta"]),
        threshold=0.75,
    )

    assert low_threshold.confusion_matrix == [[2, 0], [0, 2]]
    assert high_threshold.confusion_matrix == [[2, 0], [1, 1]]
    assert high_threshold.accuracy == 0.75
    assert high_threshold.recall == 0.5
    assert high_threshold.f1 == 0.666667


def test_evaluate_regressor_returns_natural_public_metrics():
    estimator = LinearRegression().fit([[0], [1], [2], [3]], [0, 2, 4, 6])

    metrics = metrics_module.evaluate_regressor(
        estimator, [[4], [5]], np.array([8.0, 11.0])
    )

    assert metrics.mae == pytest.approx(0.5)
    assert metrics.rmse == pytest.approx(np.sqrt(0.5))
    assert metrics.r2 == pytest.approx(7 / 9)
    assert metrics.mae >= 0
    assert metrics.rmse >= 0


def test_regression_metric_summary_never_exposes_negative_error_scores():
    rows = [
        RegressionMetrics(mae=1.0, rmse=2.0, r2=0.5),
        RegressionMetrics(mae=3.0, rmse=4.0, r2=-0.5),
    ]

    mean, std = mean_std_over_metrics(rows)

    assert mean == {"mae": 2.0, "rmse": 3.0, "r2": 0.0}
    assert std == {"mae": 1.0, "rmse": 1.0, "r2": 0.5}
    assert all(value >= 0 for key, value in mean.items() if key != "r2")


def test_feature_importances_preserve_rounding_sorting_and_total_for_trees():
    items = feature_importances(TreeImportanceClassifier(), ["a", "b", "c"])

    assert [item.feature for item in items] == ["a", "b", "c"]
    assert [item.importance for item in items] == [0.333334, 0.333333, 0.333333]
    assert sum(item.importance for item in items) == 1.0


def test_feature_importances_use_coefficient_magnitudes_for_linear_models():
    items = feature_importances(LinearImportanceClassifier(), ["f1", "f2", "f3"])

    assert [item.feature for item in items] == ["f1", "f3", "f2"]
    assert [item.importance for item in items] == [0.571429, 0.285714, 0.142857]


def test_feature_importances_are_empty_when_model_exposes_none():
    assert feature_importances(NoImportanceClassifier(), ["f1"]) == []
