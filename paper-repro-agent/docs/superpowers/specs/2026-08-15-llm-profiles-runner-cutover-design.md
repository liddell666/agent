# LLM profiles and runner cutover design

## Status

Proposed after approval of approach A. This document is the design checkpoint
for the next implementation; it does not change the active Dify application or
the running `repro-runner` container.

## Context

The merged Dify workflow is generated from
`dify/paper-dossier-workflow.yml`. Its paper-dossier LLM node currently carries
the DeepSeek provider and model as static DSL fields. The generator copies that
node when building the prepare and merged workflows, so a single imported app
cannot safely select a provider from a normal workflow input.

The local Docker environment already has an Ollama service with the
`qwen3:8b` completion model. The repository's new runner image also carries
commit and workflow-version provenance, but the active container named
`repro-runner` still uses the older image. The old container name must be
handled explicitly before Compose can create the replacement.

## Goals

1. Make the paper-extraction provider/model an explicit build-time profile.
2. Keep the current DeepSeek DSL output as the default and preserve its prompt,
   schema, validation, protocol, and report behavior.
3. Add a secret-free, deterministic Ollama profile for local acceptance using
   `qwen3:8b`.
4. Switch the active runner only after a read-only preflight confirms that no
   job is currently executing.
5. Preserve the experiment-data bind mount and Docker network alias, retain the
   old container for rollback inspection, and verify the new health provenance.

## Non-goals

- Do not implement an implicit DeepSeek-to-Ollama fallback inside one Dify run.
- Do not change the paper-dossier JSON schema or extraction prompt semantics.
- Do not put an API key, parser token, protocol secret, or Ollama credential in
  generated DSL or source control.
- Do not delete the old runner container or experiment data.
- Do not change the model-suite algorithms or comparison semantics.

## Design

### 1. Explicit LLM profiles

Add a small profile registry to `scripts/build_multimodel_dsl.py`. Each profile
contains only provider metadata and the provider dependency needed by the
imported DSL:

| Profile | Provider | Model | Intended use |
| --- | --- | --- | --- |
| `deepseek` | `langgenius/deepseek/deepseek` | `deepseek-v4-flash` | Default/shared deployment |
| `ollama` | `langgenius/ollama/ollama` | `qwen3:8b` | Local Docker acceptance |

The builder will accept a profile argument while keeping the no-argument
behavior unchanged. The profile is applied to the copied LLM node and the
workflow dependency metadata. It does not alter prompts, code nodes, URLs,
inputs, or output names.

The default profile writes the existing filenames. The Ollama profile writes
parallel, clearly named artifacts such as:

- `dify/paper-comparison-prepare-workflow-ollama.yml`
- `dify/paper-comparison-multimodel-workflow-ollama.yml`
- `dify/paper-comparison-merged-workflow-ollama.yml`

The generated profile artifacts remain deterministic and contain blank secret
values. The local Dify model provider is configured separately with an Ollama
base URL reachable on the Dify Docker network, normally
`http://ollama:11434`.

There is no runtime provider selector because that would make the imported
workflow's model dependency and reproducibility contract ambiguous. Operators
choose the profile at import/build time instead.

### 2. Runner image and reversible cutover

Keep `scripts/build_repro_runner.ps1` as the image build entry point. It passes
the current Git commit and workflow version as Docker build arguments, and the
image exposes them through `/healthz` and persisted experiment provenance.

Add a guarded cutover procedure/script with this order:

1. Resolve and record the expected commit, workflow version, old image, mounts,
   and network alias without printing secret environment values.
2. Read the runner job state and abort without mutation if a job is `queued` or
   `running`. A `needs_retry` job is retained in the mounted store and may be
   resumed after the replacement is healthy.
3. Build or select the new image and run its health/provenance smoke check.
4. Stop and rename the old container to a timestamped
   `repro-runner-legacy-*` name; retain it and its mounts for rollback.
5. Start the Compose `repro-runner` service with the same data mount,
   `docker_default` network, and `repro-runner` alias.
6. Wait for `/healthz`, then assert the expected commit, source digest, and
   workflow version. If health does not become valid, stop the replacement and
   restore the old container/name.

The procedure must fail before step 4 when the preflight is inconclusive. It
must not use broad deletion or remove the experiment-data directory.

### 3. Dify application acceptance

The existing DeepSeek app remains available. For local acceptance, import the
generated Ollama merged DSL as a separate app or explicitly select the Ollama
profile in a disposable local app. Configure only the local provider and the
two existing environment secrets in the Dify UI. Run the committed minimal PDF
and synthetic binary CSV through `prepare` and `run`, and verify:

- the LLM node completes without the DeepSeek timeout;
- dossier validation still rejects unsupported or unevidenced fields;
- the protocol/runner path succeeds;
- the final report includes runner commit and workflow provenance; and
- no secret or raw input content appears in generated files or verification
  logs.

## Compatibility and rollback

Existing no-argument DSL generation remains DeepSeek-compatible. Existing
experiment results and the shared `/data/experiments` directory are untouched.
The old runner remains as a stopped, timestamped container until the new
healthcheck and a minimal API smoke test pass. Rollback restores the old
container and the same network alias without altering the job database.

## Verification

The implementation is complete only when all of the following pass:

1. Profile-generation tests prove deterministic DeepSeek and Ollama outputs,
   correct provider/model/dependency fields, and blank secret values.
2. Existing full test suite passes.
3. The new image health check reports the expected commit and workflow version.
4. The guarded cutover reports the same provenance from the active container.
5. The local Ollama Dify acceptance completes on the committed fixtures.
6. `git diff --check` and a secret-scan check pass for generated artifacts.

