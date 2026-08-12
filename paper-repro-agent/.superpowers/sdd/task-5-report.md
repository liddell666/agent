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

## Transport re-review fixes (2026-08-10)

### Status

Resolved both Windows PowerShell 5.1 transport findings.

### Commit

- `f02f60e fix: harden smoke transport on Windows`

### Changes and validation

- Host `compare-result` JSON is written as UTF-8 bytes to a GUID temporary
  file and sent with curl `--data-binary @file`; the file is removed in a
  `finally` block.
- Container requests use a GUID-specific Python client file copied into the
  container. The request is provided as Base64 through an environment variable,
  so no quoted Python source or JSON is passed as a native PowerShell argument.
  The client, dossier, CSV, and local client file are all removed in `finally`.
- A controlled endpoint that rejects malformed comparison JSON passed both a
  Windows PowerShell host run and a forced-container run; each returned the
  normalized `mock-exp` comparison summary.
- PowerShell AST parse, all embedded Dify Python snippets, and `git diff
  --check` passed. Full tests: `156 passed`, with one existing deprecation
  warning.

## Task 5 - merged workflow operator guide (2026-08-13)

- Added `dify/paper-comparison-merged-workflow.md` as the executable Dify import,
  prepare/run, protocol lifecycle, output, error-action, safety, and rollback
  guide.
- Preserved the two original prepare and multi-model workflows as rollback
  targets and documented the shared secret names without recording a value.
- Preserved the pre-existing report content above; this section is the scoped
  merged-workflow documentation update.
- Checks: required placeholder scan returned no matches; the merged DSL contract
  test passed (`10 passed`); scoped `git diff --check` reported no whitespace
  errors.
- Full-suite attempt: `384 passed, 4 failed`; the failures are in pre-existing
  dirty implementation/workflow files outside this documentation-only scope.
