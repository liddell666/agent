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

## Review fixes (2026-08-10)

### Status

Resolved every HIGH and MEDIUM item from `task-5-review.md`.

### Commit

- `e005792 fix: complete v3 workflow failure contracts`

### Changes

- The smoke script now resets its default dossier path after parameter binding,
  so the documented `powershell.exe -File` command resolves it reliably.
- Container fallback uses GUID-specific `/tmp` paths, checks that
  `repro-runner` is actually running, enters `try/finally` before copying, and
  removes both possible copied paths as container root.
- V3 now has standalone validation and experiment HTTP-response parser nodes;
  IF conditions and downstream bindings use their normalized JSON Strings.
- All six semantic failure branches include standalone copyable normalizers,
  exact bindings, and six String terminal values without dereferencing skipped
  nodes.

### Validation

- PowerShell AST parse: passed.
- Exact documented invocation path preflight: passed; it resolved the default
  dossier and reached the parse request instead of failing on the path.
- Forced container-fallback exercise: passed; no GUID temporary files remained.
- Fixture JSON parse: passed.
- Embedded Dify Python fences: 20 parsed with Python AST.
- Full suite: `156 passed`, with one existing Starlette/httpx deprecation
  warning.
- `git diff --check`: clean before the fix commit.

### Remaining concern

The current unreconstructed live container returns 404 for `/v1/parse-dossier`,
so the smoke's complete live request sequence remains a Task 6 verification.
