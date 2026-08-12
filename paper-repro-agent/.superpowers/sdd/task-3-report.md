# Task 3 report — Dify protocol helper temporary drafts

- Date: 2026-08-13
- Implementation commit: `ce3da6c` (`feat: bind protocol tokens to temporary drafts`)
- Committed files:
  - `dify/code/experiment_workflow.py`
  - `tests/test_dify_multimodel_code.py`

## Summary

Task 3 extends the Dify protocol helper so prepared protocols are bound to temporary draft IDs and downstream draft POST/GET responses are normalized safely.

Implemented behavior:

- `prepare_protocol_artifacts(...)` now returns:
  - `protocol_preview_json`
  - `protocol_token`
  - `draft_id`
  - `draft_expires_at`
  - `protocol_ready`
  - `protocol_errors`
- Ready tokens now carry payload fields:
  - `v`
  - `exp`
  - `ready`
  - `draft_id`
  - `notes_digest`
  - `manifest`
- Draft IDs are generated as unpredictable URL-safe IDs with the required `draft-[A-Za-z0-9_-]{8,128}` shape.
- Unready prepares return no runnable `protocol_token`, no `draft_id`, and no `draft_expires_at`.
- `normalize_protocol_confirmation(...)` now returns `draft_id` and rejects legacy/old tokens missing `draft_id` with `protocol_payload_invalid`.
- Added:
  - `normalize_protocol_draft_write_response(body, status_code, expected_draft_id)`
  - `normalize_protocol_draft_read_response(body, status_code, expected_draft_id, manifest_json)`
- Draft normalizers return only fixed safe error codes/messages and never echo HTTP bodies, raw CSV/PDF content, tokens, or traceback text into errors.

## TDD evidence

### RED

Command:

```powershell
pytest tests/test_dify_multimodel_code.py -k "draft_id or protocol_draft" -q
```

Outcome before implementation:

- Failed during collection.
- Expected failure: `ImportError: cannot import name 'normalize_protocol_draft_read_response' from 'dify.code.experiment_workflow'`.

### GREEN / focused verification

Command:

```powershell
pytest tests/test_dify_multimodel_code.py -k "draft_id or protocol_draft" -q
```

Outcome:

- `6 passed, 17 deselected in 0.11s`

Command:

```powershell
pytest tests/test_dify_multimodel_code.py -q
```

Outcome:

- `23 passed in 0.10s` in the dirty working tree.
- Staged-index export for the commit content: `21 passed in 0.25s`.

## Compile / embedded-code checks

Command:

```powershell
python -m compileall dify/code
```

Outcome:

- Exit code `0`; no syntax errors.

Command:

```powershell
pytest tests/test_dify_job_workflow.py -q
```

Outcome:

- Dirty working tree: `7 passed in 1.23s`.
- Staged-index export for the commit content: `5 passed in 1.69s`.

## Relevant full/regression checks

Command:

```powershell
pytest tests/test_dify_job_workflow.py tests/test_dify_multimodel_dsl.py -q
```

Outcome in dirty working tree:

- `16 passed`, `2 failed`.
- Failures:
  - `tests/test_dify_multimodel_dsl.py::test_generator_output_is_deterministic_and_keeps_source_workflow_unchanged`
  - `tests/test_dify_multimodel_dsl.py::test_build_multimodel_dsl_returns_the_generated_document_contract`

Command against staged-index export:

```powershell
pytest tests/test_dify_job_workflow.py tests/test_dify_multimodel_dsl.py -q
```

Outcome:

- `11 passed`, `2 failed`.
- Same DSL generator/stored-document contract mismatch.

I did not modify out-of-scope workflow YAML or generator files for this task.

## Staging / dirty-worktree handling

- The worktree was dirty before Task 3, including `dify/code/experiment_workflow.py` and `tests/test_dify_multimodel_code.py`.
- I staged only synthetic Task 3 index blobs for those two files, leaving pre-existing same-file polling/test edits unstaged.
- Commit `ce3da6c` contains only Task 3 helper/test changes.
- Existing dirty working-tree changes remain present after the commit.
- I created a temporary staged-index export directory for verification: `C:\Users\17716\Documents\arcgis\.worktrees\multi-model-cv\.pytest-tmp-task3-index-3879d68d892e4646a5dbc85f09881ba8`.
- Cleanup of that temp directory was rejected by the shell safety guard, so it remains untracked.

