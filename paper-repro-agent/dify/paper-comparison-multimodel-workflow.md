# Multi-model CV workflow operator guide / 多模型交叉验证工作流操作指南

This guide is for the separate multi-model workflow at `dify/paper-comparison-multimodel-workflow.yml`.
Keep `dify/paper-comparison-workflow.yml` as the current V3 rollback target until live verification is accepted.
Do not overwrite the published V3 workflow during import, testing, or rollback.

The normal path is two-step: import and run `dify/paper-comparison-prepare-workflow.yml` first,
review its aggregate `protocol_preview_json`, then pass its short-lived `protocol_token`
to this confirmed-run workflow with `confirm_protocol=true`. The run workflow submits the
confirmed manifest to the persistent `/v1/jobs` API and polls a bounded number of times; it
does not wait synchronously for model training inside the initial request.

## 1. Workflow files / 工作流文件

- Import: `dify/paper-comparison-multimodel-workflow.yml`
- Rollback target: `dify/paper-comparison-workflow.yml`
- This multi-model workflow is a separate Dify app/version, not an in-place replacement.

## 2. Default model order / 默认模型顺序

The default suite order is fixed and should stay:

1. `logistic_regression`
2. `random_forest`
3. `xgboost`
4. `lightgbm`
5. `svm`
6. `knn`
7. `mlp`

Default `models_json`:

```json
["logistic_regression","random_forest","xgboost","lightgbm","svm","knn","mlp"]
```

## 3. Suite inputs and defaults / 输入与默认值

| Input | Default | Notes |
| --- | --- | --- |
| `paper_dossier_json` | required file | Existing dossier input retained so the six legacy output strings remain compatible. |
| `training_csv` | required file | Re-upload the exact CSV used in prepare; the runner verifies its dataset fingerprint against the confirmed token manifest. |
| `models_json` | JSON array above | Ordered suite definition. Duplicate or unknown names are rejected safely. |
| `target_column` | `Y_cls` | Binary target column. |
| `test_size` | `0.2` | Shared outer held-out test fraction for all successful models. |
| `random_state` | `42` | Shared deterministic seed for outer split and CV shuffle. |
| `cv_folds` | `5` | CV runs only inside the outer training partition. |
| `optimization_metric` | `roc_auc` | Allowed values: `roc_auc`, `f1`, `recall`, `balanced_accuracy`. |
| `threshold` | `0.5` | Backend default for final probability-to-label conversion; the current Dify start form keeps this backend default. |
| `n_iter` | `8` | RandomizedSearchCV iterations per model. |
| `use_gpu` | `false` | CPU is the default. Set `true` only when the runtime has supported GPU libraries/devices. |
| `drop_duplicates` | `false` | Applied during dataset loading before the shared split. |
| `protocol_token` | empty | Required from the prepare workflow; expires after a bounded lifetime and is bound to the prepared dataset/manifest. |
| `confirm_protocol` | `false` | Must be explicitly set to `true` after reviewing the preview. |

Idempotency / 幂等:

- The workflow sends `idempotency_key={{#sys.workflow_run_id#}}`.
- Same idempotency key + same CSV bytes + same suite config replays the same saved suite result.
- Same idempotency key with changed CSV content or changed suite config returns `409 idempotency_conflict`.

## 4. Execution behavior / 执行行为

- Cross-validation is limited to the outer training partition only.
- All successful models share the same held-out `test_digest`, `test_size`, `random_state`, `train_rows`, and `test_rows`.
- Models execute sequentially, not in parallel.
- One model failure does not erase other model results.
- Missing optional dependencies are reported per model as `unavailable`.
- Training/runtime failures are reported per model as `failed`.
- Default execution is CPU-first; GPU is opt-in with `use_gpu=true`.

Resource and dependency notes / 资源与依赖说明:

- `xgboost` and `lightgbm` are optional runtime dependencies at execution time.
- If one of them is missing, only that model is marked unavailable; other models continue.
- Bounded defaults remain `cv_folds=5`, `n_iter=8`, `n_jobs=4`, `test_size=0.2`, `use_gpu=false`.
- API validation still enforces bounded ranges such as `cv_folds=3..10`, `n_iter=1..32`, `test_size=0.1..0.5`, `n_jobs=1..16`.

## 5. Backend endpoints / 后端端点

Legacy compatibility path (kept for existing synchronous consumers):

- `POST /v1/run-model-suite`
  - Returns `ExperimentSuiteResult`
  - Includes `experiment_id`, overall `status`, shared `config`, `dataset`, `split_provenance`, per-model `results`, `performance_ranking`, and `reproducibility_status=cv_tuned`

Additional suite endpoints:

- `POST /v1/jobs`
  - Confirmed path: receives the manifest and CSV upload, returns a job ID immediately with status `queued`.
- `GET /v1/jobs/{job_id}`
  - Returns bounded stage/progress/status data for polling.
