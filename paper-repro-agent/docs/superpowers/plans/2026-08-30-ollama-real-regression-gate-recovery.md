# Ollama Real Regression Gate Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the two evidence-backed Ollama regression workflow defects, publish only the isolated candidate through the guarded rollback boundary, and pass the fixed five-case real acceptance gate.

**Architecture:** Keep paper extraction evidence-strict and let the confirmed successful regression experiment authorize comparison when the dossier is honestly `uncertain`. Increase the Ollama completion reserve to 3,072 tokens while reducing parser input to 6,277 UTF-8 bytes, preserving the exact 16,384-token context proof. Regenerate only builder-owned regression artifacts, verify protected artifacts, then use the existing release-integrity layer for readiness, backup, publication, readback, and real-gate execution.

**Tech Stack:** Python 3.12, pytest, PyYAML, Dify workflow DSL and ORM/service APIs, Ollama `qwen3:8b`, Docker Desktop, PowerShell.

## Global Constraints

- Design authority: `docs/superpowers/specs/2026-08-30-ollama-real-regression-gate-recovery-design.md`.
- Worktree: `C:\Users\17716\Documents\arcgis\.worktrees\real-regression-acceptance\paper-repro-agent` on `codex/real-regression-acceptance`.
- Keep dossier evidence semantics unchanged; never rewrite `task_type="uncertain"` to `regression`.
- Reject explicit non-regression dossiers and every request without a valid regression experiment plus one supported, unambiguous MAE, RMSE, or R² value.
- Ollama envelope is exactly `num_ctx=16384`, `num_predict=3072`, parser payload 6,277 UTF-8 bytes, notes 2,048 UTF-8 bytes, fixed prompt 4,475 UTF-8 bytes, and chat overhead 512 tokens.
- Do not add JSON repair, continuation calls, retries, permissive parsing, or arbitrary error propagation.
- Do not persist raw papers, CSV rows, prompts, completions, backend bodies, credentials, API tokens, or protocol tokens in diagnostics or evidence.
- Keep these six protected artifacts byte-identical:
  - `dify/paper-comparison-merged-workflow.yml` — `5e4bcacebb997a8ade036fff352006a05acf3d3296f8a9c9efcaed5aef253b15`
  - `dify/paper-comparison-merged-workflow-ollama.yml` — `f2055cc7d9b06b277c05252dfb3b5489248a11370c0372d56c132f07f35516dc`
  - `dify/paper-comparison-multimodel-workflow.yml` — `9d182407f01c38e8ecf6e60115fac92b258cd441d8fbae0fec11de9c0ed4f1b1`
  - `dify/paper-comparison-multimodel-workflow-ollama.yml` — `db895087afd10762f406b12784185465e4ae93e0a63d1f2b9e3e086e0b5b4bcb`
  - `dify/paper-comparison-prepare-workflow.yml` — `54749c2b649b79e5cd0e5364541d94b0b8a207ce73014310ad320ff66523f5fb`
  - `dify/paper-comparison-prepare-workflow-ollama.yml` — `1066496786d2684b1ac8b5138a954a98937a487522d3991dcf6cac5388beae18`
- Pre-publication candidate identity is app `17fe51d4-091f-4729-87ee-3c0a2e920918`, draft `912d4e05-494c-4302-a189-788a59c6c0c2`, published workflow `29528ff6-6615-44d5-8cee-a210bb50399a`, graph `sha256:ed6b1b42b1f84c9c2e79f0c9d32db975a2283b798c845d291d457814a42630a8`, and metadata `sha256:97f0389ff657384392ff4c2ae8aa7ea94c2b9113fb8b4d6b83c0398d524b1d6a`.
- Never reset Docker, delete Docker data, clear containers, or modify another Dify app.

---

### Task 1: Accept evidence-safe uncertain dossiers in regression comparison

**Files:**
- Modify: `tests/test_dify_regression_code.py`
- Modify: `scripts/build_regression_dsl.py:598-632`
- Modify: `dify/paper-comparison-regression-workflow.yml`
- Modify: `dify/paper-comparison-regression-workflow-ollama.yml`

**Interfaces:**
- Consumes: validated dossier JSON and sanitized experiment JSON passed to embedded `build_suite_comparison_request.main(dossier_json: str, experiment_json: str) -> dict`.
- Produces: the existing `suite_comparison_request_ok`, `suite_comparison_request_json`, and `suite_comparison_request_errors` outputs with no schema change.

