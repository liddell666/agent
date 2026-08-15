# Runner Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a guarded, reversible procedure that replaces the stale live repro-runner container with the provenance-bearing Compose image without deleting experiment data or interrupting an active job.

**Architecture:** Keep the application/API unchanged. A PowerShell orchestrator performs a read-only SQLite preflight inside the current runner, builds and smoke-tests the new image, disconnects and renames the old container for rollback, starts the Compose service, and validates /healthz, mounts, and the Docker network alias. Any failure after the rename restores the old container before returning an error.

**Tech Stack:** PowerShell 7-compatible syntax, Docker CLI/Compose, Python standard-library sqlite3, FastAPI /healthz, pytest static contract checks.

## Global Constraints

- Work only in codex/multi-model-cv; preserve unrelated dirty files.
- Switch only after a read-only preflight confirms no job is queued or running.
- Preserve the experiment-data bind mount and Docker network alias.
- Retain the old container as repro-runner-legacy-*; do not delete it or the experiment-data directory.
- If health does not become valid, stop the replacement and restore the old container/name.
- Do not print secret environment values, CSV rows, PDF text, protocol tokens, or full request payloads.

---

### Task 1: Specify and test the cutover contract

**Files:**
- Create: scripts/switch_repro_runner.ps1
- Create: tests/test_repro_runner_cutover_contract.py
- Modify: tests/test_runtime_build_contract.py

**Interfaces:**
- scripts/switch_repro_runner.ps1 [-WorkflowVersion <string>] [-SkipBuild] [-ContainerName <string>] performs the guarded forward cutover.
- scripts/switch_repro_runner.ps1 -Rollback [-ContainerName <string>] restores the newest retained legacy container after the same no-active-job guard.
- The script exits nonzero before any stop/rename if the current runner is missing, the job-state query fails, or a queued/running job exists.

- [ ] **Step 1: Write failing static contract tests.**

Create tests/test_repro_runner_cutover_contract.py:

~~~python
from pathlib import Path


SCRIPT = Path("scripts/switch_repro_runner.ps1")


def test_cutover_script_has_read_only_job_guard_and_reversible_steps() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for required in (
        "queued",
        "running",
        "sqlite3",
        "docker stop",
        "docker rename",
        "repro-runner-legacy-",
        "docker network disconnect",
        "docker network connect",
        "docker compose up -d --no-deps repro-runner",
        "/healthz",
        "REPRO_RUNNER_GIT_COMMIT",
        "REPRO_RUNNER_WORKFLOW_VERSION",
        "-Rollback",
    ):
        assert required in source


def test_cutover_script_never_uses_broad_data_deletion() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()
    assert "docker volume rm" not in source
    assert "docker system prune" not in source
    assert "remove-item" not in source
~~~

Extend tests/test_runtime_build_contract.py:

~~~python
def test_build_helper_exports_commit_and_workflow_version() -> None:
    source = Path("scripts/build_repro_runner.ps1").read_text(encoding="utf-8")
    assert "$env:REPRO_RUNNER_GIT_COMMIT" in source
    assert "$env:REPRO_RUNNER_WORKFLOW_VERSION" in source
    assert "docker compose build repro-runner" in source
~~~

- [ ] **Step 2: Run the focused tests and verify the expected failure.**

~~~powershell
python -m pytest -q tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py
~~~

Expected: collection or assertion failure because the cutover script does not yet exist.

- [ ] **Step 3: Implement the script command wrapper and preflight.**

Use these functions at the top of the script after parameter parsing:

~~~powershell
param(
    [string]$WorkflowVersion = "multimodel-0.8.0",
    [switch]$SkipBuild,
    [switch]$Rollback,
    [string]$ContainerName = "repro-runner"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$networkName = "docker_default"

function Invoke-DockerChecked {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $output = & docker @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed: $output"
    }
    return $output
}

