from __future__ import annotations

from hashlib import sha256
from threading import Event
from time import monotonic, sleep

import pytest

from repro_runner.config import Settings
from repro_runner.job_runner import JobRunner, stage_job_inputs
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
    ValidationErrorItem,
)
from repro_runner.storage import load_suite_result


def _csv() -> bytes:
    rows = ["x1,x2,Y_cls", "123456,654321,1"]
    for index in range(1, 41):
        rows.append(f"{index % 2},{(index // 2) % 2},{index % 2}")
    return ("\n".join(rows) + "\n").encode("utf-8")


def _manifest(content: bytes, *, manifest_hex: str, models: list[str]) -> ExperimentManifest:
    return ExperimentManifest(
        manifest_id="sha256:" + manifest_hex * 64,
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


def _suite_result(
    *,
    status: str,
    model_results: list[ModelRunResult],
) -> ExperimentSuiteResult:
    return ExperimentSuiteResult(
        experiment_id="exp-20260812T010203Z-deadbeef",
        status=status,
        config=ModelSuiteConfig(models=["logistic_regression", "random_forest"], cv_folds=3, n_iter=1, n_jobs=1),
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
        results=model_results,
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
    raise AssertionError("condition was not satisfied before timeout")


def test_job_runner_persists_partial_progress_and_terminal_result(tmp_path):
    content = _csv()
    manifest = _manifest(content, manifest_hex="1", models=["logistic_regression", "random_forest"])
    settings = Settings(
        storage_dir=tmp_path / "experiments",
        job_store_path=tmp_path / "jobs.sqlite3",
        job_work_dir=tmp_path / "job-inputs",
    )
    store = JobStore(settings.job_store_path)
    job_id = store.create(manifest.manifest_id, manifest.dataset_id)
    stage_job_inputs(job_id, manifest, content, settings)

    progress_emitted = Event()
    allow_finish = Event()

    def fake_execute_job(*, manifest, csv_bytes, progress_callback, should_stop, settings):
        del should_stop, settings
        assert manifest.models == ["logistic_regression", "random_forest"]
        assert csv_bytes == content
        progress_callback("logistic_regression", 1, 2)
        progress_emitted.set()
        assert allow_finish.wait(timeout=5)
        return _suite_result(
            status="partial",
            model_results=[
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
                ),
                ModelRunResult(
                    model="random_forest",
                    status="failed",
                    error=ValidationErrorItem(
                        code="model_training_failed",
                        message="random_forest model training failed (RuntimeError)",
                    ),
                ),
            ],
        )

    runner = JobRunner(store=store, settings=settings, execute_job=fake_execute_job)
    runner.start()
    try:
        runner.notify()
        assert progress_emitted.wait(timeout=5)
        running = store.get(job_id)
        assert running.status == "running"
        assert running.stage == "model:logistic_regression"
        assert running.progress == 0.5

        allow_finish.set()
        _wait_until(lambda: store.get(job_id).status == "partial")
        job = store.get(job_id)
        assert job.result_id == "exp-20260812T010203Z-deadbeef"

        loaded = load_suite_result(job.result_id, settings)
        assert loaded.status == "partial"
        payload = loaded.model_dump_json()
        assert "123456,654321,1" not in payload
        assert not (settings.job_work_dir / job_id / "input.csv").exists()
        assert not (settings.job_work_dir / job_id / "manifest.json").exists()
    finally:
        runner.stop()


def test_job_runner_stop_marks_interrupted_job_as_needs_retry(tmp_path):
    content = _csv()
    manifest = _manifest(content, manifest_hex="2", models=["logistic_regression"])
    settings = Settings(
        storage_dir=tmp_path / "experiments",
        job_store_path=tmp_path / "jobs.sqlite3",
        job_work_dir=tmp_path / "job-inputs",
    )
    store = JobStore(settings.job_store_path)
    job_id = store.create(manifest.manifest_id, manifest.dataset_id)
    stage_job_inputs(job_id, manifest, content, settings)

    started = Event()

    def blocking_execute_job(*, manifest, csv_bytes, progress_callback, should_stop, settings):
        del manifest, csv_bytes, progress_callback, settings
        started.set()
        while not should_stop():
            sleep(0.02)
        return _suite_result(status="failed", model_results=[])

    runner = JobRunner(store=store, settings=settings, execute_job=blocking_execute_job)
    runner.start()
    runner.notify()

    assert started.wait(timeout=5)
    _wait_until(lambda: store.get(job_id).status == "running")

    runner.stop()

    job = store.get(job_id)
    assert job.status == "needs_retry"
    assert job.result_id is None
    assert (settings.job_work_dir / job_id / "input.csv").exists()


def test_job_runner_skips_needs_retry_jobs_without_staged_inputs(tmp_path):
    content = _csv()
    manifest = _manifest(content, manifest_hex="3", models=["logistic_regression"])
    settings = Settings(
        storage_dir=tmp_path / "experiments",
        job_store_path=tmp_path / "jobs.sqlite3",
        job_work_dir=tmp_path / "job-inputs",
    )
    store = JobStore(settings.job_store_path)
    job_id = store.create(manifest.manifest_id, manifest.dataset_id)
    store.mark_running(job_id, worker_pid=111)
    store.recover_incomplete_jobs()

    runner = JobRunner(
        store=store,
        settings=settings,
        execute_job=lambda **_kwargs: _suite_result(status="succeeded", model_results=[]),
    )
    runner.start()
    try:
        runner.notify()
        sleep(0.3)
        job = store.get(job_id)
        assert job.status == "needs_retry"
        assert job.attempt == 1
    finally:
        runner.stop()


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_job_runner_stop_preserves_needs_retry_when_executor_raises(tmp_path):
    content = _csv()
    manifest = _manifest(content, manifest_hex="4", models=["logistic_regression"])
    settings = Settings(
        storage_dir=tmp_path / "experiments",
        job_store_path=tmp_path / "jobs.sqlite3",
        job_work_dir=tmp_path / "job-inputs",
    )
    store = JobStore(settings.job_store_path)
    job_id = store.create(manifest.manifest_id, manifest.dataset_id)
    stage_job_inputs(job_id, manifest, content, settings)

    started = Event()

    def failing_execute_job(*, should_stop, **_kwargs):
        started.set()
        while not should_stop():
            sleep(0.02)
        raise RuntimeError("executor stopped during shutdown")

    runner = JobRunner(store=store, settings=settings, execute_job=failing_execute_job)
    runner.start()
    runner.notify()

    assert started.wait(timeout=5)
    _wait_until(lambda: store.get(job_id).status == "running")

    runner.stop()

    job = store.get(job_id)
    assert job.status == "needs_retry"
    assert job.result_id is None
    assert (settings.job_work_dir / job_id / "input.csv").exists()
