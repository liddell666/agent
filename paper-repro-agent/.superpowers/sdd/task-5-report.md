# Task 5 report: Dify V3 workflow contract

## Status

Implemented the V3 Dify workflow contract, stable dossier fixture, host and
container-capable PowerShell smoke script, and configuration-guide additions.
The workflow keeps strict comparability separate from deterministic approximate
similarity and does not route CSV rows through prompts, logs, or output.

## Commits

- `22d7ef8 docs: define dify paper comparison workflow`

## Validation

- PowerShell AST parse of `scripts/smoke_comparison.ps1`: no parse errors.
- Fixture JSON validation with `ConvertFrom-Json`: passed.
- `git diff --check`: clean before commit.
- `& .\.venv312\Scripts\python.exe -m pytest -q`: 156 passed; one existing
  Starlette/httpx deprecation warning.

## Files

- `dify/paper-comparison-workflow.md`
- `tests/fixtures/minimal-paper-dossier.json`
- `scripts/smoke_comparison.ps1`
- `docs/configuration-guide.md`

## Concerns

- The smoke script was syntax-checked but was not run against a user CSV or a
  live Docker service in this task.
- V3 creation, publication, and live success/failure-path verification remain
  Task 6 activities; V2 remains published and unchanged.
