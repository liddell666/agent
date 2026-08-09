# Dify Experiment Resilience Implementation Report

Date: 2026-08-09

## Scope

- Added a tested boolean normalizer so `drop_duplicates` is sent to the experiment API as `"true"` or `"false"` instead of a fixed literal.
- Added explicit HTTP exception branches for `validate_dataset` and `run_experiment`.
- Added normalized, user-readable JSON and Markdown fallback outputs for both service-failure paths.
- Kept the published success and logical-rejection outputs compatible with the existing workflow.

## Automated repository verification

- Focused Dify helper tests: 12 passed.
- Full test suite before browser configuration: 106 passed, 1 warning.
- Final full test suite after publishing: 106 passed, 1 warning in 9.69 seconds.
- Baseline before implementation: 102 passed, 1 warning.

## Dify workflow verification

Workflow editor:

- Checklist: `所有问题均已解决`.
- Published update confirmed at 2026-08-09 19:58 Asia/Shanghai.
- Published run URL: `http://localhost/workflow/8fF5OEnIVVCskV2s`.

Scenarios:

1. Baseline (`drop_duplicates=false`)
   - status: `succeeded`
   - rows: 15180
   - effective_rows: 15180
   - duplicate_rows: 66
   - roc_auc: 0.871388
   - accuracy: 0.921607
2. Deduplicated (`drop_duplicates=true`)
   - status: `succeeded`
   - rows: 15180
   - effective_rows: 15114
   - duplicate_rows: 66
   - roc_auc: 0.871607
   - accuracy: 0.921932
3. Invalid target (`missing_target_column`)
   - validation returned `valid=false`
   - error code: `missing_target_column`
   - experiment output: `{}`
   - user summary directed the user to fix the CSV using `validation_json`.
4. Validation HTTP fallback code node
   - node run status: `SUCCESS`
   - error code: `validation_service_unavailable`
   - stage: `validate_dataset`
5. Experiment HTTP fallback code node
   - node run status: `SUCCESS`
   - error code: `experiment_service_unavailable`
   - stage: `run_experiment`

## Published interface check

The published page exposes:

- `training_csv`
- `target_column` with default `Y_cls`
- `test_size` with default `0.2`
- `random_state` with default `42`
- `drop_duplicates` as a checkbox
