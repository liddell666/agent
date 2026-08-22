# Repro Runner Immutable Image Cutover Design

## Problem

`switch_repro_runner.ps1 -SkipBuild` resolves and smoke-tests the correct runner image, but the forward cutover starts Compose under a fresh project name. Because the `repro-runner` service has only a `build` declaration in the base Compose file, Compose derives a new project-specific image name. With `--no-build`, that image does not exist, so cutover fails after the old runner has been stopped and renamed.

The failure path retains the old container and data, but the attempted automatic restoration can also fail when the replacement container was never created. This leaves the service requiring a manual rename, network reconnect, and restart.

## Goals

- Start the replacement from the exact immutable image ID that passed the provenance smoke test.
- Preserve the fresh Compose project boundary and all existing container configuration.
- Keep the active-job and data-source guards ahead of mutation.
- Restore the retained legacy container automatically if replacement creation fails before a new container exists.
- Keep user data, existing legacy containers, and unrelated workflow files untouched.

## Design

Add a small Compose override dedicated to the cutover image. Its `repro-runner.image` field is supplied by a process-scoped `REPRO_RUNNER_CUTOVER_IMAGE` environment variable. After `Resolve-ReproRunnerImageId` returns an immutable `sha256:` ID and the smoke test succeeds, the switch script will set this variable and include the image override in the forward `compose up` command.

The default Compose development and build behavior remains unchanged because the override is used only by the switch script. The replacement therefore uses the exact verified image regardless of the temporary Compose project name.

The restoration path will distinguish between “replacement may have been requested” and “replacement container actually exists.” It will remove `repro-runner` only when inspection confirms that a replacement exists, then rename and restart the retained legacy container. Missing replacement creation must not prevent restoration.

All process-scoped environment changes will be restored in `finally`, including failure paths.

## Data and Control Flow

1. Resolve expected Git commit and experiment data source.
2. Build unless `-SkipBuild` was requested.
3. Resolve the immutable runner image ID.
4. Run the isolated provenance smoke test against that image ID.
5. Confirm no queued or running jobs and verify the live data mount.
6. Stop and rename the old runner, retaining it as legacy.
7. Set the cutover image variable and start the fresh Compose project with the image override and `--no-build`.
8. Verify health provenance, data mount, network membership, and `repro-runner` alias.
9. On failure, remove an actual replacement if present and restore the retained legacy runner.

## Testing

Contract regressions will first demonstrate that the fresh-project command lacks an immutable image override and that restoration incorrectly assumes a replacement exists. The implementation will then make those tests pass.

Focused verification will cover the cutover, build, Compose, and external-data-source contracts. Operational verification will rerun the guarded cutover with `-SkipBuild`, then inspect the live container's image, source/data mounts, health provenance, network alias, and persisted job counts.

## Non-Goals

- Publishing or modifying either Dify app.
- Changing experiment data or job lifecycle semantics.
- Removing historical legacy containers.
- Refactoring general Compose configuration outside the runner cutover.
