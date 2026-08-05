import json
from unittest.mock import patch

import pytest

from repro_runner.compare import compare_metrics
from repro_runner.config import Settings
from repro_runner.schemas import (
    DatasetProfile,
    ExperimentConfig,
    ExperimentMetrics,
    ExperimentResult,
    SplitProvenance,
)
import repro_runner.storage as storage
from repro_runner.storage import ResultNotFoundError, create_experiment_id, load_result, save_result


DATASET_ID = "sha256:" + "a" * 64


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
        dataset=DatasetProfile(
            rows=10,
            effective_rows=10,
            features=2,
            target="Y_cls",
            missing_values=0,
            duplicate_rows=0,
            dataset_id=DATASET_ID,
        ),
        metrics=ExperimentMetrics(**metrics),
        feature_importance=[],
        split_provenance=SplitProvenance(
            test_size=0.2,
            random_state=42,
            train_rows=8,
            test_rows=2,
            test_digest="sha256:" + "c" * 64,
        ),
    )


def test_comparison_reports_absolute_and_relative_difference():
    result = make_result(metric_name="roc_auc", value=0.90, dataset="test", split="test")

    response = compare_metrics(
        result,
        [{
            "name": "AUC",
            "reported_value": 0.91,
            "dataset": "test",
            "split": "test",
            "dataset_id": DATASET_ID,
            "test_size": 0.2,
            "random_state": 42,
            "train_rows": 8,
            "test_rows": 2,
            "test_digest": "sha256:" + "c" * 64,
        }],
    )

    item = response.items[0]
    assert item.comparable is True
    assert item.absolute_difference == 0.01
    assert item.relative_difference == pytest.approx(-0.010989)
    assert item.paper_value == 0.91
    assert item.independent_value == 0.9


def test_comparison_requires_matching_dataset_and_split_qualifiers():
    result = make_result()

    response = compare_metrics(
        result,
        [
            {"name": "roc_auc", "reported_value": "0.91", "dataset": None, "split": "test"},
            {
                "name": "roc_auc",
                "reported_value": "0.91",
                "dataset": "validation",
                "split": "test",
                "dataset_id": DATASET_ID,
            },
            {
                "name": "roc_auc",
                "reported_value": "0.91",
                "dataset": "test",
                "split": None,
                "dataset_id": DATASET_ID,
            },
        ],
    )

    assert [item.comparable for item in response.items] == [False, False, False]
    assert all(item.absolute_difference == 0.01 for item in response.items)


def test_comparison_requires_complete_matching_split_provenance():
    result = make_result()

    response = compare_metrics(
        result,
        [
            {
                "name": "roc_auc",
                "reported_value": 0.91,
                "dataset": "test",
                "split": "test",
                "dataset_id": "sha256:" + "b" * 64,
                "test_size": 0.2,
                "random_state": 42,
                "train_rows": 8,
                "test_rows": 2,
                "test_digest": "sha256:" + "c" * 64,
            },
            {
                "name": "roc_auc",
                "reported_value": 0.91,
                "dataset": "test",
                "split": "test",
                "dataset_id": DATASET_ID,
                "test_size": 0.2,
                "random_state": 42,
                "train_rows": 8,
                "test_rows": 2,
                "test_digest": "sha256:" + "d" * 64,
            },
            {
                "name": "roc_auc",
                "reported_value": 0.91,
                "dataset": "test",
                "split": "test",
                "dataset_id": DATASET_ID,
                "test_size": 0.25,
                "random_state": 42,
                "train_rows": 8,
                "test_rows": 2,
                "test_digest": "sha256:" + "c" * 64,
            },
            {
                "name": "roc_auc",
                "reported_value": 0.91,
                "dataset": "test",
                "split": "test",
                "dataset_id": DATASET_ID,
                "test_size": 0.2,
                "random_state": 42,
                "train_rows": 8,
                "test_rows": 2,
                "test_digest": "sha256:" + "c" * 64,
            },
        ],
    )

    assert [item.comparable for item in response.items] == [False, False, False, True]
    assert "identity" in response.items[0].reason
    assert "digest" in response.items[1].reason
    assert "test_size" in response.items[2].reason


def test_storage_round_trip_writes_only_safe_result_artifacts(tmp_path):
    result = make_result()
    sentinel = "RAW_CSV_SECRET_07a1"
    result.__dict__["raw_csv"] = sentinel
    result.config.__dict__["raw_csv"] = sentinel
    result.dataset.__dict__["raw_csv"] = sentinel
    settings = Settings(storage_dir=tmp_path)

    experiment_id = save_result(result, settings)
    stored = tmp_path / experiment_id

    assert experiment_id == result.experiment_id
    assert {path.name for path in stored.iterdir()} == {"result.json", "config.json", "dataset_profile.json"}
    loaded = load_result(experiment_id, settings)
    assert loaded.experiment_id == result.experiment_id
    stored_config = json.loads((stored / "config.json").read_text(encoding="utf-8"))
    assert {**result.config.model_dump(mode="json"), "service_version": "0.2.0"} == stored_config
    expected_top_level_fields = {
        "experiment_id", "status", "config", "dataset", "metrics", "feature_importance", "reproducibility_status", "split_provenance"
    }
    for path in stored.iterdir():
        payload = path.read_text(encoding="utf-8")
        assert sentinel not in payload
    assert set(json.loads((stored / "result.json").read_text(encoding="utf-8"))) == expected_top_level_fields
    assert set(json.loads((stored / "config.json").read_text(encoding="utf-8"))) == {
        "model", "test_size", "random_state", "drop_duplicates", "service_version"
    }
    assert set(json.loads((stored / "dataset_profile.json").read_text(encoding="utf-8"))) == {
        "rows", "features", "target", "missing_values", "duplicate_rows", "class_counts", "class_ratios",
        "column_names", "column_types", "numeric_ranges", "effective_rows", "dataset_id"
    }


def test_storage_failure_does_not_publish_a_partial_experiment(tmp_path):
    result = make_result()
    settings = Settings(storage_dir=tmp_path)
    real_write = storage._write_json_atomic

    with patch.object(storage, "_write_json_atomic", side_effect=[real_write, OSError("disk full")]):
        with pytest.raises(OSError, match="disk full"):
            save_result(result, settings)

    assert not (tmp_path / result.experiment_id).exists()
    with pytest.raises(ResultNotFoundError):
        load_result(result.experiment_id, settings)


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
