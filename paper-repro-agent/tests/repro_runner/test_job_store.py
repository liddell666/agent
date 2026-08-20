import sqlite3
from threading import Barrier, Thread

import pytest

from repro_runner.job_store import JobStore


def test_job_store_connection_context_closes_connection(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")

    with store._connect() as connection:
        connection.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_job_store_recovers_running_job_as_needs_retry(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create("manifest-1", "sha256:" + "1" * 64)

    store.mark_running(job_id, worker_pid=123)
    store.recover_incomplete_jobs()

    assert store.get(job_id).status == "needs_retry"


def test_job_store_tracks_terminal_result_and_idempotent_lookup(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    manifest_id = "manifest-2"
    dataset_id = "sha256:" + "2" * 64

    first_job_id = store.create(manifest_id, dataset_id)
    duplicate_job_id = store.create(manifest_id, dataset_id)

    assert duplicate_job_id == first_job_id

    store.mark_running(first_job_id, worker_pid=999)
    store.update_progress(
        first_job_id,
        stage="model:logistic_regression",
        progress=0.5,
    )
    store.mark_succeeded(first_job_id, result_id="exp-20260812T010203Z-deadbeef")

    job = store.get(first_job_id)
    assert job.status == "succeeded"
    assert job.stage == "complete"
    assert job.progress == pytest.approx(1.0)
    assert job.result_id == "exp-20260812T010203Z-deadbeef"
    assert store.lookup_by_fingerprint(manifest_id, dataset_id).job_id == first_job_id
    assert store.lookup_result(manifest_id, dataset_id) == "exp-20260812T010203Z-deadbeef"


def test_job_store_rejects_invalid_transitions_and_supports_cancel_failure(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")

    queued_job_id = store.create("manifest-queued", "sha256:" + "3" * 64)
    store.request_cancel(queued_job_id)
    assert store.get(queued_job_id).status == "cancelled"

    running_job_id = store.create("manifest-running", "sha256:" + "4" * 64)
    store.mark_running(running_job_id, worker_pid=77)
    store.request_cancel(running_job_id)
    assert store.get(running_job_id).status == "cancel_requested"
    store.mark_cancelled(running_job_id)
    assert store.get(running_job_id).status == "cancelled"

    failed_job_id = store.create("manifest-failed", "sha256:" + "5" * 64)
    store.mark_running(failed_job_id, worker_pid=88)
    store.mark_failed(failed_job_id, error_code="model_training_failed")
    assert store.get(failed_job_id).status == "failed"
    assert store.get(failed_job_id).error_code == "model_training_failed"

    with pytest.raises(ValueError):
        store.mark_running(failed_job_id, worker_pid=99)


def test_job_store_only_marks_missing_inputs_for_needs_retry_jobs(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create("manifest-missing-inputs", "sha256:" + "8" * 64)

    store.mark_running(job_id, worker_pid=123)

    with pytest.raises(ValueError):
        store.mark_retry_inputs_missing(job_id)

    running = store.get(job_id)
    assert running.status == "running"
    assert running.worker_pid == 123
    assert running.error_code is None


def test_job_store_conditional_transition_rejects_stale_terminal_overwrite(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    concurrent_store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create("manifest-race", "sha256:" + "6" * 64)
    store.mark_running(job_id, worker_pid=42)

    with concurrent_store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        stale_job = concurrent_store._get_for_update(connection, job_id)
        connection.commit()

    store.mark_succeeded(job_id, result_id="exp-20260812T010203Z-deadbeef")

    with concurrent_store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="job state changed during update"):
            concurrent_store._update_locked(
                connection,
                stale_job,
                status="cancel_requested",
                stage=stale_job.stage,
                progress=stale_job.progress,
            )
        connection.rollback()

    job = store.get(job_id)
    assert job.status == "succeeded"
    assert job.result_id == "exp-20260812T010203Z-deadbeef"


def test_job_store_admit_job_atomically_deduplicates_under_race(tmp_path):
    first_store = JobStore(tmp_path / "jobs.sqlite3")
    second_store = JobStore(tmp_path / "jobs.sqlite3")
    barrier = Barrier(2)
    job_ids: list[str] = []
    created_flags: list[bool] = []
    errors: list[BaseException] = []

    def admit(store: JobStore) -> None:
        try:
            barrier.wait(timeout=5)
            record, created = store.admit_job("manifest-shared", "sha256:" + "7" * 64)
            job_ids.append(record.job_id)
            created_flags.append(created)
        except BaseException as exc:  # pragma: no cover - exercised by assertion
            errors.append(exc)

    first_thread = Thread(target=admit, args=(first_store,))
    second_thread = Thread(target=admit, args=(second_store,))
    first_thread.start()
    second_thread.start()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not errors
    assert len(job_ids) == 2
    assert len(set(job_ids)) == 1
    assert sorted(created_flags) == [False, True]

    with sqlite3.connect(tmp_path / "jobs.sqlite3") as connection:
        row_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert row_count == 1
