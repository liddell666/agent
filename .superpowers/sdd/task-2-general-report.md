# Task 2 report — general CSV diagnostics and immutable experiment protocols

Date: 2026-08-11

## Implementation summary

- Added `paper-repro-agent/src/repro_runner/protocol.py` with deterministic `create_manifest(...)` support, stable canonical hashing, CV-feasibility checks, and immutable extra-forbid manifest validation.
- Added `paper-repro-agent/src/repro_runner/preprocessing.py` with deterministic column-plan helpers only; no sklearn transformers, one-hot encoding, or model-training changes were introduced.
- Extended `paper-repro-agent/src/repro_runner/schemas.py` with additive protocol/diagnostic models and additive `DatasetOptions` fields while preserving current defaults for the existing numeric-only APIs.
- Reworked `paper-repro-agent/src/repro_runner/data.py` to add safe ordinary-CSV diagnostics, UTF-8/UTF-8-BOM support, delimiter sniffing limited to comma/tab/semicolon, stable dataset fingerprints from original bytes, deterministic column profiling, target-candidate suggestions, and structured safe diagnostic responses without exposing source rows.
- Extended `paper-repro-agent/src/repro_runner/api.py` with `/v1/diagnose-dataset` while leaving `/v1/validate-dataset`, `/v1/run-experiment`, and the multi-model suite behavior intact.

## RED / GREEN evidence

### Required RED before implementation

Command:

`PYTHONPATH=src pytest tests/repro_runner/test_protocol.py -q`

Observed result:

- Failed during collection with `ModuleNotFoundError: No module named 'repro_runner.protocol'`.
- This confirmed the new protocol surface did not exist yet.

### Focused GREEN

Command:

`PYTHONPATH=src pytest tests/repro_runner/test_data.py tests/repro_runner/test_protocol.py tests/repro_runner/test_preprocessing.py tests/repro_runner/test_api.py -q`

Observed result:

- `67 passed, 1 warning in 10.44s`

### Full suite before commit

Command:

`PYTHONPATH=src pytest -q`

Observed result:

- `266 passed, 1 warning in 24.09s`

## File list

- `paper-repro-agent/src/repro_runner/protocol.py`
- `paper-repro-agent/src/repro_runner/preprocessing.py`
- `paper-repro-agent/src/repro_runner/schemas.py`
- `paper-repro-agent/src/repro_runner/data.py`
- `paper-repro-agent/src/repro_runner/api.py`
- `paper-repro-agent/tests/repro_runner/test_data.py`
- `paper-repro-agent/tests/repro_runner/test_protocol.py`
- `paper-repro-agent/tests/repro_runner/test_preprocessing.py`
- `paper-repro-agent/tests/repro_runner/test_api.py`

## Test commands and results

- `PYTHONPATH=src pytest tests/repro_runner/test_protocol.py -q` → expected RED (`ModuleNotFoundError` for new protocol module)
- `PYTHONPATH=src pytest tests/repro_runner/test_data.py tests/repro_runner/test_protocol.py tests/repro_runner/test_preprocessing.py tests/repro_runner/test_api.py -q` → `67 passed, 1 warning`
- `PYTHONPATH=src pytest -q` → `266 passed, 1 warning`

## Self-review findings

- Confirmed the task stayed inside the Task 2 boundary: no `ColumnTransformer`, no one-hot encoding, no sklearn preprocessing pipeline changes, and no model-training behavior changes.
- Confirmed the existing numeric-only experiment APIs still pass the full repository suite with the same single warning baseline.
- Confirmed the new diagnostics surface does not include source rows and does not echo malformed row content back in structured errors.
- Confirmed the instructed unrelated edits remained untouched: `paper-repro-agent/.superpowers/sdd/task-3-report.md`, `task-4-report.md`, `task-5-report.md`, `task-7-report.md`, `task-8-report.md`, and `.pytest-tmp/`.

## Concerns

- `missing_policy="impute"` and broader mixed-type diagnostics are now represented in diagnostics/protocols, but actual sklearn preprocessing and model-time handling remain intentionally deferred to Task 3 per the brief boundary.
- The only remaining test warning is the pre-existing Starlette/TestClient deprecation warning already present in the repository test environment.

## Review follow-up fixes (CHANGES_REQUESTED on commit `96dd9c5`)

- Stopped automatic target confirmation in diagnostics:
  - `diagnose_dataset()` now leaves `dataset.target` as `null` unless the user explicitly confirms `target_column`.
  - `target_candidates` remain available for UI selection.
  - `recommended_options.target_column` may still suggest a candidate, but `recommended_options.target_column_confirmed` stays `false` until explicit confirmation.
- Tightened manifest construction:
  - `create_manifest()` now requires a confirmed target via `DatasetOptions.target_column_confirmed=True`.
  - The manifest path no longer treats diagnostic suggestions as confirmed target choices.
- Extended `/v1/diagnose-dataset` input compatibility:
  - Added support for `exclude_columns` alongside legacy `exclude_columns_json`.
  - Conflicting values now return a structured `422 invalid_request` instead of silently ignoring one input.
- Removed the invalid-exclude 500 path:
  - Diagnostic column-plan resolution is now performed once.
  - Invalid exclude columns are reported as safe structured diagnostic errors (`invalid_exclude_columns`) without echoing source rows.
- Added diagnostic non-finite numeric rejection:
  - The diagnose path now rejects `NaN`/`Infinity`-style numeric tokens with `non_finite_numeric_feature`.
  - Diagnostic `numeric_ranges` now use only finite numeric bounds.
- Added explicit diagnostic resource limits through settings:
  - `max_diagnostic_rows`
  - `max_diagnostic_cardinality`
  - Exceeding these limits now returns safe structured diagnostic errors instead of warnings only.
  - Defaults were chosen to safely allow the current ordinary binary dataset scale while keeping Task 3 preprocessing/training work out of scope.

## Review follow-up RED / GREEN evidence

### Review-fix RED

Command:

`PYTHONPATH=src pytest tests/repro_runner/test_data.py tests/repro_runner/test_protocol.py tests/repro_runner/test_api.py -q`

Observed result:

- `15 failed, 63 passed, 1 warning in 15.55s`
- Failures matched the requested review fixes: unconfirmed-target handling, manifest confirmation, exclude-column handling, non-finite diagnostics, and row/cardinality limits.

### Review-fix focused GREEN

Command:

`PYTHONPATH=src pytest tests/repro_runner/test_data.py tests/repro_runner/test_protocol.py tests/repro_runner/test_preprocessing.py tests/repro_runner/test_api.py -q`

Observed result:

- `80 passed, 1 warning in 12.96s`

### Review-fix full suite

Command:

`PYTHONPATH=src pytest -q`

Observed result:

- `279 passed, 1 warning in 28.36s`

## Review follow-up file changes

- Updated `paper-repro-agent/src/repro_runner/config.py` to add diagnostic row/cardinality limits
- Updated `paper-repro-agent/src/repro_runner/schemas.py` to track explicit target confirmation in `DatasetOptions`
- Updated `paper-repro-agent/src/repro_runner/data.py` to separate target suggestion from confirmation, validate diagnostic numeric/resource limits, and safely handle invalid exclude columns
- Updated `paper-repro-agent/src/repro_runner/protocol.py` to require explicit target confirmation for manifest creation
- Updated `paper-repro-agent/src/repro_runner/api.py` to accept both `exclude_columns` and `exclude_columns_json` safely
- Updated `paper-repro-agent/tests/repro_runner/test_data.py`, `test_protocol.py`, and `test_api.py` with regression coverage for all requested fixes