- `GET /v1/jobs/{job_id}/result`
  - Returns the saved aggregate suite result after `succeeded` or `partial`.

- `GET /v1/model-suites/{experiment_id}`
  - Returns the persisted `ExperimentSuiteResult` for the suite ID
- `POST /v1/compare-model-suite-result`
  - Returns `SuiteComparisonResponse`
  - Includes `experiment_id`, `paper_reference_metric`, and one comparison item per successful model for each requested paper metric

## 6. Output fields to inspect / 重点输出字段

Suite result fields / 套件结果字段:

- `results[].model`
- `results[].status`
- `results[].cv_best_score`
- `results[].best_params`
- `results[].metrics.roc_auc`
- `results[].metrics.accuracy`
- `results[].metrics.balanced_accuracy`
- `results[].metrics.f1`
- `results[].metrics.recall`
- `results[].metrics.confusion_matrix`
- `results[].fit_seconds`
- `results[].error.code`
- `results[].error.message`
- `performance_ranking`

Comparison fields / 对比字段:

- `items[].model`
- `items[].name`
- `items[].paper_value`
- `items[].independent_value`
- `items[].absolute_difference`
- `items[].relative_difference`
- `items[].comparable`
- `items[].reason`
- `paper_reference_metric`

Interpretation / 解释:

- `performance_ranking` is the suite’s metric ranking over successful models.
- Comparison differences are arithmetic context, not automatic proof of strict reproduction.
- Numeric closeness to a paper metric is not strict reproduction unless dataset identity and full held-out test provenance match.
- The workflow report should keep non-strict wording such as: `numeric similarity is not strict reproduction when provenance does not match.`

## 7. Import and verification / 导入与验证

1. Import `dify/paper-comparison-prepare-workflow.yml` as a separate prepare workflow/app version.
2. Upload a paper PDF and UTF-8 CSV, optionally provide a target-column suggestion and notes, and run prepare.
3. Review `protocol_preview_json`: target, final features, types, missing/duplicate counts, class ratios,
   risk flags, paper metrics and unresolved fields. Do not copy raw rows or PDF evidence into Dify variables.
4. Import `dify/paper-comparison-multimodel-workflow.yml` as a separate confirmed-run workflow/app version.
5. Pass the token from prepare, set `confirm_protocol=true`, and keep the confirmed target/features unchanged.
   Re-upload the same `training_csv`; the local fallback does not carry raw file bytes
   in the token. Keep the existing `paper_dossier_json` input for the dossier output.
6. Confirm the Start node includes the multi-model inputs:
   `models_json`, `target_column`, `test_size`, `random_state`, `cv_folds`, `optimization_metric`, `n_iter`, `use_gpu`, `drop_duplicates`, plus the existing similarity thresholds.
7. Confirm the suite/comparison nodes use:
   - `http://repro-runner:8001/v1/run-model-suite`
   - `http://repro-runner:8001/v1/compare-model-suite-result`
8. Confirm the workflow still ends in one Output node with six string outputs:
   `dossier_json`, `validation_json`, `experiment_json`, `comparison_json`, `assessment_json`, `markdown_report`
9. Run a manual-override verification in the generated comparison request. Use this exact metric payload:

```json
[{"name":"AUC","dataset":"奉节县（全域模型）","split":"测试集","reported_value":0.850}]
```

   Then confirm the generated comparison request preserves both `dataset` and `split` in `suite_comparison_request_json` instead of dropping them.
10. Run the local smoke check from the project root with a safe placeholder path:

```powershell
.\scripts\smoke_multimodel.ps1 -CsvPath 'C:\safe-placeholder\training.csv' -BaseUrl 'http://localhost:8001'
```

11. Review only safe summaries and structured JSON outputs. Do not paste API keys, tokens, raw CSV rows, or paper/PDF text into the workflow.

## 8. Rollback / 回滚

- If import verification, runner health, or live suite behavior fails, keep using the current V3 workflow at `dify/paper-comparison-workflow.yml`.
- Roll back by switching the Dify app/version back to the saved V3 export; do not delete the separate multimodel DSL file.
- Treat the multimodel workflow as non-production until live verification confirms the new import path, shared split behavior, and comparison output.
- If the protocol token expires, rerun the prepare workflow; never extend or edit the token manually.
- If the runner restarts, keep polling the same job ID. A job may become `needs_retry`; retry only after reviewing its persisted progress/result.

## 9. Safety reminders / 安全提醒

- Do not place API keys, bearer tokens, parser tokens, or environment secrets in the DSL, docs, or smoke script.
- Inject `DIFY_PROTOCOL_SECRET` through the Dify runtime secret mechanism; do not paste its value into the exported DSL.
- Do not print raw CSV rows, raw dossier JSON, or PDF text in smoke output.
- Use only safe placeholder local paths in examples, such as `C:\safe-placeholder\training.csv`.
