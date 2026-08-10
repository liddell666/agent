# Task 6 report: published V3 paper-comparison workflow

## Outcome

- Status: DONE.
- Dify app: `论文对标复现 V3`.
- App ID: `79a680e6-1d56-43ad-b5f6-04faa3f735ad`.
- Published workflow ID: `61a225f7-e245-4cf3-9cc8-f6405e4617bc`.
- Published at: `2026-08-10 08:52:42 Asia/Shanghai` (`2026-08-10 00:52:42 UTC`).
- Public workflow URL: `http://localhost/workflow/Rpyrr9DXYM8bapKF`.
- Published graph: 37 nodes and 46 edges, comprising six Variable Aggregators,
  one Output, and zero LLM nodes. The dossier input is local-only `custom`
  `.JSON`; the training input is local-only `document` `.CSV`.
- Published only after the success, boundary, semantic-rejection, and three
  required HTTP-failure paths had reached the single six-String Output.

## Deployment and automated verification

- The initial Task 6 baseline suite passed with
  `156 passed, 1 warning in 10.68s`.
- `repro-runner` was rebuilt from this worktree without restarting Dify's
  PostgreSQL, Redis, or other stateful services.
- Runner container `7aefa1a8f690` started at
  `2026-08-10 00:44:12 Asia/Shanghai`. The before/after stateful identities and
  start times remained PostgreSQL `999d9ae786bb` at `2026-08-09 15:08:33`,
  Redis `cba18c12fc6a` at `15:08:32`, API `1a9772181634` at `15:08:32`, and
  worker `41bd85974978` at `15:08:32` (all Asia/Shanghai).
- Final container evidence: `running|healthy|paper-repro-agent-repro-runner`;
  `GET http://localhost:8001/healthz` returned `{"status":"ok"}`.
- Final full suite: `158 passed, 1 warning in 10.65s`. The warning is the
  existing Starlette/httpx deprecation warning.
- Final smoke command used
  `E:\论文复现\成果\2training_samples_15180.csv` and the committed
  `tests/fixtures/minimal-paper-dossier.json`. It passed via host transport:
  dossier valid, dataset valid, 15,180 rows, experiment
  `exp-bdcaa07c265b49f8bb16edf530434749` succeeded, one comparison item, and
  `strictly_comparable=false`.

## Live defects fixed with regression tests

- Dify rejected the original multi-Output design because Output variable names
  must be unique across the workflow. The new regression
  `test_comparison_workflow_uses_one_output_after_branch_aggregation` failed
  first; the spec was changed to six Variable Aggregators feeding one Output.
  The fix is commit `3f0bcdd` (`fix: aggregate v3 terminal workflow outputs`),
  after which the full suite passed with `157 passed, 1 warning`.
- Dify's `document` upload type rejected the JSON dossier as
  `application/json`. The new regression
  `test_comparison_workflow_accepts_json_as_a_custom_file_type` failed first;
  the dossier input was changed to Dify's `custom` `.JSON` type while the CSV
  remains a `document` `.CSV`. The fix is commit `d41dcc4`
  (`fix: accept JSON dossier uploads in Dify`), after which the full suite
  passed with `158 passed, 1 warning`.

## Successful Dify paths

### Default inputs

- Final Dify run ID: `a0af479a-bc40-41d8-a4b9-bd108279f9c8`.
- Run window: `2026-08-10 08:52:07` to `08:52:11 Asia/Shanghai`.
- Dify status: `SUCCESS`; duration `3.716s`; `27` steps; `0` model tokens.
- Inputs: `tests/fixtures/minimal-paper-dossier.json`, the user CSV above,
  target `Y_cls`, test size `0.2`, random state `42`, duplicate removal off,
  close threshold `0.05`, and partial threshold `0.10`.
- Dossier: valid; paper AUC `0.91`; source `paper_dossier`; evidence page `2`.
- Dataset: 15,180 rows, 16 features, 0 missing values, and 66 duplicate rows.
- Experiment ID: `exp-2b52531768814075bdb6b0607e79b508`.
- Independent metrics: ROC AUC `0.871388`, accuracy `0.921607`, balanced
  accuracy `0.689493`, precision `0.602151`, recall `0.405797`, and F1
  `0.484848`.
