# Release Baseline Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the existing runtime, multi-model, asynchronous-job, Dify-generation, and V3.1 publishing work into a tested, deterministic, reviewable release baseline without changing the currently published V3.1 until final acceptance.

**Architecture:** Preserve the current isolated worktree and review its existing changes in dependency order. Treat Python workflow code and the DSL generator as the source of truth, add pure canonicalization/manifest/drift functions that run without Dify, and keep Dify database/service access behind a small adapter reused by the V3.1 updater. Publish only after focused tests, full regression, deterministic generation, a clean candidate state, backup creation, and post-publish verification.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, pytest 9, PyYAML, scikit-learn, SQLite, Docker Compose, Dify 1.16 service layer, PowerShell 5.1-compatible operational scripts.

## Global Constraints

- Preserve all pre-existing worktree changes; never reset, overwrite, or delete them to simplify the review.
- Do not commit `.pytest-tmp*`, `.live-artifacts`, raw PDFs, raw CSV rows, database files, secrets, session tokens, or Dify credentials.
- Keep the currently published V3.1 unchanged until Task 9 explicitly reaches live acceptance.
- Every newly discovered behavior defect requires a failing regression test before its fix.
- `dify/code/*.py` and `scripts/build_multimodel_dsl.py` are the executable workflow source of truth; YAML files are deterministic build artifacts.
- Optional model dependency failures produce `unavailable`; one model failure produces a `partial` suite and does not erase successful results.
- Read-only drift checks must perform no Dify save, backup, publish, or rollback call.
- Any live mutation requires identity validation, a restorable backup, and post-publish verification.
- Runtime and release metadata must not expose environment secrets or absolute user-data paths.
- Use the existing `codex/multi-model-cv` worktree; do not create another worktree.

---

## File Responsibility Map

- `src/repro_runner/runtime.py`: deterministic runner source identity and safe build metadata.
- `src/repro_runner/{schemas,metrics,dossier,suite_engine,compare,storage}.py`: multi-model contracts, execution, persistence, and comparison.
- `src/repro_runner/{api,job_store,job_runner}.py`: asynchronous job lifecycle and API.
- `dify/code/{experiment_workflow,comparison_workflow}.py`: testable workflow-node code.
- `scripts/build_multimodel_dsl.py`: deterministic generation of run, prepare, merged, and profile-specific DSL files.
- `scripts/workflow_release_integrity.py`: new pure canonicalization, digest, manifest, and drift contracts.
- `scripts/update_v31_similarity_workflow.py`: Dify service adapter and backward-compatible V3.1 command entrypoint.
- `scripts/check_workflow_release.py`: new read-only CLI for repository/snapshot integrity checks.
- `tests/test_workflow_release_integrity.py`: pure release-integrity contract tests.
- `tests/test_v31_workflow_updater.py`: updater adapter, identity, backup, verification, and rollback tests.
- `docs/release-workflow.md`: operator-facing build, inspect, publish, verify, and rollback instructions.

---

### Task 1: Freeze and Commit Runtime Provenance

**Files:**
- Review/Modify: `.env.example`
- Review/Modify: `Dockerfile.repro`
- Review/Modify: `compose.yaml`
- Create already present: `src/repro_runner/runtime.py`
- Review/Modify: `src/repro_runner/api.py`
- Review/Modify: `src/repro_runner/schemas.py`
- Review/Modify: `src/repro_runner/storage.py`
- Create already present: `tests/repro_runner/test_runtime.py`
- Review/Modify: `tests/repro_runner/test_compose_contract.py`
- Review: `tests/test_runtime_build_contract.py`

**Interfaces:**
- Produces: `RuntimeProvenance(service_version: str, source_digest: str, git_commit: str, workflow_version: str)`.
- Produces: `source_tree_digest(source_root: Path | None = None) -> str`.
- Produces: `runtime_provenance(workflow_version: str | None = None) -> RuntimeProvenance`.
- Consumed by: health response, suite result persistence, release manifest in Task 5.

- [ ] **Step 1: Record the exact runtime-only candidate file set**

Run:

```powershell
git diff -- .env.example Dockerfile.repro compose.yaml src/repro_runner/runtime.py src/repro_runner/api.py src/repro_runner/schemas.py src/repro_runner/storage.py tests/repro_runner/test_runtime.py tests/repro_runner/test_compose_contract.py tests/test_runtime_build_contract.py
```

