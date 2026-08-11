# Multi-model workflow verification record

Date: Tuesday, August 11, 2026
Branch: `codex/multi-model-cv`
Worktree: `C:\Users\17716\Documents\arcgis\.worktrees\multi-model-cv`
Overall status: PASS WITH LIMITATIONS

## Scope note

This record covers the Task 9 verification brief only. I did not modify the legacy published workflow YAML. The worktree already contained unrelated unstaged scratch-report edits under `.superpowers/sdd/task-{3,4,5,7,8}-report.md`; they were left untouched.

## Verification summary

- PASS - required focused pytest gate now passes from a plain shell with repository-managed pytest configuration and `PYTHONPATH` unset.
- PASS - required full `pytest -q` gate now passes from a plain shell with repository-managed pytest configuration and `PYTHONPATH` unset.
- PASS - multi-model DSL regeneration is deterministic and clean.
- PASS - no secret-like matches were found in `dify/paper-comparison-multimodel-workflow.yml`.
- LIMITATION - `docker compose build repro-runner` remains blocked locally because `.env` is absent.
- PASS - direct `Dockerfile.repro` image build succeeded; the built image runs as non-root `app` and served `{"status":"ok"}` on `/healthz`.
- PASS - two-model live API smoke test succeeded with a safe synthetic local fixture.
- LIMITATION - seven-model real-data experiment remains unavailable because the scoped workspace did not contain the original training CSV required by the brief.

## Detailed results

### 1) Focused runner and Dify tests

Status: PASS

Command:

```powershell
pytest -q tests/repro_runner/test_suite_schemas.py tests/repro_runner/test_split.py tests/repro_runner/test_metrics.py tests/repro_runner/test_model_registry.py tests/repro_runner/test_suite_engine.py tests/repro_runner/test_suite_compare.py tests/repro_runner/test_suite_api.py tests/test_dify_multimodel_code.py tests/test_dify_multimodel_dsl.py tests/test_dify_comparison_dsl.py
```

Environment:

- `PYTHONPATH` unset (`Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue`)

Observed result:

- Exit code `0`
- `79 passed, 1 warning in 13.33s`

Evidence note:

- the follow-up brief captured the RED-state plain-shell collection failure caused by pytest `pythonpath` including only `src`
- `pyproject.toml` now includes both `src` and the repository root, preserving `src` imports while allowing the top-level `dify` and `scripts` packages used by the Dify tests to resolve without shell overrides

### 2) Full test suite

Status: PASS

Command:

```powershell
pytest -q
```

Environment:

- `PYTHONPATH` unset (`Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue`)

Observed result:

- Exit code `0`
- `256 passed, 1 warning in 23.91s`

### 3) Generated files, diff cleanliness, and secret scan

#### 3a) Multi-model DSL regeneration

Status: PASS

Command:

```powershell
python scripts/build_multimodel_dsl.py
```

Observed result:

- Exit code `0`
- no output
- subsequent `git status --short` showed no new generated-file diff beyond the pre-existing scratch-report edits outside Task 9

#### 3b) Diff/whitespace check

Status: PASS

Command:

```powershell
git diff --check
```

Observed result:

- Exit code `0`
- no whitespace or patch-format errors reported
- only CRLF warnings referencing the pre-existing `.superpowers/sdd/task-{3,4,5,7,8}-report.md` files

#### 3c) Secret-pattern scan

Status: PASS

Command:

```powershell
rg -n "sk-[A-Za-z0-9]+|api[_-]?key\s*[:=]" dify/paper-comparison-multimodel-workflow.yml
```

Observed result:

- Exit code `1`
- no matches

### 4) Docker build and runner health

#### 4a) Compose-based build

Status: LIMITATION

Command:

```powershell
docker compose build repro-runner
```

Observed result:

- Exit code `1`
- compose refused to start because `paper-repro-agent\.env` was missing

#### 4b) Direct image build fallback

Status: PASS

Commands:

```powershell
docker build -f Dockerfile.repro -t task9-repro-runner .
docker image inspect task9-repro-runner --format '{{.Config.User}}'
docker run -d --rm --name task9-repro-runner -p 127.0.0.1:18001:8001 task9-repro-runner
Invoke-RestMethod http://localhost:18001/healthz
docker rm -f task9-repro-runner
```

Observed result:

- image build succeeded
- image user was `app`
- health response was `{"status":"ok"}`
- container was removed after verification

### 5) Two-model live API smoke test

Status: PASS

Fixture:

- safe synthetic CSV equivalent to the `tests/repro_runner/test_suite_api.py::_csv()` pattern
- no user dataset or raw project data used

Request summary:

- `POST /v1/run-model-suite`
- `models_json=["logistic_regression","random_forest"]`
- `cv_folds=5`
- `n_iter=1`
- `optimization_metric=roc_auc`
- `random_state=42`
- `test_size=0.2`

Observed result:

- exit code `0`
- suite status `succeeded`
- experiment id `exp-89f24834def441068243b02f6f1f332b`
- both models succeeded
- shared `test_digest`: `sha256:e412d361104ebeac01fc29ea8c7318307cca06802350ef0c41509a7e689c7910`
- ranking: `["random_forest","logistic_regression"]`

Returned metrics:

- logistic_regression: `roc_auc=1.0`, `accuracy=0.461538`, `f1=0.0`, `recall=0.0`
- random_forest: `roc_auc=1.0`, `accuracy=1.0`, `f1=1.0`, `recall=1.0`

### 6) Seven-model real-data experiment

Status: LIMITATION

Reason:

- the brief requires the original paper PDF/dossier plus the same training CSV
- scoped workspace search found `tests/fixtures/minimal-paper.pdf` and `tests/fixtures/minimal-paper-dossier.json`
- no original real training CSV was present in the scoped workspace
- I did not invent substitute "real-data" inputs

Search command:

```powershell
rg --files -g "*.csv" -g "*.pdf" -g "*dossier*.json"
```

Observed result:

- `tests\fixtures\minimal-paper.pdf`
- `tests\fixtures\minimal-paper-dossier.json`
- `dify\paper-dossier-schema.json`

### 7) Failure matrix and rollback checks

#### 7a) Malformed `models_json`

Status: PASS

Observed live API result:

- `POST /v1/run-model-suite`
- `models_json=not-json`
- HTTP `422`
- error code `invalid_request`

#### 7b) Invalid CV folds

Status: PASS

Observed live API result:

- `POST /v1/run-model-suite`
- `models_json=["logistic_regression"]`, `cv_folds=2`
- HTTP `422`
- error code `invalid_request`

Additional targeted test evidence:

```powershell
$env:PYTHONPATH='.;src'; pytest -q tests/repro_runner/test_suite_engine.py::test_run_model_suite_rejects_invalid_cv_folds_before_search
```

- covered in the targeted batch below; passed

#### 7c) Missing optional dependency behavior

Status: PASS

This was verified by targeted tests rather than destructive environment mutation.

Command:

```powershell
$env:PYTHONPATH='.;src'; pytest -q tests/repro_runner/test_suite_engine.py::test_run_model_suite_marks_missing_dependency_unavailable_and_continues tests/repro_runner/test_model_registry.py::test_xgboost_missing_dependency_is_lazy_and_has_stable_message tests/repro_runner/test_model_registry.py::test_lightgbm_missing_dependency_is_lazy_and_has_stable_message
```

Observed result:

- included in a 6-test targeted batch
- exit code `0`
- dependency absence is reported as per-model `unavailable`
- other models continue

#### 7d) Invalid CSV

Status: PASS

Observed live API result:

- malformed CSV uploaded to `/v1/run-model-suite`
- HTTP `422`
- error code `invalid_csv`

#### 7e) Timeout / stop handling and failure branches

Status: PASS

Evidence:

- `dify/paper-comparison-multimodel-workflow.yml` contains bounded timeout blocks for the multimodel HTTP request nodes
- targeted tests passed for bounded retry/fail-branch structure and safe terminal outputs

Targeted commands/results:

```powershell
$env:PYTHONPATH='.;src'; pytest -q tests/test_dify_comparison_dsl.py::test_dsl_has_only_the_four_official_bounded_retry_http_nodes tests/test_dify_comparison_dsl.py::test_dsl_has_no_llm_six_aggregators_and_one_output
$env:PYTHONPATH='.;src'; pytest -q tests/test_dify_multimodel_dsl.py::test_multimodel_dsl_has_one_end_six_string_outputs_and_no_secrets tests/test_dify_multimodel_dsl.py::test_multimodel_embedded_python_compiles_and_failure_branches_avoid_http_outputs tests/test_dify_multimodel_code.py::test_suite_request_returns_stable_error_without_echoing_invalid_input
```

Observed results:

- first targeted batch was part of a 6-test run: `6 passed in 13.97s`
- second targeted batch: `3 passed in 0.68s`

#### 7f) Legacy published V3 workflow preservation / rollback target

Status: PASS

Evidence:

- `dify/paper-comparison-workflow.yml` remained unchanged during DSL regeneration
- operator documentation still names the V3 workflow as the rollback target
- legacy single-model endpoint remained usable

Observed live API result:

- `POST /v1/run-experiment`
- HTTP `200`
- status `succeeded`
- experiment id `exp-49e1c4cd0332499cb54af36859c6122e`

## Inspected files

- `requirements-repro.lock` - includes `xgboost==3.4.0` and `lightgbm==4.7.0`
- `dify/paper-comparison-multimodel-workflow.yml` - regenerated cleanly; no secret-like matches
- `compose.yaml` - compose path requires `.env`
- `Dockerfile.repro` - image defaults include non-root `app` user and `uvicorn` command

## Conclusion

Final verification outcome: PASS WITH LIMITATIONS

Why PASS:

1. the exact focused pytest command from the brief now passes in a plain shell with `PYTHONPATH` unset
2. the exact full `pytest -q` command from the brief now passes in a plain shell with `PYTHONPATH` unset
3. the change is configuration-only and preserves existing `src` import behavior while making the repository-managed pytest gate self-contained

Remaining explicit limitations:

- `docker compose build repro-runner` still requires the local `.env` file, which was not present in the scoped workspace
- the seven-model real-data run remains unavailable because the scoped workspace did not include the original training CSV required by the brief

Why this is still useful:

- the direct Docker image build and `/healthz` probe succeeded
- the two-model live API smoke test succeeded
- the legacy V3 single-model route still works
- the seven-model real-data run was correctly left unavailable instead of invented