function Get-RunnerContainerJson {
    param([Parameter(Mandatory)][string]$Name)
    $raw = Invoke-DockerChecked @("inspect", $Name)
    return (($raw -join [Environment]::NewLine) | ConvertFrom-Json)[0]
}

function Get-ActiveJobs {
    param([Parameter(Mandatory)][string]$Name)
    $query = @'
import json
import sqlite3

with sqlite3.connect("/data/experiments/jobs.sqlite3") as connection:
    rows = connection.execute(
        "SELECT job_id, status FROM jobs WHERE status IN (?, ?)",
        ("queued", "running"),
    ).fetchall()
print(json.dumps([{"job_id": job_id, "status": status} for job_id, status in rows]))
'@
    $raw = Invoke-DockerChecked @("exec", $Name, "python", "-c", $query)
    return (($raw -join [Environment]::NewLine) | ConvertFrom-Json)
}

function Assert-NoActiveJobs {
    param([Parameter(Mandatory)][string]$Name)
    $active = @(Get-ActiveJobs $Name)
    if ($active.Count -gt 0) {
        $summary = ($active | ForEach-Object { "$($_.job_id):$($_.status)" }) -join ", "
        throw "runner has active jobs; cutover aborted: $summary"
    }
}
~~~

The query is read-only and returns job IDs/statuses only. If the SQLite file or container is unavailable, the wrapper must throw before any mutation.

- [ ] **Step 4: Run the contract tests and verify the preflight strings are present.**

~~~powershell
python -m pytest -q tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py
~~~

Expected: tests still fail only for the not-yet-implemented forward/rollback command body.

### Task 2: Implement build, smoke, and forward cutover

**Files:**
- Modify: scripts/switch_repro_runner.ps1
- Modify: tests/test_repro_runner_cutover_contract.py

**Interfaces:**
- The script uses scripts/build_repro_runner.ps1 -WorkflowVersion $WorkflowVersion unless -SkipBuild is supplied.
- The expected commit is the validated git rev-parse HEAD value; the expected workflow version is the parameter default multimodel-0.8.0.
- The temporary smoke container is removed in a finally block and never receives the production protocol secret.

- [ ] **Step 1: Add failing assertions for image smoke and mount/alias verification.**

Add to the contract test:

~~~python
def test_cutover_script_validates_image_before_and_container_after_switch() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for required in (
        "docker run",
        "Invoke-RestMethod",
        "source_digest",
        "expectedCommit",
        "workflow_version",
        "/data/experiments",
        "Aliases",
        "127.0.0.1",
    ):
        assert required in source
~~~

- [ ] **Step 2: Run the focused test and verify the expected failure.**

~~~powershell
python -m pytest -q tests/test_repro_runner_cutover_contract.py
~~~

Expected: failure for the missing image-smoke and post-cutover checks.

- [ ] **Step 3: Implement commit resolution and image smoke.**

Use this sequence before stopping the old container:

~~~powershell
$expectedCommit = (& git -C $projectRoot rev-parse HEAD 2>$null).Trim()
if ($expectedCommit -notmatch "^[0-9a-f]{7,64}$") {
    throw "could not resolve a valid Git commit"
}

if (-not $SkipBuild) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot "scripts/build_repro_runner.ps1") -WorkflowVersion $WorkflowVersion
    if ($LASTEXITCODE -ne 0) { throw "runner image build failed" }
}

$imageId = (Invoke-DockerChecked @("compose", "images", "-q", "repro-runner") | Select-Object -Last 1).Trim()
if ($imageId -notmatch "^sha256:") { throw "Compose did not produce a runner image" }

$smokeName = "repro-runner-provenance-$PID"
$smokePort = 18081
try {
    Invoke-DockerChecked @("run", "-d", "--name", $smokeName, "-p", "127.0.0.1:$($smokePort):8001", $imageId) | Out-Null
    $smoke = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $smoke = Invoke-RestMethod "http://127.0.0.1:$($smokePort)/healthz"
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if ($null -eq $smoke -or $smoke.status -ne "ok" -or $smoke.git_commit -ne $expectedCommit -or $smoke.workflow_version -ne $WorkflowVersion) {
        throw "new runner image failed provenance smoke check"
    }
} finally {
    & docker rm -f $smokeName 2>$null | Out-Null
}
~~~