Expected: only provenance fields, build arguments, health exposure, result persistence, and their tests; defer unrelated hunks in shared files to later tasks by staging selected hunks.

- [ ] **Step 2: Run the existing runtime contract tests**

Run:

```powershell
python -m pytest tests/repro_runner/test_runtime.py tests/repro_runner/test_compose_contract.py tests/test_runtime_build_contract.py -q --basetemp=.pytest-tmp-baseline-runtime
```

Expected: PASS. If a test fails because existing behavior is defective, keep the failure as RED and perform Steps 3–4. If all pass, skip directly to Step 5 because this task is reviewing pre-existing implementation rather than adding new behavior.

- [ ] **Step 3: Add the missing failing provenance case when needed**

Add a focused test of the observed defect. For source ordering or platform leakage, use this shape in `tests/repro_runner/test_runtime.py`:

```python
def test_source_tree_digest_is_stable_across_creation_order(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "b.py").write_text("B = 2\n", encoding="utf-8")
    (left / "a.py").write_text("A = 1\n", encoding="utf-8")
    (right / "a.py").write_text("A = 1\n", encoding="utf-8")
    (right / "b.py").write_text("B = 2\n", encoding="utf-8")

    assert source_tree_digest(left) == source_tree_digest(right)
```

Run the single test and verify it fails for the observed reason, not an import or fixture error.

- [ ] **Step 4: Make the minimal provenance fix and rerun focused tests**

Implementation must retain deterministic relative POSIX paths, file bytes, NUL separators, safe metadata validation, and `sha256:<64 lowercase hex>` output. Rerun Step 2 and expect PASS.

- [ ] **Step 5: Stage only runtime-provenance hunks and inspect the index**

Run `git add -p` for shared files and ordinary `git add` for files wholly owned by this task. Then run:

```powershell
git diff --cached --check
git diff --cached --name-only
git diff --cached
```

Expected: no multi-model ranking, dossier alias, async API, or DSL generator behavior is staged.

- [ ] **Step 6: Commit the runtime baseline**

```powershell
git commit -m "feat: persist runner runtime provenance"
```

---

### Task 2: Freeze and Commit Multi-Model Execution and Comparison

**Files:**
- Review/Modify: `src/repro_runner/schemas.py`
- Review/Modify: `src/repro_runner/metrics.py`
- Review/Modify: `src/repro_runner/dossier.py`
- Review/Modify: `src/repro_runner/suite_engine.py`
- Review/Modify: `src/repro_runner/compare.py`
- Review/Modify: `src/repro_runner/storage.py`
- Review/Modify: `src/repro_runner/api.py`
- Review/Modify: `tests/repro_runner/test_{api,compare,dossier,protocol,suite_engine}.py`

**Interfaces:**
- Consumes: `RuntimeProvenance` from Task 1.
- Produces: `ExperimentSuiteResult` with shared `SplitProvenance`, per-model status, runtime provenance, performance ranking, and paper-closeness ranking.
- Produces: normalized paper metrics that preserve model, dataset, split, threshold, source, and evidence qualifiers.
- Produces: backward-compatible single random-forest endpoints plus multi-model suite endpoints.

- [ ] **Step 1: Partition multi-model hunks from async API hunks**

Inspect the listed files with `git diff`. Classify endpoint handlers that directly execute suites as Task 2; leave `/v1/jobs*`, job-store setup, wait, cancel, and recovery hunks unstaged for Task 3.

- [ ] **Step 2: Run focused multi-model tests before changing code**

```powershell
python -m pytest tests/repro_runner/test_suite_engine.py tests/repro_runner/test_compare.py tests/repro_runner/test_dossier.py tests/repro_runner/test_api.py -q --basetemp=.pytest-tmp-baseline-multimodel
```

Expected: PASS for the existing candidate. Any failure becomes the RED observation for a minimal repair.

- [ ] **Step 3: Add regression coverage for any review defect**

Use real suite results, not estimator-call mocks. For model failure isolation, the required assertion shape is:

