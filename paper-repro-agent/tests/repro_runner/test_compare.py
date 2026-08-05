import json

import pytest

from repro_runner.compare import compare_metrics
from repro_runner.config import Settings
from repro_runner.schemas import (
    DatasetProfile,
    ExperimentConfig,
    ExperimentMetrics,
    ExperimentResult,
)
from repro_runner.storage import ResultNotFoundError, create_experiment_id, load_result, save_result


def make_result(metric_name="roc_auc", value=0.90, dataset="test", split="test"):
    del dataset, split  # V2 reports metrics for its fixed held-out test split.
    metrics = {
        "roc_auc": 0.8,
        "accuracy": 0.8,
        "balanced_accuracy": 0.8,
        "precision": 0.8,
        "recall": 0.8,
        "f1": 0.8,
        "confusion_matrix": [[4, 1], [1, 4]],
    }
    metrics[metric_name] = value
    return ExperimentResult(
        experiment_id="exp-20260805T010203Z-deadbeef",
        status="succeeded",
        config=ExperimentConfig(),
        dataset=DatasetProfile(rows=10, features=2, target="Y_cls", missing_values=0, duplicate_rows=0),
        metrics=ExperimentMetrics(**metrics),
        feature_importance=[],
    )


def test_comparison_reports_absolute_and_relative_difference():
    result = make_result(metric_name="roc_auc", value=0.90, dataset="test", split="test")

    response = compare_metrics(
        result,
        [{"name": "AUC", "reported_value": 0.91, "dataset": "test", "split": "test"}],
    )

    item = response.items[0]
    assert item.comparable is True
    assert item.absolute_difference == -0.01
    assert item.relative_difference == pytest.approx(-0.010989)
    assert item.paper_value == 0.91
    assert item.independent_value == 0.9


def test_comparison_requires_matching_dataset_and_split_qualifiers():
    result = make_result()

    response = compare_metrics(
        result,
        [
            {"name": "roc_auc", "reported_value": "0.91", "dataset": None, "split": "test"},
            {"name": "roc_auc", "reported_value": "0.91", "dataset": "validation", "split": "test"},
            {"name": "roc_auc", "reported_value": "0.91", "dataset": "test", "split": None},
        ],
    )

    assert [item.comparable for item in response.items] == [False, False, False]
    assert all(item.absolute_difference == -0.01 for item in response.items)


def test_storage_round_trip_writes_only_safe_result_artifacts(tmp_path):
    result = make_result()
    settings = Settings(storage_dir=tmp_path)

    experiment_id = save_result(result, settings)
    stored = tmp_path / experiment_id

    assert experiment_id == result.experiment_id
    assert {path.name for path in stored.iterdir()} == {"result.json", "config.json", "dataset_profile.json"}
    assert load_result(experiment_id, settings) == result
    assert "CSV" not in (stored / "result.json").read_text(encoding="utf-8")
    assert json.loads((stored / "config.json").read_text(encoding="utf-8")) == result.config.model_dump(mode="json")


@pytest.mark.parametrize("experiment_id", ["../outside", "exp-2026/../../outside", "", "not-an-experiment"])
def test_load_result_rejects_invalid_or_missing_experiment_ids(tmp_path, experiment_id):
    with pytest.raises(ResultNotFoundError):
        load_result(experiment_id, Settings(storage_dir=tmp_path))


def test_experiment_ids_are_prefixed_and_do_not_repeat():
    first = create_experiment_id()
    second = create_experiment_id()

    assert first.startswith("exp-")
    assert second.startswith("exp-")
    assert first != second