- Comparison: paper AUC `0.91` versus independent AUC `0.871388`; signed
  relative difference `-0.042431`; strict status `not_comparable` because the
  paper metric has no dataset identity; approximate status `highly_similar`.
- The report included page-2 evidence and the warning
  `近似指标一致不等于严格复现。`; it never labeled an approximate result
  `复现成功`.

### Manual override and exact boundaries

- Run `500344fe-4e02-4794-80c2-5548d42637d4` used paper value `0.917251`,
  preserved source `manual_override`, and graded an exact `0.05` difference
  `highly_similar` (experiment `exp-e5f3e76908d640168d9733c5e517be22`).
- Run `4d4809a5-aff5-4e68-97c0-1f1bd83da7f2` used paper value `0.968209`,
  preserved source `manual_override`, and graded an exact `0.10` difference
  `partially_similar` (experiment `exp-48011ddc84b04120b1e5dffe03884bf8`).
- Both used independent ROC AUC `0.871388`, remained `not_comparable`, and did
  not claim exact reproduction.

## Rejection and HTTP-failure evidence

Every case below reached the workflow's one Output node with exactly six String
values: `dossier_json`, `validation_json`, `experiment_json`,
`comparison_json`, `assessment_json`, and `markdown_report`. No branch read a
node that had not executed.

### Semantic rejection

- Damaged JSON, run `da16c43c-765a-4f3d-9a04-f6244bfb831e`: `invalid_dossier`.
- No metrics, run `aa9f9974-1595-4e0c-b3a0-ea3953252e6c`: `invalid_dossier`.
- Reversed `0.10`/`0.05` thresholds, run
  `5372adfb-536d-4b5f-8b13-2a53f74c10ac`:
  `invalid_similarity_thresholds`.
- Missing target column, run `c0683b63-b45a-413a-ab72-f1b56f37b3e0`:
  `dataset_validation_failed`.

### Temporary bad-URL tests

Only one HTTP node was changed at a time, and its documented URL was restored
and autosaved before the next test.

- Dossier outage, run `06c281ba-9e9a-4c68-924e-a86975fa34fa`:
  `dossier_service_unavailable`, followed by validation/experiment/comparison
  not-run sentinels and an `insufficient_metrics` assessment.
- Experiment outage, run `f2a370d9-0ec4-4224-9012-71c5606363a6`:
  valid dossier and dataset were retained; `experiment_service_unavailable`
  and `comparison_not_run` were returned.
- Comparison outage, run `cc2c6ba2-a446-49cb-ba96-ee437ded7349`:
  experiment `exp-82f947f224684993a1355b6cbdb560fc` completed successfully and
  the same ID was preserved in the comparison failure payload with
  `comparison_service_unavailable`.
- Before publication, all four live HTTP nodes were rechecked at their official
  URLs under `http://repro-runner:8001`: `/v1/parse-dossier`,
  `/v1/validate-dataset`, `/v1/run-experiment`, and `/v1/compare-result`.

## V2 and draft cleanup

- V2 app `27f04e8d-3ba5-4b94-825b-021e3aa3017c` remains published and was not
  edited or republished. Its public URL remains
  `http://localhost/workflow/8fF5OEnIVVCskV2s`; the latest published workflow
  row remains `91106c4c-e336-4e58-bfdf-f14409696e82`, created
  `2026-08-09 19:58:14 Asia/Shanghai`.
- Removed only invalid unpublished draft app
  `7222021e-ad4d-4585-abcd-3a8319b1b29b`. It was safely identified by its
  `未发布` state and 11 checklist groups reporting duplicate Output variable
  names from the obsolete multi-Output graph. Dify deletion is not recoverable.
- Removed the second unpublished duplicate
  `9654fc5e-da3d-4114-92c7-19b3ee066395` after the published V3 app, final
  restored success run, and public page were independently verified. The
  cleanup left exactly one app named `论文对标复现 V3`, app
  `79a680e6-1d56-43ad-b5f6-04faa3f735ad`. Dify deletion is not recoverable.

## Privacy and safety

- No raw CSV row was placed in a Dify prompt, workflow output, repository
  report, or diagnostic log.
- The workflow used deterministic code and `repro-runner`; it used no LLM or
  DeepSeek scoring.
- Approximate similarity and strict comparability remained separate conclusions.