```python
assert result.status == "partial"
assert [item.status for item in result.results] == ["succeeded", "failed", "succeeded"]
assert result.performance_ranking == ["random_forest", "logistic_regression"]
assert {item.split_provenance.test_digest for item in result.results if item.status == "succeeded"} == {TEST_DIGEST}
```

Run the new single test and verify it fails on the exact contract violation.

- [ ] **Step 4: Apply only the minimal repair and rerun focused tests**

Do not redesign model search spaces or add metrics. Preserve the seven-model registry, bounded sequential training, shared outer split, backward-compatible baseline API, and supported binary metrics.

- [ ] **Step 5: Run persistence and schema compatibility tests**

```powershell
python -m pytest tests/repro_runner/test_schemas.py tests/repro_runner/test_suite_schemas.py tests/repro_runner/test_suite_compare.py tests/repro_runner/test_suite_api.py tests/repro_runner/test_protocol.py -q --basetemp=.pytest-tmp-baseline-multimodel-contracts
```

Expected: PASS; stored historical baseline results remain readable.

- [ ] **Step 6: Stage only multi-model core hunks and commit**

Inspect the staged patch with `git diff --cached --check` and commit:

```powershell
git commit -m "feat: finalize shared-split multi-model comparison"
```

---

### Task 3: Freeze and Commit Persistent Asynchronous Jobs

**Files:**
- Review/Modify: `src/repro_runner/api.py`
- Review: `src/repro_runner/job_store.py`
- Review: `src/repro_runner/job_runner.py`
- Review/Modify: `src/repro_runner/schemas.py`
- Review/Modify: `src/repro_runner/storage.py`
- Review/Modify: `tests/repro_runner/test_job_api.py`
- Review: `tests/repro_runner/test_job_store.py`
- Review: `tests/repro_runner/test_job_runner.py`

**Interfaces:**
- Consumes: multi-model execution and persistence from Task 2.
- Produces: `POST /v1/jobs`, `GET /v1/jobs/{job_id}`, `POST /v1/jobs/{job_id}/wait-result`, `POST /v1/jobs/{job_id}/cancel`, and `GET /v1/jobs/{job_id}/result`.
- Produces: deterministic lifecycle states `queued`, `running`, `cancel_requested`, `cancelled`, `needs_retry`, `failed`, `partial`, and `succeeded`.

- [ ] **Step 1: Run asynchronous lifecycle tests**

```powershell
python -m pytest tests/repro_runner/test_job_store.py tests/repro_runner/test_job_runner.py tests/repro_runner/test_job_api.py -q --basetemp=.pytest-tmp-baseline-jobs
```

Expected: PASS or a precise RED failure in lifecycle behavior.

- [ ] **Step 2: Verify restart and damaged-input contracts explicitly**

If not already covered, add tests with these assertions:

```python
recovered = restarted_store.get(job.job_id)
assert recovered.status == "needs_retry"

runner._process_job(job.job_id)
damaged = store.get(job.job_id)
assert damaged.status == "needs_retry"
assert damaged.error_code == "job_inputs_missing"
```

The first test must preserve complete staged inputs; the second must delete only that test job's manifest file inside its pytest temporary directory. Run each new test and observe the correct RED failure.

- [ ] **Step 3: Apply minimal lifecycle fixes**

Keep job admission idempotent by `(manifest_id, dataset_id)`, retain bounded capacity, clean staged inputs only after terminal outcomes, and never expose host paths through API responses.

- [ ] **Step 4: Rerun job and API tests**

Run Step 1 plus `tests/repro_runner/test_api.py`. Expected: PASS with no dangling worker-thread or temporary-directory warning.

- [ ] **Step 5: Stage async-only hunks and commit**

```powershell
git commit -m "feat: finalize persistent asynchronous experiment jobs"
```

---

### Task 4: Make Dify DSL Generation the Deterministic Source Path

**Files:**
- Review/Modify: `dify/code/comparison_workflow.py`
- Review/Modify: `dify/code/experiment_workflow.py`
- Review/Modify: `scripts/build_multimodel_dsl.py`
- Regenerate: `dify/paper-comparison-multimodel-workflow.yml`
- Regenerate: `dify/paper-comparison-prepare-workflow.yml`
- Regenerate: `dify/paper-comparison-merged-workflow.yml`
- Regenerate: supported `*-ollama.yml` profile artifacts
- Review/Modify: `dify/paper-comparison-multimodel-workflow.md`
- Review/Modify: `tests/test_dify_{multimodel_code,multimodel_dsl,merged_dsl,job_workflow}.py`

