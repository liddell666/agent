from __future__ import annotations

from collections.abc import Sequence
import math

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)

from repro_runner.schemas import ExperimentMetrics, FeatureImportance, RegressionMetrics


NUMERIC_METRIC_NAMES: tuple[str, ...] = (
    "roc_auc",
    "accuracy",
    "balanced_accuracy",
    "precision",
    "recall",
    "f1",
)


def mean_std_over_metrics(
    metrics_list: Sequence[ExperimentMetrics | RegressionMetrics],
) -> tuple[dict[str, float], dict[str, float]]:
    """Return per-metric mean and population std across a list of fold scores."""
    means: dict[str, float] = {}
    stds: dict[str, float] = {}
    if not metrics_list:
        return means, stds
    for name in _numeric_metric_names(metrics_list[0]):
        values = [float(getattr(item, name)) for item in metrics_list]
        means[name] = _round_metric(float(np.mean(values)))
        stds[name] = _round_metric(float(np.std(values)))
    return means, stds


def evaluate_regressor(regressor, x_test, y_test) -> RegressionMetrics:
    predicted = np.asarray(regressor.predict(x_test), dtype=float)
    if not np.isfinite(predicted).all():
        raise ValueError("regression predictions must be finite")
    return RegressionMetrics(
        mae=_round_metric(mean_absolute_error(y_test, predicted)),
        rmse=_round_metric(math.sqrt(mean_squared_error(y_test, predicted))),
        r2=_round_metric(r2_score(y_test, predicted)),
    )


def evaluate_classifier(
    classifier,
    x_test,
    y_test,
    classes,
    threshold: float,
) -> ExperimentMetrics:
    sorted_classes = np.sort(np.unique(classes))
    negative_class, positive_class = sorted_classes
    positive_scores, predicted = _classification_outputs(
        classifier, x_test, positive_class, negative_class, threshold
    )

    return ExperimentMetrics(
        roc_auc=_round_metric(roc_auc_score(y_test == positive_class, positive_scores)),
        accuracy=_round_metric(accuracy_score(y_test, predicted)),
        balanced_accuracy=_round_metric(balanced_accuracy_score(y_test, predicted)),
        precision=_round_metric(
            precision_score(y_test, predicted, pos_label=positive_class, zero_division=0)
        ),
        recall=_round_metric(
            recall_score(y_test, predicted, pos_label=positive_class, zero_division=0)
        ),
        f1=_round_metric(
            f1_score(y_test, predicted, pos_label=positive_class, zero_division=0)
        ),
        confusion_matrix=confusion_matrix(y_test, predicted, labels=sorted_classes)
        .astype(int)
        .tolist(),
    )


def feature_importances(classifier, feature_columns) -> list[FeatureImportance]:
    if hasattr(classifier, "feature_importances_"):
        importances = np.asarray(classifier.feature_importances_, dtype=float)
    elif hasattr(classifier, "coef_"):
        coefficients = np.asarray(classifier.coef_, dtype=float)
        if coefficients.ndim == 1:
            magnitudes = np.abs(coefficients)
        else:
            magnitudes = np.abs(coefficients).sum(axis=0)
        total = float(magnitudes.sum())
        if total == 0.0:
            return []
        importances = magnitudes / total
    else:
        return []

    if len(importances) != len(feature_columns):
        raise ValueError("feature_importance_length_mismatch")
    if not feature_columns:
        return []

    sorted_importances = sorted(
        zip(feature_columns, importances),
        key=lambda item: (-float(item[1]), item[0]),
    )
    rounded = [
        (feature, _round_metric(importance))
        for feature, importance in sorted_importances
    ]
    residual = _round_metric(1.0 - sum(importance for _, importance in rounded))
    first_feature, first_importance = rounded[0]
    rounded[0] = (first_feature, _round_metric(first_importance + residual))

    return [
        FeatureImportance(feature=feature, importance=importance)
        for feature, importance in sorted(rounded, key=lambda item: (-item[1], item[0]))
    ]


def _classification_outputs(
    classifier,
    x_test,
    positive_class,
    negative_class,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    classifier_classes = np.asarray(classifier.classes_)
    positive_index = int(np.where(classifier_classes == positive_class)[0][0])

    if hasattr(classifier, "predict_proba"):
        positive_scores = np.asarray(classifier.predict_proba(x_test), dtype=float)[
            :, positive_index
        ]
        predicted = np.where(
            positive_scores >= threshold, positive_class, negative_class
        )
        return positive_scores, predicted

    decision_scores = np.asarray(classifier.decision_function(x_test), dtype=float)
    if decision_scores.ndim > 1:
        aligned_scores = decision_scores[:, positive_index]
    else:
        aligned_scores = decision_scores
        if len(classifier_classes) > 1 and classifier_classes[1] != positive_class:
            aligned_scores = -aligned_scores
    predicted = np.where(aligned_scores >= 0.0, positive_class, negative_class)
    return aligned_scores, predicted


def _round_metric(value: float) -> float:
    return round(float(value), 6)


def _numeric_metric_names(metrics: ExperimentMetrics | RegressionMetrics) -> tuple[str, ...]:
    return tuple(
        name
        for name in type(metrics).model_fields
        if isinstance(getattr(metrics, name), float)
    )