- [ ] **Step 1: Write the failing uncertain-dossier test**

Add this test beside `test_regression_comparison_request_accepts_natural_values_and_aliases_only`:

```python
def test_regression_comparison_request_accepts_uncertain_dossier_when_execution_is_regression() -> None:
    dossier = {
        "task_type": "uncertain",
        "metrics": [
            {
                "name": "MAE",
                "supported": True,
                "ambiguous": False,
                "reported_value": 1.25,
                "dataset": "benchmark",
                "split": "test",
            }
        ],
    }

    result = _exec_code_node("build_suite_comparison_request")(
        json.dumps(dossier),
        json.dumps({"experiment_id": "exp-uncertain-dossier", "task_type": "regression"}),
    )

    assert result["suite_comparison_request_ok"] is True
    assert json.loads(result["suite_comparison_request_json"]) == {
        "experiment_id": "exp-uncertain-dossier",
        "reported_metrics": [
            {
                "name": "mae",
                "reported_value": 1.25,
                "dataset": "benchmark",
                "split": "test",
            }
        ],
    }
    assert dossier["task_type"] == "uncertain"
```

- [ ] **Step 2: Add contradiction and missing-authority regression tests**

```python
@pytest.mark.parametrize(
    ("dossier_task_type", "suite_task_type", "experiment_id"),
    [
        ("classification", "regression", "exp-explicit-conflict"),
        ("uncertain", "classification", "exp-wrong-suite"),
        ("uncertain", "regression", "not valid"),
    ],
)
def test_regression_comparison_request_rejects_conflicts_and_invalid_execution_authority(
    dossier_task_type: str,
    suite_task_type: str,
    experiment_id: str,
) -> None:
    result = _exec_code_node("build_suite_comparison_request")(
        json.dumps(
            {
                "task_type": dossier_task_type,
                "metrics": [
                    {
                        "name": "RMSE",
                        "supported": True,
                        "ambiguous": False,
                        "reported_value": 2.0,
                    }
                ],
            }
        ),
        json.dumps({"experiment_id": experiment_id, "task_type": suite_task_type}),
    )

    assert result["suite_comparison_request_ok"] is False
    assert result["suite_comparison_request_json"] == "{}"
    assert json.loads(result["suite_comparison_request_errors"]) == [
        {
            "code": "invalid_regression_comparison_request",
            "message": "Regression experiment and supported metrics are required.",
        }
    ]
```

- [ ] **Step 3: Run the focused test and verify RED**

Run:

```powershell
python -m pytest -q tests/test_dify_regression_code.py -k "uncertain_dossier or conflicts_and_invalid_execution_authority"
```

Expected: the uncertain-dossier test fails because `suite_comparison_request_ok` is `False`; the rejection cases pass.

- [ ] **Step 4: Implement the minimum comparison condition**

In the embedded helper returned by `_comparison_request_code`, replace the dossier task-type guard with:

```python
dossier_task_type = dossier.get("task_type")
if dossier_task_type in {"regression", "uncertain"} and suite.get("task_type") == "regression":
```

Do not alter metric filtering, numeric validation, qualifier sanitization, experiment ID validation, errors, or output fields.

- [ ] **Step 5: Regenerate the two builder-owned regression artifacts**

Run:

```powershell
python scripts/build_regression_dsl.py --all-profiles
```

Expected: both regression DSLs contain the new comparison guard; no protected artifact changes.

- [ ] **Step 6: Verify GREEN and regression safety**

Run:

```powershell
python -m pytest -q tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py
git diff --check
```

Expected: all tests pass and `git diff --check` emits no output.

- [ ] **Step 7: Commit the comparison contract**

```powershell
git add -- tests/test_dify_regression_code.py scripts/build_regression_dsl.py dify/paper-comparison-regression-workflow.yml dify/paper-comparison-regression-workflow-ollama.yml
git commit -m "fix: compare uncertain regression dossiers safely"
```

### Task 2: Rebalance the closed Ollama token envelope

**Files:**
- Modify: `tests/test_ollama_context_budget.py`
- Modify: `tests/test_ollama_readiness_contract.py`
- Modify: `tests/test_ollama_readiness.py`
- Modify: `scripts/build_multimodel_dsl.py:23-29`
- Verify: `scripts/check_ollama_readiness.py`
- Modify: `dify/paper-comparison-regression-workflow-ollama.yml`