**Interfaces:**
- Consumes: suite, async-job, dossier, comparison, runtime, and protocol contracts from Tasks 1–3.
- Produces: `build_multimodel_dsl(profile)`, `build_prepare_dsl(profile)`, `build_merged_dsl(profile)`, and `write_profile_dsls(...)`.
- Produces: deterministic YAML files whose embedded Python matches `dify/code/*.py` contracts.

- [ ] **Step 1: Run existing Dify code and DSL tests**

```powershell
python -m pytest tests/test_dify_multimodel_code.py tests/test_dify_multimodel_dsl.py tests/test_dify_merged_dsl.py tests/test_dify_job_workflow.py tests/test_dify_llm_profiles.py -q --basetemp=.pytest-tmp-baseline-dify
```

Expected: PASS or a precise RED failure.

- [ ] **Step 2: Add a failing generator-idempotence test**

Add to `tests/test_dify_multimodel_dsl.py`:

```python
def test_generated_dsls_are_byte_stable(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write_profile_dsls(profile="deepseek", output_root=first)
    write_profile_dsls(profile="deepseek", output_root=second)

    first_files = {path.name: path.read_bytes() for path in first.iterdir()}
    second_files = {path.name: path.read_bytes() for path in second.iterdir()}
    assert first_files == second_files
```

Run the test and verify RED because the current writer cannot target an isolated output directory.

- [ ] **Step 3: Add an explicit output-root parameter and deterministic writer**

The public interface must be:

```python
def write_profile_dsls(
    profile: str = DEFAULT_LLM_PROFILE,
    *,
    output_root: Path = PROJECT_ROOT / "dify",
) -> tuple[Path, ...]:
    ...
```

Use `yaml.safe_dump(..., allow_unicode=True, sort_keys=False, width=10_000)` and UTF-8 with `newline="\n"`. Return all written paths in stable filename order.

- [ ] **Step 4: Regenerate all tracked DSL artifacts twice**

Run the generator once, record `git diff --stat`, run it again, and verify the second run adds no new diff. Do not hand-edit YAML to make tests pass; change source code or generator logic and regenerate.

- [ ] **Step 5: Rerun Dify tests and validate YAML parsing**

Run Step 1 and additionally load every generated YAML through `yaml.safe_load`. Expected: PASS.

- [ ] **Step 6: Stage source, tests, docs, and generated DSL together and commit**

```powershell
git commit -m "feat: make Dify workflow generation deterministic"
```

---

### Task 5: Add Pure Release Manifest and Canonical Graph Contracts

**Files:**
- Create: `scripts/workflow_release_integrity.py`
- Create: `tests/test_workflow_release_integrity.py`

**Interfaces:**
- Produces: `canonical_graph(graph: dict[str, object]) -> dict[str, object]`.
- Produces: `graph_digest(graph: dict[str, object]) -> str`.
- Produces: `source_digest(paths: Sequence[Path], root: Path) -> str`.
- Produces: `build_release_manifest(*, git_commit: str, worktree_clean: bool, source_sha256: str, dsl_sha256: str, graph_sha256: str, workflow_kind: str, workflow_version: str, app_id: str | None = None, draft_workflow_id: str | None = None, published_workflow_id: str | None = None, rollback_workflow_id: str | None = None) -> dict[str, object]`.
- Produces: `compare_release_layers(*, expected_source_digest: str, actual_source_digest: str, dsl_graph: dict[str, object], draft_graph: dict[str, object] | None = None, published_graph: dict[str, object] | None = None) -> list[dict[str, str]]`.

- [ ] **Step 1: Write failing canonicalization tests**

Create tests asserting that node/edge order, UI selection, positions, viewport, and graph-level environment metadata do not change the digest, while node IDs, code, inputs, outputs, conditions, and connections do change it:

```python
def test_graph_digest_ignores_layout_but_not_behavior():
    baseline = graph_fixture()
    layout_only = deepcopy(baseline)
    layout_only["nodes"].reverse()
    layout_only["nodes"][0]["position"] = {"x": 999, "y": 999}
    assert graph_digest(layout_only) == graph_digest(baseline)

    behavior = deepcopy(baseline)
    behavior["nodes"][0]["data"]["code"] = "return {'changed': True}"
    assert graph_digest(behavior) != graph_digest(baseline)
```

