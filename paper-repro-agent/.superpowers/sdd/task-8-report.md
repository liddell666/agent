# Task 8 report: final Dify review fixes and replacement publication

## Post-review duplicate-metric evidence fix (2026-08-10)

Final whole-branch review found that the report formatter could associate a
comparison item with the first dossier metric of the same normalized name,
even when that metric was ambiguous and a later manual override was the metric
actually sent to comparison. The formatter now derives the same ordered set of
supported, non-ambiguous, valued metrics as `build_comparison_request` and
associates response items by that request order. Regression coverage includes
selecting the second duplicate and retaining the order of multiple selected
duplicates; canonical and embedded-DSL focused tests pass (`34 passed`).

The corrected embedded formatter was synchronized through the official Dify
editor and republished at `2026-08-10 16:07:57 Asia/Shanghai` without changing
the app or public URL. The new published workflow is
`7561584f-491b-4b47-83d0-be7b75c79e12`; a read-only database check confirms its
formatter is 7,674 characters and contains both ordered-selection helpers.
To prevent users selecting the earlier public rollback app by mistake, that
app was renamed to `论文对标复现 V3（旧版·仅回滚）`; its URL and rollback state
remain available.

## Outcome

- Status: DONE.
- Canonical source: `dify/paper-comparison-workflow.yml` at commit `82d4b9f`.
- Replacement Dify app: `论文对标复现 V3`.
- Replacement app ID: `471a5361-cbd7-404b-8db7-cc04a33d7072`.
- Final public URL: `http://localhost/workflow/vCjB0Nju4oOLTduz`.
- Published workflow ID: `bf163667-7e38-408b-a4a8-53131b081e47`.
- Final publication observed through the official UI and confirmed in Dify at
  `2026-08-10 15:22:19 Asia/Shanghai`.
- Final restored draft run ID: `c51bb081-c417-4171-9721-9ecb23f80253`,
  `Success`, `3.363s`, 27 traced steps, zero model tokens.
- Final draft experiment ID: `exp-09fa0c6862d5417a8bb3ced8de09e1a0`.

Dify's official import flow created a new app instead of updating the existing
app in place. The replacement was not published until the full draft matrix,
URL restoration, export parity, and final success had passed. The previous V3
published app and V2 were not deleted or republished.

## Live graph and export parity

The canonical DSL was imported through Workspace -> Create -> Import DSL.
The first official export after import was
`C:\Users\17716\Downloads\论文对标复现 V3 (1).yml` at
`2026-08-10 14:38:28`; a recursive YAML comparison reported zero differences
from the committed DSL.

After the outage matrix, every URL was restored and autosaved. The final
post-publication official export was
`C:\Users\17716\Downloads\论文对标复现 V3 (4).yml` at
`2026-08-10 15:25:08`. It is executable-semantically identical to the
committed DSL. The eight raw differences are only Dify UI serialization:
four HTTP node heights, one top-level node `selected` marker, and three
viewport values.
After those fields are removed, the complete parsed YAML values compare equal.

The saved and published executable contract therefore contains:

- required local-only Custom `.JSON` `paper_dossier_json` and required local
  `.CSV` `training_csv` Start files;
- optional Paragraph `metric_overrides_json` with default `[]`;
- all four official `http://repro-runner:8001/v1/...` URLs;
- two retries at 1,000 ms on each HTTP node and finite 60/120/600/60-second
  read/write timeouts with 10-second connections;
- `run_experiment.idempotency_key={{#sys.workflow_run_id#}}`;
- the 7,085-character, 162-line formatter using actual newline characters;
- deterministic code only, zero LLM nodes, six Variable Aggregators, and one
  six-String Output.

## Draft acceptance matrix

All runs used `tests/fixtures/minimal-paper-dossier.json` and
`E:\论文复现\成果\2training_samples_15180.csv` unless the case describes an
input override. Dify's official Test Run history exposes the timestamps below,
but Dify 1.16 does not display a workflow-run UUID in its editor UI. The exact
experiment IDs and error codes are recorded instead; no hidden page API was
used.

| Time (Asia/Shanghai) | Case | Evidence |
| --- | --- | --- |
| 14:41:18 | Default success | `exp-7d57873cd7014a258f0badc9943dc994`; paper AUC `0.91`; independent AUC `0.871388`; strict `not_comparable`; approximate `highly_similar`; full multiline report, source `paper_dossier`, evidence `p.2`, and warning present. |
| 14:43:07 | Exact 0.05 | Override paper value `0.917251`; `exp-e5cea75e88d24167a4ffa65535e395fc`; relative difference `-0.05`, grade `highly_similar`, source `manual_override`. |
| 14:43:27 | Exact 0.10 | Override paper value `0.968209`; `exp-3dd44febad144bc3ae42f7341874bad3`; relative difference `-0.1`, grade `partially_similar`, source `manual_override`. |
| 14:44:04 | Semantic rejection | Reversed thresholds `0.10`/`0.05`; `invalid_similarity_thresholds`; downstream outputs were safe not-run sentinels. |
| 14:45:19 | Dossier HTTP outage | Temporary bad dossier URL; `dossier_service_unavailable`; retry card showed `2/2`; safe not-run sentinels followed. URL restored. |
| 14:47:43 | Experiment HTTP outage | Valid dossier and dataset retained; `experiment_service_unavailable`; `comparison_not_run`. URL restored. |
| 14:49:14 | Comparison HTTP outage | Experiment `exp-81f5c08e47d149c5b1883202d889a0ac` succeeded and the identical ID was retained with `comparison_service_unavailable`. URL restored. |
| 14:50:18 | Restored draft success | `exp-5aeb5c5bf40a458e8e78bf70874ba5ba`; all official URLs, complete outputs, and real Markdown newlines. |
| 15:12:10 | Repeated comparison HTTP outage | Run `3ce20936-c7e3-4b3f-83b7-ce85fce039f7`; experiment `exp-b296a825c4a94f3d8a62df8677157c75` succeeded and the identical ID was retained with `comparison_service_unavailable`. URL restored and autosaved. |
| 15:20:53 | Final restored draft success | Run `c51bb081-c417-4171-9721-9ecb23f80253`; experiment `exp-09fa0c6862d5417a8bb3ced8de09e1a0`; 27 steps, zero tokens, complete outputs, and rendered Markdown structure. |

