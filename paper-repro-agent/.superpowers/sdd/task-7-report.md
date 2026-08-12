# Task 7 UI acceptance blocker follow-up - 2026-08-13

## Root causes

Live Dify acceptance reproduced a real merged-DSL prepare failure after importing
the workflow and uploading a PDF plus CSV:

`Start -> normalize_run_mode -> run_mode? -> prepare_inputs_ok?` failed with
`Type is not JSON serializable: File`.

The merged graph had a Code node variable wired directly to `Start.paper_pdf`
with `value_type: file`. Dify Code/Template-style nodes serialize inputs before
execution, and raw File objects are not safely JSON-serializable there. The
legacy prepare workflow avoided this because it sent the Start file directly to
the HTTP form-data parser node and never passed the File through Code. Dify
IF/ELSE supports presence checks on file arrays, so the safe prepare guard is an
IF/ELSE condition on `Start.paper_pdf`, not a Code node.

Live Dify acceptance also reproduced a run-branch failure when
`confirm_protocol=false`: `normalize_protocol_confirmation` failed with
`Not all output parameters are validated.` The embedded helper returned
`protocol_ok`, `manifest_json`, `draft_id`, and `protocol_errors`, while the
generated Code node declared only `protocol_ok`, `manifest_json`, and
`protocol_errors`. Dify validates Code node return keys against declared
outputs, so both false and true branches must return exactly the declared key
set. The merged run branch also uses `draft_id` downstream to read the saved
protocol draft, so the correct fix is to declare `draft_id` instead of dropping
it from the entrypoint.

## Fix

- Replaced the merged prepare-side `prepare_inputs_ok?` Code node and
  `prepare_inputs_valid?` boolean IF node with one `prepare_pdf_present?`
  IF/ELSE node.
- The new guard checks `Start.paper_pdf` with `comparison_operator: not empty`
  and `varType: array[file]`.
- Kept `parse_paper` uploading `Start.paper_pdf` directly as HTTP form-data.
- Made `prepare_input_failure` a static Code node with no variables. It always
  emits the safe `paper_pdf_required` failure outputs and no stale file
  selectors.
- Added `draft_id` to the generated `normalize_protocol_confirmation` Code node
  outputs so its false/true branch return keys match Dify's declared outputs and
  the merged run branch keeps its protocol-draft read selector.

## RED evidence

Command:

`$env:PYTHONPATH='.;src'; python -m pytest tests\test_dify_merged_dsl.py -q -k "prepare_pdf_guard"`

Outcome:

- `1 failed, 10 deselected`
- Expected failure: `prepare_inputs_ok?` had a Code variable
  `{'value_selector': ['3900000000001', 'paper_pdf'], 'value_type': 'file', 'variable': 'paper_pdf'}`.

An earlier attempt with `.\.venv312\Scripts\python.exe` failed because that venv
does not exist in this worktree; the Python 3.13 system runner was then used for
the actual RED/GREEN cycle.

Second RED command:

`$env:PYTHONPATH='.;src'; python -m pytest tests\test_dify_merged_dsl.py -q -k "protocol_confirmation_outputs"`

Outcome:

- `1 failed, 11 deselected`
- Expected failure: false branch returned extra `draft_id` compared with the
  declared Dify Code node outputs.

## GREEN and verification evidence

Regenerate:

`python scripts\build_multimodel_dsl.py`

Outcome:

- exit `0`
- regenerated `dify/paper-comparison-merged-workflow.yml`
- regenerated `dify/paper-comparison-multimodel-workflow.yml` because the
  shared generated run-branch `normalize_protocol_confirmation` output
  declaration is used there too

Focused GREEN:

`$env:PYTHONPATH='.;src'; python -m pytest tests\test_dify_merged_dsl.py -q -k "prepare_pdf_guard or graph_wires_prepare or protocol_confirmation_outputs"`

Outcome:

- `3 passed, 9 deselected`

Relevant Dify DSL/helper suite:

`$env:PYTHONPATH='.;src'; python -m pytest -q tests\test_dify_merged_dsl.py tests\test_dify_multimodel_dsl.py tests\test_dify_multimodel_code.py tests\test_dify_comparison_dsl.py tests\test_dify_code.py`

Outcome:

- `86 passed in 20.14s`

Compile check:

`python -m compileall -q scripts dify\code src tests`

Outcome:

- exit `0`

Generated graph inspection:

- `code_file_vars= []`
- `guard_operator= not empty`
- `guard_varType= array[file]`
- `guard_selector= ['3900000000001', 'paper_pdf']`
- `prepare_failure_variables= []`
- `parse_form_file= ['3900000000001', 'paper_pdf']`
- `confirmation_outputs= ['draft_id', 'manifest_json', 'protocol_errors', 'protocol_ok']`