Run: `python -m pytest tests/test_workflow_release_integrity.py -q --basetemp=.pytest-tmp-release-integrity-red`.

Expected: FAIL because the module does not exist.

- [ ] **Step 2: Implement minimal canonical graph and digest behavior**

Implementation contract:

```python
_VOLATILE_GRAPH_KEYS = frozenset({
    "selected", "position", "positionAbsolute", "viewport", "width", "height", "zIndex"
})

def graph_digest(graph: dict[str, object]) -> str:
    payload = json.dumps(
        canonical_graph(graph),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
```

Canonicalization recursively removes only the listed volatile keys, sorts nodes by `id`, and sorts edges by `(source, sourceHandle, target, targetHandle, id)`. Reject missing/non-list `nodes` or `edges` with `ValueError("workflow graph must contain node and edge arrays")`.

- [ ] **Step 3: Write failing source and manifest determinism tests**

Assert paths are hashed by relative POSIX name and bytes in sorted order; `build_release_manifest` contains no implicit current time and validates every digest and ID. Required manifest shape:

```python
{
    "schema_version": 1,
    "git_commit": "abc123",
    "worktree_clean": True,
    "source_digest": "sha256:" + "a" * 64,
    "dsl_digest": "sha256:" + "b" * 64,
    "graph_digest": "sha256:" + "c" * 64,
    "workflow_kind": "paper-comparison-multimodel",
    "workflow_version": "multimodel-0.8.0",
    "dify": {
        "app_id": "b9a766a0-0ad0-415b-8d42-60459c92bec7",
        "draft_workflow_id": "0fd3ec44-599e-4936-841d-a6df3b8094eb",
        "published_workflow_id": "94e00245-a1f8-48e3-aba6-41549ab75c6e",
        "rollback_workflow_id": "53a5a8ec-6639-4080-8b15-fc2fc93116a5",
    },
}
```

Run the new tests and observe RED because the functions are missing.

- [ ] **Step 4: Implement source digest and deterministic manifest builder**

Reject unsafe metadata instead of silently coercing it. Allow `None` only for Dify IDs before live inspection; omit live timestamp from the build manifest.

- [ ] **Step 5: Write failing layer-comparison tests**

Required error codes:

```python
assert [item["code"] for item in compare_release_layers(
    expected_source_digest="sha256:" + "a" * 64,
    actual_source_digest="sha256:" + "b" * 64,
    dsl_graph=graph_fixture(),
    draft_graph=changed_graph_fixture(),
    published_graph=other_changed_graph_fixture(),
)] == ["source_dsl_drift", "dsl_draft_drift", "draft_published_drift"]
```

Each item contains only `code`, `expected_digest`, and `actual_digest`.

- [ ] **Step 6: Implement comparison and rerun all pure tests**

Expected: all release-integrity tests PASS and `json.dumps(..., allow_nan=False)` succeeds for manifests and drift results.

- [ ] **Step 7: Commit the pure release contracts**

```powershell
git commit -m "feat: add deterministic workflow release manifests"
```

---

### Task 6: Add a Read-Only Repository and Snapshot Drift CLI

**Files:**
- Create: `scripts/check_workflow_release.py`
- Modify: `tests/test_workflow_release_integrity.py`
- Create: `docs/release-workflow.md`

**Interfaces:**
- Consumes: pure functions from Task 5.
- Produces CLI: `python scripts/check_workflow_release.py --dsl PATH --source PATH... [--draft-snapshot PATH] [--published-snapshot PATH] [--json]`.
- Exit codes: `0` no drift, `1` drift found, `2` invalid input or identity.

- [ ] **Step 1: Write failing CLI tests**

Use `subprocess.run` against temporary YAML/JSON snapshots. Assert clean output returns 0, behavioral drift returns 1 with the exact error code, malformed graph returns 2, and input files remain byte-identical before/after.

- [ ] **Step 2: Run CLI tests and verify RED**

Expected: FAIL because `check_workflow_release.py` does not exist.

- [ ] **Step 3: Implement the minimal read-only CLI**

