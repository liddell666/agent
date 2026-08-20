# Workflow release checks

Generate the tracked workflow DSL before comparing it:

```powershell
python scripts/build_multimodel_dsl.py --profile ollama --output-dir dify
```

The repository-only checker reads the DSL, source files, and optional offline
JSON snapshots. It does not open a network connection or contact an
application service.

```powershell
python scripts/check_workflow_release.py `
  --dsl dify/paper-comparison-merged-workflow-ollama.yml `
  --source scripts/build_multimodel_dsl.py src/repro_runner/runtime.py `
  --draft-snapshot .live-artifacts/draft-workflow.json `
  --published-snapshot .live-artifacts/published-workflow.json `
  --json
```

Snapshots are JSON objects containing either a graph directly or a `graph`
object. A snapshot may include `app_id` (or `identity.app_id`); provided draft
and published application IDs must be canonical UUIDs and agree with the Task
5 manifest identity at `document.release.dify.app_id`. To enable a
source-to-DSL baseline, declare the digest explicitly as
`document.release.source_digest`:

```yaml
release:
  source_digest: sha256:...
  dify:
    app_id: b9a766a0-0ad0-415b-8d42-60459c92bec7
```

When `release.source_digest` is absent, the checker still compares the DSL,
draft, and published graphs.

The command writes only its result to standard output. It never saves a draft,
creates a backup, publishes a workflow, rolls back a workflow, changes an
input file, or sends data over the network.

Exit codes:

- `0`: no drift was found.
- `1`: one or more drift records were found. Text output contains one stable
  drift code per line; `--json` emits `{"drift":[...]}`.
- `2`: an input, graph, source digest, or application identity was invalid.

Do not place credentials, tokens, raw PDFs, CSV rows, databases, or live
service exports under version control. Keep any locally collected snapshots in
an ignored directory such as `.live-artifacts/` and inspect them before sharing.

## Safe in-process Dify publishing

`scripts/update_v31_similarity_workflow.py` exposes a testable release adapter
for code running inside the Dify API container. Importing the module does not
import Dify or open a database connection. `inspect_release_state(...)` accepts
an injected service, validates the exact application ID before the first read,
reads draft and published workflows once each, and returns canonical graph
digests without saving, backing up, publishing, or rolling back anything.

All writes go through `publish_verified_graph(...)`. The caller must provide an
`ExpectedReleaseIdentity` containing the application ID and the exact digest of
the draft that was inspected. The operation then performs these calls in order:

1. Read and validate the current draft and published workflow.
2. Create a named, restorable backup of the current draft.
3. Save the candidate graph and publish a named version.
4. Read the active published workflow and compare its canonical digest with the
   candidate digest.
5. If saving, publishing, or post-publish verification fails, restore only the
   recorded backup ID, publish that restored state, and verify its graph digest.
   It never chooses a rollback target by version recency. On Dify versions that
   expose the complete restore service, this also restores serialized RAG
   variables and frozen agent-node bindings before the rollback is published.

A successful result has `status: published` and records the backup and
published workflow IDs. A recovered verification failure has
`status: rolled_back` and records `failed_published_workflow_id`, the explicit
`backup_workflow_id`, the new `rollback_workflow_id`, and the failed/restored
digests. When Dify returns one publication ID but activates another, the record
keeps the actual active ID in `failed_published_workflow_id` and the service
return in `requested_published_workflow_id`. Exception recovery also records a
stable `failure_code`, operation, and exception type without copying the
exception message. Treat either a raised exception or `rolled_back` as a failed
candidate release.

Build the deterministic Task 5 manifest with `build_release_manifest(...)`
before live inspection. After inspection or publishing, build the persisted
manifest again with only the explicit IDs and digests returned by these
functions; do not infer an ID from Dify version ordering.

The backward-compatible V3.1 command still runs only inside the Dify API
container:

```powershell
python scripts/update_v31_similarity_workflow.py --verify
python scripts/update_v31_similarity_workflow.py --apply
```

Use `--verify` first. It is read-only and reports the draft/published IDs and
digests. `--apply` keeps the existing `already_current` and `updated` success
statuses, but now also creates the explicit backup and performs post-publish
verification. A verification mismatch is reported as `rolled_back` with all
three relevant workflow IDs. Never run `--apply` against an application ID
other than the `APP_ID` declared in the updater, and do not copy service
credentials, database contents, or exported graphs into the repository.
