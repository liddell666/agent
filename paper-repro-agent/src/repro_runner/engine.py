"""Deterministic random-forest baseline experiments."""

from __future__ import annotations

from uuid import uuid4

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from repro_runner.data import DatasetBundle
from repro_runner.metrics import evaluate_classifier, feature_importances
from repro_runner.schemas import (
    ExperimentConfig,
    ExperimentResult,
    SplitProvenance,
)
from repro_runner.split import ExperimentError, make_stratified_split, test_set_digest


def run_random_forest(
    bundle: DatasetBundle, config: ExperimentConfig
) -> ExperimentResult:
    """Train the V2 reproducible random-forest baseline on a validated bundle."""
    features = bundle.frame[bundle.feature_columns].to_numpy(dtype=float)
    target = bundle.frame[bundle.target_column].to_numpy()
    classes = np.sort(np.unique(target))
    train_indices, test_indices = make_stratified_split(
        target, test_size=config.test_size, random_state=config.random_state
    )

    x_train, x_test = features[train_indices], features[test_indices]
    y_train, y_test = target[train_indices], target[test_indices]
    classifier = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced",
        n_jobs=-1,
        random_state=config.random_state,
    )
    classifier.fit(x_train, y_train)
    metrics = evaluate_classifier(classifier, x_test, y_test, classes, threshold=0.5)
    feature_importance = feature_importances(classifier, bundle.feature_columns)

    return ExperimentResult(
        experiment_id=f"exp-{uuid4().hex}",
        status="succeeded",
        config=config,
        dataset=bundle.profile,
        metrics=metrics,
        feature_importance=feature_importance,
        split_provenance=SplitProvenance(
            test_size=config.test_size,
            random_state=config.random_state,
            train_rows=len(train_indices),
            test_rows=len(test_indices),
            test_digest=test_set_digest(bundle, test_indices),
        ),
        reproducibility_status="baseline_only",
    )
