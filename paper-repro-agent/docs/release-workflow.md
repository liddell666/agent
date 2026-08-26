# Workflow release checks

Generate the tracked workflow DSL before comparing it:

```powershell
python scripts/build_multimodel_dsl.py --profile ollama --output-dir dify
```

## Build an offline candidate manifest

Build a candidate only from a clean export of the exact commit being reviewed.
This avoids treating unrelated files in an operator's checkout as part of the
release. The index must be empty. After that unconditional staged-change gate,
the only unstaged local paths allowed when checking a checkout are
`.live-artifacts/`, `.pytest-tmp*/`, and `.pytest_cache/`; every other modified
or untracked path blocks use of that checkout as the candidate.

Run this check when the current checkout itself is intended to be the
candidate. If it reports an unexpected path, preserve that checkout and use
the exact-commit export below; the export deliberately excludes all
uncommitted files.

```powershell
$staged = @(git diff --cached --name-only)
if ($LASTEXITCODE -ne 0) { throw 'unable to inspect the Git index' }
if ($staged.Count -ne 0) {
  $staged
  throw 'the Git index is not empty; never build a candidate with staged content'
}

$allowedLocal = '^[ MADRCU?!]{2} (?:\.live-artifacts/|\.pytest-tmp[^/]*\/|\.pytest_cache/)'
$unexpected = git status --porcelain=v1 | Where-Object { $_ -notmatch $allowedLocal }
if ($unexpected) {
  $unexpected
  Write-Warning 'checkout is not clean; candidate will be built from the exact commit archive'
}
```

For a reproducible candidate, export the commit instead of creating a linked
worktree. The commands below disable checkout conversion for the archive,
verify the extracted release inputs byte-for-byte against their Git blobs and
require LF repository bytes, capture the committed DSL baseline, regenerate
both profiles twice, and require the baseline, first, and second captures to be
identical. They then require the repository-only drift check to exit zero and
write the manifest to the caller's local `.live-artifacts/` directory. They do
not contact Dify.

```powershell
$mainCheckout = (Get-Location).Path
$repoRoot = (git rev-parse --show-toplevel).Trim()
$commit = (git rev-parse HEAD).Trim()
$prefix = (git rev-parse --show-prefix).TrimEnd('/')
$archiveTree = if ($prefix) { "$commit`:$prefix" } else { $commit }
$archive = Join-Path ([System.IO.Path]::GetTempPath()) ("release-baseline-$($commit.Substring(0, 12))-$([guid]::NewGuid().ToString('N'))")
New-Item -ItemType Directory -Path $archive | Out-Null
$archiveZip = Join-Path $archive 'candidate.zip'
git -c core.autocrlf=false -c core.eol=lf -C $repoRoot archive --format=zip --prefix='' --output $archiveZip $archiveTree
if ($LASTEXITCODE -ne 0) { throw 'git archive export failed' }
if ((Get-Item -LiteralPath $archiveZip).Length -le 22) { throw 'git archive export is empty' }
Expand-Archive -LiteralPath $archiveZip -DestinationPath $archive
$candidateRoot = $archive
if (-not (Test-Path (Join-Path $candidateRoot 'scripts')) -or
    -not (Test-Path (Join-Path $candidateRoot 'src'))) {
  throw 'git archive export is missing scripts or src'
}

function Assert-ArchiveBlobBytes([string] $relativePath) {
  $repoPath = if ($prefix) { "$prefix/$relativePath" } else { $relativePath }
  $expectedBlob = (git -C $repoRoot rev-parse "$commit`:$repoPath").Trim()
  if ($LASTEXITCODE -ne 0) { throw "unable to resolve release blob $repoPath" }
  $archivePath = Join-Path $candidateRoot $relativePath
  $actualBlob = (git hash-object --no-filters $archivePath).Trim()
  if ($LASTEXITCODE -ne 0 -or $actualBlob -ne $expectedBlob) {
    throw "archive byte mismatch for $relativePath"
  }
  $bytes = [System.IO.File]::ReadAllBytes($archivePath)
  for ($index = 0; $index -lt ($bytes.Length - 1); $index++) {
    if ($bytes[$index] -eq 13 -and $bytes[$index + 1] -eq 10) {
      throw "release input is not canonical LF: $relativePath"
    }
  }
}