Keep the smoke container outside docker_default; it only tests the image health contract.

- [ ] **Step 4: Implement the guarded forward transition and post-check.**

Use this mutation order after Assert-NoActiveJobs and image smoke pass:

~~~powershell
$old = Get-RunnerContainerJson $ContainerName
$legacyName = "repro-runner-legacy-$(Get-Date -Format yyyyMMdd-HHmmss)"
$oldStopped = $false
$oldRenamed = $false
$newStarted = $false

try {
    Invoke-DockerChecked @("stop", $ContainerName) | Out-Null
    $oldStopped = $true
    Invoke-DockerChecked @("rename", $ContainerName, $legacyName) | Out-Null
    $oldRenamed = $true
    Invoke-DockerChecked @("network", "disconnect", $networkName, $legacyName) | Out-Null

    Push-Location $projectRoot
    try {
        Invoke-DockerChecked @("compose", "up", "-d", "--no-deps", "repro-runner") | Out-Null
    } finally {
        Pop-Location
    }
    $newStarted = $true

    $health = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:8001/healthz"
            if ($health.status -eq "ok") { break }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if ($null -eq $health -or $health.status -ne "ok" -or $health.git_commit -ne $expectedCommit -or $health.workflow_version -ne $WorkflowVersion) {
        throw "active runner failed provenance health check"
    }

    $active = Get-RunnerContainerJson $ContainerName
    $mounts = @($active.Mounts | ForEach-Object { $_.Destination })
    if ($mounts -notcontains "/data/experiments") { throw "active runner lost /data/experiments mount" }
    $aliases = @($active.NetworkSettings.Networks.$networkName.Aliases)
    if ($aliases -notcontains "repro-runner") { throw "active runner lost repro-runner network alias" }
    Write-Host "Runner cutover succeeded; legacy container retained as $legacyName."
} catch {
    if ($newStarted) { & docker rm -f $ContainerName 2>$null | Out-Null }
    if ($oldRenamed) {
        Invoke-DockerChecked @("rename", $legacyName, $ContainerName) | Out-Null
        Invoke-DockerChecked @("network", "connect", "--alias", "repro-runner", $networkName, $ContainerName) | Out-Null
    } elseif ($oldStopped) {
        Invoke-DockerChecked @("start", $ContainerName) | Out-Null
    }
    throw
}
~~~

If the old container was already disconnected from docker_default, handle only that specific Docker error as nonfatal; every other disconnect error aborts before the rename. Never remove legacyName in the forward path.

- [ ] **Step 5: Run focused tests and a non-mutating script parse check.**

~~~powershell
python -m pytest -q tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py
powershell.exe -NoProfile -Command "[System.Management.Automation.Language.Parser]::ParseFile('scripts/switch_repro_runner.ps1',[ref]$null,[ref]$null) | Out-Null"
~~~

Expected: pytest passes and PowerShell reports no parse error.

- [ ] **Step 6: Commit the guarded forward cutover script.**

~~~powershell
git add scripts/switch_repro_runner.ps1 tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py
git commit -m "feat: add guarded repro runner cutover"
~~~

### Task 3: Implement rollback and operator documentation

**Files:**
- Modify: scripts/switch_repro_runner.ps1
- Modify: tests/test_repro_runner_cutover_contract.py
- Modify: docs/configuration-guide.md

- [ ] **Step 1: Add failing rollback assertions.**

~~~python
def test_rollback_requires_no_active_jobs_and_restores_alias() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "Sort-Object Name -Descending" in source
    assert "docker rm -f" in source
    assert "docker rename" in source
    assert "--alias" in source
    assert "Assert-NoActiveJobs" in source
~~~