The CLI loads YAML DSL graph from `document["workflow"]["graph"]`, loads optional JSON graph snapshots, calls Task 5 functions, writes either stable JSON or short text, and never imports Dify models/services.

- [ ] **Step 4: Rerun pure and CLI tests**

```powershell
python -m pytest tests/test_workflow_release_integrity.py -q --basetemp=.pytest-tmp-release-cli
```

Expected: PASS.

- [ ] **Step 5: Document exact operator commands and commit**

Document build, offline snapshot comparison, exit codes, secret exclusions, and the fact that this command never publishes. Commit:

```powershell
git commit -m "feat: add read-only workflow drift checks"
```

---

### Task 7: Generalize Safe Dify Backup, Publish, Verify, and Rollback

**Files:**
- Modify: `scripts/update_v31_similarity_workflow.py`
- Create: `tests/test_v31_workflow_updater.py`
- Modify: `docs/release-workflow.md`

**Interfaces:**
- Consumes: `graph_digest`, `compare_release_layers`, and `build_release_manifest` from Task 5.
- Produces: `inspect_release_state(service, app_model, session, expected_app_id) -> ReleaseState` with no writes.
- Produces: `publish_verified_graph(service, app_model, session, candidate_graph, expected_identity, mark) -> dict[str, object]`.
- Preserves: existing V3.1 updater CLI and idempotent `transform_graph` behavior.

- [ ] **Step 1: Extract a testable adapter seam without changing behavior**

Move Dify imports inside the CLI/runtime function as they are now, and pass a service protocol into the pure orchestration functions. Do not publish during this step.

- [ ] **Step 2: Write failing identity and read-only tests**

Use an in-memory fake service recording method calls:

```python
state = inspect_release_state(fake, app, session, expected_app_id=APP_ID)
assert state.app_id == APP_ID
assert fake.write_calls == []

with pytest.raises(ValueError, match="Dify application identity mismatch"):
    inspect_release_state(fake, other_app, session, expected_app_id=APP_ID)
assert fake.write_calls == []
```

Run and verify RED because the new interface is missing.

- [ ] **Step 3: Implement read-only inspection**

Read draft and published workflow once each, validate app ID, canonicalize both graphs, and return IDs/digests. Do not save drafts or create versions.

- [ ] **Step 4: Write failing backup/publish/post-verify/rollback tests**

Assert call order exactly:

```python
assert fake.calls == [
    "read_draft",
    "read_published",
    "create_backup",
    "save_draft",
    "publish",
    "read_published",
]
```

For a post-publish digest mismatch, require an explicit rollback call using the recorded backup ID and assert the result contains both failed published ID and rollback workflow ID.

- [ ] **Step 5: Implement verified publishing and controlled rollback**

Reject pre-existing drift unless the caller passes the exact expected draft digest. Create backup before `save_draft`. After publish, re-read and compare candidate graph digest. On mismatch, restore the recorded backup through Dify's workflow service and verify the restored digest. Never infer a rollback target from recency.

- [ ] **Step 6: Preserve updater idempotence and boundary tests**

Add tests proving running `transform_graph` twice changes the graph only once, invalid similarity thresholds remain structured, and existing V3.1 graph contracts still pass.

- [ ] **Step 7: Run updater and release tests**

```powershell
python -m pytest tests/test_v31_workflow_updater.py tests/test_workflow_release_integrity.py -q --basetemp=.pytest-tmp-release-adapter
```

Expected: PASS without a live Dify dependency.

- [ ] **Step 8: Commit the safe adapter**

```powershell
git commit -m "feat: verify and roll back Dify workflow releases"
```

---

### Task 8: Complete Repository Regression and Candidate Release Artifacts

**Files:**
- Modify only if a failing test proves a defect: files owned by Tasks 1–7
- Regenerate: tracked Dify DSL artifacts
- Update: `docs/release-workflow.md`
- Create locally but do not commit: `.live-artifacts/release-baseline-candidate.json`

**Interfaces:**
- Consumes all earlier tasks.
- Produces a clean, deterministic candidate commit and a local release manifest.

- [ ] **Step 1: Run the complete test suite**

```powershell
python -m pytest -q --basetemp=.pytest-tmp-release-baseline-full
```

