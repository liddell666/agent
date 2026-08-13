# Task 7 UI acceptance blocker follow-up - 2026-08-12

## Root causes

After commit `2b0d9e2` fixed the merged-graph PDF guard and the run-branch
`draft_id` output contract, live Dify UI prepare advanced past parser and
diagnosis into `POST /v1/protocol-drafts`, but that request failed with
HTTP `422` / `protocol_token_tampered`.

The remaining mismatch was secret propagation across Dify Code nodes. The
embedded helper code still defined `_PROTOCOL_SECRET` via
`os.environ.get("DIFY_PROTOCOL_SECRET")`, but Dify workflow environment
variables are not exposed inside Code-node Python through `os.environ`. In the
real UI this meant `prepare_protocol_artifacts(...)` signed the preview token
with an empty secret inside the Code node while repro-runner validated the same
token with the configured server-side secret. The HMAC no longer matched, so
`/v1/protocol-drafts` rejected the draft write as `protocol_token_tampered`.

The correct Dify boundary is an explicit Code-node input sourced from
`["env", "DIFY_PROTOCOL_SECRET"]`, with the entrypoint threading that input as
`secret=protocol_secret`. The blank secret declaration still belongs only in the
workflow `environment_variables` export; the serialized DSL must not embed any
real secret value.

The earlier August 12, 2026 UI findings still applied too:

- the merged prepare guard could not pass raw `paper_pdf` file objects through a
  Code node because Dify serializes Code-node inputs before execution; and
- the merged run branch needed `draft_id` declared in
  `normalize_protocol_confirmation` outputs because Dify validates returned keys
  against the node contract and the downstream draft-read path consumes
  `draft_id`.

## Fix

- Added a shared `_protocol_secret_input()` generator helper that binds
  `protocol_secret` from `value_selector: ["env", "DIFY_PROTOCOL_SECRET"]` with
  `value_type: string`.
- Updated the shared multimodel protocol-confirmation Code node entrypoint to
  accept `protocol_secret: str = ""` and call
  `normalize_protocol_confirmation(..., secret=protocol_secret)`.
- Updated the standalone prepare builder entrypoint, legacy prepare builder
  entrypoint, and merged prepare entrypoint to accept `protocol_secret: str = ""`
  and call `prepare_protocol_artifacts(..., secret=protocol_secret)`.
- Regenerated the relevant workflow YAML so the multimodel, prepare, and merged
  DSLs all declare the explicit Code-node secret input while preserving the
  blank `DIFY_PROTOCOL_SECRET` environment declaration and keeping the DSL free
  of any actual secret value.
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

Clean-snapshot regression against commit `2b0d9e2` with only the new tests
overlaid:

`$env:PYTHONPATH='.;src'; python -m pytest tests\test_dify_multimodel_dsl.py -q -k "generated_protocol_code_nodes_bind_workflow_secret_explicitly or prepare_builders_bind_workflow_secret_explicitly"`

Outcome:

- `2 failed, 12 deselected`
- Expected failure: generated and builder-produced
  `prepare_protocol_artifacts` / `normalize_protocol_confirmation` nodes were
  missing any `protocol_secret` variable binding, so the assertions raised
  `StopIteration`.

Second clean-snapshot regression against commit `2b0d9e2`:

`$env:PYTHONPATH='.;src'; python -m pytest tests\test_dify_merged_dsl.py -q -k "merged_protocol_code_nodes_bind_workflow_secret_explicitly"`

Outcome:

- `1 failed, 12 deselected`
- Expected failure: merged `prepare_protocol_artifacts` and
  `normalize_protocol_confirmation` nodes also lacked the explicit
  `protocol_secret` input.

Earlier August 12, 2026 RED evidence for the file-guard and output-contract
fixes:

- `tests\test_dify_merged_dsl.py -q -k "prepare_pdf_guard"` failed because
  `prepare_inputs_ok?` had a Code variable
  `{'value_selector': ['3900000000001', 'paper_pdf'], 'value_type': 'file', 'variable': 'paper_pdf'}`.
- `tests\test_dify_merged_dsl.py -q -k "protocol_confirmation_outputs"` failed
  because the false branch returned extra `draft_id` compared with the declared
  Dify Code node outputs.

## GREEN and verification evidence

Regenerate:

`python scripts\build_multimodel_dsl.py`

Outcome:

- exit `0`
- regenerated `dify/paper-comparison-merged-workflow.yml`
- regenerated `dify/paper-comparison-multimodel-workflow.yml`
- regenerated `dify/paper-comparison-prepare-workflow.yml`

Secret-binding GREEN:

`$env:PYTHONPATH='.;src'; python -m pytest tests\test_dify_multimodel_dsl.py -q -k "generated_protocol_code_nodes_bind_workflow_secret_explicitly or prepare_builders_bind_workflow_secret_explicitly"`

Outcome:

- `2 passed, 12 deselected`

Merged secret-binding GREEN:

`$env:PYTHONPATH='.;src'; python -m pytest tests\test_dify_merged_dsl.py -q -k "merged_protocol_code_nodes_bind_workflow_secret_explicitly"`

Outcome:

- `1 passed, 12 deselected`

Relevant Dify DSL/helper suite:

`$env:PYTHONPATH='.;src'; python -m pytest -q tests\test_dify_multimodel_dsl.py tests\test_dify_merged_dsl.py tests\test_dify_multimodel_code.py`

Outcome:

- `54 passed in 19.62s`

Determinism checks:

`$env:PYTHONPATH='.;src'; python -m pytest -q tests\test_dify_multimodel_dsl.py::test_generator_output_is_deterministic_and_keeps_source_workflow_unchanged tests\test_dify_merged_dsl.py::test_merged_builder_is_deterministic_and_matches_generated_file`

Outcome:

- `2 passed in 4.21s`

Compile check:

`python -m compileall -q scripts dify\code src tests`

Outcome:

- exit `0`

Generated graph inspection now confirms:

- multimodel `normalize_protocol_confirmation` binds
  `protocol_secret <- ["env", "DIFY_PROTOCOL_SECRET"]`
- merged `prepare_protocol_artifacts` binds
  `protocol_secret <- ["env", "DIFY_PROTOCOL_SECRET"]`
- merged `normalize_protocol_confirmation` binds
  `protocol_secret <- ["env", "DIFY_PROTOCOL_SECRET"]`
- standalone prepare builders bind the same explicit secret input
- the blank secret environment declaration remains exported with `value: ""`
  and `value_type: secret`

## UI acceptance status

- The remaining live-secret propagation bug is addressed at graph contract
  level: every affected Code node now receives `protocol_secret` explicitly from
  the workflow environment selector instead of relying on `os.environ` inside
  Dify Python.
- Generated merged DSL is ready for re-import.
- The previously reproduced UI failure is addressed at graph level: no Code node
  receives raw File variables, and the prepare PDF guard is a Dify IF/ELSE
  `not empty` check on the Start file array.
- The run-branch `confirm_protocol=false` UI failure is addressed at graph
  contract level: `normalize_protocol_confirmation` declares `draft_id` and both
  tested false/true branches return exactly the declared key set.
- Remaining UI acceptance: import the regenerated merged DSL into Dify and rerun
  prepare mode with a PDF plus CSV to confirm the live UI reaches
  `parse_paper` instead of failing at input preparation, then rerun run mode
  with the same secret configured on both sides to confirm Dify accepts the
  protocol-draft write and no longer reports `protocol_token_tampered`.

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

## Final UI acceptance

After the follow-up secret-binding fix, the live Dify workflow was imported into
a new disposable application. Re-importing intentionally restored Secret fields
as masked placeholders, so the two runtime secrets were re-entered through the
UI without putting their values into the DSL.

- Prepare mode with the fixture PDF and CSV completed with `SUCCESS` and
  returned a non-empty protocol preview/token.
- Run mode with `confirm_protocol=false` stopped safely with
  `protocol_not_confirmed` and did not submit an experiment.
- The confirmed run initially exposed one final generated-reference defect:
  the draft-read request header used the literal `Start.protocol_token` label
  instead of the generated Start node ID. The builder now emits the concrete
  Start selector, and the regression test asserts the exact header.
- After regenerating and re-importing, prepare completed with `SUCCESS`, the
  draft was written with HTTP 200, the draft-read request returned HTTP 200,
  and the confirmed run completed with `SUCCESS` and all six declared outputs:
  `dossier_json`, `validation_json`, `experiment_json`, `comparison_json`,
  `assessment_json`, and `markdown_report`.
- The application remained unpublished; the two original workflows were not
  overwritten.