- [ ] **Step 2: Run the focused test and verify the expected failure.**

~~~powershell
python -m pytest -q tests/test_repro_runner_cutover_contract.py
~~~

Expected: failure for the missing -Rollback branch.

- [ ] **Step 3: Implement rollback with the same active-job guard.**

The rollback branch must:

1. Resolve the newest repro-runner-legacy-* container using docker ps -a --format '{{.Names}}' | Where-Object ... | Sort-Object Name -Descending | Select-Object -First 1.
2. Run Assert-NoActiveJobs against the current repro-runner before stopping it.
3. Stop and remove only the current replacement container.
4. Rename the legacy container back to repro-runner.
5. Reconnect docker_default with alias repro-runner.
6. Poll /healthz and print only status, service version, commit, source digest, and workflow version.

If no legacy container exists, exit nonzero without mutation. Do not remove experiment data or touch any other container.

- [ ] **Step 4: Document the operator flow.**

Add to docs/configuration-guide.md:

~~~powershell
# Build, preflight, and switch. The command aborts if a job is queued/running.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\switch_repro_runner.ps1

# Roll back to the retained legacy container after confirming no active job.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\switch_repro_runner.ps1 -Rollback
~~~

Document that needs_retry data remains in /data/experiments and can resume after a healthy replacement, while queued/running blocks the cutover.

- [ ] **Step 5: Run focused tests and commit rollback/docs.**

~~~powershell
python -m pytest -q tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py
git diff --check
git add scripts/switch_repro_runner.ps1 tests/test_repro_runner_cutover_contract.py docs/configuration-guide.md
git commit -m "docs: add repro runner rollback procedure"
~~~

### Task 4: Execute the live cutover and verify provenance

**Files:**
- No source files; operate only on the existing repro-runner container and its retained legacy name.
- Verification output must be stored only in the task report, without secrets or raw inputs.

- [ ] **Step 1: Snapshot the current state without secrets.**

~~~powershell
docker inspect repro-runner --format '{{.Config.Image}}|{{.State.Status}}|{{range .Mounts}}{{println .Destination}}{{end}}'
Invoke-RestMethod http://localhost:8001/healthz
~~~

Record image, status, mount destinations, service version, source digest, commit, and workflow version only.

- [ ] **Step 2: Run the guarded cutover.**

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\switch_repro_runner.ps1 -WorkflowVersion multimodel-0.8.0
~~~

Expected: a retained repro-runner-legacy-* container, a healthy repro-runner, the expected current commit, and the /data/experiments mount/alias.

- [ ] **Step 3: Run a minimal API smoke test against the active service.**

~~~powershell
Invoke-RestMethod http://localhost:8001/healthz
python -m pytest -q tests/repro_runner/test_api.py tests/repro_runner/test_job_api.py
~~~

Expected: health provenance matches the image build and focused API tests pass.

- [ ] **Step 4: Verify rollback readiness without executing rollback.**

~~~powershell
docker ps -a --filter "name=repro-runner-legacy-" --format '{{.Names}}|{{.Status}}'
docker inspect repro-runner --format '{{range .Mounts}}{{println .Destination}}{{end}}'
~~~

Expected: at least one retained legacy container and the active /data/experiments mount.

### Task 5: Final verification

- [ ] **Step 1: Run the full repository tests.**

~~~powershell
python -m pytest -q
~~~

Expected: the existing full suite remains green.

- [ ] **Step 2: Validate Compose configuration and image metadata.**

~~~powershell
docker compose config --quiet
docker image inspect paper-repro-agent-repro-runner --format '{{.Config.User}}|{{json .Config.Env}}'
~~~

Expected: Compose validates, the image user is app, and the image environment contains the workflow version and current commit without any secret value.

- [ ] **Step 3: Inspect the final diff.**

~~~powershell
git diff --check
git status --short
~~~

Confirm only the planned files were changed by this work; preserve unrelated user changes and do not clean temporary artifacts owned by the user.
