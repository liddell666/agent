import pandas as pd

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
