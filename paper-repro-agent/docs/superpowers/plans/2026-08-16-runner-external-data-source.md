# Runner External Data Source Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

Goal: Add an opt-in -ExperimentDataSource cutover path that runs the reviewed current runner code against the existing live experiment-data directory while preserving preflight and rollback guards.

Architecture: Keep the user's dirty compose.yaml unchanged. Add compose.runner-data-source.yaml to override only the /data/experiments bind source through REPRO_RUNNER_CUTOVER_DATA_SOURCE. Extend both PowerShell helpers to use the same base-plus-override Compose file set; the switch script validates the directory, restores the process environment on exit, and keeps the existing reversible container flow.

Tech Stack: PowerShell, Docker CLI/Compose, Python contract tests, pytest, FastAPI /healthz.

## Global Constraints

- Work only in codex/multi-model-cv; preserve unrelated dirty files.
- Do not modify the user's current uncommitted compose.yaml changes.
- -ExperimentDataSource must be an existing absolute directory; never copy, move, delete, or rewrite experiment data.
- Compose build, config, image lookup, and up use the same effective file set when the option is supplied.
- Preserve the active-job guard, /data/experiments mount, docker_default membership, and repro-runner alias.
- Retain the old container as repro-runner-legacy-*; do not delete it or the experiment-data directory.
- On invalid health or post-mutation verification, remove only the replacement and restore the legacy container/name and alias.
- Do not print secret environment values, CSV rows, PDF text, protocol tokens, or full request payloads.

---

### Task 1: Add the opt-in Compose data-source override to the build path

Files:
- Create: compose.runner-data-source.yaml
- Modify: scripts/build_repro_runner.ps1
- Modify: tests/test_runtime_build_contract.py
- Create: tests/test_repro_runner_data_source_override_contract.py

Interfaces:
- The override consumes REPRO_RUNNER_CUTOVER_DATA_SOURCE and produces exactly one bind volume at /data/experiments for repro-runner.
- build_repro_runner.ps1 -ComposeOverrideFile path-value optionally adds the override to Compose; an empty value preserves the current build command.

- [ ] Step 1: Write failing contract tests.

Create tests/test_repro_runner_data_source_override_contract.py:

    from pathlib import Path


    def test_override_targets_only_runner_experiment_data() -> None:
        source = Path("compose.runner-data-source.yaml").read_text(encoding="utf-8")
        assert "repro-runner:" in source
        assert "REPRO_RUNNER_CUTOVER_DATA_SOURCE" in source
        assert "target: /data/experiments" in source
        assert "target: /app/src" not in source


    def test_build_helper_accepts_optional_compose_override() -> None:
        source = Path("scripts/build_repro_runner.ps1").read_text(encoding="utf-8")
        assert "ComposeOverrideFile" in source
        assert '"-f"' in source
        assert "docker compose" in source
        assert "build repro-runner" in source

Add to tests/test_runtime_build_contract.py:

    def test_build_helper_keeps_empty_override_compatible() -> None:
        source = Path("scripts/build_repro_runner.ps1").read_text(encoding="utf-8")
        assert '[string]$ComposeOverrideFile = ""' in source
        assert "if (-not [string]::IsNullOrWhiteSpace($ComposeOverrideFile))" in source

- [ ] Step 2: Run the focused tests and verify the expected failure.

    python -m pytest -q tests/test_runtime_build_contract.py tests/test_repro_runner_data_source_override_contract.py

Expected: failure because the override file and optional build parameter do not exist.

- [ ] Step 3: Create compose.runner-data-source.yaml.

Use exactly:

    services:
      repro-runner:
        volumes:
          - type: bind
            source: "${REPRO_RUNNER_CUTOVER_DATA_SOURCE:?REPRO_RUNNER_CUTOVER_DATA_SOURCE must be set when using this override}"
            target: /data/experiments

- [ ] Step 4: Extend scripts/build_repro_runner.ps1.

Keep the current commit/workflow environment setup. Add [string]$ComposeOverrideFile = "" to param. Before Push-Location, build @("-f", (Join-Path $projectRoot "compose.yaml"), "-f", $overridePath) only when the parameter is nonblank, where $overridePath = (Resolve-Path -LiteralPath $ComposeOverrideFile -ErrorAction Stop).Path. Invoke & docker compose @composeArguments build repro-runner; leave @composeArguments empty for the no-override path.

- [ ] Step 5: Run focused tests and base Compose syntax validation.

    python -m pytest -q tests/test_runtime_build_contract.py tests/test_repro_runner_data_source_override_contract.py
    docker compose -f .\compose.yaml config --quiet

Expected: focused tests pass and base Compose exits 0. Do not include the override in this check until the switch script sets its required variable.

- [ ] Step 6: Commit the build/override unit.

    git add compose.runner-data-source.yaml scripts/build_repro_runner.ps1 tests/test_runtime_build_contract.py tests/test_repro_runner_data_source_override_contract.py
    git commit -m "feat: add runner data source compose override"

