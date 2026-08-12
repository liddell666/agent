# Task 6 Report — Cross-layer protocol regression tests

Date: 2026-08-13

## Scope

- Added cross-layer regression coverage in `tests/repro_runner/test_api.py`.
- Added one Dify helper-level secret non-echo assertion in `tests/test_dify_multimodel_code.py`.
- Did not change production behavior.
- Did not modify legacy workflow test scope.
- Did not edit `tests/test_dify_merged_dsl.py`; current graph, selector, and failure-output invariant coverage was already present and non-duplicative.

## Coverage added

- Generated one protocol token with `prepare_protocol_artifacts(...)` using `test-secret`.
- Posted the generated `draft_id`, `protocol_token`, and safe dossier JSON to `POST /v1/protocol-drafts`.
- Confirmed the same token with `normalize_protocol_confirmation(..., confirm_protocol=True, secret="test-secret")`.
- Read the draft with `GET /v1/protocol-drafts/{draft_id}` and `X-Protocol-Token`, asserting status `200` and exact dossier match.
- Posted a one-byte changed CSV to `POST /v1/jobs` with the confirmed manifest and asserted `manifest_dataset_mismatch` plus zero job rows.
- Confirmed `confirm_protocol=False` returns `protocol_not_confirmed` and zero job rows.
- Advanced draft-store time to the token expiry and asserted `protocol_draft_expired` and zero job rows.
- Deleted the temporary draft directory and asserted `protocol_draft_not_found` and zero job rows.
- Asserted `prepare_protocol_artifacts(...)` does not echo the protocol secret in helper outputs.

## Verification commands and exact results

Focused new-test checks:

```powershell
pytest tests/repro_runner/test_api.py -k generated_protocol_token_flows -q
```

Result: `1 passed, 60 deselected, 1 warning in 3.25s`

```powershell
pytest tests/test_dify_multimodel_code.py -k never_echoes_protocol_secret -q
```

Result: `1 passed, 26 deselected in 0.12s`

Required layered regression command:

```powershell
pytest tests/repro_runner/test_protocol_drafts.py tests/repro_runner/test_api.py tests/test_dify_multimodel_code.py tests/test_dify_merged_dsl.py -q
```

Result: `2 failed, 110 passed, 1 warning in 21.61s`

Failures observed:

- `tests/repro_runner/test_api.py::test_corrupt_stored_result_returns_409_not_not_found[get]` — expected `409`, got `404`.
- `tests/repro_runner/test_api.py::test_corrupt_stored_result_returns_409_not_not_found[compare]` — expected `409`, got `404`.

These failures are outside the new Task 6 cross-layer protocol assertions.

Full regression command:

```powershell
pytest -q
```

Result: `4 failed, 386 passed, 1 warning in 45.83s`

Failures observed:

- `tests/repro_runner/test_api.py::test_corrupt_stored_result_returns_409_not_not_found[get]` — expected `409`, got `404`.
- `tests/repro_runner/test_api.py::test_corrupt_stored_result_returns_409_not_not_found[compare]` — expected `409`, got `404`.
- `tests/test_dify_job_workflow.py::test_prepare_workflow_parses_pdf_before_building_protocol` — expected only `PARSER_API_TOKEN`, got an additional `DIFY_PROTOCOL_SECRET`.
- `tests/test_dify_job_workflow.py::test_run_workflow_embeds_confirmation_and_polling_code` — expected prepare helper output keys `protocol_preview_json` and `protocol_token`; current output also includes `draft_id`, `draft_expires_at`, `protocol_ready`, and `protocol_errors`.

These failures appear to be pre-existing legacy-scope expectations relative to the current merged protocol workflow state.

Generator determinism:

```powershell
python scripts/build_multimodel_dsl.py
python scripts/build_multimodel_dsl.py
```

Result: both runs exited `0`. SHA-256 hashes for generated tracked DSL/Markdown files were unchanged before the first run, after the first run, and after the second run. The second generation was byte-identical and made no generated tracked changes.

Whitespace check:

```powershell
git diff --check
```

Result: exit `0`; output contained only existing LF-to-CRLF working-copy warnings.

## Dirty worktree note

Pre-existing unrelated dirty files were present before Task 6 work, including prior task reports, Dify code/workflow files, production API/dossier files, and several tests. I preserved those changes and will stage only the Task 6 hunks/files needed for this report and the new regression coverage.
