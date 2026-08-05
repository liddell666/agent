"""Deterministic random-forest baseline experiments."""

from __future__ import annotations

import hashlib
import json
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
    SplitProvenance,
)


class ExperimentError(ValueError):
    """A safe experiment error that can be mapped to a stable API response."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def run_random_forest(
    bundle: DatasetBundle, config: ExperimentConfig
) -> ExperimentResult:
    """Train the V2 reproducible random-forest baseline on a validated bundle."""
    features = bundle.frame[bundle.feature_columns].to_numpy(dtype=float)
    target = bundle.frame[bundle.target_column].to_numpy()
    classes = np.sort(np.unique(target))
    negative_class, positive_class = classes

    try:
        train_indices, test_indices = train_test_split(
            np.arange(len(target)),
            test_size=config.test_size,
            stratify=target,
            random_state=config.random_state,
        )
    except ValueError as exc:
        raise ExperimentError(
            "invalid_split",
            "the requested stratified test split cannot represent both target classes",
        ) from exc

    x_train, x_test = features[train_indices], features[test_indices]
    y_train, y_test = target[train_indices], target[test_indices]
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
    feature_importance = _public_feature_importances(sorted_importances)

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
            test_digest=_test_set_digest(bundle, test_indices),
        ),
        reproducibility_status="baseline_only",
    )


def _round_metric(value: float) -> float:
    return round(float(value), 6)


def _public_feature_importances(
    sorted_importances: list[tuple[str, float]],
) -> list[FeatureImportance]:
    """Round importances without exposing a total other than one."""
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


def _test_set_digest(bundle: DatasetBundle, test_indices: np.ndarray) -> str:
    """Hash held-out feature rows plus labels without retaining their contents."""
    columns = [*bundle.feature_columns, bundle.target_column]
    held_out = bundle.frame.iloc[test_indices][columns]
    payload = {
        "columns": columns,
        "rows": [
            [_digest_value(value) for value in row]
            for row in held_out.itertuples(index=False, name=None)
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _digest_value(value: object) -> object:
    """Convert pandas/numpy scalars to JSON-safe values for deterministic hashing."""
    if isinstance(value, np.generic):
        return value.item()
    return value
