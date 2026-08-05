"""Deterministic random-forest baseline experiments."""

from __future__ import annotations

from uuid import uuid4

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

from repro_runner.data import DatasetBundle
from repro_runner.schemas import (
    ExperimentConfig,
    ExperimentMetrics,
    ExperimentResult,
    FeatureImportance,
)


def run_random_forest(
    bundle: DatasetBundle, config: ExperimentConfig
) -> ExperimentResult:
    """Train the V2 reproducible random-forest baseline on a validated bundle."""
    features = bundle.frame[bundle.feature_columns].to_numpy(dtype=float)
    target = bundle.frame[bundle.target_column].to_numpy()
    classes = np.sort(np.unique(target))
    negative_class, positive_class = classes

    x_train, x_test, y_train, y_test = train_test_split(
        features,
        target,
        test_size=config.test_size,
        stratify=target,
        random_state=config.random_state,
    )
    classifier = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced",
        n_jobs=-1,
        random_state=config.random_state,
    )
    classifier.fit(x_train, y_train)

    positive_index = int(np.where(classifier.classes_ == positive_class)[0][0])
    positive_probabilities = classifier.predict_proba(x_test)[:, positive_index]
    predicted = np.where(
        positive_probabilities >= 0.5, positive_class, negative_class
    )
    metrics = ExperimentMetrics(
        roc_auc=_round_metric(roc_auc_score(y_test == positive_class, positive_probabilities)),
        accuracy=_round_metric(accuracy_score(y_test, predicted)),
        balanced_accuracy=_round_metric(balanced_accuracy_score(y_test, predicted)),
        precision=_round_metric(
            precision_score(y_test, predicted, pos_label=positive_class, zero_division=0)
        ),
        recall=_round_metric(
            recall_score(y_test, predicted, pos_label=positive_class, zero_division=0)
        ),
        f1=_round_metric(f1_score(y_test, predicted, pos_label=positive_class, zero_division=0)),
        confusion_matrix=confusion_matrix(y_test, predicted, labels=classes)
        .astype(int)
        .tolist(),
    )

    sorted_importances = sorted(
        zip(bundle.feature_columns, classifier.feature_importances_),
        key=lambda item: (-float(item[1]), item[0]),
    )
    feature_importance = [
        FeatureImportance(feature=feature, importance=_round_metric(importance))
        for feature, importance in sorted_importances
    ]

    return ExperimentResult(
        experiment_id=f"exp-{uuid4().hex}",
        status="succeeded",
        config=config,
        dataset=bundle.profile,
        metrics=metrics,
        feature_importance=feature_importance,
        reproducibility_status="baseline_only",
    )


def _round_metric(value: float) -> float:
    return round(float(value), 6)
