import json

import pytest
from fastapi.testclient import TestClient

from repro_runner import api
from repro_runner.compare import compare_suite_metrics
from repro_runner.config import Settings, get_settings
from repro_runner.schemas import (
    DatasetProfile,
    ExperimentMetrics,
    ExperimentSuiteResult,
    ModelRunResult,
    ModelSuiteConfig,
    SplitProvenance,
    ValidationErrorItem,
)
from repro_runner.storage import save_suite_result


DATASET_ID = "sha256:" + "a" * 64
TEST_DIGEST = "sha256:" + "c" * 64


def _metrics(*, accuracy: float, roc_auc: float = 0.9) -> ExperimentMetrics:
    return ExperimentMetrics(
        roc_auc=roc_auc,
        accuracy=accuracy,
        balanced_accuracy=round(accuracy, 6),
        precision=round(max(accuracy - 0.01, 0.0), 6),
        recall=round(max(accuracy - 0.02, 0.0), 6),
        f1=round(max(accuracy - 0.015, 0.0), 6),
        confusion_matrix=[[4, 1], [1, 4]],
    )


@pytest.fixture
def suite_result() -> ExperimentSuiteResult:
    return ExperimentSuiteResult(
        experiment_id="exp-20260811T010203Z-deadbeef",
        status="partial",
        config=ModelSuiteConfig(
            models=["logistic_regression", "random_forest", "xgboost"],
            n_iter=1,
        ),
        dataset=DatasetProfile(
            rows=10,
            effective_rows=10,
            features=2,
            target="Y_cls",
            missing_values=0,
            duplicate_rows=0,
            class_counts={"0": 5, "1": 5},
            class_ratios={"0": 0.5, "1": 0.5},
            column_names=["x1", "x2", "Y_cls"],
            column_types={"x1": "int64", "x2": "int64", "Y_cls": "int64"},
            numeric_ranges={"x1": (0.0, 1.0), "x2": (0.0, 1.0)},
            dataset_id=DATASET_ID,
        ),
        split_provenance=SplitProvenance(
            test_size=0.2,
            random_state=42,
            train_rows=8,
            test_rows=2,
            test_digest=TEST_DIGEST,
        ),
        results=[
            ModelRunResult(
                model="logistic_regression",
                status="succeeded",
                cv_best_score=0.91,
                metrics=_metrics(accuracy=0.93, roc_auc=0.94),
            ),
            ModelRunResult(
                model="random_forest",
                status="succeeded",
                cv_best_score=0.89,
                metrics=_metrics(accuracy=0.89, roc_auc=0.91),
            ),
            ModelRunResult(
                model="xgboost",
                status="unavailable",
                error=ValidationErrorItem(
                    code="missing_dependency",
                    message="xgboost dependency is not installed",
                ),
            ),
        ],
        performance_ranking=["logistic_regression", "random_forest"],
    )


@pytest.fixture
def client(tmp_path) -> TestClient:
    settings = Settings(storage_dir=tmp_path, max_upload_mb=1)
    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()


def _reported_metric(**overrides):
    metric = {
        "name": "accuracy",
        "reported_value": 0.91,
        "dataset": "test",
        "split": "test",
        "dataset_id": DATASET_ID,
        "test_size": 0.2,
        "random_state": 42,
        "train_rows": 8,
        "test_rows": 2,
        "test_digest": TEST_DIGEST,
    }
    metric.update(overrides)
    return metric


def test_suite_comparison_returns_one_item_per_successful_model(suite_result):
    response = compare_suite_metrics(suite_result, [_reported_metric()])

    assert response.paper_reference_metric == "accuracy"
    assert [item.model for item in response.items] == [
        "logistic_regression",
        "random_forest",
    ]
    assert all(item.name == "accuracy" for item in response.items)
    assert [item.independent_value for item in response.items] == [0.93, 0.89]
    assert [item.absolute_difference for item in response.items] == [0.02, 0.02]
    assert [item.relative_difference for item in response.items] == [
        pytest.approx(0.021978),
        pytest.approx(-0.021978),
    ]


def test_missing_dataset_identity_preserves_reason_and_arithmetic_difference(suite_result):
    item = compare_suite_metrics(
        suite_result,
        [_reported_metric(dataset_id=None)],
    ).items[0]

    assert item.comparable is False
    assert item.reason == "paper metric is missing dataset identity"
    assert item.absolute_difference == 0.02
    assert item.relative_difference == pytest.approx(0.021978)


