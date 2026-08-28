# Regression Comparison Request Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewire the isolated regression candidate's comparison request to validated dossier evidence, publish a digest-verified candidate version, and pass the five-case real regression gate.

**Architecture:** The regression DSL builder owns the trust-boundary wiring and validates it structurally. The pure comparison-request code remains strict and unchanged. The existing metadata-aware Dify release adapter publishes only the isolated candidate after exact preflight identity checks, and the existing privacy-safe acceptance runner supplies the live gate.

**Tech Stack:** Python 3.12, pytest, PyYAML, Dify 1.16 workflow graph/API, SQLAlchemy, Docker Desktop, PowerShell.

## Global Constraints

- Work only in `C:\Users\17716\Documents\arcgis\.worktrees\real-regression-acceptance\paper-repro-agent` on branch `codex/real-regression-acceptance`.
- Candidate App UUID is `17fe51d4-091f-4729-87ee-3c0a2e920918`; never write to a different Dify App.
- Pre-change draft UUID is `912d4e05-494c-4302-a189-788a59c6c0c2`, published UUID is `8cc0de24-ed72-4026-9850-a9a4cccc0a23`, graph digest is `sha256:d63e730b785fd20b31372fb853129d891b80b169b62eac4e78b01e222db4236a`, and metadata digest is `sha256:97f0389ff657384392ff4c2ae8aa7ea94c2b9113fb8b4d6b83c0398d524b1d6a`.
- Do not modify, regenerate, stage, or publish the six user-owned merged, multimodel, and prepare DSL files listed in `tests/test_dify_regression_dsl.py::PROTECTED_DSLS`.
- Do not weaken canonical `task_type == "regression"`, citation, metric, privacy, or strict-comparability validation.
- Store live snapshots, API keys, downloaded sources, workflow payloads, and aggregate evidence only in ignored paths; never print or commit secrets, PDF text, or CSV rows.
- Stop and preserve safe evidence if a generalized defect remains after the corrected candidate is published.

---

### Task 1: Specify the validated-dossier graph boundary

**Files:**
- Modify: `tests/test_dify_regression_dsl.py`
- Test: `tests/test_dify_regression_dsl.py`

**Interfaces:**
- Consumes: `_builder().build_regression_dsl(profile)` and `_node_map(document)`.
- Produces: an executable contract requiring `build_suite_comparison_request.dossier_json` to select `validate_paper_dossier.validated_json` in both profiles.

- [ ] **Step 1: Write the failing profile wiring test**

```python
@pytest.mark.parametrize("profile", ["deepseek", "ollama"])
def test_regression_comparison_request_uses_validated_dossier(profile: str) -> None:
    nodes = _node_map(_builder().build_regression_dsl(profile))
    validator = nodes["validate_paper_dossier"]
    request = nodes["build_suite_comparison_request"]
    dossier = next(
        item for item in request["data"]["variables"]
        if item["variable"] == "dossier_json"
    )

    assert dossier["value_selector"] == [validator["id"], "validated_json"]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest -q tests/test_dify_regression_dsl.py::test_regression_comparison_request_uses_validated_dossier
```

Expected: two failures showing the current selector points at the raw dossier node.

- [ ] **Step 3: Extend the existing mutation test before implementation**

Add `"raw_dossier_wiring"` to the mutation parameter list and this branch:

```python
elif mutation == "raw_dossier_wiring":
    request = nodes["build_suite_comparison_request"]
    dossier = next(
        item for item in request["data"]["variables"]
        if item["variable"] == "dossier_json"
    )
    dossier["value_selector"] = [nodes["extract_paper_dossier"]["id"], "dossier_json"]
```

Keep the assertion:

```python
with pytest.raises(ValueError, match="regression validator/comparison chain"):
    builder._validate_regression_graph(document)
```

- [ ] **Step 4: Run the mutation test and verify RED**

Run:

```powershell
python -m pytest -q tests/test_dify_regression_dsl.py::test_regression_graph_validation_rejects_broken_validator_comparison_chain
```

Expected: only `raw_dossier_wiring` fails because the graph validator does not yet inspect selectors.

- [ ] **Step 5: Commit the red tests**

```powershell
git add -- tests/test_dify_regression_dsl.py
git commit -m "test: require validated regression dossier wiring"
```

---

### Task 2: Rewire and structurally validate the regression graph

**Files:**
- Modify: `scripts/build_regression_dsl.py`
- Test: `tests/test_dify_regression_dsl.py`

**Interfaces:**
- Consumes: title-indexed node mappings from `_by_title(document)`.
- Produces: `_wire_validated_comparison_dossier(nodes: dict[str, dict]) -> None` and fail-closed selector validation in `_validate_regression_graph`.

