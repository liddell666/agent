from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

import repro_runner.engine as engine
from repro_runner.config import Settings
from repro_runner.data import load_dataset
from repro_runner.engine import run_random_forest
from repro_runner.schemas import DatasetOptions, ExperimentConfig


def test_same_seed_produces_same_metrics():
    frame = pd.DataFrame(
        {
            "x1": [0, 1, 0, 1] * 30,
            "x2": [1, 1, 0, 0] * 30,
            "Y_cls": [0, 1, 0, 1] * 30,
        }
    )
    bundle = load_dataset(frame.to_csv(index=False).encode(), DatasetOptions(), Settings())

    first = run_random_forest(bundle, ExperimentConfig())
    second = run_random_forest(bundle, ExperimentConfig())

    assert first.metrics == second.metrics
    assert first.reproducibility_status == "baseline_only"


def test_feature_importances_are_sorted_and_sum_to_one():
    frame = pd.DataFrame(
        {
            "signal": [0, 0, 1, 1] * 40,
            "noise": [1, 0, 1, 0] * 40,
            "Y_cls": [0, 0, 1, 1] * 40,
        }
    )
    bundle = load_dataset(frame.to_csv(index=False).encode(), DatasetOptions(), Settings())

    result = run_random_forest(bundle, ExperimentConfig())

    values = [item.importance for item in result.feature_importance]
    assert values == sorted(values, reverse=True)
    assert abs(sum(values) - 1.0) < 1e-6


def test_rounding_preserves_feature_importance_total():
    class ResidualForest:
        def __init__(self, **_kwargs):
            pass

        def fit(self, _features, _target):
            self.classes_ = np.array([0, 1])
            self.feature_importances_ = np.array([0.3333334, 0.3333333, 0.3333333])
            return self

        def predict_proba(self, features):
            return np.tile([0.6, 0.4], (len(features), 1))

    frame = pd.DataFrame(
        {
            "a": [0, 1] * 20,
            "b": [1, 0] * 20,
            "c": [0, 0, 1, 1] * 10,
            "Y_cls": [0, 1] * 20,
        }
    )
    bundle = load_dataset(frame.to_csv(index=False).encode(), DatasetOptions(), Settings())

    with patch.object(engine, "RandomForestClassifier", ResidualForest):
        result = run_random_forest(bundle, ExperimentConfig())

    assert [item.feature for item in result.feature_importance] == ["a", "b", "c"]
    assert [item.importance for item in result.feature_importance] == [
        0.333334,
        0.333333,
        0.333333,
    ]
    assert sum(item.importance for item in result.feature_importance) == 1.0


def test_config_controls_split_and_fixed_forest_parameters():
    frame = pd.DataFrame(
        {
            "x": [0, 1, 0, 1] * 40,
            "Y_cls": [0, 1, 0, 1] * 40,
        }
    )
    bundle = load_dataset(frame.to_csv(index=False).encode(), DatasetOptions(), Settings())
    config = ExperimentConfig(test_size=0.25, random_state=7)

    with (
        patch.object(engine, "train_test_split", wraps=engine.train_test_split) as split,
        patch.object(
            engine, "RandomForestClassifier", wraps=RandomForestClassifier
        ) as forest,
    ):
        run_random_forest(bundle, config)

    assert split.call_args.kwargs["test_size"] == 0.25
    assert split.call_args.kwargs["random_state"] == 7
    assert split.call_args.kwargs["stratify"].tolist() == bundle.frame["Y_cls"].tolist()
    assert forest.call_args.kwargs == {
        "n_estimators": 300,
        "class_weight": "balanced",
        "n_jobs": -1,
        "random_state": 7,
    }


def test_uses_sorted_second_label_as_positive_class_and_half_threshold():
    class ReversedClassForest:
        def __init__(self, **_kwargs):
            pass

        def fit(self, _features, _target):
            self.classes_ = np.array(["zeta", "alpha"])
            self.feature_importances_ = np.array([1.0])
            return self

        def predict_proba(self, features):
            zeta_probability = np.where(features[:, 0] == 1, 0.4, 0.1)
            return np.column_stack((zeta_probability, 1 - zeta_probability))

    frame = pd.DataFrame(
        {"score": [0, 1] * 10, "Y_cls": ["alpha", "zeta"] * 10}
    )
    bundle = load_dataset(frame.to_csv(index=False).encode(), DatasetOptions(), Settings())

    with patch.object(engine, "RandomForestClassifier", ReversedClassForest):
        result = run_random_forest(bundle, ExperimentConfig())

    assert result.metrics.roc_auc == 1.0
    assert result.metrics.accuracy == 0.5
    assert result.metrics.balanced_accuracy == 0.5
    assert result.metrics.precision == 0.0
    assert result.metrics.recall == 0.0
    assert result.metrics.f1 == 0.0
    assert result.metrics.confusion_matrix == [[2, 0], [2, 0]]
