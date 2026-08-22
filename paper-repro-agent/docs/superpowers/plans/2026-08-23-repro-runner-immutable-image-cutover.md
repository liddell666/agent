# Repro Runner Immutable Image Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make guarded runner cutover start the exact smoke-tested image under a fresh Compose project and reliably restore the retained legacy runner when replacement creation fails.

**Architecture:** Add a cutover-only Compose override whose image value comes from a process-scoped environment variable set to the resolved immutable image ID. Harden rollback by inspecting whether a replacement container exists before removing it, while retaining the existing stop/rename/network/start sequence.

**Tech Stack:** Windows PowerShell 5.1, Docker Compose v2, YAML, Python 3.12, pytest contract tests.

## Global Constraints

- Start only the immutable `sha256:` image ID that passed the provenance smoke test.
- Keep the active-job and `/data/experiments` source guards ahead of all forward mutations.
- Preserve the fresh Compose project boundary, container configuration, and `docker_default` alias `repro-runner`.
- Restore every process-scoped environment variable in `finally` on success and failure.
- Do not modify Dify apps, experiment data, historical legacy containers, or unrelated workflow DSL files.
- Write each regression test first and observe the expected failure before changing production files.

---

## File Responsibility Map

- `compose.runner-image.yaml`: cutover-only binding from `REPRO_RUNNER_CUTOVER_IMAGE` to `services.repro-runner.image`.
- `scripts/switch_repro_runner.ps1`: immutable-image selection, process environment cleanup, and legacy restoration.
- `tests/test_repro_runner_cutover_contract.py`: static and native PowerShell regressions for image pinning and restoration behavior.

### Task 1: Pin the Verified Image in the Fresh Compose Project

**Files:**
- Create: `compose.runner-image.yaml`
- Modify: `scripts/switch_repro_runner.ps1`
- Test: `tests/test_repro_runner_cutover_contract.py`

**Interfaces:**
- Consumes: `$imageId: string` returned by `Resolve-ReproRunnerImageId`, matching `^sha256:[0-9a-f]{64}$`.
- Produces: process variable `REPRO_RUNNER_CUTOVER_IMAGE` and Compose service field `services.repro-runner.image`.

- [ ] **Step 1: Write the failing immutable-image contract test**

Add:

```python
def test_forward_cutover_pins_the_smoke_tested_image_id() -> None:
    source = _script_source()
    override = Path("compose.runner-image.yaml").read_text(encoding="utf-8")

    assert "REPRO_RUNNER_CUTOVER_IMAGE" in override
    assert "image:" in override
    assert "$composeImageOverrideFile" in source
    assert '$env:REPRO_RUNNER_CUTOVER_IMAGE = $imageId' in source
    cutover = source[source.index("$imageId = Resolve-ReproRunnerImageId") :]
    _assert_in_order(
        cutover,
        "$imageId = Resolve-ReproRunnerImageId",
        '$env:REPRO_RUNNER_CUTOVER_IMAGE = $imageId',
        'Invoke-DockerChecked @("stop", $ContainerName)',
        '$composeImageOverrideFile',
        '"up", "-d", "--no-build", "--no-deps", "repro-runner"',
    )
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/test_repro_runner_cutover_contract.py::test_forward_cutover_pins_the_smoke_tested_image_id -q --basetemp=.tr1
```

Expected: FAIL because `compose.runner-image.yaml` does not exist.

- [ ] **Step 3: Add the minimal image override**

Create `compose.runner-image.yaml`:

```yaml
services:
  repro-runner:
    image: "${REPRO_RUNNER_CUTOVER_IMAGE:?REPRO_RUNNER_CUTOVER_IMAGE must be set when using this override}"
```

In `switch_repro_runner.ps1`, define the override path and environment variable name beside the existing Compose variables, capture any previous process value, set it to `$imageId` after the smoke test and before the first mutation, append the image override to the forward Compose file arguments, and restore or remove the process value in the outer `finally`.

Use this exact environment assignment:

```powershell
$env:REPRO_RUNNER_CUTOVER_IMAGE = $imageId
$composeFileArguments += @("-f", $composeImageOverrideFile)
```

- [ ] **Step 4: Run the focused contract and verify GREEN**

Run:

```powershell
python -m pytest tests/test_repro_runner_cutover_contract.py::test_forward_cutover_pins_the_smoke_tested_image_id tests/test_repro_runner_cutover_contract.py::test_forward_cutover_uses_a_fresh_compose_project_after_renaming_runner -q --basetemp=.tr1
```

Expected: `2 passed`.

- [ ] **Step 5: Commit the immutable image pin**

```powershell
git add -- compose.runner-image.yaml scripts/switch_repro_runner.ps1 tests/test_repro_runner_cutover_contract.py
git commit -m "fix: pin verified image during runner cutover"
```

### Task 2: Restore Legacy When Replacement Creation Fails

**Files:**
- Modify: `scripts/switch_repro_runner.ps1`
- Test: `tests/test_repro_runner_cutover_contract.py`