- [ ] **Step 1: Add the minimal wiring helper**

```python
def _wire_validated_comparison_dossier(nodes: dict[str, dict]) -> None:
    validator = nodes["validate_paper_dossier"]
    request = nodes["build_suite_comparison_request"]
    dossier = next(
        item
        for item in request["data"]["variables"]
        if item.get("variable") == "dossier_json"
    )
    dossier["value_selector"] = [validator["id"], "validated_json"]
```

Call it at the end of `_replace_regression_code_nodes(document)` after the request node code is replaced:

```python
_wire_validated_comparison_dossier(nodes)
```

- [ ] **Step 2: Add structural validation**

In `_validate_regression_graph`, after `nodes = _by_title(document)`, add:

```python
try:
    dossier = next(
        item
        for item in nodes["build_suite_comparison_request"]["data"]["variables"]
        if item.get("variable") == "dossier_json"
    )
except (KeyError, StopIteration):
    raise ValueError("regression validator/comparison chain is invalid") from None
if dossier.get("value_selector") != [
    nodes["validate_paper_dossier"]["id"],
    "validated_json",
]:
    raise ValueError("regression validator/comparison chain is invalid")
```

- [ ] **Step 3: Run focused tests and verify GREEN**

```powershell
python -m pytest -q tests/test_dify_regression_dsl.py::test_regression_comparison_request_uses_validated_dossier tests/test_dify_regression_dsl.py::test_regression_graph_validation_rejects_broken_validator_comparison_chain tests/test_dify_regression_code.py::test_regression_validator_feeds_supported_natural_metrics_to_comparison_request
```

Expected: all parameterized cases pass.

- [ ] **Step 4: Run all regression DSL and code tests**

```powershell
python -m pytest -q tests/test_dify_regression_dsl.py tests/test_dify_regression_code.py
```

Expected: all tests pass with no new warning.

- [ ] **Step 5: Commit the implementation**

```powershell
git add -- scripts/build_regression_dsl.py
git commit -m "fix: compare validated regression dossier"
```

---

### Task 3: Regenerate only canonical regression artifacts

**Files:**
- Modify: `dify/paper-comparison-regression-workflow.yml`
- Modify: `dify/paper-comparison-regression-workflow-ollama.yml`
- Test: `tests/test_dify_regression_dsl.py`

**Interfaces:**
- Consumes: deterministic `write_all_profile_dsls` output.
- Produces: two byte-stable regression DSLs whose request node selects validated evidence.

- [ ] **Step 1: Snapshot protected DSL hashes**

```powershell
$protected = @(
  'dify/paper-comparison-merged-workflow.yml',
  'dify/paper-comparison-merged-workflow-ollama.yml',
  'dify/paper-comparison-multimodel-workflow.yml',
  'dify/paper-comparison-multimodel-workflow-ollama.yml',
  'dify/paper-comparison-prepare-workflow.yml',
  'dify/paper-comparison-prepare-workflow-ollama.yml'
)
$before = @{}; foreach ($path in $protected) { $before[$path] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash }
```

- [ ] **Step 2: Regenerate both regression profiles**

```powershell
python scripts/build_regression_dsl.py --all-profiles --output-dir dify
```

- [ ] **Step 3: Verify protected files and deterministic output**

```powershell
foreach ($path in $protected) {
  if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $before[$path]) { throw "protected DSL changed: $path" }
}
python -m pytest -q tests/test_dify_regression_dsl.py::test_regression_cli_writes_only_two_profile_artifacts_and_matches_builder tests/test_dify_regression_dsl.py::test_committed_regression_dsls_match_deterministic_builder_output
```

Expected: protected hashes match and both deterministic tests pass.

- [ ] **Step 4: Review the narrow diff**

```powershell
git diff --stat
git diff -- dify/paper-comparison-regression-workflow.yml dify/paper-comparison-regression-workflow-ollama.yml
git diff --check
```

Expected: only the `dossier_json` selectors and serialization consequences owned by the deterministic builder differ.

- [ ] **Step 5: Commit generated artifacts**

```powershell
git add -- dify/paper-comparison-regression-workflow.yml dify/paper-comparison-regression-workflow-ollama.yml
git commit -m "build: regenerate regression workflow candidates"
```

---

### Task 4: Complete offline release verification

**Files:**
- Read: `dify/paper-comparison-regression-workflow.yml`
- Create ignored: `.live-artifacts/regression-wiring-release-manifest.json`

**Interfaces:**
- Consumes: committed regression builder and DSLs.
- Produces: clean focused/full tests and an immutable candidate graph/source/DSL identity for publication.

