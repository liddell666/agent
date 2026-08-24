# Windows-Safe Atomic Result Storage Design

**Date:** 2026-08-24

**Status:** Approved for implementation planning

## Problem

`save_result(...)` and `save_suite_result(...)` currently derive a staging
directory name from the complete experiment ID. Each JSON file is then written
through another temporary name derived from the complete destination file
name. Under a long pytest `basetemp` on Windows, these nested names have
produced paths around 264 characters. The same tests pass with a short
`basetemp`, identifying path length as the primary failure boundary rather than
a data race.

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
- Retrying permission, disk-space, or path errors.
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
5. Rename the complete staging directory once to the final experiment ID.

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
  do not collide. Exactly one final directory rename may win; the other writer
  propagates `FileExistsError` or the operating-system `OSError` without
  replacing the winner.
- Each writer cleans only its own staging directory or temporary file.
- A failure before final rename leaves no published experiment directory.
- A file-update failure preserves the previously published `result.json`.
- Cleanup remains best effort so cleanup errors cannot replace the original
  storage exception.
- No retry is added. Deterministic path failures must be fixed structurally,
  while permission and disk failures must remain visible to callers.

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

## Acceptance Criteria

- All new RED/GREEN regressions pass on Windows.
- Existing duplicate-ID and privacy contracts remain unchanged.
- No partial final directory or temporary residue remains after injected
  failures.
- Concurrent duplicate saves never overwrite a completed result.
- The complete repository suite passes with its default pytest configuration.
- The six pre-existing user DSL modifications remain unstaged and untouched.
