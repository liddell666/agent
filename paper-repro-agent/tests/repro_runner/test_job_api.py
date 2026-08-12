from __future__ import annotations

import json
from hashlib import sha256
from threading import Event
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from repro_runner import api
from repro_runner.config import Settings, get_settings
from repro_runner.job_store import JobStore
from repro_runner.schemas import (
    DatasetProfile,
    ExperimentManifest,
    ExperimentMetrics,
    ExperimentSuiteResult,
    FeatureImportance,
    ModelRunResult,
    ModelSuiteConfig,
    PreprocessingSummary,
    SplitProvenance,
)


def _csv(rows: int = 40) -> bytes:
    data = ["x1,x2,Y_cls", "123456,654321,1"]
    for index in range(rows):
        data.append(f"{index % 2},{(index // 2) % 2},{index % 2}")
    return ("\n".join(data) + "\n").encode("utf-8")


def _manifest(content: bytes, *, hex_digit: str, model_count: int = 2) -> ExperimentManifest:
    models = ["logistic_regression", "random_forest"][:model_count]
    return ExperimentManifest(
        manifest_id="sha256:" + hex_digit * 64,
        dataset_id="sha256:" + sha256(content).hexdigest(),
        target_column="Y_cls",
        feature_columns=["x1", "x2"],
        missing_policy="reject",
        sampling_strategy="original",
        comparison_mode="paper_comparable",
        test_size=0.2,
        random_state=42,
        cv_folds=3,
        optimization_metric="roc_auc",
        threshold=0.5,
        models=models,
    )


def _suite_result(status: str = "succeeded") -> ExperimentSuiteResult:
    return ExperimentSuiteResult(
        experiment_id="exp-20260812T010203Z-deadbeef",
        status=status,
        config=ModelSuiteConfig(models=["logistic_regression"], cv_folds=3, n_iter=1, n_jobs=1),
        dataset=DatasetProfile(
            rows=41,
            effective_rows=41,
            features=2,
            target="Y_cls",
            missing_values=0,
            duplicate_rows=0,
            class_counts={"0": 20, "1": 21},
            class_ratios={"0": 20 / 41, "1": 21 / 41},
            column_names=["x1", "x2", "Y_cls"],
            column_types={"x1": "int64", "x2": "int64", "Y_cls": "int64"},
            numeric_ranges={"x1": (0.0, 1.0), "x2": (0.0, 1.0)},
            dataset_id="sha256:" + "a" * 64,
        ),
        split_provenance=SplitProvenance(
            test_size=0.2,
            random_state=42,
            train_rows=32,
            test_rows=9,
            test_digest="sha256:" + "b" * 64,
        ),
        results=[
            ModelRunResult(
                model="logistic_regression",
                status="succeeded",
                cv_best_score=0.9,
                best_params={"C": 1},
                metrics=ExperimentMetrics(
                    roc_auc=0.91,
                    accuracy=0.9,
                    balanced_accuracy=0.9,
                    precision=0.9,
                    recall=0.9,
                    f1=0.9,
                    confusion_matrix=[[4, 1], [0, 4]],
                ),
                feature_importance=[FeatureImportance(feature="x1", importance=1.0)],
                fit_seconds=0.123,
            )
        ],
        performance_ranking=["logistic_regression"],
        preprocessing=PreprocessingSummary(
            numeric_columns=["x1", "x2"],
            categorical_columns=[],
            transformed_feature_names=["x1", "x2"],
            sampling_strategy="original",
        ),
    )


def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.02)
    raise AssertionError("condition was not satisfy before timeout")


def _reset_app_state() -> None:
    for name in (
        "experiment_limiter",
        "experiment_idempotency_registry",
        "model_suite_idempotency_registry",
        "job_store",
        "job_runner",
    ):
        if hasattr(api.app.state, name):
            delattr(api.app.state, name)


@pytest.fixture
def settings(tmp_path) -> Settings:
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