$releaseInputs = @(
  'scripts/build_multimodel_dsl.py'
  'src/repro_runner/runtime.py'
) + @(Get-ChildItem (Join-Path $candidateRoot 'dify') -Filter '*.yml' -File |
  Sort-Object Name | ForEach-Object { "dify/$($_.Name)" })
$releaseInputs | ForEach-Object { Assert-ArchiveBlobBytes $_ }

Push-Location $candidateRoot
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
Save-WorkflowHashes .live-artifacts/dsl-hashes-committed.json
Invoke-DslGeneration deepseek
Invoke-DslGeneration ollama
Save-WorkflowHashes .live-artifacts/dsl-hashes-first.json
Invoke-DslGeneration deepseek
Invoke-DslGeneration ollama
Save-WorkflowHashes .live-artifacts/dsl-hashes-second.json
$committedHashes = Get-Content .live-artifacts/dsl-hashes-committed.json -Raw
$firstHashes = Get-Content .live-artifacts/dsl-hashes-first.json -Raw
$secondHashes = Get-Content .live-artifacts/dsl-hashes-second.json -Raw
if ($committedHashes -ne $firstHashes -or $firstHashes -ne $secondHashes) {
  throw 'tracked DSL differs from deterministic generation'
}

$repositoryCheck = python scripts/check_workflow_release.py `
  --dsl dify/paper-comparison-merged-workflow-ollama.yml `
  --source scripts/build_multimodel_dsl.py src/repro_runner/runtime.py --json
if ($LASTEXITCODE -ne 0) { throw 'repository-only release check failed' }
$repositoryCheck | Set-Content .live-artifacts/repository-check.json -Encoding utf8

$env:PYTHONDONTWRITEBYTECODE = '1'
python -c "from pathlib import Path; import hashlib, json, sys, yaml; sys.path.insert(0, 'scripts'); from workflow_release_integrity import build_release_manifest, graph_digest, source_digest; paths = [Path('scripts/build_multimodel_dsl.py').resolve(), Path('src/repro_runner/runtime.py').resolve()]; dsl = Path('dify/paper-comparison-merged-workflow-ollama.yml'); document = yaml.safe_load(dsl.read_text(encoding='utf-8')); manifest = build_release_manifest(git_commit='$commit', worktree_clean=True, source_sha256=source_digest(paths, Path('.').resolve()), dsl_sha256='sha256:' + hashlib.sha256(dsl.read_bytes()).hexdigest(), graph_sha256=graph_digest(document['workflow']['graph']), workflow_kind=str(document['kind']), workflow_version=str(document['version'])); Path('.live-artifacts/release-baseline-candidate.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')"
if ($LASTEXITCODE -ne 0) { throw 'candidate manifest generation failed' }
Pop-Location

New-Item -ItemType Directory -Force (Join-Path $mainCheckout '.live-artifacts') | Out-Null
Copy-Item (Join-Path $candidateRoot '.live-artifacts/release-baseline-candidate.json') (Join-Path $mainCheckout '.live-artifacts/release-baseline-candidate.json') -Force
Copy-Item (Join-Path $candidateRoot '.live-artifacts/repository-check.json') (Join-Path $mainCheckout '.live-artifacts/release-baseline-repository-check.json') -Force
Copy-Item (Join-Path $candidateRoot '.live-artifacts/dsl-hashes-committed.json') (Join-Path $mainCheckout '.live-artifacts/release-baseline-dsl-hashes-committed.json') -Force
Copy-Item (Join-Path $candidateRoot '.live-artifacts/dsl-hashes-first.json') (Join-Path $mainCheckout '.live-artifacts/release-baseline-dsl-hashes-first.json') -Force
Copy-Item (Join-Path $candidateRoot '.live-artifacts/dsl-hashes-second.json') (Join-Path $mainCheckout '.live-artifacts/release-baseline-dsl-hashes-second.json') -Force
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
and workflow-metadata digests without saving, backing up, publishing, or
rolling back anything. Metadata covers normalized features, environment
variables, and conversation variables. Results and exceptions expose only
digests, never variable values.

