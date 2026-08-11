# Task 1 General Reliable Binary Workflow Report

## Implementation summary

I fixed the suite comparison request path so the embedded Dify comparison code now preserves valid manual override qualifiers instead of dropping them.

What changed:

- `build_suite_comparison_request` now uses a small allowlist loop for each permitted qualifier/metric field instead of a chain of one-off assignments.
- `safe_qualifier` was widened so it accepts ordinary Unicode labels like the manual paper override text, while still rejecting control characters and obvious secret-like payloads.
- The DSL generator now emits the same comparison code shape as the runtime helper, so the generated YAML stays in sync with the source generator.
- I added a regression test in the DSL tests for the manual override case and a matching direct helper test in the code tests.

## TDD evidence

RED:

- Ran:
  - `pytest -q paper-repro-agent/tests/test_dify_multimodel_dsl.py::test_generated_suite_request_keeps_manual_override_qualifiers`
- Result:
  - Failed because `dataset` and `split` were missing from `reported_metrics`.

GREEN:

- Implemented the minimal fix in the helper and generator, then regenerated the YAML.
- Reran the same focused test.
- Result:
  - Passed.

## Files changed

- `paper-repro-agent/dify/code/comparison_workflow.py`
- `paper-repro-agent/scripts/build_multimodel_dsl.py`
- `paper-repro-agent/dify/paper-comparison-multimodel-workflow.yml`
- `paper-repro-agent/tests/test_dify_multimodel_code.py`
- `paper-repro-agent/tests/test_dify_multimodel_dsl.py`

## Test commands and results

Focused Dify tests:

- `pytest -q paper-repro-agent/tests/test_dify_multimodel_code.py paper-repro-agent/tests/test_dify_multimodel_dsl.py`
- Result: `16 passed`

Full suite:

- From `paper-repro-agent` with `PYTHONPATH=src`:
  - `pytest -q`
- Result: `258 passed, 1 warning`

Note:

- I also tried the full suite from the repository root first, but that run failed during import collection because the package root was not on `PYTHONPATH`. Rerunning from `paper-repro-agent` with `PYTHONPATH=src` produced the passing result above.

## Self-review findings

- The generated Dify workflow now matches the source generator after regeneration.
- The comparison request still only forwards the approved fields:
  - `name`
  - `reported_value`
  - `dataset`
  - `split`
  - `dataset_id`
  - `test_size`
  - `random_state`
  - `train_rows`
  - `test_rows`
  - `test_digest`
- Existing rollback / fallback workflow behavior was not changed.
- Pre-existing task reports were left untouched.

## Concerns

- `safe_qualifier` is intentionally broader now so it can preserve real manual override labels. It still blocks control characters and a few obvious secret-like patterns, but it is less restrictive than the original ASCII-only filter.
- The test environment needs the package directory as the working directory for the full suite. Running from the repository root without that path setup will fail collection.