## Self-review

- Verified `prepare_protocol_artifacts` returns the required six protocol/draft fields.
- Verified token payload includes `draft_id` and does not issue runnable values when the protocol is unready.
- Verified `normalize_protocol_confirmation` rejects missing/invalid draft IDs and returns `draft_id` only on success.
- Verified draft read/write normalizers reject HTTP error statuses and metadata mismatches without body/token/raw-data echo.
- Verified safe draft error codes were added to `_SAFE_ERROR_CODES`.
- Verified committed diff excludes the pre-existing polling helper changes in the same files.

## Remaining concerns

- The DSL generator contract tests fail because generated multimodel DSL content differs from the stored workflow document. This appears outside the Task 3 helper files and was already reproducible from the staged-index export; I left it unchanged.
- The temporary staged-index export directory remains untracked because the shell cleanup command was blocked by the safety guard.

## Review-fix update — POST metadata binding and safe unready codes

- Date: 2026-08-13
- Fix commit: this review-fix commit
- Files changed:
  - `dify/code/experiment_workflow.py`
  - `tests/test_dify_multimodel_code.py`
  - `.superpowers/sdd/task-3-report.md`

### Findings addressed

- `normalize_protocol_draft_write_response(...)` now accepts a backward-compatible fourth argument, `expected_manifest_json=None`.
- A 2xx draft-write response can only return `draft_saved_ok=True` when:
  - `expected_manifest_json` contains valid `manifest_id` and `dataset_id`;
  - the response draft ID matches `expected_draft_id`;
  - the response `manifest_id` and `dataset_id` exactly match the expected metadata;
  - `expires_at` is a valid positive integer.
- Missing expected metadata or mismatched well-formed response metadata now returns `protocol_draft_token_mismatch` without echoing the HTTP body.
- `prepare_protocol_artifacts(...)` now maps internal unready markers to safe public protocol codes before serializing:
  - `dataset_not_valid` -> `protocol_payload_invalid`
  - `dataset_id` -> `protocol_dataset_missing`
  - `target_column` -> `protocol_target_missing`
  - `feature_columns` -> `protocol_features_missing`
  - `sampling_strategy` -> `protocol_options_invalid`
- `protocol_errors` and `protocol_preview_json.unresolved_protocol_fields` no longer expose the internal unresolved markers.

### RED command and outcome

Command:

```powershell
pytest tests/test_dify_multimodel_code.py -k "protocol_draft_write_response or unresolved_fields or unready_sampling" -q
```

Outcome before implementation:

- `4 failed, 22 deselected in 0.32s`
- Expected failures:
  - unready prepare errors collapsed to `protocol_payload_invalid`;
  - `normalize_protocol_draft_write_response(...)` rejected the new fourth argument;
  - the existing three-argument POST write normalizer allowed unbound success.

### GREEN commands and outcomes

Focused reviewer regression:

```powershell
pytest tests/test_dify_multimodel_code.py -k "protocol_draft_write_response or unresolved_fields or unready_sampling" -q
```

- Outcome: `4 passed, 22 deselected in 0.10s`

Focused draft coverage:

```powershell
pytest tests/test_dify_multimodel_code.py -k "draft_id or protocol_draft or unresolved_fields or unready_sampling" -q
```

- Outcome: `10 passed, 16 deselected in 0.08s`

Full helper coverage:

```powershell
pytest tests/test_dify_multimodel_code.py -q
```

- Outcome: `26 passed in 0.10s`

Compile check:

```powershell
python -m compileall dify/code
```

- Outcome: exit code `0`; no syntax errors.

Embedded Dify workflow helper coverage:

```powershell
pytest tests/test_dify_job_workflow.py -q
```

- Outcome: `7 passed in 1.29s`

### Self-review

- Confirmed the three-argument POST draft normalizer call remains syntactically accepted but cannot succeed without expected metadata.
- Confirmed successful POST draft normalization now binds response metadata to expected manifest/dataset metadata.
- Confirmed mismatched POST metadata returns only stable safe errors and blank metadata pass-through fields.
- Confirmed unready prepare output uses safe protocol codes and deduplicates mapped errors.
- Confirmed existing draft ID generation, legacy token rejection, read normalizer behavior, and redaction behavior are preserved.
