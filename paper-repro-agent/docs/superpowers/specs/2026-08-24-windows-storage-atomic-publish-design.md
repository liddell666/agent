# Windows-Safe Atomic Result Storage Design

**Date:** 2026-08-24

**Status:** Approved, with Windows publication retry amendment

## Problem

`save_result(...)` and `save_suite_result(...)` currently derive a staging
directory name from the complete experiment ID. Each JSON file is then written
through another temporary name derived from the complete destination file
name. Under a long pytest `basetemp` on Windows, these nested names have
produced paths around 264 characters. Shortening those paths fixed the
deterministic boundary, but fresh complete-suite verification then exposed an
independent intermittent Windows failure: moving a newly written staging
directory can raise `PermissionError` with `WinError 5` even when the source
and destination paths are only about 150 characters long. Replacing
`Path.replace(...)` with non-overwriting `Path.rename(...)` did not change the
failure rate. Isolated reruns pass, while complete-suite runs intermittently
fail, which is consistent with a short-lived external handle on the newly
written directory tree rather than path length or destination replacement
semantics.

The fix must retain atomic publication, privacy-preserving payloads, and the
existing refusal to overwrite a result whose experiment ID already exists.

## Goals

- Make single-result save, suite-result save, and suite-result update reliable
  under the repository's normal Windows test paths.
- Keep final result directory names, JSON filenames, payload formats, public
  function signatures, and load behavior unchanged.
- Preserve same-filesystem atomic publication.
- Ensure failed writes do not publish partial results or damage an existing
  result.
- Keep duplicate experiment IDs strictly non-overwriting.

## Non-Goals

- Supporting an arbitrarily long storage root after Windows path capacity has
  already been exhausted.
- Retrying disk-space, path-length, duplicate-destination, or non-Windows
  errors.
- Changing experiment ID generation or shortening final experiment directory
  names.
- Changing stored schemas, API responses, or retention behavior.
- Modifying Dify workflows or the user's existing DSL changes.

## Architecture

### Directory publication

`save_result(...)` and `save_suite_result(...)` will use one internal directory
publication helper. The helper will:

1. Resolve and create the configured storage root.
2. Reject an already existing final experiment directory with
   `FileExistsError`.
3. Create a short, random staging directory such as `.tmp-<token>` directly
   under the storage root.
4. Write `result.json`, `config.json`, and `dataset_profile.json` directly into
   that unpublished directory.
5. Publish the complete staging directory by renaming it to the final
   experiment ID, using the bounded Windows retry policy below when required.

On Windows only, a `PermissionError` from the final directory rename will be
retried at most five additional times after delays of 10, 20, 40, 80, and 160
milliseconds. The maximum added delay is therefore 310 milliseconds. Each
attempt uses the same staging and final paths; payloads are not rewritten.
Before each retry, the helper checks whether the final directory now exists. If
it does, publication stops immediately with `FileExistsError` so a competing
writer is never mistaken for a transient access denial.

The staging directory and final directory therefore remain on the same file
system. The directory rename is the publication boundary. Per-file atomic
renames inside an unpublished directory are unnecessary and will not be used.

The helper will accept explicit payloads rather than result model types so the
single-result and suite-result public functions retain responsibility for
their existing payload builders.

### File update

`update_suite_result(...)` must update an already published `result.json`, so
it will continue to use a same-directory file replacement. Its temporary file
will use a short random basename such as `.tmp-<token>` rather than embedding
`result.json` in the temporary name. A successful replacement remains atomic;
a failed replacement leaves the old published file in place.

## Error and Concurrency Semantics

- The existing preflight existence check remains for a clear duplicate-ID
  error in the normal case.
- If two writers pass preflight concurrently, their random staging directories
  do not collide. Exactly one final directory rename may win. A losing writer
  observes the destination before any retry and raises `FileExistsError`
  without replacing the winner.
- Each writer cleans only its own staging directory or temporary file.
- A failure before final rename leaves no published experiment directory.
- A file-update failure preserves the previously published `result.json`.
- Cleanup remains best effort so cleanup errors cannot replace the original
  storage exception.
- Only Windows `PermissionError` from the final directory publication boundary
  is retryable. All other exceptions propagate immediately. The final
  `PermissionError` propagates unchanged after the sixth failed attempt.
- The retry loop is bounded by both attempt count and fixed delay schedule; it
  contains no unbounded wait, jitter, or configuration surface.

## Security and Privacy

The change affects only temporary path construction and the publication
sequence. Existing explicit payload builders remain authoritative, so raw CSV
bytes, paper text, credentials, and arbitrary model attributes are still
excluded. Temporary names contain only an opaque random token and reveal
neither the experiment ID nor the target JSON filename.

## Testing

The implementation will follow test-driven development and cover:

1. A Windows long-path regression for single-result save, suite-result save,
   and suite-result update using the repository's normal pytest path shape.
2. A contract proving staging basenames do not embed the experiment ID or
   destination filename.
3. Failure injection during the second staged JSON write: no final directory
   and no `.tmp-*` residue.
4. Two concurrent saves of one experiment ID: exactly one success, one
   duplicate/storage failure, and one complete loadable result.
5. Failed suite-result update: the original result remains loadable and the
   temporary file is removed.
6. Existing storage, API, job, and comparison tests.
7. The complete repository test suite using the default configured basetemp,
   without relying on a shortened override.
8. A Windows publication regression proving transient `PermissionError`
   attempts follow the exact bounded delay schedule and eventually succeed
   without rewriting payloads.
9. Regressions proving a destination created between attempts stops retrying
   with `FileExistsError`, non-permission failures are never retried, and the
   sixth `PermissionError` is preserved.

## Acceptance Criteria

- All new RED/GREEN regressions pass on Windows.
- Existing duplicate-ID and privacy contracts remain unchanged.
- No partial final directory or temporary residue remains after injected
  failures.
- Concurrent duplicate saves never overwrite a completed result.
- Windows directory publication retries only the approved transient error,
  never waits more than 310 milliseconds, and never retries after a competing
  destination appears.
- The complete repository suite passes with its default pytest configuration.
- The six pre-existing user DSL modifications remain unstaged and untouched.
