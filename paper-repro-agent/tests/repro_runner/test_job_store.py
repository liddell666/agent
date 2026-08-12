import sqlite3

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
