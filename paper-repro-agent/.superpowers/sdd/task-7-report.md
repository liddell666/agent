# Task 7 report: backend final-review fixes

## Status

Implemented all backend findings in `task-7-review-fixes-backend.md` without
changing the live Dify application. The final implementation preserves the
existing no-key experiment behavior and admission limit, adds bounded retry
idempotency, and keeps unsupported paper metrics available as evidence while
excluding them from comparison requests.

## Commits

- `1403b8f fix: harden paper comparison backend`
- `aeac904 fix: make experiment retries race safe`

## Changes

- Duplicate dossier overrides apply every explicitly supplied selector before
  deciding whether a duplicate metric is unique. Unique-metric overrides retain
  their prior update semantics.
- Normalized dossier metrics now require `supported: bool`, derived from
  `SUPPORTED_METRICS`. Unsupported metrics and evidence remain in the normalized
  dossier, while the Dify comparison helper excludes them.
- Effective dossier and override limits are `min(configured, hard cap)`, with
  immutable hard caps of 5 MiB and 64 KiB respectively.
- `/v1/run-experiment` accepts an optional 1-128 character `idempotency_key`.
  The app-owned registry is globally bounded to 128 keys per process, stores
  only request fingerprints and aggregate results, and shares completed and
  in-flight state safely across event loops.
- Same-key followers wait before admission, then serialize their admitted
  upload fingerprint checks. Identical requests replay the same result and ID;
  conflicting requests return a fixed `409 idempotency_conflict`; independent
  requests retain immediate admission rejection and no-key calls remain
  independent.
- Owner work runs in the admitted request task rather than a detached task, so
  cancellation cannot release admission while parse/train/save continues.

## TDD evidence

Each finding was reproduced before its production change:

- Conflicting duplicate selectors: `1 failed, 1 passed`.
- Required support flag and Dify filtering: `3 failed`.
- Raised-settings hard-cap regressions: `2 failed`.
- Initial replay/conflict/registry regressions: `4 failed, 1 passed`.
- Keyed read outside admission: `1 failed`.
- Loop-local completed cache: `1 failed`.
- Endpoint concurrent join and owner cancellation: `2 failed`.
- Three-request fan-out with a blocked first replay read: failed with
  `[200, 200, 429]` before per-key verification serialization.

The independent review was repeated after fixes. Its final verdict reported no
remaining Critical or Important findings.

## Verification

- Focused backend/Dify suite:
  `\.venv312\Scripts\python.exe -m pytest tests\repro_runner\test_api.py tests\repro_runner\test_dossier.py tests\test_dify_code.py -q`
  — `99 passed`, one pre-existing deprecation warning.
- Full suite:
  `\.venv312\Scripts\python.exe -m pytest -q`
  — `172 passed`, one pre-existing deprecation warning.
- `git diff --check` — clean before both implementation commits.

## Concerns and follow-up

- The idempotency registry is intentionally process-local and in-memory. A
  process restart or multi-worker deployment does not preserve keys across
  processes; the current single-process service topology matches the approved
  app-owned-registry scope.
- Live Dify was not modified. A later workflow task must supply a stable
  `idempotency_key` on retrying `run-experiment` requests.
- Starlette emits the existing `httpx` TestClient deprecation warning.
- Two Windows-only transient `PermissionError` failures occurred while tests
  atomically renamed freshly written experiment directories. Both disappeared
  on rerun, production code was unchanged, and the final focused/full runs were
  green. New call-count/concurrency tests stub persistence where filesystem
  behavior is outside their assertion scope.