def test_create_job_returns_before_executor_completes_and_polling_shows_progress(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    content = _csv()
    manifest = _manifest(content, hex_digit="1", model_count=2)
    progress_emitted = Event()
    allow_finish = Event()

    def fake_execute_job(*, manifest, csv_bytes, progress_callback, should_stop, settings):
        del should_stop, settings
        assert manifest.manifest_id == "sha256:" + "1" * 64
        assert csv_bytes == content
        progress_callback("logistic_regression", 1, 2)
        progress_emitted.set()
        assert allow_finish.wait(timeout=5)
        return _suite_result()

    monkeypatch.setattr(api, "_default_execute_job", fake_execute_job)

    response = client.post(
        "/v1/jobs",
        data={"manifest_json": manifest.model_dump_json()},
        files={"file": ("data.csv", content, "text/csv")},
    )

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {
        "job_id",
        "manifest_id",
        "dataset_id",
        "status",
        "stage",
        "progress",
        "attempt",
        "result_id",
        "error_code",
    }
    assert body["job_id"].startswith("job-")
    assert body["status"] in {"queued", "running"}
    assert progress_emitted.wait(timeout=5)

    polling = client.get(f"/v1/jobs/{body['job_id']}")
    assert polling.status_code == 200
    assert polling.json()["stage"] == "model:logistic_regression"
    assert polling.json()["progress"] == 0.5

    allow_finish.set()
    _wait_until(lambda: client.get(f"/v1/jobs/{body['job_id']}").json()["status"] == "succeeded")

    result = client.get(f"/v1/jobs/{body['job_id']}/result")
    assert result.status_code == 200
    assert result.json()["experiment_id"] == "exp-20260812T010203Z-deadbeef"


def test_create_job_returns_capacity_response_while_single_worker_is_occupied(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    first_content = _csv()
    second_content = _csv(rows=50)
    first_manifest = _manifest(first_content, hex_digit="2", model_count=1)
    second_manifest = _manifest(second_content, hex_digit="3", model_count=1)
    allow_finish = Event()

    def blocking_execute_job(*, progress_callback, **kwargs):
        progress_callback("logistic_regression", 1, 1)
        assert allow_finish.wait(timeout=5)
        return _suite_result()

    monkeypatch.setattr(api, "_default_execute_job", blocking_execute_job)

    first = client.post(
        "/v1/jobs",
        data={"manifest_json": first_manifest.model_dump_json()},
        files={"file": ("first.csv", first_content, "text/csv")},
    )
    assert first.status_code == 202

    second = client.post(
        "/v1/jobs",
        data={"manifest_json": second_manifest.model_dump_json()},
        files={"file": ("second.csv", second_content, "text/csv")},
    )

    allow_finish.set()

    assert second.status_code == 429
    detail = second.json()["detail"]
    assert detail["code"] == "job_capacity_reached"
    assert detail["request_id"]


def test_restart_recovers_running_job_as_needs_retry(settings: Settings):
    _reset_app_state()
    store = JobStore(settings.job_store_path)
    job_id = store.create("manifest-running", "sha256:" + "4" * 64)
    store.mark_running(job_id, worker_pid=321)

    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.get(f"/v1/jobs/{job_id}")

    api.app.dependency_overrides.clear()
    _reset_app_state()

    assert response.status_code == 200
    assert response.json()["status"] == "needs_retry"


def test_completed_job_is_reused_without_retraining(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    content = _csv()
    manifest = _manifest(content, hex_digit="5", model_count=1)
    calls = 0

    def fake_execute_job(**_kwargs):
        nonlocal calls
        calls += 1
        return _suite_result()

    monkeypatch.setattr(api, "_default_execute_job", fake_execute_job)

    created = client.post(
        "/v1/jobs",
        data={"manifest_json": manifest.model_dump_json()},
        files={"file": ("data.csv", content, "text/csv")},
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    _wait_until(lambda: client.get(f"/v1/jobs/{job_id}").json()["status"] == "succeeded")

    duplicate = client.post(
        "/v1/jobs",
        data={"manifest_json": manifest.model_dump_json()},
        files={"file": ("data.csv", content, "text/csv")},
    )

    assert duplicate.status_code == 202
    assert duplicate.json()["job_id"] == job_id
    assert calls == 1
    result = client.get(f"/v1/jobs/{job_id}/result")
    assert result.status_code == 200
    assert result.json()["experiment_id"] == "exp-20260812T010203Z-deadbeef"


def test_create_job_rejects_invalid_manifest_json(client: TestClient):
    response = client.post(
        "/v1/jobs",
        data={"manifest_json": json.dumps({"manifest_id": "bad"})},
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_request"
    assert detail["request_id"]


def test_create_job_staging_failure_returns_safe_internal_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    content = _csv()
    manifest = _manifest(content, hex_digit="6", model_count=1)

    def fail_stage_job_inputs(*_args, **_kwargs):
        raise OSError(f"raw csv leaked: {content.decode('utf-8')}")

    monkeypatch.setattr(api, "stage_job_inputs", fail_stage_job_inputs)

    response = client.post(
        "/v1/jobs",
        data={"manifest_json": manifest.model_dump_json()},
        files={"file": ("data.csv", content, "text/csv")},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "job_create_failed"
    assert detail["message"] == "the experiment service could not complete the request"
    assert "traceback" not in response.text.lower()
    assert "raw csv leaked" not in response.text
    assert "123456,654321,1" not in response.text