Broader Dify test attempt:

`$env:PYTHONPATH='.;src'; python -m pytest -q tests\test_dify_merged_dsl.py tests\test_dify_multimodel_dsl.py tests\test_dify_multimodel_code.py tests\test_dify_job_workflow.py tests\test_dify_comparison_dsl.py tests\test_dify_code.py`

Outcome:

- `91 passed`, `2 failed`
- Failures are in the separate `tests/test_dify_job_workflow.py` prepare/run
  workflow contract, where existing generated prepare code includes
  `DIFY_PROTOCOL_SECRET` and returns draft readiness fields while that dirty
  test still expects only `PARSER_API_TOKEN` and two prepare outputs. No
  `paper-comparison-prepare-workflow.yml` file was changed by this follow-up.

## UI acceptance status

- Generated merged DSL is ready for re-import.
- The previously reproduced UI failure is addressed at graph level: no Code node
  receives raw File variables, and the prepare PDF guard is a Dify IF/ELSE
  `not empty` check on the Start file array.
- The run-branch `confirm_protocol=false` UI failure is addressed at graph
  contract level: `normalize_protocol_confirmation` declares `draft_id` and both
  tested false/true branches return exactly the declared key set.
- Remaining UI acceptance: import the regenerated merged DSL into Dify and rerun
  prepare mode with a PDF plus CSV to confirm the live UI reaches
  `parse_paper` instead of failing at input preparation, then rerun run mode with
  `confirm_protocol=false` to confirm Dify accepts
  `normalize_protocol_confirmation` outputs and follows the false branch.

# Task 7 report - 2026-08-11

Fix commit hash: `80d4c71`

## Scope

Follow-up fix for the Task 7 review findings inside `C:\Users\17716\Documents\arcgis\.worktrees\multi-model-cv\paper-repro-agent`.

Committed fix files:

- `dify/code/comparison_workflow.py`
- `scripts/build_multimodel_dsl.py`
- `dify/paper-comparison-multimodel-workflow.yml`
- `tests/test_dify_multimodel_code.py`
- `tests/test_dify_multimodel_dsl.py`

Not committed:

- `.superpowers/sdd/task-7-report.md`
- pre-existing edits to `.superpowers/sdd/task-3-report.md`
- pre-existing edits to `.superpowers/sdd/task-4-report.md`
- pre-existing edits to `.superpowers/sdd/task-5-report.md`

## RED evidence

Command:

`$env:PYTHONPATH='.;src'; pytest -q tests/test_dify_multimodel_code.py tests/test_dify_multimodel_dsl.py`

Outcome:

- 4 expected failures from the new privacy/provenance regression tests
- helper request builder forwarded raw suite provenance fields unchanged
- helper formatter reproduced raw backend error text in `markdown_report`
- embedded generated DSL request/formatter code showed the same two leaks

## GREEN evidence

Command:

`python scripts/build_multimodel_dsl.py`

Outcome:

- regenerated `dify/paper-comparison-multimodel-workflow.yml`

Command:

`$env:PYTHONPATH='.;src'; pytest -q tests/test_dify_multimodel_code.py tests/test_dify_multimodel_dsl.py tests/test_dify_comparison_dsl.py tests/test_dify_code.py`

Outcome:

- `48 passed in 3.04s`

## Generator and safety checks

Published workflow unchanged:

`git diff -- 'dify/paper-comparison-workflow.yml'`

Outcome:

- no diff output

Determinism:

- `tests/test_dify_multimodel_dsl.py` writes the generated DSL twice and confirms byte-identical output
- the stored generated workflow matches fresh generator output

Structural and safety checks covered by tests:

- two suite URLs present
- six suite inputs present with safe defaults
- one stable `{{#sys.workflow_run_id#}}` idempotency key
- one `end` node
- six string outputs
- embedded Python code nodes compile
- failure-branch code nodes do not reference HTTP node outputs directly
- helper and embedded suite request builders strip suspicious provenance values instead of echoing them
- helper and embedded suite formatters preserve only allowlisted safe backend messages and otherwise emit `details redacted for privacy.`

## Self-review

- preserved the existing published `dify/paper-comparison-workflow.yml`
- kept the current Dify helper and old DSL tests green
- confined the review fix to the suite helper path and the generated multimodel DSL copy
- sanitized suite request provenance with bounded numeric checks, safe `exp-` IDs, exact lowercase `sha256:` digests, and bounded qualifier strings that still allow values like `full sample` and `test`
- replaced arbitrary backend error text in suite markdown reports with a privacy-preserving redaction message unless the backend message is explicitly allowlisted as safe

## Concerns

- Git reports CRLF normalization warnings for several Task 7 files. They did not affect test outcomes, but the worktree is configured to rewrite line endings on future git writes.