### Task 2: Add explicit source validation and unified Compose plumbing to the switch script

Files:
- Modify: scripts/switch_repro_runner.ps1
- Modify: tests/test_repro_runner_cutover_contract.py

Interfaces:
- switch_repro_runner.ps1 -ExperimentDataSource absolute-existing-directory enables the override for forward cutover.
- Resolve-ExperimentDataSource([string]$Path) returns a normalized host path or throws before any build or Docker mutation.
- Invoke-ComposeChecked([string[]]$Arguments) prefixes the effective Compose file arguments to every Compose command.

- [ ] Step 1: Add failing contract assertions.

Extend the cutover contract test with:

    def test_explicit_data_source_is_validated_before_build() -> None:
        source = _script_source()
        for required in (
            "ExperimentDataSource",
            "Resolve-ExperimentDataSource",
            "REPRO_RUNNER_CUTOVER_DATA_SOURCE",
            "compose.runner-data-source.yaml",
            "ComposeOverrideFile",
            "composeFileArguments",
            "IsPathFullyQualified",
            "PSIsContainer",
        ):
            assert required in source


    def test_compose_wrapper_uses_effective_file_arguments() -> None:
        source = _script_source()
        wrapper = _between(source, "function Invoke-ComposeChecked {", "function Resolve-ReproRunnerImageId {")
        assert "($composeFileArguments + $Arguments)" in wrapper

- [ ] Step 2: Run the cutover tests and verify the new assertions fail.

    python -m pytest -q tests/test_repro_runner_cutover_contract.py

Expected: existing safety assertions pass while the new interface assertions fail.

- [ ] Step 3: Add the parameter and script-scope Compose paths.

Add [string]$ExperimentDataSource = "" to the existing parameter block, then add:

    $composeBaseFile = Join-Path $projectRoot "compose.yaml"
    $composeOverrideFile = Join-Path $projectRoot "compose.runner-data-source.yaml"
    $composeFileArguments = @("-f", $composeBaseFile)
    $composeDataSourceEnvName = "REPRO_RUNNER_CUTOVER_DATA_SOURCE"

After Normalize-HostPath, add:

    function Resolve-ExperimentDataSource {
        param([Parameter(Mandatory)][string]$Path)

        if (-not [System.IO.Path]::IsPathFullyQualified($Path)) {
            throw "runner data source must be an absolute host directory"
        }

        $item = Get-Item -LiteralPath $Path -ErrorAction Stop
        if (-not [bool]$item.PSIsContainer) {
            throw "runner data source must be a directory"
        }

        return Normalize-HostPath -Path $item.FullName
    }

- [ ] Step 4: Prefix every Compose call with the effective file set.

Change the wrapper's Docker call to:

    return Invoke-DockerChecked -Arguments (@("compose") + $composeFileArguments + $Arguments)

Before the forward branch, save the prior environment value. For non-rollback invocations with a nonblank parameter, resolve the path, set $env:REPRO_RUNNER_CUTOVER_DATA_SOURCE, mark the environment as configured, and append @("-f", $composeOverrideFile) to $composeFileArguments. If both -Rollback and -ExperimentDataSource are supplied, throw before inspecting containers.

- [ ] Step 5: Resolve effective Compose data source before build and pass the override to the child build.

Before $expectedCommit, call:

    $expectedDataSource = Get-ComposeExperimentDataSource
    $buildArguments = @("-WorkflowVersion", $WorkflowVersion)
    if ($dataSourceEnvConfigured) {
        $buildArguments += @("-ComposeOverrideFile", $composeOverrideFile)
    }

Invoke the child as & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot "scripts/build_repro_runner.ps1") @buildArguments. Keep the current live mount comparison against $expectedDataSource before docker stop; the explicit parameter must not bypass that guard.

- [ ] Step 6: Restore the prior data-source environment value on every forward exit.

Wrap the forward path in try/finally. In finally, if the environment was configured, remove Env:REPRO_RUNNER_CUTOVER_DATA_SOURCE when no previous value existed; otherwise restore the saved value with Set-Item. Keep rollback before this setup so rollback does not depend on Compose override state.

- [ ] Step 7: Run focused tests and a read-only override config check.

    python -m pytest -q tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py tests/test_repro_runner_data_source_override_contract.py
    $env:REPRO_RUNNER_CUTOVER_DATA_SOURCE = 'C:\Users\17716\Documents\arcgis\paper-repro-agent\data\experiments'
    docker compose -f .\compose.yaml -f .\compose.runner-data-source.yaml config --format json | ConvertFrom-Json | Out-Null
    Remove-Item Env:REPRO_RUNNER_CUTOVER_DATA_SOURCE -ErrorAction SilentlyContinue

Expected: focused tests pass, Compose exits 0, and no container state changes.