**Interfaces:**
- Consumes: shared constants imported by the regression builder and readiness helper.
- Produces: Ollama model parameters `num_ctx=16384`, `num_predict=3072`, parser default budget 6,277 bytes, production input bytes 12,800, and maximum prompt tokens 13,312.

- [ ] **Step 1: Change budget tests before constants**

Update `test_ollama_budget_constants_prove_the_fixed_context_envelope` to require:

```python
assert OLLAMA_CONTEXT_NUM_CTX == 16_384
assert OLLAMA_CONTEXT_NUM_PREDICT == 3_072
assert OLLAMA_FIXED_PROMPT_UTF8_BYTES == 4_475
assert OLLAMA_MAX_PROTOCOL_NOTES_UTF8_BYTES == 2_048
assert OLLAMA_CHAT_OVERHEAD_TOKENS == 512
assert OLLAMA_COMPLETION_RESERVE_TOKENS == 3_072
assert OLLAMA_PARSER_PAYLOAD_BYTES == 6_277
assert (
    OLLAMA_FIXED_PROMPT_UTF8_BYTES
    + OLLAMA_MAX_PROTOCOL_NOTES_UTF8_BYTES
    + OLLAMA_PARSER_PAYLOAD_BYTES
    + OLLAMA_CHAT_OVERHEAD_TOKENS
    + OLLAMA_COMPLETION_RESERVE_TOKENS
    == OLLAMA_CONTEXT_NUM_CTX
)
```

Change the embedded parser assertion to:

```python
assert "DEFAULT_CONTEXT_BUDGET_BYTES: int | None = 6277" in parser
```

- [ ] **Step 2: Change readiness contract fixtures before constants**

In `tests/test_ollama_readiness_contract.py`, require these exact values everywhere the old envelope is asserted or represented in child output:

```python
assert readiness.OLLAMA_CONTEXT_NUM_CTX == 16_384
assert readiness.OLLAMA_CONTEXT_NUM_PREDICT == 3_072
assert readiness.OLLAMA_MAX_PROMPT_TOKENS == 13_312
assert readiness.OLLAMA_PARSER_PAYLOAD_BYTES == 6_277
assert len(rendered.encode("utf-8")) == 12_800
```

Use `12_800` for `input_bytes`, `13_312` for `prompt_token_limit`, `3_072` for `num_predict`, and `13_313` as the overflow case. Require direct probe options exactly:

```python
{"num_ctx": 16_384, "num_predict": 3_072}
```

Update `tests/test_ollama_readiness.py` fixture dictionaries to derive values from `readiness.OLLAMA_PRODUCTION_INPUT_BYTES`, `readiness.OLLAMA_MAX_PROMPT_TOKENS`, and `readiness.OLLAMA_CONTEXT_NUM_PREDICT`, avoiding copied old literals.

- [ ] **Step 3: Run the focused tests and verify RED**

```powershell
python -m pytest -q tests/test_ollama_context_budget.py tests/test_ollama_readiness_contract.py tests/test_ollama_readiness.py
```

Expected: failures report the old 2,048/7,301/14,336/13,824 values.

- [ ] **Step 4: Change only the shared production constants**

In `scripts/build_multimodel_dsl.py`, set:

```python
OLLAMA_CONTEXT_NUM_CTX = 16_384
OLLAMA_CONTEXT_NUM_PREDICT = 3_072
OLLAMA_FIXED_PROMPT_UTF8_BYTES = 4_475
OLLAMA_MAX_PROTOCOL_NOTES_UTF8_BYTES = 2_048
OLLAMA_CHAT_OVERHEAD_TOKENS = 512
OLLAMA_COMPLETION_RESERVE_TOKENS = 3_072
OLLAMA_PARSER_PAYLOAD_BYTES = 6_277
```

No parser algorithm or DeepSeek profile code changes are permitted.

- [ ] **Step 5: Regenerate artifacts and verify GREEN**

```powershell
python scripts/build_regression_dsl.py --all-profiles
python -m pytest -q tests/test_ollama_context_budget.py tests/test_ollama_readiness_contract.py tests/test_ollama_readiness.py tests/test_dify_regression_dsl.py
git diff --check
```

Expected: all tests pass; only the Ollama regression artifact changes in this task because the provider-independent comparison guard was already generated in Task 1.