def test_suite_comparison_uses_distinct_provenance_reasons(suite_result):
    response = compare_suite_metrics(
        suite_result,
        [
            _reported_metric(dataset="validation"),
            _reported_metric(split="validation"),
            _reported_metric(dataset_id="sha256:" + "b" * 64),
            _reported_metric(test_digest="sha256:" + "d" * 64),
            _reported_metric(test_size=0.25),
        ],
    )

    items = [item for item in response.items if item.model == "logistic_regression"]
    assert [item.reason for item in items] == [
        "paper metric dataset differs from the independent test dataset",
        "paper metric split differs from the independent test split",
        "paper metric dataset identity differs from the independent dataset",
        "paper metric held-out test digest differs from the independent test split",
        "paper metric test_size differs from the independent test split",
    ]
    assert all(item.absolute_difference == 0.02 for item in items)


def test_suite_comparison_supports_auc_alias_and_explicit_metric_errors(suite_result):
    response = compare_suite_metrics(
        suite_result,
        [
            _reported_metric(name="AUC", reported_value="0.92"),
            _reported_metric(name="mcc"),
            _reported_metric(reported_value="not-a-number"),
        ],
    )

    auc_items = [item for item in response.items if item.name == "AUC"]
    unsupported_items = [item for item in response.items if item.name == "mcc"]
    non_numeric_items = [
        item
        for item in response.items
        if item.name == "accuracy" and item.paper_value is None
    ]

    assert response.paper_reference_metric == "roc_auc"
    assert [item.independent_value for item in auc_items] == [0.94, 0.91]
    assert all(item.reason is None for item in auc_items)
    assert all(item.reason == "metric name is not supported" for item in unsupported_items)
    assert all(item.reason == "reported value is not numeric" for item in non_numeric_items)


def test_suite_comparison_keeps_absolute_difference_when_paper_value_is_zero(suite_result):
    response = compare_suite_metrics(
        suite_result,
        [_reported_metric(reported_value=0.0)],
    )

    assert [item.absolute_difference for item in response.items] == [0.93, 0.89]
    assert all(item.relative_difference is None for item in response.items)


def test_compare_model_suite_result_route_returns_suite_response(
    client: TestClient, tmp_path, suite_result: ExperimentSuiteResult
):
    save_suite_result(suite_result, Settings(storage_dir=tmp_path))

    response = client.post(
        "/v1/compare-model-suite-result",
        json={
            "experiment_id": suite_result.experiment_id,
            "reported_metrics": [_reported_metric()],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["experiment_id"] == suite_result.experiment_id
    assert body["paper_reference_metric"] == "accuracy"
    assert [item["model"] for item in body["items"]] == [
        "logistic_regression",
        "random_forest",
    ]


@pytest.mark.parametrize(
    ("experiment_id", "expected_status"),
    [
        ("exp-20260811T010203Z-notfound", 404),
        ("not-an-experiment", 404),
    ],
)
def test_compare_model_suite_result_route_returns_safe_not_found(
    client: TestClient, experiment_id: str, expected_status: int
):
    response = client.post(
        "/v1/compare-model-suite-result",
        json={"experiment_id": experiment_id, "reported_metrics": [_reported_metric()]},
    )

    assert response.status_code == expected_status
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_not_found"
    assert detail["request_id"]


def test_compare_model_suite_result_route_returns_safe_conflict_for_corrupt_result(
    client: TestClient, tmp_path
):
    experiment_id = "exp-20260811T010203Z-deadbeef"
    directory = tmp_path / experiment_id
    directory.mkdir()
    (directory / "result.json").write_bytes(b"{not-json")

    response = client.post(
        "/v1/compare-model-suite-result",
        json={"experiment_id": experiment_id, "reported_metrics": [_reported_metric()]},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_result_incompatible"
    assert detail["request_id"]


def test_compare_model_suite_result_route_returns_safe_internal_error(
    client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch, suite_result: ExperimentSuiteResult
):
    save_suite_result(suite_result, Settings(storage_dir=tmp_path))

    def explode(*_args, **_kwargs):
        raise RuntimeError("secret failure details")

    monkeypatch.setattr(api, "compare_suite_metrics", explode, raising=False)
    response = client.post(
        "/v1/compare-model-suite-result",
        json={
            "experiment_id": suite_result.experiment_id,
            "reported_metrics": [_reported_metric()],
        },
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "comparison_failed"
    assert detail["request_id"]
    assert "secret failure details" not in response.text
