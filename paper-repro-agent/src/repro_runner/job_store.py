from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import sqlite3
from pathlib import Path
import secrets
from typing import Callable, Iterator

from repro_runner.schemas import JobRecord


_ACTIVE_STATUSES = ("queued", "running", "cancel_requested", "needs_retry")
_QUEUEABLE_STATUSES = ("queued", "needs_retry")
_RECOVERABLE_STATUSES = ("running", "cancel_requested")
_TERMINAL_STATUSES = ("succeeded", "partial", "failed", "cancelled")


class JobCapacityError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _job_id() -> str:
    return f"job-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(8)}"


class JobStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create(self, manifest_id: str, dataset_id: str) -> str:
        record, _created = self._create_or_get_job(
            manifest_id=manifest_id,
            dataset_id=dataset_id,
            enforce_capacity=False,
        )
        return record.job_id

    def admit_job(self, manifest_id: str, dataset_id: str) -> tuple[JobRecord, bool]:
        return self._create_or_get_job(
            manifest_id=manifest_id,
            dataset_id=dataset_id,
            enforce_capacity=True,
        )

    def get(self, job_id: str) -> JobRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError("job was not found")
        return self._record_from_row(row)

    def lookup_by_fingerprint(self, manifest_id: str, dataset_id: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE fingerprint = ?",
                (self._fingerprint(manifest_id, dataset_id),),
            ).fetchone()
        if row is None:
            return None
        return self._record_from_row(row)

    def lookup_result(self, manifest_id: str, dataset_id: str) -> str | None:
        record = self.lookup_by_fingerprint(manifest_id, dataset_id)
        if record is None:
            return None
        return record.result_id

    def has_active_job(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM jobs WHERE status IN (?, ?, ?, ?) LIMIT 1",
                _ACTIVE_STATUSES,
            ).fetchone()
        return row is not None

    def claim_next(
        self,
        *,
        worker_pid: int,
        is_claimable: Callable[[JobRecord], bool] | None = None,
    ) -> JobRecord | None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status IN (?, ?)
                ORDER BY created_at ASC
                """,
                _QUEUEABLE_STATUSES,
            ).fetchall()
            for row in rows:
                job = self._record_from_row(row)
                if is_claimable is not None and not is_claimable(job):
                    continue
                updated = connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, stage = ?, progress = ?, worker_pid = ?, attempt = ?, updated_at = ?
                    WHERE job_id = ? AND status IN (?, ?)
                    """,
                    (
                        "running",
                        "starting",
                        0.0,
                        worker_pid,
                        job.attempt + 1,
                        now,
                        job.job_id,
                        *_QUEUEABLE_STATUSES,
                    ),
                )
                if updated.rowcount == 1:
                    connection.commit()
                    return self.get(job.job_id)
            connection.commit()
        return None

    def mark_running(self, job_id: str, *, worker_pid: int) -> None:
        self._transition(
            job_id,
            invalid_message="job cannot transition to running",
            transition=lambda job: (
                {
                    "queued",
                    "needs_retry",
                },
                {
                    "status": "running",
                    "stage": "starting",
                    "progress": 0.0,
                    "worker_pid": worker_pid,
                    "attempt": job.attempt + 1,
                },
            ),
        )

    def update_progress(self, job_id: str, *, stage: str, progress: float) -> None:
        self._transition(
            job_id,
            invalid_message="job progress can only update while running",
            transition=lambda _job: (
                {"running", "cancel_requested"},
                {"stage": stage, "progress": progress},
            ),
        )

    def request_cancel(self, job_id: str) -> None:
        self._transition(
            job_id,
            invalid_message="job cannot be cancelled",
            transition=lambda job: (
                None
                if job.status in _TERMINAL_STATUSES or job.status == "cancel_requested"
                else (
                    {"queued", "needs_retry"}
                    if job.status in {"queued", "needs_retry"}
                    else {"running"}
                ),
                None
                if job.status in _TERMINAL_STATUSES or job.status == "cancel_requested"
                else (
                    {
                        "status": "cancelled",
                        "stage": "cancelled",
                        "progress": job.progress,
                        "worker_pid": None,
                    }
                    if job.status in {"queued", "needs_retry"}
                    else {
                        "status": "cancel_requested",
                        "stage": job.stage,
                        "progress": job.progress,
                    }
                ),
            ),
        )

    def mark_cancelled(self, job_id: str) -> None:
        self._transition(
            job_id,
            invalid_message="job cannot transition to cancelled",
            transition=lambda job: (
                {"running", "cancel_requested", "queued", "needs_retry"},
                {
                    "status": "cancelled",
                    "stage": "cancelled",
                    "progress": job.progress,
                    "worker_pid": None,
                },
            ),
        )

    def mark_needs_retry(self, job_id: str) -> None:
        self._transition(
            job_id,
            invalid_message="job cannot transition to needs_retry",
            transition=lambda job: (
                {"running", "cancel_requested"},
                {
                    "status": "needs_retry",
                    "stage": "needs_retry",
                    "progress": job.progress,
                    "worker_pid": None,
                },
            ),
        )

    def mark_succeeded(self, job_id: str, *, result_id: str) -> None:
        self._mark_terminal(job_id, status="succeeded", result_id=result_id, error_code=None)

    def mark_partial(self, job_id: str, *, result_id: str) -> None:
        self._mark_terminal(job_id, status="partial", result_id=result_id, error_code=None)

    def mark_failed(
        self,
        job_id: str,
        *,
        error_code: str | None = None,
        result_id: str | None = None,
    ) -> None:
        self._mark_terminal(job_id, status="failed", result_id=result_id, error_code=error_code)

    def recover_incomplete_jobs(self) -> None:
        now = _now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = ?, worker_pid = NULL, updated_at = ?
                WHERE status IN (?, ?)
                """,
                ("needs_retry", "needs_retry", now, *_RECOVERABLE_STATUSES),
            )
            connection.commit()

    def _mark_terminal(
        self,
        job_id: str,
        *,
        status: str,
        result_id: str | None,
        error_code: str | None,
    ) -> None:
        def transition(job: JobRecord):
            allowed_statuses = {"running", "cancel_requested"}
            if status == "failed":
                allowed_statuses.add("queued")
            return allowed_statuses, {
                "status": status,
                "stage": "complete" if status in {"succeeded", "partial"} else status,
                "progress": job.progress if job.status == "queued" else 1.0,
                "worker_pid": None,
                "result_id": result_id,
                "error_code": error_code,
            }

        self._transition(
            job_id,
            invalid_message="job cannot enter a terminal state from its current status",
            transition=transition,
        )

    def _create_or_get_job(
        self,
        *,
        manifest_id: str,
        dataset_id: str,
        enforce_capacity: bool,
    ) -> tuple[JobRecord, bool]:
        now = _now_iso()
        fingerprint = self._fingerprint(manifest_id, dataset_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM jobs WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._record_from_row(existing), False
            if enforce_capacity:
                active = connection.execute(
                    "SELECT 1 FROM jobs WHERE status IN (?, ?, ?, ?) LIMIT 1",
                    _ACTIVE_STATUSES,
                ).fetchone()
                if active is not None:
                    connection.commit()
                    raise JobCapacityError("job capacity reached")
            job_id = _job_id()
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, manifest_id, dataset_id, fingerprint, status, stage, progress,
                    attempt, worker_pid, result_id, error_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    manifest_id,
                    dataset_id,
                    fingerprint,
                    "queued",
                    "queued",
                    0.0,
                    0,
                    None,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            connection.commit()
        assert row is not None
        return self._record_from_row(row), True

    def _update_status(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: float | None = None,
        attempt: int | None = None,
        worker_pid: int | None = ...,
        result_id: str | None = ...,
        error_code: str | None = ...,
    ) -> None:
        updates: list[str] = []
        parameters: list[object] = []
        if status is not None:
            updates.append("status = ?")
            parameters.append(status)
        if stage is not None:
            updates.append("stage = ?")
            parameters.append(stage)
        if progress is not None:
            updates.append("progress = ?")
            parameters.append(progress)
        if attempt is not None:
            updates.append("attempt = ?")
            parameters.append(attempt)
        if worker_pid is not ...:
            updates.append("worker_pid = ?")
            parameters.append(worker_pid)
        if result_id is not ...:
            updates.append("result_id = ?")
            parameters.append(result_id)
        if error_code is not ...:
            updates.append("error_code = ?")
            parameters.append(error_code)
        updates.append("updated_at = ?")
        parameters.append(_now_iso())
        parameters.append(job_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = ?",
                tuple(parameters),
            )
            connection.commit()

    def _transition(
        self,
        job_id: str,
        *,
        invalid_message: str,
        transition: Callable[
            [JobRecord],
            tuple[set[str] | None, dict[str, object | None] | None],
        ],
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = self._get_for_update(connection, job_id)
            expected_statuses, updates = transition(job)
            if updates is None:
                connection.commit()
                return
            if expected_statuses is not None and job.status not in expected_statuses:
                connection.commit()
                raise ValueError(invalid_message)
            self._update_locked(connection, job, **updates)
            connection.commit()

    def _get_for_update(self, connection: sqlite3.Connection, job_id: str) -> JobRecord:
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError("job was not found")
        return self._record_from_row(row)

    def _update_locked(
        self,
        connection: sqlite3.Connection,
        job: JobRecord,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: float | None = None,
        attempt: int | None = None,
        worker_pid: int | None = ...,
        result_id: str | None = ...,
        error_code: str | None = ...,
    ) -> None:
        updates: list[str] = []
        parameters: list[object] = []
        if status is not None:
            updates.append("status = ?")
            parameters.append(status)
        if stage is not None:
            updates.append("stage = ?")
            parameters.append(stage)
        if progress is not None:
            updates.append("progress = ?")
            parameters.append(progress)
        if attempt is not None:
            updates.append("attempt = ?")
            parameters.append(attempt)
        if worker_pid is not ...:
            updates.append("worker_pid = ?")
            parameters.append(worker_pid)
        if result_id is not ...:
            updates.append("result_id = ?")
            parameters.append(result_id)
        if error_code is not ...:
            updates.append("error_code = ?")
            parameters.append(error_code)
        updates.append("updated_at = ?")
        parameters.append(_now_iso())
        parameters.extend((job.job_id, job.status))
        updated = connection.execute(
            f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = ? AND status = ?",
            tuple(parameters),
        )
        if updated.rowcount != 1:
            raise ValueError("job state changed during update")

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    manifest_id TEXT NOT NULL,
                    dataset_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress REAL NOT NULL,
                    attempt INTEGER NOT NULL,
                    worker_pid INTEGER,
                    result_id TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _fingerprint(manifest_id: str, dataset_id: str) -> str:
        return f"{manifest_id}\x00{dataset_id}"

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> JobRecord:
        payload = dict(row)
        payload.pop("fingerprint", None)
        return JobRecord.model_validate(payload)