All writes go through `publish_verified_graph(...)`. The caller must provide an
`ExpectedReleaseIdentity` containing the application ID and the exact digest of
the draft that was inspected. Metadata-aware callers must also provide the
exact inspected draft metadata digest and the candidate
`WorkflowReleaseMetadata`. A caller may also pass the deterministic Task 5
manifest as `release_manifest`. The adapter reconstructs it through
`build_release_manifest(...)`, rejects non-canonical shapes, and requires its
application ID and graph digest to match this release before the first service
call. After the read-only inspection, its explicit draft and published IDs must
also match live state before the first mutation. The validated manifest is
returned unchanged in the release result. The operation then performs these
calls in order:

1. Read and validate the current draft and published workflow.
2. Create a named, restorable backup of the current draft and verify both its
   graph and metadata identities.
3. Save the candidate graph and complete workflow metadata, then publish a
   named version. Dify/Pydantic variable models are serialized canonically for
   identity and rehydrated through Dify's variable factory before saving.
4. Read the active published workflow and compare its canonical graph and
   metadata digests with the candidate identities.
5. If saving, publishing, or post-publish verification fails, restore only the
   recorded backup ID, publish that restored state, and verify its graph and
   metadata digests. It never chooses a rollback target by version recency. On
   Dify versions that expose the complete restore service, this also restores
   serialized RAG variables and frozen agent-node bindings before the rollback
   is published.

The returned backup ID, graph digest, and metadata digest are validated before
the draft is saved. If backup validation fails but a usable backup ID was
returned, the adapter immediately restores and verifies that exact ID. If no
usable ID was returned, recovery cannot be targeted safely: the adapter raises
an explicit unrecoverable backup-validation error and does not claim that
rollback occurred.

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

## Production baseline 0.7.0 evidence

The independently verified candidate was promoted on 2026-08-24 through the
metadata-aware boundary above. The disposable promotion script and its exact
container directory were deleted after verification.

- Candidate app: `397fc669-c89b-4cfa-975d-2807495f7a5b`.
- Candidate published workflow: `d94aec0e-7beb-41e6-8886-ee5e3da7e49b`.
- Production app: `b9a766a0-0ad0-415b-8d42-60459c92bec7`.
- Original production workflow: `94e00245-a1f8-48e3-aba6-41549ab75c6e`.
- A first save attempt created backup
  `250fe582-1765-44e6-8d9b-f303ad0ad0a9`, failed before candidate
  publication, and restored the verified production state as
  `e627cacb-dedf-490d-b13c-a6024e082cf6`.
- The successful release created backup
  `1d3a9781-7b72-4e5d-bdb4-a473006fccb0` and activated production workflow
  `17ffb2a3-1034-4af5-9718-29be45f60b63` with no rollback workflow.
- Verified graph digest:
  `sha256:25834f464834cfd45ad6e9b9215416075a1850b576ba42e66decca88c0a9ed80`.
- Verified metadata digest:
  `sha256:97f0389ff657384392ff4c2ae8aa7ea94c2b9113fb8b4d6b83c0398d524b1d6a`.
- Final independent readback found identical draft and published graph and
  metadata digests. The repository suite passed with `546 passed` and the one
  pre-existing Starlette/httpx deprecation warning.

## Regression candidate 1.0.0 (candidate only)

`Paper comparison Regression Candidate` is an isolated Dify workflow candidate.
It is not production, has not been promoted, and is not fully accepted. The
candidate was created and imported independently; no existing Dify application
or one of the six user-owned DSL files was edited or published.

The input contract is a two-step prepare/confirm flow. Prepare receives the
synthetic paper PDF and a UTF-8 training CSV, forces `task_type=regression`, and
builds a short-lived protocol token without returning raw document text or CSV
rows. Run receives that token with `confirm_protocol=true`, target column
`target`, and the confirmed bounded configuration. The acceptance configuration
is `cv_folds=3`, `n_iter=1`, and the asynchronous runner's fixed `n_jobs=1`.
Supported models are `linear_regression`, `random_forest`,
`gradient_boosting`, and `xgboost`; reported metrics are MAE, RMSE, and R², with
separate performance and paper-closeness rankings plus distinct strict-
comparability and approximate-similarity conclusions.

