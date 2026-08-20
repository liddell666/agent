# Workflow release checks

Generate the tracked workflow DSL before comparing it:

```powershell
python scripts/build_multimodel_dsl.py --profile ollama --output-dir dify
```

## Build an offline candidate manifest

Build a candidate only from a clean export of the exact commit being reviewed.
This avoids treating unrelated files in an operator's checkout as part of the
release. The only local paths allowed when checking a checkout are
`.live-artifacts/`, `.pytest-tmp*/`, and `.pytest_cache/`; every other staged,
modified, or untracked path blocks use of that checkout as the candidate.

Run this check when the current checkout itself is intended to be the
candidate. If it reports an unexpected path, preserve that checkout and use
the exact-commit export below; the export deliberately excludes all
uncommitted files.

```powershell
$allowedLocal = '^[ MADRCU?!]{2} (?:\.live-artifacts/|\.pytest-tmp[^/]*\/|\.pytest_cache/)'
$unexpected = git status --porcelain=v1 | Where-Object { $_ -notmatch $allowedLocal }
if ($unexpected) {
  $unexpected
  Write-Warning 'checkout is not clean; candidate will be built from the exact commit archive'
}
```

For a reproducible candidate, export the commit instead of creating a linked
worktree. The commands below regenerate both profiles twice, compare the two
hash captures, require the repository-only drift check to exit zero, and then
write the manifest to the caller's local `.live-artifacts/` directory. They do
not contact Dify.

```powershell
$mainCheckout = (Get-Location).Path
$commit = git rev-parse HEAD
$prefix = (git rev-parse --show-prefix).TrimEnd('/')
$archiveTree = if ($prefix) { "$commit`:$prefix" } else { $commit }
$archive = Join-Path ([System.IO.Path]::GetTempPath()) ("release-baseline-$($commit.Substring(0, 12))-$([guid]::NewGuid().ToString('N'))")
New-Item -ItemType Directory -Path $archive | Out-Null
$archiveZip = Join-Path $archive 'candidate.zip'
git archive --format=zip --output $archiveZip $archiveTree
if ($LASTEXITCODE -ne 0) { throw 'git archive export failed' }
Expand-Archive -LiteralPath $archiveZip -DestinationPath $archive

Push-Location $archive
function Save-WorkflowHashes([string] $path) {
  $hashes = Get-ChildItem -Path dify -Filter '*.yml' -File |
    Sort-Object Name |
    ForEach-Object { [ordered]@{ path = "dify/$($_.Name)"; sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower() } }
  $hashes | ConvertTo-Json | Set-Content -Path $path -Encoding utf8
}
function Invoke-DslGeneration([string] $profile) {
  python scripts/build_multimodel_dsl.py --profile $profile --output-dir dify
  if ($LASTEXITCODE -ne 0) { throw "DSL generation failed for profile $profile" }
}
New-Item -ItemType Directory -Force .live-artifacts | Out-Null
Invoke-DslGeneration deepseek
Invoke-DslGeneration ollama
Save-WorkflowHashes .live-artifacts/dsl-hashes-first.json
Invoke-DslGeneration deepseek
Invoke-DslGeneration ollama
Save-WorkflowHashes .live-artifacts/dsl-hashes-second.json
if ((Get-Content .live-artifacts/dsl-hashes-first.json -Raw) -ne (Get-Content .live-artifacts/dsl-hashes-second.json -Raw)) { throw 'DSL generation is not byte-stable' }

python scripts/check_workflow_release.py `
  --dsl dify/paper-comparison-merged-workflow-ollama.yml `
  --source scripts/build_multimodel_dsl.py src/repro_runner/runtime.py --json
if ($LASTEXITCODE -ne 0) { throw 'repository-only release check failed' }

$env:PYTHONDONTWRITEBYTECODE = '1'
python -c "from pathlib import Path; import hashlib, json, sys, yaml; sys.path.insert(0, 'scripts'); from workflow_release_integrity import build_release_manifest, graph_digest, source_digest; paths = [Path('scripts/build_multimodel_dsl.py').resolve(), Path('src/repro_runner/runtime.py').resolve()]; dsl = Path('dify/paper-comparison-merged-workflow-ollama.yml'); document = yaml.safe_load(dsl.read_text(encoding='utf-8')); manifest = build_release_manifest(git_commit='$commit', worktree_clean=True, source_sha256=source_digest(paths, Path('.').resolve()), dsl_sha256='sha256:' + hashlib.sha256(dsl.read_bytes()).hexdigest(), graph_sha256=graph_digest(document['workflow']['graph']), workflow_kind=str(document['kind']), workflow_version=str(document['version'])); Path('.live-artifacts/release-baseline-candidate.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')"
if ($LASTEXITCODE -ne 0) { throw 'candidate manifest generation failed' }
Pop-Location

New-Item -ItemType Directory -Force (Join-Path $mainCheckout '.live-artifacts') | Out-Null
Copy-Item (Join-Path $archive '.live-artifacts/release-baseline-candidate.json') (Join-Path $mainCheckout '.live-artifacts/release-baseline-candidate.json') -Force
Copy-Item (Join-Path $archive '.live-artifacts/dsl-hashes-first.json') (Join-Path $mainCheckout '.live-artifacts/release-baseline-dsl-hashes-first.json') -Force
Copy-Item (Join-Path $archive '.live-artifacts/dsl-hashes-second.json') (Join-Path $mainCheckout '.live-artifacts/release-baseline-dsl-hashes-second.json') -Force
Get-Content (Join-Path $mainCheckout '.live-artifacts/release-baseline-candidate.json')
```

Keep the generated manifest local and unstaged. It contains only digests and
workflow identity metadata; do not add snapshots, credentials, raw PDFs, CSV
rows, or databases to it.

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
the draft that was inspected. A caller may also pass the deterministic Task 5
manifest as `release_manifest`. The adapter reconstructs it through
`build_release_manifest(...)`, rejects non-canonical shapes, and requires its
application ID and graph digest to match this release before the first service
call. After the read-only inspection, its explicit draft and published IDs must
also match live state before the first mutation. The validated manifest is
returned unchanged in the release result. The operation then performs these
calls in order:

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

The returned backup ID and graph digest are validated before the draft is
saved. If graph validation fails but a usable backup ID was returned, the
adapter immediately restores and verifies that exact ID. If no usable ID was
returned, recovery cannot be targeted safely: the adapter raises an explicit
unrecoverable backup-validation error and does not claim that rollback occurred.

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
before live inspection and pass it as `release_manifest` when publishing. After
inspection or publishing, build the persisted manifest again with only the
explicit IDs and digests returned by these functions; do not infer an ID from
Dify version ordering.

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
