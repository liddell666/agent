# Task 9 chunked report

status: implementation complete; readiness/privacy gate passed.

commits:
- `12f6ebf` — `test: gate chunked ollama extraction readiness`

tests:
- TDD RED: the initial readiness/contract run reported `2 failed, 21 passed` for the missing extractor-boundary contract.
- Focused readiness/privacy run: `28 passed`, with one existing dependency warning.
- Related focused gate: `163 passed`, `0 failed`.
- Child extractor source syntax and deterministic boundary checks passed.
- Full-repository tests and live Docker/Dify/Ollama readiness were intentionally not run.

concerns:
- No live network/container readiness was run; live authentication remains a runtime prerequisite, and no secret material was persisted.
- One existing FastAPI/Starlette dependency deprecation warning remains.
- Legacy Dify and DeepSeek workflows were preserved and not modified.

## 2026-09-04 Fix

status: fixed; readiness now requires extractor-owned configuration, source metadata, and token evidence.

commit: `4bde3d6` — `fix: require extractor-owned readiness evidence`

RED:
- `python -m pytest tests/paper_dossier_extractor/test_service.py::test_service_diagnostics_include_safe_effective_config_and_actual_call_evidence tests/paper_dossier_extractor/test_schemas.py::test_diagnostics_forbid_source_content tests/test_ollama_readiness_contract.py::test_extractor_boundary_rejects_missing_token_evidence -q`
- Result: `5 failed in 0.98s`; the service diagnostics contract was missing the new safe fields, and readiness still accepted missing extractor token evidence.

GREEN:
- `python -m pytest tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py -q`
- Result: `28 passed in 0.42s`
- `python -m pytest tests/paper_dossier_extractor tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py -q`
- Result: `169 passed, 1 warning in 118.15s (0:01:58)`

concerns:
- The existing FastAPI/Starlette `TestClient` deprecation warning remains.
- Live Docker/Dify/Ollama readiness was not run in this task; only the focused automated suites above were used for verification.

## 2026-09-04 Review Fix

status: fixed; normal `POST /v1/extract-dossier` diagnostics are back to the Task 1 exact schema, and extractor readiness proof now comes from a dedicated authenticated content-free boundary owned by the extractor service.

commit: `6e67f53` — `fix: isolate extractor readiness evidence`

RED:
- `python -m pytest tests/paper_dossier_extractor/test_schemas.py tests/paper_dossier_extractor/test_service.py tests/paper_dossier_extractor/test_api.py tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py -q`
- Result: `2 errors, 0 failed in 1.44s`; collection failed because `ExtractorReadinessResponse` and `extract_readiness_probe` did not exist yet.

GREEN:
- `python -m pytest tests/paper_dossier_extractor/test_schemas.py tests/paper_dossier_extractor/test_service.py tests/paper_dossier_extractor/test_api.py tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py -q`
- Result: `71 passed, 1 warning in 1.19s`
- `python -m pytest tests/paper_dossier_extractor tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py -q`
- Result: `174 passed, 1 warning in 116.62s (0:01:56)`

concerns:
- The existing FastAPI/Starlette `TestClient` deprecation warning remains.
- Live Docker/Dify/Ollama readiness was not run in this task; verification was limited to the focused automated suites above.

## 2026-09-04 Review Fix 2

status: fixed; readiness now fails closed on impossible completion-token evidence, and extractor capacity reservation is shared, deterministic, and non-blocking across extract and readiness routes.

commit_message: `fix: harden readiness evidence and capacity reservation`

RED:
- `python -m pytest tests/paper_dossier_extractor/test_service.py::test_readiness_probe_fails_closed_when_completion_tokens_exceed_reserved_budget tests/paper_dossier_extractor/test_api.py::test_second_concurrent_readiness_is_rejected_without_waiting_after_reservation_race -q`
- Result: `2 failed, 1 warning in 1.01s`; readiness accepted completion tokens beyond the configured reserve, and the readiness route allowed a second request to wait through the check/acquire race instead of returning a stable capacity error.

GREEN:
- `python -m pytest tests/paper_dossier_extractor/test_service.py::test_readiness_probe_fails_closed_when_completion_tokens_exceed_reserved_budget tests/paper_dossier_extractor/test_api.py::test_capacity_reservation_rejects_second_reservation_without_waiting tests/paper_dossier_extractor/test_api.py::test_second_concurrent_readiness_is_rejected_and_slot_recovers -q`
- Result: `3 passed, 1 warning in 0.82s`
- `python -m pytest tests/paper_dossier_extractor tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py -q`
- Result: `121 passed, 1 warning in 1.38s`

concerns:
- The existing FastAPI/Starlette `TestClient` deprecation warning remains.
- Live Docker/Dify/Ollama readiness was not run in this task; verification was limited to focused extractor and readiness suites.
