# Task 1 Report

Status: complete

Implemented:
- Added `scripts/switch_repro_runner.ps1` with the required PowerShell parameters, `$ErrorActionPreference = "Stop"`, `$projectRoot` resolution, `docker_default` network constant, and the read-only preflight helper functions.
- Added `tests/test_repro_runner_cutover_contract.py` with the requested static contract assertions and deletion guard checks.
- Extended `tests/test_runtime_build_contract.py` with the requested build-helper contract assertions.

Verification:
- `python -m pytest -q tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py`
- `powershell.exe -NoProfile -Command '$tokens = $null; $errors = $null; [System.Management.Automation.Language.Parser]::ParseFile("scripts/switch_repro_runner.ps1",[ref]$tokens,[ref]$errors) | Out-Null'`
- `git diff --check -- scripts/switch_repro_runner.ps1 tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py`

Notes:
- The cutover script intentionally stops at the Task 1 preflight/contract boundary and does not implement the later build, smoke, mutation, or rollback bodies.
- Unrelated dirty files in the worktree were left untouched.

Concerns:
- None for Task 1 scope. Later tasks still need the actual cutover and rollback bodies.

## Fix Round 1

Reason for fix:
- The reviewed range needed to match the brief exactly. `tests/test_runtime_build_contract.py` had extra Dockerfile/compose assertions outside the requested Task 1 scope, and the helper script read by the test needed to be included in the reviewed change set.

Changes:
- Reduced `tests/test_runtime_build_contract.py` to only the required build-helper contract assertions.
- Kept `scripts/build_repro_runner.ps1` in the Task 1 change set without modifying `Dockerfile.repro` or `compose.yaml`.

Commands and outputs:
- `python -m pytest -q tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py`
  - `5 passed in 0.12s`
- `powershell.exe -NoProfile -Command '$tokens = $null; $errors = $null; [System.Management.Automation.Language.Parser]::ParseFile("scripts/switch_repro_runner.ps1",[ref]$tokens,[ref]$errors) | Out-Null'`
  - no parse errors
- `git diff --check -- tests/test_runtime_build_contract.py scripts/switch_repro_runner.ps1 scripts/build_repro_runner.ps1`
  - no diff-check errors

Notes:
- The unrelated dirty files in the worktree were left untouched.

## Fix Round 2

Reason for fix:
- The runtime build contract still included two tests that read `Dockerfile.repro` and `compose.yaml`, which kept the reviewed range dependent on dirty provenance files outside the Task 1 brief.

Changes:
- Removed `test_repro_dockerfile_declares_and_exports_build_identity`.
- Removed `test_compose_passes_runtime_identity_to_repro_build`.
- Removed the now-unused `yaml` import.

Commands and outputs:
- `python -m pytest -q tests/test_repro_runner_cutover_contract.py tests/test_runtime_build_contract.py`
  - `3 passed in 0.09s`
- `powershell.exe -NoProfile -Command '$tokens = $null; $errors = $null; [System.Management.Automation.Language.Parser]::ParseFile("scripts/switch_repro_runner.ps1",[ref]$tokens,[ref]$errors) | Out-Null'`
  - no parse errors
- `git diff --check -- tests/test_runtime_build_contract.py scripts/build_repro_runner.ps1 scripts/switch_repro_runner.ps1 tests/test_repro_runner_cutover_contract.py`
  - no diff-check errors

Notes:
- `Dockerfile.repro` and `compose.yaml` were not modified.
- Unrelated dirty files remain untouched.
