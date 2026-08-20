import json

import pytest
from fastapi.testclient import TestClient

import repro_runner.storage as storage
from repro_runner import api
from repro_runner.config import Settings, get_settings
from repro_runner.schemas import (
    DatasetProfile,
    ExperimentConfig,
    ExperimentMetrics,
    ExperimentResult,
    SplitProvenance,
)
from repro_runner.storage import ResultFormatError, load_result, load_suite_result, save_result


def _csv() -> bytes:
    rows = ["x1,x2,Y_cls", "123456,654321,1"]
    for index in range(1, 61):
        rows.append(f"{index % 2},{(index // 2) % 2},{index % 2}")
    return ("\n".join(rows) + "\n").encode()


def _legacy_result(experiment_id: str) -> ExperimentResult:
    return ExperimentResult(
        experiment_id=experiment_id,
        status="succeeded",
        config=ExperimentConfig(),
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
            dataset_id="sha256:" + "0" * 64,
        ),
        metrics=ExperimentMetrics(
            roc_auc=0.8,
            accuracy=0.8,
            balanced_accuracy=0.8,
            precision=0.8,
            recall=0.8,
            f1=0.8,
            confusion_matrix=[[4, 1], [1, 4]],
        ),
        feature_importance=[],
        split_provenance=SplitProvenance(
            test_size=0.2,
            random_state=42,
            train_rows=8,
            test_rows=2,
            test_digest="sha256:" + "1" * 64,
        ),
    )


@pytest.fixture
def client(tmp_path) -> TestClient:
    settings = Settings(storage_dir=tmp_path, max_upload_mb=1)
    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()


def test_run_model_suite_returns_result_and_persists_privacy_safe_artifacts(
    client: TestClient, tmp_path
):
    response = client.post(
        "/v1/run-model-suite",
        data={
            "models_json": '["logistic_regression"]',
            "cv_folds": "3",
            "n_iter": "1",
        },
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["experiment_id"].startswith("exp-")
    assert body["config"]["models"] == ["logistic_regression"]
    assert body["split_provenance"]["test_digest"].startswith("sha256:")
    assert body["results"][0]["model"] == "logistic_regression"
    assert body["results"][0]["status"] == "succeeded"

    stored = tmp_path / body["experiment_id"]
    assert {path.name for path in stored.iterdir()} == {
        "result.json",
        "config.json",
        "dataset_profile.json",
    }
    for path in stored.iterdir():
        payload = path.read_text(encoding="utf-8")
        assert "123456,654321,1" not in payload

    loaded = load_suite_result(body["experiment_id"], Settings(storage_dir=tmp_path))
    assert loaded.model_dump(mode="json") == body
    with pytest.raises(ResultFormatError):
        load_result(body["experiment_id"], Settings(storage_dir=tmp_path))


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        ({"models_json": "not-json sentinel"}, "not-json sentinel"),
        ({"models_json": '{"models":["logistic_regression"]}'}, '{"models":["logistic_regression"]}'),
        ({"models_json": '["logistic_regression", 9, "secret"]'}, "secret"),
        ({"models_json": '["logistic_regression", "logistic_regression"]'}, '["logistic_regression", "logistic_regression"]'),
        ({"models_json": '["unknown_model"]'}, "unknown_model"),
        (
            {
                "models_json": '["logistic_regression"]',
                "cv_folds": "2",
                "optimization_metric": "private_metric_name",
            },
            "private_metric_name",
        ),
    ],
)
def test_run_model_suite_rejects_invalid_form_inputs_with_sanitized_422(
    client: TestClient, data: dict[str, str], needle: str
):
    response = client.post(
        "/v1/run-model-suite",
        data=data,
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_request"
    assert detail["request_id"]
    assert needle not in response.text


def test_run_model_suite_same_idempotency_key_replays_and_conflicts_on_change(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    run_calls = 0
    save_calls = 0
    original_run_model_suite = api.run_model_suite

    def counted_run_model_suite(*args, **kwargs):
        nonlocal run_calls
        run_calls += 1
        return original_run_model_suite(*args, **kwargs)

    def counted_save_suite_result(result, settings):
        nonlocal save_calls
        save_calls += 1
        return storage.save_suite_result(result, settings)

    monkeypatch.setattr(api, "run_model_suite", counted_run_model_suite)
    monkeypatch.setattr(api, "save_suite_result", counted_save_suite_result)
    request = {
        "data": {
            "models_json": '["logistic_regression"]',
            "cv_folds": "3",
            "n_iter": "1",
            "idempotency_key": "suite-replay",
        },
        "files": {"file": ("data.csv", _csv(), "text/csv")},
    }

    first = client.post("/v1/run-model-suite", **request)
    second = client.post("/v1/run-model-suite", **request)
    changed_config = client.post(
        "/v1/run-model-suite",
        data={**request["data"], "random_state": "7"},
        files=request["files"],
    )
    changed_file = client.post(
        "/v1/run-model-suite",
        data=request["data"],
        files={"file": ("data.csv", _csv() + b"9,9,1\n", "text/csv")},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert changed_config.status_code == 409
    assert changed_config.json()["detail"]["code"] == "idempotency_conflict"
    assert changed_file.status_code == 409
    assert changed_file.json()["detail"]["code"] == "idempotency_conflict"
    assert run_calls == 1
    assert save_calls == 1


def test_get_model_suite_round_trips_the_persisted_result(client: TestClient):
    created = client.post(
        "/v1/run-model-suite",
        data={
            "models_json": '["logistic_regression"]',
            "cv_folds": "3",
            "n_iter": "1",
        },
        files={"file": ("data.csv", _csv(), "text/csv")},
    ).json()

    fetched = client.get(f"/v1/model-suites/{created['experiment_id']}")

    assert fetched.status_code == 200
    assert fetched.json() == created


def test_load_suite_result_defaults_missing_historical_paper_closeness_ranking(
    client: TestClient, tmp_path
):
    created = client.post(
        "/v1/run-model-suite",
        data={
            "models_json": '["logistic_regression"]',
            "cv_folds": "3",
            "n_iter": "1",
        },
        files={"file": ("data.csv", _csv(), "text/csv")},
    ).json()
    result_path = tmp_path / created["experiment_id"] / "result.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload.pop("paper_closeness_ranking")
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_suite_result(created["experiment_id"], Settings(storage_dir=tmp_path))

    assert loaded.paper_closeness_ranking == []


@pytest.mark.parametrize(
    "experiment_id", ["exp-20260811T010203Z-deadbeef", "not-an-experiment"]
)
def test_get_model_suite_returns_safe_not_found(client: TestClient, experiment_id: str):
    response = client.get(f"/v1/model-suites/{experiment_id}")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_not_found"
    assert detail["request_id"]


def test_get_model_suite_returns_safe_conflict_for_corrupt_suite_result(
    client: TestClient, tmp_path
):
    experiment_id = "exp-20260811T010203Z-deadbeef"
    directory = tmp_path / experiment_id
    directory.mkdir()
    (directory / "result.json").write_bytes(b"{not-json")

    response = client.get(f"/v1/model-suites/{experiment_id}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_result_incompatible"
    assert detail["request_id"]


def test_load_suite_result_rejects_legacy_single_model_payload(tmp_path):
    settings = Settings(storage_dir=tmp_path)
    result = _legacy_result("exp-20260811T010203Z-deadbeef")

    save_result(result, settings)

    with pytest.raises(ResultFormatError):
        load_suite_result(result.experiment_id, settings)
