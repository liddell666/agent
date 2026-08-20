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