- [ ] Step 8: Commit the switch-script unit.

    git add scripts/switch_repro_runner.ps1 tests/test_repro_runner_cutover_contract.py
    git commit -m "feat: allow guarded runner cutover with external data source"

### Task 3: Document operator usage and preserve the safety contract

Files:
- Modify: docs/configuration-guide.md
- Modify: tests/test_repro_runner_cutover_contract.py

Interfaces:
- The guide documents the default guarded command, the explicit old-root data-source command, and the unchanged rollback command.
- The guide states that the source is compared before stop/rename and that the directory is not copied or deleted.

- [ ] Step 1: Add the failing documentation contract.

    def test_configuration_guide_documents_explicit_data_source_cutover() -> None:
        guide = Path("docs/configuration-guide.md").read_text(encoding="utf-8")
        assert "-ExperimentDataSource" in guide
        assert "paper-repro-agent\\data\\experiments" in guide
        assert "does not copy" in guide.lower()
        assert "does not delete" in guide.lower()

- [ ] Step 2: Run the documentation test and verify the expected failure.

    python -m pytest -q tests/test_repro_runner_cutover_contract.py -k configuration_guide

Expected: failure because the explicit command is not documented yet.

- [ ] Step 3: Add the explicit command and safety notes to section 9.

Insert after the default forward command as a single-line PowerShell command:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\switch_repro_runner.ps1 -ExperimentDataSource 'C:\Users\17716\Documents\arcgis\paper-repro-agent\data\experiments'

State that the effective Compose source is compared with the live /data/experiments bind source before stop/rename and that the script does not copy or delete the directory. Keep the rollback command unchanged.

- [ ] Step 4: Run focused tests and diff validation.

    python -m pytest -q tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py tests/test_repro_runner_data_source_override_contract.py
    git diff --check

Expected: all focused tests pass and diff check exits 0.

- [ ] Step 5: Commit the documentation unit.

    git add docs/configuration-guide.md tests/test_repro_runner_cutover_contract.py
    git commit -m "docs: explain external runner data source cutover"

### Task 4: Verify the guarded path and perform the live cutover only after read-only gates

Files:
- Verify: scripts/switch_repro_runner.ps1
- Verify: compose.runner-data-source.yaml
- Verify: docs/configuration-guide.md
- Update report: .superpowers/sdd/2026-08-15-runner-cutover/task-4-report.md

Interfaces:
- Forward: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\switch_repro_runner.ps1 -ExperimentDataSource 'C:\Users\17716\Documents\arcgis\paper-repro-agent\data\experiments'.
- Rollback: powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\switch_repro_runner.ps1 -Rollback.

- [ ] Step 1: Run full tests, base Compose validation, and diff check before Docker mutation.

    python -m pytest -q
    docker compose -f .\compose.yaml config --quiet
    git diff --check

Expected: full suite passes, Compose exits 0, and diff check exits 0.

- [ ] Step 2: Perform an aggregate-only read-only preflight.

Record only container state, source equality, health/provenance fields, alias status, and aggregate job counts. Confirm the explicit source resolves to C:\Users\17716\Documents\arcgis\paper-repro-agent\data\experiments. Abort if the runner is unhealthy, any job is queued or running, source equality fails, or the override resolves anything other than one /data/experiments target.

- [ ] Step 3: Run guarded forward cutover.

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\switch_repro_runner.ps1 -ExperimentDataSource 'C:\Users\17716\Documents\arcgis\paper-repro-agent\data\experiments'

Expected: active health is ok, commit/workflow/source digest are valid, /data/experiments, docker_default, and alias are present, and the old container remains under repro-runner-legacy-*.

- [ ] Step 4: Run API smoke and aggregate job verification.

    python -m pytest -q tests/repro_runner/test_api.py tests/repro_runner/test_job_api.py

Expected: API/job smoke passes and aggregate job state is unchanged from preflight.

- [ ] Step 5: Verify rollback and restored source.

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\switch_repro_runner.ps1 -Rollback

Expected: the legacy container is restored as repro-runner, health is ok, the original data source and alias are present, and aggregate jobs match preflight.

- [ ] Step 6: Record the verification report without raw inputs and leave unrelated files untouched.

Record commit IDs, pass counts, provenance, mount equality, alias status, aggregate job counts, and any limitation. Do not record secrets, raw request/response bodies, CSV/PDF text, or full job rows. Verify with git status --short; do not run reset, checkout, clean, volume deletion, or data-directory deletion.

## Self-review checklist

- Spec coverage: parameter, override, unified Compose commands, validation, environment cleanup, unchanged base Compose, documentation, tests, full suite, live guard, forward cutover, and rollback each have a task.
- Placeholder scan: no unfinished marker or unspecified implementation step appears in this plan.
- Type consistency: -ExperimentDataSource, REPRO_RUNNER_CUTOVER_DATA_SOURCE, compose.runner-data-source.yaml, Resolve-ExperimentDataSource, ComposeOverrideFile, and composeFileArguments use the same names in every task.
