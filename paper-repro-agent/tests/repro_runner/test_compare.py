import os
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest

from repro_runner.compare import compare_metrics, compare_suite_metrics
from repro_runner.config import Settings
from repro_runner.schemas import (
    DatasetProfile,
    ExperimentConfig,
    ExperimentMetrics,
    ExperimentResult,
    ExperimentSuiteResult,
    ModelRunResult,
    ModelSuiteConfig,
    SuiteComparisonItem,
    SplitProvenance,
)
import repro_runner.storage as storage
from repro_runner.storage import (
    ResultFormatError,
    ResultNotFoundError,
    create_experiment_id,
    load_result,
    load_suite_result,
    save_result,
    save_suite_result,
    update_suite_result,
)


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


def make_suite_result() -> ExperimentSuiteResult:
    return ExperimentSuiteResult(
        experiment_id="exp-20260814T010203Z-suite",
        status="succeeded",
        config=ModelSuiteConfig(
            models=["random_forest", "mlp"],
            workflow_version="multimodel-0.8.0",
            cv_folds=3,
            n_iter=1,
            n_jobs=1,
            threshold=0.5,
        ),
        dataset=DatasetProfile(
            rows=10,
            effective_rows=10,
            features=2,
            target="Y_cls",
            missing_values=0,
            duplicate_rows=0,
            dataset_id=DATASET_ID,
        ),
        split_provenance=SplitProvenance(
            test_size=0.2,
            random_state=42,
            train_rows=8,
            test_rows=2,
            test_digest="sha256:" + "c" * 64,
        ),
        results=[
            ModelRunResult(
                model="random_forest",
                status="succeeded",
                metrics=ExperimentMetrics(
                    roc_auc=0.90,
                    accuracy=0.80,
                    balanced_accuracy=0.75,
                    precision=0.70,
                    recall=0.60,
                    f1=0.64,
                    confusion_matrix=[[4, 1], [1, 4]],
                ),
            ),
            ModelRunResult(
                model="mlp",
                status="succeeded",
                metrics=ExperimentMetrics(
                    roc_auc=0.85,
                    accuracy=0.78,
                    balanced_accuracy=0.73,
                    precision=0.68,
                    recall=0.58,
                    f1=0.62,
                    confusion_matrix=[[4, 1], [1, 4]],
                ),
            ),
        ],
        performance_ranking=["random_forest", "mlp"],
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


def test_suite_comparison_selects_explicit_model_qualifier():
    result = make_suite_result()

    response = compare_suite_metrics(
        result,
        [
            {
                "name": "AUC",
                "model": "random_forest",
                "reported_value": 0.91,
                "dataset": "test",
                "split": "test",
                "dataset_id": DATASET_ID,
                "test_size": 0.2,
                "random_state": 42,
                "train_rows": 8,
                "test_rows": 2,
                "test_digest": "sha256:" + "c" * 64,
            }
        ],
    )

    assert len(response.items) == 1
    item = response.items[0]
    assert isinstance(item, SuiteComparisonItem)
    assert item.model == "random_forest"
    assert item.independent_value == 0.9
    assert item.absolute_difference == 0.01
    assert item.comparable is True


def test_suite_comparison_reports_threshold_mismatch_without_losing_difference():
    result = make_suite_result()

    response = compare_suite_metrics(
        result,
        [
            {
                "name": "accuracy",
                "model": "random_forest",
                "threshold": 0.25,
                "reported_value": 0.95,
                "dataset": "test",
                "split": "test",
                "dataset_id": DATASET_ID,
                "test_size": 0.2,
                "random_state": 42,
                "train_rows": 8,
                "test_rows": 2,
                "test_digest": "sha256:" + "c" * 64,
            }
        ],
    )

    assert len(response.items) == 1
    item = response.items[0]
    assert item.independent_value == 0.8
    assert item.absolute_difference == 0.15
    assert item.comparable is False
    assert item.reason == "paper metric threshold differs from the independent run"


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
    real_write = storage._write_json

    with patch.object(storage, "_write_json", side_effect=[real_write, OSError("disk full")]):
        with pytest.raises(OSError, match="disk full"):
            save_result(result, settings)

    assert not (tmp_path / result.experiment_id).exists()
    with pytest.raises(ResultNotFoundError):
        load_result(result.experiment_id, settings)


@pytest.mark.skipif(os.name != "nt", reason="Windows path-boundary regression")
def test_storage_operations_succeed_beyond_the_legacy_nested_temp_boundary(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "a" * 16
    single = make_result()
    suite = make_suite_result()
    legacy_tail = (
        Path(f".{single.experiment_id}.{token}.tmp")
        / f".tmp-{token}"
    )
    fixed_length = len(str(tmp_path / "x" / legacy_tail)) - 1
    padding_length = 262 - fixed_length
    assert 1 <= padding_length <= 255
    storage_root = tmp_path / ("x" * padding_length)
    short_tail = Path(f".tmp-{token}") / "dataset_profile.json"

    assert len(str(storage_root / legacy_tail)) == 262
    assert len(str(storage_root / short_tail)) < 260
    monkeypatch.setattr(storage.secrets, "token_hex", lambda _length: token)
    settings = Settings(storage_dir=storage_root)

    assert save_result(single, settings) == single.experiment_id
    assert load_result(single.experiment_id, settings) == single
    assert save_suite_result(suite, settings) == suite.experiment_id
    assert load_suite_result(suite.experiment_id, settings) == suite
    assert update_suite_result(suite, settings) == suite.experiment_id
    assert list(storage_root.glob(".tmp-*")) == []


def test_staged_result_failure_cleans_only_its_short_directory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = make_result()
    settings = Settings(storage_dir=tmp_path)
    real_write = storage._write_json
    write_count = 0

    def fail_second_write(path: Path, payload: object) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("simulated disk full")
        real_write(path, payload)

    monkeypatch.setattr(storage, "_write_json", fail_second_write)

    with pytest.raises(OSError, match="disk full"):
        save_result(result, settings)

    assert not (tmp_path / result.experiment_id).exists()
    assert list(tmp_path.glob(".tmp-*")) == []


def test_concurrent_duplicate_saves_publish_exactly_one_complete_result(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = make_result()
    settings = Settings(storage_dir=tmp_path)
    first_write = Barrier(2)
    real_write = storage._write_json

    def synchronize_first_write(path: Path, payload: object) -> None:
        if path.name == "result.json":
            first_write.wait(timeout=5)
        real_write(path, payload)

    monkeypatch.setattr(storage, "_write_json", synchronize_first_write)
    outcomes: list[object] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save_result, result, settings) for _ in range(2)]
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except OSError as exc:
                outcomes.append(exc)

    assert sum(value == result.experiment_id for value in outcomes) == 1
    assert sum(isinstance(value, OSError) for value in outcomes) == 1
    assert load_result(result.experiment_id, settings) == result
    assert list(tmp_path.glob(".tmp-*")) == []


def test_suite_update_uses_short_temp_and_preserves_published_result_on_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = make_suite_result()
    settings = Settings(storage_dir=tmp_path)
    save_suite_result(result, settings)
    result_path = tmp_path / result.experiment_id / "result.json"
    original = result_path.read_bytes()
    replace_sources: list[str] = []
    real_replace = Path.replace

    def fail_result_replacement(source: Path, target: Path) -> Path:
        if target == result_path:
            replace_sources.append(source.name)
            raise PermissionError("simulated locked destination")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_result_replacement)

    with pytest.raises(PermissionError, match="locked destination"):
        update_suite_result(result, settings)

    assert len(replace_sources) == 1
    assert replace_sources[0].startswith(".tmp-")
    assert "result.json" not in replace_sources[0]
    assert result_path.read_bytes() == original
    assert load_suite_result(result.experiment_id, settings) == result
    assert list(result_path.parent.glob(".tmp-*")) == []