Candidate release identity:

- App UUID: `17fe51d4-091f-4729-87ee-3c0a2e920918`.
- Draft workflow UUID: `912d4e05-494c-4302-a189-788a59c6c0c2`.
- Published workflow UUID: `6129249f-0ba5-46f9-9039-b7b5000dfbc4`.
- Explicit rollback backup UUID: `0c9dcebc-ae9f-4963-bc67-5f25bedd8897`.
- Graph digest:
  `sha256:3a65b0a72c4f095c2c4ea8728d5abf844a14eb9640126ca210fad32fae1ac997`.
- Metadata digest:
  `sha256:97f0389ff657384392ff4c2ae8aa7ea94c2b9113fb8b4d6b83c0398d524b1d6a`.
- Source digest:
  `sha256:d809237d41cd183ba3f3711ff45a0de5d8a112851a01674b660969edcbbc6d6d`.
- DSL digest:
  `sha256:98354a1c21b7b613fa328500d266d5cfac90f681723d77b452bfa089302cee44`.
- No rollback workflow was created after the successful verified publication.
  The exact backup graph and metadata identities are stored in the ignored,
  privacy-safe `.live-artifacts/regression-candidate-verification.json` record.

The recorded source digest is reproducible from the final candidate code state
using the source files that directly generate or are read into the regression
DSL: `scripts/build_regression_dsl.py`, `scripts/build_multimodel_dsl.py`,
`dify/paper-comparison-workflow.yml`, `dify/paper-dossier-workflow.yml`,
`dify/code/validate_evidence.py`, `dify/code/comparison_workflow.py`,
`dify/code/experiment_workflow.py`, and `dify/code/validate_parser.py`.
`workflow_release_integrity.source_digest` sorts their repository-relative
POSIX paths, then feeds SHA-256 a length-prefixed path field followed by a
length-prefixed raw-byte field for each file. The read-only final verification
command is:

```powershell
python scripts/check_workflow_release.py `
  --dsl dify/paper-comparison-regression-workflow.yml `
  --source scripts/build_regression_dsl.py scripts/build_multimodel_dsl.py dify/paper-comparison-workflow.yml dify/paper-dossier-workflow.yml dify/code/validate_evidence.py dify/code/comparison_workflow.py dify/code/experiment_workflow.py dify/code/validate_parser.py `
  --json
```

It returned `{"drift":[]}` with exit code `0`. The checker accepts optional
draft and published graph snapshots. During the 2026-08-26 read-only evidence
repair, the local Dify container engine was unavailable, so neither candidate
snapshot could be fetched or retained and the snapshot-enabled checker was not
run. That source-only clean result does not substantiate equality with the
live draft or published graph; the previously recorded candidate IDs and
publication digests remain unchanged.

Before safe use, require runner and Dify health, zero queued/running jobs, a
configured DeepSeek provider, and a runner contract that exposes explicit
regression `task_type`. Keep both candidate environment values in Dify secret
fields, run only bounded inputs, inspect aggregate results, and never persist
tokens, cookies, document text, CSV rows, or full request payloads as evidence.
Rollback must target the exact backup UUID above; never select a version by
recency.

The controlled acceptance was not executed on 2026-08-24. Although the active
runner was healthy, it was mounted from the older main checkout at commit
`58fcd7b8c03f7e08410869133e94abafe533ac72`, whose installed
`ExperimentManifest` has no `task_type` field. Submitting the synthetic fixtures
could not exercise regression correctly. The shared runner was not switched,
restarted, replaced, or otherwise mutated. Acceptance remains blocked until a
separately authorized task-aware runner is deployed; after that gate passes,
rerun the synthetic PDF/CSV acceptance and verify paper evidence values 1.5,
2.0, and 0.80. Because the paper is synthetic, even a future successful run is
a controlled regression-path check, not evidence that results generalize to
real research papers or datasets.