- [ ] **Step 6: Commit the token envelope**

```powershell
git add -- tests/test_ollama_context_budget.py tests/test_ollama_readiness_contract.py tests/test_ollama_readiness.py scripts/build_multimodel_dsl.py dify/paper-comparison-regression-workflow-ollama.yml
git commit -m "fix: expand Ollama dossier completion reserve"
```

### Task 3: Verify generated boundaries, protected artifacts, and full suite

**Files:**
- Verify: all tracked source and tests
- Create/update: privacy-safe ignored evidence under `.live-artifacts/`

**Interfaces:**
- Consumes: commits from Tasks 1 and 2.
- Produces: independently checked source/DSL/graph digests and a clean offline verification boundary.

- [ ] **Step 1: Verify protected hashes exactly**

Run this PowerShell block and require no mismatch:

```powershell
$expected = @{
  'dify/paper-comparison-merged-workflow.yml'='5e4bcacebb997a8ade036fff352006a05acf3d3296f8a9c9efcaed5aef253b15'
  'dify/paper-comparison-merged-workflow-ollama.yml'='f2055cc7d9b06b277c05252dfb3b5489248a11370c0372d56c132f07f35516dc'
  'dify/paper-comparison-multimodel-workflow.yml'='9d182407f01c38e8ecf6e60115fac92b258cd441d8fbae0fec11de9c0ed4f1b1'
  'dify/paper-comparison-multimodel-workflow-ollama.yml'='db895087afd10762f406b12784185465e4ae93e0a63d1f2b9e3e086e0b5b4bcb'
  'dify/paper-comparison-prepare-workflow.yml'='54749c2b649b79e5cd0e5364541d94b0b8a207ce73014310ad320ff66523f5fb'
  'dify/paper-comparison-prepare-workflow-ollama.yml'='1066496786d2684b1ac8b5138a954a98937a487522d3991dcf6cac5388beae18'
}
foreach ($path in $expected.Keys) {
  $actual=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
  if ($actual -ne $expected[$path]) { throw "protected digest mismatch: $path" }
}
```

- [ ] **Step 2: Prove deterministic generated artifacts**

```powershell
$generated = Join-Path ([System.IO.Path]::GetTempPath()) ('ollama-gate-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $generated | Out-Null
python scripts/build_regression_dsl.py --all-profiles --output-dir $generated
if ((Get-FileHash dify/paper-comparison-regression-workflow.yml).Hash -ne (Get-FileHash (Join-Path $generated 'paper-comparison-regression-workflow.yml')).Hash) { throw 'DeepSeek DSL drift' }
if ((Get-FileHash dify/paper-comparison-regression-workflow-ollama.yml).Hash -ne (Get-FileHash (Join-Path $generated 'paper-comparison-regression-workflow-ollama.yml')).Hash) { throw 'Ollama DSL drift' }
```

Expected: both comparisons succeed. Remove only the exact temporary directory after resolving and verifying its absolute path is under `[System.IO.Path]::GetTempPath()`.

- [ ] **Step 3: Run focused and full verification**

```powershell
python -m pytest -q tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py tests/test_ollama_context_budget.py tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py tests/test_parser_context_compaction.py
python -m pytest -q
git diff --check
git status --short
```

Expected: all tests pass, no whitespace errors, and no uncommitted tracked changes.

- [ ] **Step 4: Obtain independent code review**

Use `requesting-code-review` against commits from Tasks 1 and 2. Resolve every correctness, privacy, generated-artifact, and scope finding with a new RED→GREEN cycle before live work.

### Task 4: Run live readiness and freeze the release manifest

**Files:**
- Create: `.live-artifacts/ollama-real-regression-gate-recovery-readiness-pre-publication.json`
- Create: `.live-artifacts/ollama-real-regression-gate-recovery-release-manifest.json`
- Temporarily create and remove: two graph snapshots under a verified task-specific directory returned by `[System.IO.Path]::GetTempPath()`

**Interfaces:**
- Consumes: verified Ollama DSL and the live local Docker/Dify/Ollama boundaries.
- Produces: content-free readiness evidence and exact source/DSL/graph/candidate identities for publication.

- [ ] **Step 1: Perform read-only health and idle checks**

