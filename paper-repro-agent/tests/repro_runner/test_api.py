from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from fastapi.testclient import TestClient

from repro_runner import api
from repro_runner.config import Settings, get_settings


def _csv(rows: int = 40) -> bytes:
    data = ["x1,x2,Y_cls"]
    for index in range(rows):
        data.append(f"{index % 2},{(index // 2) % 2},{index % 2}")
    return ("\n".join(data) + "\n").encode()


@pytest.fixture
def client(tmp_path) -> TestClient:
    settings = Settings(storage_dir=tmp_path, max_upload_mb=1)
    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()


def test_healthz_is_public(client: TestClient):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_validate_dataset_returns_profile(client: TestClient):
    response = client.post(
        "/v1/validate-dataset",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["dataset"]["target"] == "Y_cls"


def test_run_experiment_returns_id_and_metrics_and_persists_it(client: TestClient):
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 200
    result = response.json()
    fetched = client.get(f"/v1/experiments/{result['experiment_id']}")

    assert result["experiment_id"].startswith("exp-")
    assert "roc_auc" in result["metrics"]
    assert result["reproducibility_status"] == "baseline_only"
    assert fetched.status_code == 200
    assert fetched.json() == result


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"x1\n1\n", "missing_target_column"),
        (b"x1,Y_cls\n1,0\n2,0\n", "invalid_target_classes"),
        (b"x1,Y_cls\n1,0\n,1\n", "missing_values"),
        (b"x1,Y_cls\na,0\n2,1\n", "non_numeric_feature"),
        (b"x1,Y_cls\nNaN,0\n2,1\n", "non_finite_numeric_feature"),
        (b"x1,Y_cls\n1,0,extra\n2,1\n", "invalid_csv"),
    ],
)
def test_validate_dataset_returns_200_with_structured_errors(
    client: TestClient, content: bytes, code: str
):
    response = client.post(
        "/v1/validate-dataset",
        files={"file": ("data.csv", content, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["dataset"] is None
    assert body["errors"] == [
        {"code": code, "message": pytest.ANY if hasattr(pytest, "ANY") else body["errors"][0]["message"]}
    ]
    assert body["errors"][0]["message"]


def test_run_experiment_keeps_dataset_failures_as_422(client: TestClient):
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", b"x1\n1\n", "text/csv")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "missing_target_column"


def test_missing_experiment_has_not_found_code_and_request_id(client: TestClient):
    response = client.get("/v1/experiments/exp-20260805T010203Z-deadbeef")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_not_found"
    assert detail["request_id"]


def test_compare_result_uses_persisted_experiment(client: TestClient):
    result = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    ).json()
    response = client.post(
        "/v1/compare-result",
        json={
            "experiment_id": result["experiment_id"],
            "reported_metrics": [
                {
                    "name": "AUC",
                    "reported_value": 0.91,
                    "dataset": "test",
                    "split": "test",
                    "dataset_id": result["dataset"]["dataset_id"],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["experiment_id"] == result["experiment_id"]
    assert response.json()["items"][0]["comparable"] is True


def test_file_larger_than_configured_limit_is_413_with_request_id(client: TestClient):
    response = client.post(
        "/v1/validate-dataset",
        files={"file": ("large.csv", b"x" * (1024 * 1024 + 1), "text/csv")},
    )

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["code"] == "file_too_large"
    assert detail["request_id"]
    assert "traceback" not in response.text.casefold()


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/v1/run-experiment", None),
        ("/v1/run-experiment", {"model": "other"}),
        ("/v1/run-experiment", {"test_size": "0.9"}),
    ],
)
def test_invalid_request_is_sanitized_with_request_id(
    client: TestClient, path: str, data: dict[str, str] | None
):
    kwargs = {} if data is None else {
        "data": data,
        "files": {"file": ("data.csv", _csv(), "text/csv")},
    }
    response = client.post(path, **kwargs)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_request"
    assert detail["request_id"]
    assert "traceback" not in response.text.casefold()


def test_training_failure_is_sanitized_with_request_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fail_training(*_args, **_kwargs):
        raise RuntimeError("private training stack")

    monkeypatch.setattr(api, "run_random_forest", fail_training)
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_failed"
    assert detail["request_id"]
    assert "private" not in response.text
    assert "traceback" not in response.text.casefold()


def test_requested_experiment_configuration_is_returned(client: TestClient):
    csv = (
        "x1,label\n" + "\n".join(f"{index},{index % 2}" for index in range(80)) + "\n"
    ).encode()
    response = client.post(
        "/v1/run-experiment",
        data={
            "target_column": "label",
            "random_state": "7",
            "drop_duplicates": "true",
        },
        files={"file": ("data.csv", csv, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dataset"]["target"] == "label"
    assert body["config"]["random_state"] == 7
    assert body["config"]["drop_duplicates"] is True
    assert body["dataset"]["effective_rows"] == 80


def test_experiment_execution_is_serialized_by_default(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    active = 0
    peak = 0
    lock = threading.Lock()
    sentinel = object()

    def slow_training(_bundle, _config):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.08)
        with lock:
            active -= 1
        return sentinel

    monkeypatch.setattr(api, "run_random_forest", slow_training)
    monkeypatch.setattr(api, "save_result", lambda *_args: None)
    api._experiment_gate.cache_clear()
    settings = Settings(storage_dir=tmp_path)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(api._execute_experiment, object(), object(), settings)
            for _ in range(3)
        ]
        assert [future.result() for future in futures] == [sentinel] * 3

    assert peak == 1
