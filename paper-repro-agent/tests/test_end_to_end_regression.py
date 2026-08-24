from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient

from repro_runner import api
from repro_runner.config import Settings, get_settings
from repro_runner.protocol import create_manifest
from repro_runner.schemas import DatasetDiagnosticResponse, DatasetOptions, ModelSuiteConfig


PROJECT_ROOT = Path(__file__).parents[1]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures"


def _reset_app_state() -> None:
    for name in ("job_store", "job_runner"):
        if hasattr(api.app.state, name):
            delattr(api.app.state, name)


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


def test_regression_diagnose_job_result_compare_and_idempotency(tmp_path: Path) -> None:
    settings = Settings(
        storage_dir=tmp_path / "experiments",
        max_upload_mb=1,
        job_store_path=tmp_path / "jobs.sqlite3",
        job_work_dir=tmp_path / "job-inputs",
    )
    content = (FIXTURE_ROOT / "regression_mixed.csv").read_bytes()
    api.app.dependency_overrides[get_settings] = lambda: settings
    _reset_app_state()
    try:
        with TestClient(api.app, raise_server_exceptions=False) as client:
            diagnosed_response = client.post(
                "/v1/diagnose-dataset",
                data={"task_type": "regression", "target_column": "target"},
                files={"file": ("regression.csv", content, "text/csv")},
            )
            assert diagnosed_response.status_code == 200
            diagnostic = DatasetDiagnosticResponse.model_validate(diagnosed_response.json())
            assert diagnostic.valid is True
            assert diagnostic.dataset is not None
            assert diagnostic.dataset.dataset_id == "sha256:" + sha256(content).hexdigest()

            suite_config = ModelSuiteConfig(
                task_type="regression",
                models=["linear_regression", "random_forest"],
                cv_folds=3,
                n_iter=1,
                n_jobs=1,
            )
            options = DatasetOptions(
                task_type="regression",
                target_column="target",
                target_column_confirmed=True,
                missing_policy="reject",
                comparison_mode="paper_comparable",
                feature_columns=["x1", "x2", "region"],
            )
            manifest = create_manifest(diagnostic, options, suite_config)

            created = client.post(
                "/v1/jobs",
                data={"manifest_json": manifest.model_dump_json()},
                files={"file": ("regression.csv", content, "text/csv")},
            )
            assert created.status_code == 202
            job = created.json()
            terminal = _wait_for_terminal(client, job["job_id"])
            assert terminal["status"] == "succeeded"

            result_response = client.get(f"/v1/jobs/{job['job_id']}/result")
            assert result_response.status_code == 200
            result = result_response.json()
            assert result["task_type"] == "regression"
            assert result["config"]["task_type"] == "regression"
            assert set(result["results"][0]["metrics"]) == {"mae", "rmse", "r2"}
            assert content.decode("utf-8").splitlines()[1] not in result_response.text

            split = result["split_provenance"]
            rmse = result["results"][0]["metrics"]["rmse"]
            comparison = client.post(
                "/v1/compare-model-suite-result",
                json={
                    "experiment_id": result["experiment_id"],
                    "reported_metrics": [
                        {
                            "name": "RMSE",
                            "reported_value": rmse,
                            "model": "linear_regression",
                            "dataset": "test",
                            "split": "test",
                            "dataset_id": result["dataset"]["dataset_id"],
                            **split,
                        }
                    ],
                },
            )
            assert comparison.status_code == 200
            item = comparison.json()["items"][0]
            assert item["comparable"] is True
            assert item["absolute_difference"] == 0.0

            duplicate = client.post(
                "/v1/jobs",
                data={"manifest_json": manifest.model_dump_json()},
                files={"file": ("regression.csv", content, "text/csv")},
            )
            assert duplicate.status_code == 202
            assert duplicate.json()["job_id"] == job["job_id"]

            storage_payload = (settings.storage_dir / result["experiment_id"] / "result.json").read_text(
                encoding="utf-8"
            )
            assert content.decode("utf-8").splitlines()[1] not in storage_payload
            assert "raw_rows" not in json.dumps(result)
    finally:
        api.app.dependency_overrides.clear()
        _reset_app_state()