```powershell
docker version
docker ps --format '{{.Names}}|{{.Status}}'
docker exec repro-runner python -c "import sqlite3; c=sqlite3.connect('/data/experiments/jobs.sqlite3'); print(c.execute(\"select count(*) from jobs where status in ('queued','running')\").fetchone()[0])"
```

Expected: Docker server responds, Dify API/parser/runner/database/Redis/sandboxes are up, and runner active count is `0`.

- [ ] **Step 2: Run both production-shaped readiness probes**

```powershell
python scripts/check_ollama_readiness.py --output .live-artifacts/ollama-real-regression-gate-recovery-readiness-pre-publication.json
```

Expected: aggregate status `ready`; direct and Dify probes both record `num_ctx=16384`, `num_predict=3072`, prompt token limit `13312`, non-empty completion digests, and no content.

- [ ] **Step 3: Read and freeze exact candidate state**

Use the existing Dify ORM readback procedure to assert the Global Constraints app/draft/published/graph/metadata identities. Create a task-specific directory under `[System.IO.Path]::GetTempPath()`, verify its resolved absolute path remains beneath that root, and write the draft and published graph objects there solely for the checker. Convert the generated Ollama DSL to its canonical graph with `scripts.workflow_release_integrity.canonical_graph`, then record source, DSL, and graph SHA-256 digests plus node/edge counts in `.live-artifacts/ollama-real-regression-gate-recovery-release-manifest.json`. Persist no graph body, prompt template, or model text under `.live-artifacts`.

- [ ] **Step 4: Verify the release manifest read-only**

```powershell
python scripts/check_workflow_release.py --dsl dify/paper-comparison-regression-workflow-ollama.yml --source scripts/build_multimodel_dsl.py scripts/build_regression_dsl.py dify/code/validate_parser.py dify/paper-dossier-system-prompt.md --draft-snapshot $draftSnapshot --published-snapshot $publishedSnapshot --json
```

Expected result: the checker reports no source/DSL/draft/published drift before mutation. After capturing only the safe checker result and digests, resolve both snapshot paths and their parent again, verify all remain under the task-specific temporary directory, then remove that exact directory with `Remove-Item -LiteralPath $temporarySnapshotDirectory -Recurse`. Confirm neither snapshot exists and no prompt-bearing snapshot was copied to `.live-artifacts`.

### Task 5: Publish only the isolated Ollama candidate

**Files:**
- Create/update: `.live-artifacts/ollama-real-regression-gate-recovery-publication.json`
- Update: `.superpowers/sdd/ollama-failover-progress.md`

**Interfaces:**
- Consumes: the frozen release manifest and `publish_verified_graph`, `DifyReleaseService`, `ExpectedReleaseIdentity`, and `ReleaseMark` from `scripts/update_v31_similarity_workflow.py`.
- Produces: one rollback backup, one active published workflow, and independently verified content-free publication evidence.

- [ ] **Step 1: Recheck the frozen preconditions immediately before mutation**

Require app `17fe51d4-091f-4729-87ee-3c0a2e920918`, draft `912d4e05-494c-4302-a189-788a59c6c0c2`, published `29528ff6-6615-44d5-8cee-a210bb50399a`, graph `sha256:ed6b1b42b1f84c9c2e79f0c9d32db975a2283b798c845d291d457814a42630a8`, metadata `sha256:97f0389ff657384392ff4c2ae8aa7ea94c2b9113fb8b4d6b83c0398d524b1d6a`, ready probes, protected hashes, and runner active count `0`. Stop without mutation on any mismatch.

- [ ] **Step 2: Use the existing guarded release primitive**

Copy the current `scripts/update_v31_similarity_workflow.py`, `scripts/workflow_release_integrity.py`, and generated Ollama DSL into `docker-api-1` under `/tmp`. In a Dify app context, instantiate `WorkflowService` and `DifyReleaseService`, load only app `17fe51d4-091f-4729-87ee-3c0a2e920918`, canonicalize `document["workflow"]["graph"]`, and call:

```python
publish_verified_graph(
    release_service,
    app_model,
    session,
    candidate_graph,
    ExpectedReleaseIdentity(
        app_id="17fe51d4-091f-4729-87ee-3c0a2e920918",
        draft_digest="sha256:ed6b1b42b1f84c9c2e79f0c9d32db975a2283b798c845d291d457814a42630a8",
        draft_workflow_id="912d4e05-494c-4302-a189-788a59c6c0c2",
    ),
    ReleaseMark(
        name="Ollama regression gate recovery",
        comment="Accepts evidence-safe uncertain regression dossiers and reserves 3072 completion tokens.",
        backup_name="Backup before Ollama regression gate recovery",
        backup_comment="Restorable published graph before the guarded gate-recovery release.",
    ),
)
```

