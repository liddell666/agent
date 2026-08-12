# Task 4 implementation report: persistent restartable single-user jobs

## Implementation summary

- Added a SQLite-backed `JobStore` with queued/running/cancel-requested/cancelled/needs-retry/succeeded/partial/failed states, atomic claim/update transactions, manifest/dataset idempotency lookup, progress fields, result references, and restart recovery.
- Added a single background `JobRunner` with a bounded wake event, dynamic executor resolution for test and application injection, per-job staged manifest/CSV inputs, progress callbacks, cancellation/shutdown handling, and terminal-input cleanup.
- Added `POST /v1/jobs`, `GET /v1/jobs/{job_id}`, `POST /v1/jobs/{job_id}/cancel`, and `GET /v1/jobs/{job_id}/result` while preserving the existing synchronous and comparison routes.
- Added safe job response schemas and persistent job path settings. Existing Compose persistence already mounts `/data/experiments`, which contains the default SQLite job database and job input directory.
- Job results continue to use the existing privacy-preserving suite storage contract; raw CSV is retained only in the temporary staged job directory and is removed after terminal persistence. Shutdown/retry paths retain staged input for recovery.
- Closed every SQLite connection after each operation to avoid resource leaks.

## Review fixes (2026-08-12)

- Hardened worker post-execution persistence so `save_suite_result` or later terminal writeback failures no longer kill the background thread or strand jobs in `running`. The worker now records a safe `result_persistence_failed` error, applies terminal cleanup rules, and keeps processing later jobs.
- Moved job-state transitions behind transactional helpers that read and update within the same SQLite `BEGIN IMMEDIATE` transaction and use conditional `WHERE job_id = ? AND status = ?` updates to reject stale overwrites.
- Added atomic job admission in `JobStore`: duplicate manifest/dataset fingerprints return the existing job, otherwise the same transaction checks active-capacity and inserts a new queued job. The API now maps capacity saturation to a structured 429 without leaking SQLite internals.
- Integrated immediate staged-input cleanup for `queued` and `needs_retry` cancellations through the API cancel route while preserving existing shutdown/retry retention rules for `running` and `cancel_requested`.
- Replaced the staged-input directory handoff with a Windows-safe move operation so `/v1/jobs` staging no longer fails with `PermissionError` during atomic directory promotion.

## TDD and verification evidence

- RED: `PYTHONPATH=src pytest tests/repro_runner/test_job_store.py tests/repro_runner/test_job_runner.py tests/repro_runner/test_job_api.py -q` initially failed because the job modules did not exist.
- Initial GREEN attempt exposed three API/worker boundaries: response-model field filtering, stale executor binding, and recovery/shutdown races. The failing run was `3 failed, 7 passed`.
- Focused GREEN after fixes: `PYTHONPATH=src pytest --basetemp=.pytest-tmp-task4-focused-final tests/repro_runner/test_job_store.py tests/repro_runner/test_job_runner.py tests/repro_runner/test_job_api.py -q` -> `14 passed, 1 warning`.
- Connection-lifecycle RED: the new `test_job_store_connection_context_closes_connection` failed because SQLite's native context manager did not close the connection.
- Connection-lifecycle GREEN: `PYTHONPATH=src pytest --basetemp=.pytest-tmp-task4-green tests/repro_runner/test_job_store.py -q` -> `4 passed`.
- Full gate: `PYTHONPATH=src pytest --basetemp=.pytest-tmp-task4-full-final -q` -> `305 passed, 1 warning`.
- The one warning is the existing Starlette/httpx TestClient deprecation.
- Review RED: `PYTHONPATH=src pytest --basetemp=.pytest-tmp-task4-review-red tests/repro_runner/test_job_store.py tests/repro_runner/test_job_runner.py tests/repro_runner/test_job_api.py -q` initially failed with the new review regressions plus the existing Windows staging issue.
- Review focused GREEN: `PYTHONPATH=src pytest --basetemp=.pytest-tmp-task4-review-red tests/repro_runner/test_job_store.py tests/repro_runner/test_job_runner.py tests/repro_runner/test_job_api.py -q` -> `19 passed, 1 warning`.
- Review full GREEN: `PYTHONPATH=src pytest --basetemp=.pytest-tmp-task4-review-full -q` -> `310 passed, 1 warning`.

## Changed files

- `.superpowers/sdd/task-4-general-report.md`
- `src/repro_runner/job_store.py`
- `src/repro_runner/job_runner.py`
- `src/repro_runner/schemas.py`
- `src/repro_runner/config.py`
- `src/repro_runner/suite_engine.py`
- `src/repro_runner/api.py`
- `tests/repro_runner/test_job_store.py`
- `tests/repro_runner/test_job_runner.py`
- `tests/repro_runner/test_job_api.py`

## Known limitations

- The job API currently accepts the confirmed manifest and CSV; an optional dossier reference is not persisted because dossier content is deliberately excluded from job/result storage.
- `balanced_undersample` remains explicitly unsupported until a fold-safe sampler is implemented.
- The worker is intentionally single-process/single-job and CPU-bound; it does not parallelize model training.
- Test runs must use a fresh pytest base directory when rerunning fixed-result fixtures; stale SQLite files from an interrupted local run can otherwise be mistaken for persisted application state.