def test_old_result_without_split_provenance_loads_as_legacy_and_incomparable(tmp_path):
    result = make_result()
    payload = storage._result_payload(result)
    payload.pop("split_provenance")
    directory = tmp_path / result.experiment_id
    directory.mkdir()
    (directory / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_result(result.experiment_id, Settings(storage_dir=tmp_path))
    comparison = compare_metrics(
        loaded,
        [{
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
        }],
    )

    assert loaded.split_provenance is None
    assert loaded.reproducibility_status == "legacy_incomparable"
    assert comparison.items[0].comparable is False
    assert "legacy" in comparison.items[0].reason


def test_incompatible_result_is_not_misreported_as_missing(tmp_path):
    experiment_id = "exp-20260805T010203Z-deadbeef"
    directory = tmp_path / experiment_id
    directory.mkdir()
    (directory / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ResultFormatError):
        load_result(experiment_id, Settings(storage_dir=tmp_path))


@pytest.mark.parametrize("content", [b"{not-json", b"\xff\xfe\x00"])
def test_corrupt_result_content_is_not_misreported_as_missing(tmp_path, content):
    experiment_id = "exp-20260805T010203Z-deadbeef"
    directory = tmp_path / experiment_id
    directory.mkdir()
    (directory / "result.json").write_bytes(content)

    with pytest.raises(ResultFormatError):
        load_result(experiment_id, Settings(storage_dir=tmp_path))


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