- [ ] **Step 1: Run focused acceptance and regression suites**

```powershell
python -m pytest -q tests/real_regression_acceptance tests/test_end_to_end_regression.py tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the complete suite**

```powershell
python -m pytest -q
```

Expected: all tests pass with only the already-recorded deprecation warning and intentional skip.

- [ ] **Step 3: Run release integrity and repository checks**

```powershell
python scripts/check_workflow_release.py --dsl dify/paper-comparison-regression-workflow.yml --source scripts/build_regression_dsl.py scripts/build_multimodel_dsl.py dify/paper-comparison-workflow.yml dify/paper-dossier-workflow.yml dify/code/validate_evidence.py dify/code/comparison_workflow.py dify/code/experiment_workflow.py dify/code/validate_parser.py --json
git diff --check
git status --short
```

Expected: `{"drift":[]}`, clean whitespace, and no uncommitted task-owned file.

- [ ] **Step 4: Build the ignored release manifest**

Use `workflow_release_integrity.build_release_manifest`, `graph_digest`, and `source_digest` to write `.live-artifacts/regression-wiring-release-manifest.json` with the current commit, clean-worktree flag, exact source/DSL/graph hashes, workflow kind/version, candidate App ID, and the fixed preflight draft/published IDs. Validate it with `validate_release_manifest` before publication and print only IDs and digests.

Expected: the manifest graph digest differs from `sha256:d63e730b785fd20b31372fb853129d891b80b169b62eac4e78b01e222db4236a`, while the metadata digest remains fixed.

---

### Task 5: Publish and verify the corrected isolated candidate

**Files:**
- Read: `.live-artifacts/regression-wiring-release-manifest.json`
- Create ignored: `.live-artifacts/regression-wiring-publication.json`
- Create ignored: `.live-artifacts/regression-candidate-draft-snapshot.json`
- Create ignored: `.live-artifacts/regression-candidate-published-snapshot.json`

**Interfaces:**
- Consumes: `DifyReleaseService`, `inspect_release_state`, `publish_verified_graph`, `ExpectedReleaseIdentity`, `ReleaseMark`, `WorkflowReleaseMetadata`, and the validated manifest.
- Produces: a new backup UUID, new draft/published workflow UUIDs, exact graph/metadata digests, and fresh checker snapshots.

- [ ] **Step 1: Require healthy services and an idle runner**

Check Docker Engine, Dify console API, `paper-parser`, `repro-runner /healthz`, PostgreSQL, Redis, sandbox, and plugin daemon. Query the runner SQLite store read-only and require no `queued` or `running` jobs.

- [ ] **Step 2: Perform immutable preflight**

Inside the Dify API container, call `inspect_release_state` and require the exact App, draft, published, graph, and metadata identities listed in Global Constraints. Abort before backup on any mismatch.

- [ ] **Step 3: Publish through the metadata-aware release boundary**

Load `workflow.graph` from the deterministic DeepSeek DSL and current metadata from the inspected candidate. Call:

```python
result = publish_verified_graph(
    release_service,
    app_model,
    session,
    candidate_graph,
    ExpectedReleaseIdentity(
        app_id=APP_ID,
        draft_digest=OLD_GRAPH_DIGEST,
        draft_workflow_id=OLD_DRAFT_ID,
        draft_metadata_digest=OLD_METADATA_DIGEST,
    ),
    ReleaseMark(
        name="Regression validated dossier wiring",
        comment="Routes comparison requests through validated regression evidence.",
        backup_name="Backup before regression validated dossier wiring",
        backup_comment="Restorable candidate snapshot before comparison wiring correction.",
    ),
    release_manifest=manifest,
    candidate_metadata=state.draft_metadata,
)
```

Commit the SQLAlchemy session only when `result["status"] == "published"`. A raised exception or `rolled_back` result fails the task and must retain the safe rollback record.

- [ ] **Step 4: Persist only safe publication evidence**

Write schema version, old/new IDs, backup/rollback IDs, graph/metadata/source/DSL digests, release status, and UTC timestamp to `.live-artifacts/regression-wiring-publication.json`. Do not store graph content, environment-variable values, credentials, or exception messages.

- [ ] **Step 5: Independently read back and run the snapshot checker**

Export fresh draft/published snapshots to ignored files, then run:

```powershell
python scripts/check_workflow_release.py --dsl dify/paper-comparison-regression-workflow.yml --source scripts/build_regression_dsl.py scripts/build_multimodel_dsl.py dify/paper-comparison-workflow.yml dify/paper-dossier-workflow.yml dify/code/validate_evidence.py dify/code/comparison_workflow.py dify/code/experiment_workflow.py dify/code/validate_parser.py --draft-snapshot .live-artifacts/regression-candidate-draft-snapshot.json --published-snapshot .live-artifacts/regression-candidate-published-snapshot.json --json
```

Expected: `{"drift":[]}`, equal draft/published graph digests matching the manifest, equal metadata digests matching the old fixed metadata digest, and distinct new draft/published UUIDs.

---

### Task 6: Run the five-case real gate without code changes

**Files:**
- Create ignored: `.live-artifacts/real-regression-acceptance.json`
- Preserve ignored: `.live-artifacts/real-regression-acceptance.failed-contract-*.json`

**Interfaces:**
- Consumes: the corrected published candidate, pinned `real_world/regression_cases.yml`, verified cache, and candidate-scoped API key.
- Produces: five privacy-safe terminal results and a pass/fail corpus evaluation.

- [ ] **Step 1: Confirm an idle runner and fixed candidate identity**

Repeat Task 5 health/readback checks and require no active runner job. Record the new candidate IDs/digests in memory before the first case.

- [ ] **Step 2: Run all five cases**

Set `DIFY_REGRESSION_API_KEY` only in the current PowerShell process and run:

```powershell
python scripts/run_real_regression_acceptance.py run --registry real_world/regression_cases.yml --output .live-artifacts/real-regression-acceptance.json --resume
```

Do not edit code, DSL, workflow metadata, or configuration between cases.

- [ ] **Step 3: Evaluate and independently scan evidence**

```powershell
python scripts/run_real_regression_acceptance.py evaluate --input .live-artifacts/real-regression-acceptance.json
python -m json.tool .live-artifacts/real-regression-acceptance.json > $null
rg -n -i "authorization|bearer |cookie|protocol_token|api_key|paper_text|csv_rows" .live-artifacts/real-regression-acceptance.json
```

Expected: evaluation passes with at least four completed cases, JSON parsing succeeds, and `rg` returns no match. Require zero false strict-comparability results.

- [ ] **Step 4: Verify post-run identity and runner quiescence**

Repeat independent Dify readback and the snapshot checker. Require the exact Task 5 new IDs/digests, `{"drift":[]}`, and zero queued/running runner jobs.

- [ ] **Step 5: Stop safely on another generalized defect**

If the gate fails, preserve only its safe aggregate distribution, do not patch the published candidate inline, and start a separately approved design/TDD cycle.

---

### Task 7: Document, verify, review, and integrate

**Files:**
- Modify: `docs/release-workflow.md`
- Read: all task-owned commits and ignored safe evidence

**Interfaces:**
- Consumes: passing Task 6 evidence and final immutable candidate identities.
- Produces: reproducible operating documentation, final verification, reviewed commits, and safe integration into `master`.

- [ ] **Step 1: Record aggregate outcomes**

Add the five case IDs, completion/failure distribution, strict and approximate status counts, new App/draft/published/backup UUIDs, graph/metadata/source/DSL digests, safe evidence path, and exact rerun command to `docs/release-workflow.md`. Include no raw metrics beyond the safe aggregate schema.

- [ ] **Step 2: Run final verification from a clean tree**

```powershell
python -m pytest -q tests/real_regression_acceptance tests/test_end_to_end_regression.py tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py
python -m pytest -q
python scripts/check_workflow_release.py --dsl dify/paper-comparison-regression-workflow.yml --source scripts/build_regression_dsl.py scripts/build_multimodel_dsl.py dify/paper-comparison-workflow.yml dify/paper-dossier-workflow.yml dify/code/validate_evidence.py dify/code/comparison_workflow.py dify/code/experiment_workflow.py dify/code/validate_parser.py --draft-snapshot .live-artifacts/regression-candidate-draft-snapshot.json --published-snapshot .live-artifacts/regression-candidate-published-snapshot.json --json
git diff --check
```

Expected: all tests pass, checker returns `{"drift":[]}`, and whitespace is clean.

- [ ] **Step 3: Commit documentation**

```powershell
git add -- docs/release-workflow.md
git commit -m "docs: record validated regression candidate acceptance"
```

- [ ] **Step 4: Request code review and address only verified findings**

Use the requesting-code-review skill against the full task commit range. Apply review findings with receiving-code-review and TDD; rerun the affected and full verification commands after any change.

- [ ] **Step 5: Integrate safely**

Use the finishing-a-development-branch skill. Verify the main checkout's six user-owned DSL modifications remain unstaged and byte-identical to their pre-integration state. Integrate only the task-owned commits into `master`, rerun the focused smoke checks there, and remove the worktree only after successful verification.
