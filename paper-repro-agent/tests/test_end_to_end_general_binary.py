from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from repro_runner import api
from repro_runner.config import Settings, get_settings
from repro_runner.protocol import create_manifest
from repro_runner.schemas import (
    DatasetDiagnosticResponse,
    DatasetOptions,
    ModelSuiteConfig,
)


PROJECT_ROOT = Path(__file__).parents[1]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures"


def _reset_app_state() -> None:
    for name in ("job_store", "job_runner"):
        if hasattr(api.app.state, name):
            delattr(api.app.state, name)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        storage_dir=tmp_path / "experiments",
        max_upload_mb=1,
        job_store_path=tmp_path / "jobs.sqlite3",
        job_work_dir=tmp_path / "job-inputs",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    _reset_app_state()
    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()
    _reset_app_state()


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURE_ROOT / name).read_bytes()


def _wait_for_terminal(client: TestClient, job_id: str, timeout: float = 20.0) -> dict:
    deadline = monotonic() + timeout
    terminal = {"succeeded", "partial", "failed", "cancelled", "needs_retry"}
    while monotonic() < deadline:
        response = client.get(f"/v1/jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in terminal:
            return body
        sleep(0.05)
    raise AssertionError(f"job did not reach a terminal state: {job_id}")


@pytest.mark.parametrize(
    ("fixture_name", "expected_valid"),
    [
        ("general_binary_numeric.csv", True),
        ("general_binary_mixed.csv", True),
        ("general_binary_missing.csv", False),
        ("general_binary_imbalanced.csv", True),
        ("general_binary_one_class.csv", False),
    ],
)
def test_general_binary_fixtures_have_safe_diagnostic_contract(
    client: TestClient, fixture_name: str, expected_valid: bool
) -> None:
    content = _fixture_bytes(fixture_name)
    response = client.post(
        "/v1/diagnose-dataset",
        data={"target_column": "Y_cls"},
        files={"file": (fixture_name, content, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is expected_valid
    assert content.decode("utf-8").splitlines()[1] not in response.text
    assert "Traceback" not in response.text


def test_general_binary_diagnose_job_result_compare_and_idempotency(
    client: TestClient, settings: Settings
) -> None:
    content = _fixture_bytes("general_binary_numeric.csv")

    diagnosed_response = client.post(
        "/v1/diagnose-dataset",
        data={"target_column": "Y_cls"},
        files={"file": ("general.csv", content, "text/csv")},
    )
    assert diagnosed_response.status_code == 200
    diagnostic = DatasetDiagnosticResponse.model_validate(diagnosed_response.json())
    assert diagnostic.valid is True
    assert diagnostic.dataset is not None
    assert diagnostic.dataset.dataset_id == "sha256:" + sha256(content).hexdigest()

    suite_config = ModelSuiteConfig(
        models=["logistic_regression"],
        cv_folds=3,
        n_iter=1,
        n_jobs=1,
    )
    options = DatasetOptions(
        target_column="Y_cls",
        target_column_confirmed=True,
        missing_policy="reject",
        comparison_mode="paper_comparable",
        feature_columns=["x1", "x2"],
    )
    manifest = create_manifest(diagnostic, options, suite_config)

    created = client.post(
        "/v1/jobs",
        data={"manifest_json": manifest.model_dump_json()},
        files={"file": ("general.csv", content, "text/csv")},
    )
    assert created.status_code == 202
    job = created.json()
    terminal = _wait_for_terminal(client, job["job_id"])
    assert terminal["status"] == "succeeded"

    result_response = client.get(f"/v1/jobs/{job['job_id']}/result")
    assert result_response.status_code == 200
    result = result_response.json()
    assert result["dataset"]["dataset_id"] == manifest.dataset_id
    assert result["results"][0]["model"] == "logistic_regression"
    assert result["results"][0]["status"] == "succeeded"
    assert content.decode("utf-8").splitlines()[1] not in result_response.text

    metric = result["results"][0]["metrics"]["accuracy"]
    split = result["split_provenance"]
    comparison = client.post(
        "/v1/compare-model-suite-result",
        json={
            "experiment_id": result["experiment_id"],
            "reported_metrics": [
                {
                    "name": "accuracy",
                    "reported_value": metric,
                    "dataset": "test",
                    "split": "test",
                    "dataset_id": result["dataset"]["dataset_id"],
                    **split,
                }
            ],
        },
    )
    assert comparison.status_code == 200
    comparison_body = comparison.json()
    assert comparison_body["experiment_id"] == result["experiment_id"]
    assert len(comparison_body["items"]) == 1
    assert comparison_body["items"][0]["comparable"] is True

    duplicate = client.post(
        "/v1/jobs",
        data={"manifest_json": manifest.model_dump_json()},
        files={"file": ("general.csv", content, "text/csv")},
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["job_id"] == job["job_id"]

    changed_manifest = manifest.model_copy(
        update={
            "random_state": 43,
            "manifest_id": "sha256:" + "d" * 64,
        }
    )
    changed = client.post(
        "/v1/jobs",
        data={"manifest_json": changed_manifest.model_dump_json()},
        files={"file": ("general.csv", content, "text/csv")},
    )
    assert changed.status_code == 202
    assert changed.json()["job_id"] != job["job_id"]
    cancelled = client.post(f"/v1/jobs/{changed.json()['job_id']}/cancel")
    assert cancelled.status_code in {200, 409}

    assert settings.storage_dir.exists()
    assert json.dumps(result, ensure_ascii=False).find("Traceback") == -1
