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