Commit the Dify database transaction only when `publish_verified_graph` reports `published`. Its built-in rollback path must run on draft write, publish, or readback failure.

- [ ] **Step 3: Independently verify publication**

Read draft and published workflows in a fresh session. Require both canonical graph digests to equal the offline candidate graph digest, metadata digest to remain `sha256:97f0389ff657384392ff4c2ae8aa7ea94c2b9113fb8b4d6b83c0398d524b1d6a`, draft ID to remain fixed, active published ID to equal the publication result, and backup ID to resolve to the exact pre-publication graph. Run `compare_release_layers` and require `drift=[]`.

- [ ] **Step 4: Persist only safe publication evidence**

Write IDs, digests, node/edge counts, status, timestamps, and rollback outcome to `.live-artifacts/ollama-real-regression-gate-recovery-publication.json`. Parse the JSON and scan it for credentials, protocol token values, prompt/completion fields, paper text, and CSV rows. Update the progress ledger with the same safe scalar facts.

### Task 6: Rerun the fixed gate and finish only on verified success

**Files:**
- Replace safely: `.live-artifacts/real-regression-acceptance.json`
- Create/update: `.live-artifacts/sdd/ollama-real-regression-gate-recovery-report.md`
- Update: `.superpowers/sdd/ollama-failover-progress.md`

**Interfaces:**
- Consumes: exact fixed registry `real_world/regression_cases.yml` and the candidate App API token held only in process memory.
- Produces: privacy-safe five-case evidence and a final pass/fail decision.

- [ ] **Step 1: Archive the failed evidence without deleting it**

Run this PowerShell block so the archived path is explicit and verified before moving:

```powershell
$artifactRoot = (Resolve-Path -LiteralPath '.live-artifacts').Path
$source = (Resolve-Path -LiteralPath '.live-artifacts/real-regression-acceptance.json').Path
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$destination = Join-Path $artifactRoot ("real-regression-acceptance.failed-gate-recovery-input-$stamp.json")
$rootPrefix = $artifactRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $source.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'archive source escaped artifact root' }
if (-not $destination.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'archive destination escaped artifact root' }
Move-Item -LiteralPath $source -Destination $destination
```

- [ ] **Step 2: Recheck readiness, candidate identity, and runner idle**

Run the same read-only checks from Task 4 and require the newly published graph identity. Do not start the gate if any check fails.

- [ ] **Step 3: Run the exact five cases**

Resolve the candidate API token by exact app UUID inside Dify, retain it only in a PowerShell variable and child-process environment, never print it, then run:

```powershell
python scripts/run_real_regression_acceptance.py run --registry real_world/regression_cases.yml --output .live-artifacts/real-regression-acceptance.json --resume
python scripts/run_real_regression_acceptance.py evaluate --input .live-artifacts/real-regression-acceptance.json
```

Expected: total `5`, completed at least `4`, `false_strict=0`, `unknown=0`, and no gate errors.

- [ ] **Step 4: Diagnose any residual failure safely**

For a non-passing case, inspect only workflow/node status, finish reason, token counts, output key/type/length shapes, safe error codes, booleans, result counts/statuses, and digest presence. Never print or persist raw node inputs, prompts, completions, paper text, CSV content, protocol tokens, API keys, or arbitrary backend messages. Keep the gate fail-closed.

- [ ] **Step 5: Run final verification**

```powershell
python -m pytest -q
python scripts/check_ollama_readiness.py --output .live-artifacts/ollama-real-regression-gate-recovery-readiness-post-gate.json
git diff --check
git status --short
```

Also require protected hashes unchanged, candidate draft/published graph and metadata readback exact, runner active count `0`, both evidence JSON files parse, and privacy scans are clean.

- [ ] **Step 6: Review and document the outcome**

Use `requesting-code-review` for the final code/release evidence. Record exact commits, graph/DSL/source digests, published and backup IDs, readiness digests, case counts, privacy result, drift result, and runner-idle result in the report and progress ledger. Claim completion only if every release gate passes; otherwise report the remaining evidence-backed blocker without weakening classification.
