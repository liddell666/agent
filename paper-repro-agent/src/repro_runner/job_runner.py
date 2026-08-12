from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
from threading import Event, Lock, Thread
from typing import Callable

from repro_runner.config import Settings
from repro_runner.job_store import JobStore
from repro_runner.schemas import ExperimentManifest, ExperimentSuiteResult
from repro_runner.storage import save_suite_result


logger = logging.getLogger(__name__)

def stage_job_inputs(
    job_id: str,
    manifest: ExperimentManifest,
    csv_bytes: bytes,
    settings: Settings,
) -> None:
    directory = _job_directory(job_id, settings)
    temporary = directory.parent / f".{job_id}.tmp"
    if temporary.exists():
        shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True, exist_ok=True)
    try:
        (temporary / "manifest.json").write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        (temporary / "input.csv").write_bytes(csv_bytes)
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        shutil.move(str(temporary), str(directory))
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


class JobRunner:
    def __init__(
        self,
        *,
        store: JobStore,
        settings: Settings,
        execute_job: Callable[..., ExperimentSuiteResult] | None = None,
        execute_job_resolver: Callable[[], Callable[..., ExperimentSuiteResult]] | None = None,
    ) -> None:
        if execute_job is None and execute_job_resolver is None:
            raise ValueError("JobRunner requires an execute_job function or resolver")
        self.store = store
        self.settings = settings
        self._execute_job = execute_job
        self._execute_job_resolver = execute_job_resolver
        self._wake_event = Event()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._current_job_id: str | None = None
        self._current_lock = Lock()

    def start(self) -> None:
        self.store.recover_incomplete_jobs()
        self._thread = Thread(target=self._run_loop, name="repro-job-runner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._current_lock:
            current_job_id = self._current_job_id
        if current_job_id is not None:
            try:
                self.store.mark_needs_retry(current_job_id)
            except ValueError:
                pass
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def notify(self) -> None:
        self._wake_event.set()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=0.25)
            self._wake_event.clear()
            while not self._stop_event.is_set():
                job = self.store.claim_next(
                    worker_pid=os.getpid(),
                    is_claimable=self._is_claimable_job,
                )
                if job is None:
                    break
                with self._current_lock:
                    self._current_job_id = job.job_id
                try:
                    try:
                        self._process_job(job.job_id)
                    except Exception:
                        logger.exception("job processing crashed job_id=%s", job.job_id)
                        self._fail_job_safely(job.job_id, error_code="job_processing_failed")
                finally:
                    with self._current_lock:
                        self._current_job_id = None

    def _process_job(self, job_id: str) -> None:
        try:
            manifest, csv_bytes = _read_job_inputs(job_id, self.settings)
        except FileNotFoundError:
            logger.warning("job inputs missing job_id=%s", job_id)
            self._preserve_retryable_job(job_id)
            return

        def progress_callback(model_name: str, completed: int, total: int) -> None:
            try:
                self.store.update_progress(
                    job_id,
                    stage=f"model:{model_name}",
                    progress=0.0 if total <= 0 else completed / total,
                )
            except ValueError:
                return

        try:
            result = self._resolve_execute_job()(
                manifest=manifest,
                csv_bytes=csv_bytes,
                progress_callback=progress_callback,
                should_stop=self._stop_event.is_set,
                settings=self.settings,
            )
        except Exception:
            logger.exception("job execution failed job_id=%s", job_id)
            current = self.store.get(job_id)
            if self._stop_event.is_set():
                self._preserve_retryable_job(job_id)
                return
            if current.status == "cancel_requested":
                self.store.mark_cancelled(job_id)
                cleanup_job_inputs(job_id, self.settings)
                return
            if current.status in {"running", "cancel_requested"}:
                self.store.mark_failed(job_id, error_code="job_execution_failed")
                cleanup_job_inputs(job_id, self.settings)
            return

        if self._stop_event.is_set():
            return

        current = self.store.get(job_id)
        if current.status == "cancel_requested":
            self.store.mark_cancelled(job_id)
            cleanup_job_inputs(job_id, self.settings)
            return

        try:
            result_id = save_suite_result(result, self.settings)
            if result.status == "succeeded":
                self.store.mark_succeeded(job_id, result_id=result_id)
            elif result.status == "partial":
                self.store.mark_partial(job_id, result_id=result_id)
            else:
                self.store.mark_failed(
                    job_id,
                    result_id=result_id,
                    error_code="model_training_failed",
                )
        except Exception:
            logger.exception("job result persistence failed job_id=%s", job_id)
            self._fail_job_safely(job_id, error_code="result_persistence_failed")
            return
        cleanup_job_inputs(job_id, self.settings)

    def _resolve_execute_job(self) -> Callable[..., ExperimentSuiteResult]:
        if self._execute_job_resolver is not None:
            return self._execute_job_resolver()
        assert self._execute_job is not None
        return self._execute_job

    def _is_claimable_job(self, job) -> bool:
        return job.status != "needs_retry" or _job_inputs_exist(job.job_id, self.settings)

    def _preserve_retryable_job(self, job_id: str) -> None:
        current = self.store.get(job_id)
        if current.status in {"running", "cancel_requested"}:
            try:
                self.store.mark_needs_retry(job_id)
            except ValueError:
                return

    def _fail_job_safely(self, job_id: str, *, error_code: str) -> None:
        try:
            current = self.store.get(job_id)
        except LookupError:
            return
        if self._stop_event.is_set():
            self._preserve_retryable_job(job_id)
            return
        if current.status == "cancel_requested":
            try:
                self.store.mark_cancelled(job_id)
            except ValueError:
                return
            cleanup_job_inputs(job_id, self.settings)
            return
        if current.status in {"running", "cancel_requested", "queued"}:
            try:
                self.store.mark_failed(job_id, error_code=error_code)
            except ValueError:
                return
            cleanup_job_inputs(job_id, self.settings)


def _job_directory(job_id: str, settings: Settings) -> Path:
    return settings.job_work_dir.resolve() / job_id


def _read_job_inputs(job_id: str, settings: Settings) -> tuple[ExperimentManifest, bytes]:
    directory = _job_directory(job_id, settings)
    manifest = ExperimentManifest.model_validate_json(
        (directory / "manifest.json").read_text(encoding="utf-8")
    )
    return manifest, (directory / "input.csv").read_bytes()


def cleanup_job_inputs(job_id: str, settings: Settings) -> None:
    directory = _job_directory(job_id, settings)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)


def _job_inputs_exist(job_id: str, settings: Settings) -> bool:
    directory = _job_directory(job_id, settings)
    return (directory / "manifest.json").exists() and (directory / "input.csv").exists()