An extra 14:46:47 run was an input-selection mistake and reached the semantic
threshold rejection; it is not counted as experiment-outage evidence.

## Published-path verification

The replacement was published through the official Publish Update action. A
public web run immediately before the final republish used the same real files
and defaults, and the final public page was loaded successfully after the
`15:22:19` publication:

- Dify log row: `2026-08-10 15:20:40`, `Success`, `3.116s`, web-app trigger.
- Experiment: `exp-5b4dd3a8f5874e4e975840ba5bba043f`, `succeeded`.
- Dataset: 15,180 rows/effective rows, 16 features, target `Y_cls`, zero
  missing values, 66 duplicate rows, train/test rows 12,144/3,036.
- Metrics: AUC `0.871388`, accuracy `0.921607`, balanced accuracy `0.689493`,
  precision `0.602151`, recall `0.405797`, F1 `0.484848`.
- Comparison: paper AUC `0.91`, absolute difference `0.038612`, signed relative
  difference `-0.042431`, strict `not_comparable`, approximate
  `highly_similar`.
- Rendering: actual H1/H2/H3 structure and line breaks, complete dataset/split
  and per-metric fields, source `paper_dossier`, evidence `p.2`, and warning
  `近似指标一致不等于严格复现。`; no approximate reproduction-success claim.

The public web run ID is `33809073-1370-4614-9dae-263f9c25c3e4`; its browser
end-user session ID `465d1db2-aa05-4cc4-8057-fadef58ee541` is kept distinct.
Read-only local Dify database queries confirmed that run ID, the end-user
trigger role, and final published workflow
`bf163667-7e38-408b-a4a8-53131b081e47`.

## Retry idempotency

The final exported graph binds the retrying experiment request to
`sys.workflow_run_id`, so Dify reuses one stable key within a workflow run.
Live backend confirmation sent two identical multipart requests with key
`task8-live-final-20260810-1522`; both returned
`exp-d338a4375dee4024866b4fbd2b2c5b9a`, status `succeeded`, and AUC
`0.871388`. The focused regression also verifies that identical same-key calls
train and save exactly once.

## Preservation and availability

- V2 app `27f04e8d-3ba5-4b94-825b-021e3aa3017c` was not semantically edited or
  republished: published workflow `91106c4c-e336-4e58-bfdf-f14409696e82`
  remained in place. Its draft shows only UI/layout metadata autosave
  differences from inspection. `http://localhost/workflow/8fF5OEnIVVCskV2s`
  returns HTTP 200.
- Previous V3 app `79a680e6-1d56-43ad-b5f6-04faa3f735ad`, now explicitly named
  `论文对标复现 V3（旧版·仅回滚）`, remains published at
  `http://localhost/workflow/Rpyrr9DXYM8bapKF`, which returns HTTP 200. It was
  not republished or deleted. Before the switch to import, its unpublished
  draft received only two local Start max-length edits; its working published
  version and public URL were unaffected.
- Replacement `http://localhost/workflow/vCjB0Nju4oOLTduz` returns HTTP 200.
- Only runner container `d3495f0177a6` was rebuilt; final state is
  `running|healthy|repro-runner`. Dify stateful services were not restarted.

## Final verification

- DSL contract tests: `9 passed in 1.20s`.
- Same-key execution/save regression: `1 passed, 1 warning in 4.90s`.
- Final full suite: `182 passed, 1 warning in 16.79s`.
- One immediately preceding full-suite run hit the already documented
  Windows-only transient `PermissionError` while atomically renaming a fresh
  experiment directory (`181 passed, 1 failed`). The exact failing test then
  passed alone (`1 passed in 6.30s`) and the complete suite passed without a
  code or environment change, confirming the failure was not reproducible.
- Comparison smoke with the real CSV: PASS over host transport; experiment
  `exp-cd6fd2ee89bb4c5780e4283d5f7174f2`, one comparison item, strictly
  comparable false.
- PowerShell AST parse for `scripts/smoke_comparison.ps1`: PASS.
- Runner health endpoint: `{"status":"ok"}`; container healthy.
- Replacement, previous V3, and V2 public URLs: HTTP 200.
- `git diff --check`: clean after report updates.
- No raw CSV rows, secrets, LLM scoring, or hidden Dify page APIs were used.