Expected: all tests PASS and no new warning. For any failure, add or retain the focused failing test, repair minimally, rerun focused tests, then rerun full suite.

- [ ] **Step 2: Run deterministic DSL generation twice**

After the first generation, capture hashes for every tracked workflow YAML. Run generation again and verify identical hashes and no additional Git diff.

- [ ] **Step 3: Run repository-only release integrity check**

Generate `.live-artifacts/release-baseline-candidate.json` from the current commit and tracked DSL. Expected: `worktree_clean=true` after ignoring explicitly excluded local artifact and pytest directories, and no `source_dsl_drift`.

- [ ] **Step 4: Inspect repository status and exclusion hygiene**

Confirm no temporary directory, PDF, CSV, database, or live artifact is staged. Preserve pre-existing user-owned scratch files without committing or deleting them.

- [ ] **Step 5: Commit any final test-proven integration repair**

Use a narrowly named commit such as:

```powershell
git commit -m "fix: align release baseline integration contracts"
```

Skip the commit if no repair was required.

---

### Task 9: Perform Live Read-Only Inspection and Controlled Acceptance

**Files:**
- Create locally but do not commit: `.live-artifacts/release-baseline-live-inspection.json`
- Create locally but do not commit: `.live-artifacts/release-baseline-e2e.txt`
- Update after successful verification: `docs/release-workflow.md` only if an operator command was inaccurate

**Interfaces:**
- Consumes candidate commit, manifest, drift checker, and Dify adapter.
- Produces verified live-state evidence and, only after all gates pass, a published candidate workflow distinct from current V3.1.

- [ ] **Step 1: Inspect Dify identity and state without writes**

Run the adapter in inspection mode inside the local Dify API container. Record app ID, draft ID/digest, published ID/digest, candidate DSL digest, and detected drift codes. Verify logs show no backup/save/publish call.

- [ ] **Step 2: Resolve expected drift without modifying current V3.1**

If candidate multi-model DSL differs from current V3.1, create or target the explicitly configured candidate application/workflow rather than overwriting V3.1. Identity must come from existing configuration or the user's already authorized local candidate app; never invent an app ID.

- [ ] **Step 3: Backup and publish the candidate**

Require focused tests, full tests, deterministic generation, clean candidate commit, matching expected draft digest, and successful backup. Record backup and new published workflow IDs.

- [ ] **Step 4: Verify the published graph**

Re-read the published workflow and require candidate graph digest equality. On mismatch, execute Task 7 rollback and stop acceptance.

- [ ] **Step 5: Run current PDF/CSV end-to-end acceptance**

Use the existing local PDF and CSV through the installed candidate app. Require terminal success, shared test digest for successful models, explicit per-model failures/unavailable states, strict/approximate separation, paper AUC `0.91`, and preservation of the 66-duplicate warning.

- [ ] **Step 6: Rerun the full regression suite after live acceptance**

```powershell
python -m pytest -q --basetemp=.pytest-tmp-release-baseline-postlive
```

Expected: PASS.

- [ ] **Step 7: Record final release evidence and baseline status**

Write IDs, digests, elapsed time, model statuses, comparison summary, duplicate warning count, rollback target, and test result to `.live-artifacts`; exclude source documents, CSV rows, credentials, and tokens.

- [ ] **Step 8: Commit documentation correction only if required**

If live execution proved an operator command inaccurate, fix it, verify it, and commit:

```powershell
git commit -m "docs: finalize verified workflow release procedure"
```

Otherwise make no extra commit.

---

## Final Verification Checklist

- [ ] Runtime, multi-model, async-job, Dify-generation, release-contract, and release-adapter commits are independently reviewable.
- [ ] All focused test commands pass.
- [ ] Full suite passes before and after live acceptance.
- [ ] DSL generation is byte-stable on two consecutive runs.
- [ ] Offline and live drift checks report the correct layer.
- [ ] Read-only inspection performs no writes.
- [ ] Candidate publish creates a backup and verifies the published digest.
- [ ] Rollback is tested and uses only the explicit backup ID.
- [ ] Current V3.1 is not accidentally overwritten.
- [ ] Real PDF/CSV acceptance preserves strict/approximate separation and the 66-row duplicate warning.
- [ ] No secret, raw data, pytest temp directory, or live artifact is committed.
