from fastapi.testclient import TestClient

from repro_runner.config import Settings, get_settings


def _csv(rows: int = 40) -> bytes:
    data = ["x1,x2,Y_cls"]
    for index in range(rows):
        data.append(f"{index % 2},{(index // 2) % 2},{index % 2}")
    return ("\n".join(data) + "\n").encode()


def _client(tmp_path) -> TestClient:
    from repro_runner.api import app

    app.dependency_overrides[get_settings] = lambda: Settings(storage_dir=tmp_path)
    return TestClient(app, raise_server_exceptions=False)


def test_healthz_is_public(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_validate_dataset_returns_profile(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/v1/validate-dataset",
            files={"file": ("data.csv", _csv(), "text/csv")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["dataset"]["target"] == "Y_cls"


def test_run_experiment_returns_id_and_metrics_and_persists_it(tmp_path):
    with _client(tmp_path) as client:
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


def test_dataset_errors_have_stable_code_and_request_id(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            "/v1/validate-dataset",
            files={"file": ("data.csv", b"x1\n1\n", "text/csv")},
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "missing_target_column"
    assert detail["message"]
    assert detail["request_id"]


def test_missing_experiment_has_not_found_code_and_request_id(tmp_path):
    with _client(tmp_path) as client:
        response = client.get("/v1/experiments/exp-20260805T010203Z-deadbeef")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_not_found"
    assert detail["request_id"]


def test_compare_result_uses_persisted_experiment(tmp_path):
    with _client(tmp_path) as client:
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
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert response.json()["experiment_id"] == result["experiment_id"]
    assert response.json()["items"][0]["comparable"] is True