**Interfaces:**
- Produces: `Test-RunnerContainerExists -Name string -> bool` using exact Docker container inspection.
- Consumes: `Restore-RunnerState(..., [bool]$ReplacementMayExist)` and existing retained legacy container name.

- [ ] **Step 1: Write the failing restoration contract test**

Add:

```python
def test_restore_checks_for_an_actual_replacement_before_removal() -> None:
    source = _script_source()
    restore = _between(
        source,
        "function Restore-RunnerState {",
        "# The forward cutover mutates only after preflight succeeds:",
    )

    assert "function Test-RunnerContainerExists" in source
    assert "if ($ReplacementMayExist -and (Test-RunnerContainerExists -Name $Container))" in restore
    assert 'Invoke-DockerChecked @("rm", "-f", $Container)' in restore
    _assert_in_order(
        restore,
        "Test-RunnerContainerExists -Name $Container",
        'Invoke-DockerChecked @("rm", "-f", $Container)',
        'Invoke-DockerChecked @("rename", $LegacyName, $Container)',
        'Invoke-DockerChecked @("start", $Container)',
    )
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/test_repro_runner_cutover_contract.py::test_restore_checks_for_an_actual_replacement_before_removal -q --basetemp=.tr2
```

Expected: FAIL because `Test-RunnerContainerExists` is absent.

- [ ] **Step 3: Add exact existence detection and guarded removal**

Add a helper that runs `docker container inspect $Name`, returns `$true` only for exit code 0, returns `$false` for Docker's no-such-container response, and throws for other failures. Replace the unconditional best-effort removal with:

```powershell
if ($ReplacementMayExist -and (Test-RunnerContainerExists -Name $Container)) {
    Invoke-DockerChecked @("rm", "-f", $Container) | Out-Null
}
```

Keep rename, alias restoration, and start checked through `Invoke-DockerChecked`.

- [ ] **Step 4: Run the focused contract and verify GREEN**

Run:

```powershell
python -m pytest tests/test_repro_runner_cutover_contract.py::test_restore_checks_for_an_actual_replacement_before_removal -q --basetemp=.tr2
```

Expected: `1 passed`.

- [ ] **Step 5: Run all cutover-related tests**

```powershell
python -m pytest tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py tests/test_repro_runner_data_source_override_contract.py tests/repro_runner/test_compose_contract.py -q --basetemp=.tr-all
```

Expected: all tests pass with no new warnings.

- [ ] **Step 6: Commit the restoration fix**

```powershell
git add -- scripts/switch_repro_runner.ps1 tests/test_repro_runner_cutover_contract.py
git commit -m "fix: restore runner after pre-creation failure"
```

### Task 3: Execute and Verify the Operational Cutover

**Files:**
- No tracked file changes.
- Remove after verification: `C:\Users\17716\Documents\arcgis\.worktrees\multi-model-cv` (temporary Docker-created mount recovery directory only).

**Interfaces:**
- Consumes: cached image `paper-repro-agent-repro-runner:latest`, guarded switch script, existing `/data/experiments` mount.
- Produces: healthy active `repro-runner` sourced from the main checkout and one retained legacy container.

- [ ] **Step 1: Confirm the old runner is healthy and has no active jobs**

Run the health endpoint and the script's SQLite query against the live container. Expected: health status `ok`; zero `queued` or `running` jobs.

- [ ] **Step 2: Execute the guarded cutover without rebuilding**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\switch_repro_runner.ps1 -SkipBuild
```

Expected: `Runner cutover succeeded`, Git commit equal to current `git rev-parse HEAD`, workflow version `multimodel-0.8.0`, and a valid `sha256:` source digest.

- [ ] **Step 3: Verify live identity and persistence**

Inspect `repro-runner` and assert:

- image ID equals the previously smoke-tested immutable ID;
- `/app/src` source is `C:\Users\17716\Documents\arcgis\paper-repro-agent\src`;
- `/data/experiments` source is `C:\Users\17716\Documents\arcgis\paper-repro-agent\data\experiments`;
- network is `docker_default` with alias `repro-runner`;
- health reports the current Git commit and `multimodel-0.8.0`;
- persisted job aggregation remains six `succeeded` jobs and no active jobs.

- [ ] **Step 4: Remove only the temporary stale mount tree**

Resolve `C:\Users\17716\Documents\arcgis\.worktrees\multi-model-cv`, confirm it is below the intended `.worktrees` directory and absent from `git worktree list`, then remove that exact directory recursively. The contents are a hash-verified temporary copy of main `src` and remain recoverable from the repository.

- [ ] **Step 5: Run final focused verification and inspect Git state**

```powershell
python -m pytest tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py tests/test_repro_runner_data_source_override_contract.py tests/repro_runner/test_compose_contract.py -q --basetemp=.tr-final
git status --short
```

Expected: all focused tests pass; only the user's pre-existing DSL and unrelated workspace files remain dirty.
