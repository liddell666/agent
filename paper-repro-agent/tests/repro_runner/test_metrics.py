import numpy as np

from repro_runner.metrics import evaluate_classifier, feature_importances


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
