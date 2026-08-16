# Runner external data-source cutover design

## Status

Proposed after approval of approach A. This document is the design checkpoint
for adding an explicit external experiment-data source to the guarded runner
cutover. It does not change the active runner or any experiment data.

## Context

The guarded cutover script currently resolves `/data/experiments` from the
current Compose project directory and compares that source with the live
container before stopping anything. That protection correctly aborted the last
forward attempt: the live runner uses
`C:\\Users\\17716\\Documents\\arcgis\\paper-repro-agent\\data\\experiments`,
while the current worktree's Compose configuration resolves the source under
the multi-model worktree.

The current implementation branch is 70 commits ahead of the live `master`
root. The original project root also contains unrelated uncommitted work, so
the cutover must not overwrite that root or modify its Compose file. The
desired result is to run the reviewed current code while retaining the live
experiment-data directory and the existing container/network contracts.

## Goals

1. Allow an operator to pass an explicit, existing absolute host directory for
   `/data/experiments`.
2. Make Compose configuration, image resolution, and service startup use the
   same data-source override.
3. Preserve the current no-argument behavior and its pre-mutation mismatch
   guard.
4. Keep the old data directory and legacy container intact and rollback-safe.
5. Verify the explicit path through contract tests, Compose resolution, and a
   live read-only guard before any real cutover.

## Non-goals

- Do not copy, move, delete, or rewrite the existing experiment data.
- Do not overwrite or reset the original project root.
- Do not modify the user's unrelated uncommitted Compose changes.
- Do not weaken the active-job, mount, health, provenance, or network-alias
  checks.
- Do not change the runner API, job schema, workflow semantics, or image build
  provenance contract.

## Design

### 1. Operator interface

Add an optional `-ExperimentDataSource` parameter to
`scripts/switch_repro_runner.ps1`.

When present, the value must be an existing absolute directory. The script
normalizes the path before any build, container stop, rename, or network
mutation. A missing path, file path, relative path, or inaccessible directory
causes an immediate error.

When omitted, the script keeps its current behavior: Compose resolves the
default `/data/experiments` source and the live source must match it. This
preserves the existing safe failure mode for accidental project-root
mismatches.

### 2. Compose override

Add the committed
`compose.runner-data-source.yaml` override file, used only when the explicit
parameter is supplied. It replaces the `repro-runner` volume whose target is
`/data/experiments` with the normalized operator path. The base
`compose.yaml` remains untouched.

The script passes the base Compose file and the override together to every
Compose operation in the forward path:

- `build` for the new runner image;
- `config` for expected mount-source resolution;
- image lookup after the build; and
- `up -d --no-deps repro-runner` for the replacement container.

The override source is supplied through the process-scoped Compose variable
`REPRO_RUNNER_CUTOVER_DATA_SOURCE`. The script restores the prior value when it
exits and does not write the path into generated DSL, image labels, source
control, or logs beyond the aggregate cutover result. The override is not used
for rollback, which restores the retained container directly.

### 3. Cutover flow

The forward path remains guarded and reversible:

1. Normalize and validate `-ExperimentDataSource`, if supplied.
2. Resolve the expected Git commit and build the current runner image with the
   effective Compose file set.
3. Resolve the image using the same Compose file set that will be used for
   startup.
4. Run the image's isolated health/provenance smoke check.
5. Inspect the live runner, query the configured job store, and abort if any
   job is queued or running.
6. Compare the live `/data/experiments` source with the effective Compose
   source. A mismatch aborts before stopping or renaming the live container.
7. Stop and rename the live container to the existing timestamped
   `repro-runner-legacy-*` form.
8. Start the replacement with the effective Compose file set.
9. Verify health, expected commit, workflow version, source digest, data
   mount, `docker_default` membership, and the `repro-runner` alias.
10. On any post-mutation failure, remove only the replacement container and
    restore the retained legacy container and alias using the existing rollback
    path.

The original old-root data source is passed explicitly as:

```powershell
powershell -File .\\scripts\\switch_repro_runner.ps1 `
  -ExperimentDataSource 'C:\\Users\\17716\\Documents\\arcgis\\paper-repro-agent\\data\\experiments'
```

### 4. Error handling and observability

Errors remain aggregate-only. The script may report normalized path equality,
container names, job counts, health fields, and pass/fail decisions. It must
not print environment secrets, raw job payloads, CSV/PDF contents, or protocol
credentials.

The explicit override is treated as an operator assertion, not as permission
to bypass the mount guard: the live source still has to equal the effective
Compose source. The active-job check runs against the live container's
configured job-store path, including the existing default for legacy
containers.

### 5. Testing and rollout

Add contract tests that prove:

- the new parameter validates an absolute existing directory;
- the override targets exactly `/data/experiments`;
- Compose operations use the same base-plus-override file set;
- no-argument behavior remains unchanged;
- a mismatched live source still aborts before stop/rename; and
- the explicit old-root source resolves to the source used by `up`.

Run the following gates in order:

1. focused runner contract tests;
2. `docker compose config --quiet` for the base file;
3. a read-only Compose resolution check with the old-root data source;
4. image build and provenance smoke check;
5. only then, a real guarded cutover followed by API smoke and job-count
   verification.

The old container remains retained after a successful cutover. Rollback is
verified independently and must restore the original data source and the
previous aggregate job state.

## Compatibility and rollback

Existing invocations without `-ExperimentDataSource` are behavior-compatible.
The new override is opt-in and affects only the replacement Compose service.
Rollback does not recreate the service and therefore does not depend on the
override path. The experiment-data directory is never deleted or rewritten by
the cutover script.

## Acceptance criteria

The implementation is complete only when:

1. Focused contract tests and the full existing suite pass.
2. The explicit old-root source resolves in Compose to the live source before
   mutation.
3. The replacement reports the expected commit, workflow version, and valid
   source digest.
4. The replacement retains `/data/experiments`, `docker_default`, and the
   `repro-runner` alias.
5. The API smoke check passes and the aggregate job state remains intact.
6. Rollback restores the legacy runner successfully.
7. `git diff --check` passes and no secret/raw-input artifact is created.
