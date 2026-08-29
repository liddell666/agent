# Ollama provider failover design

## Context

The corrected isolated regression candidate passed graph, metadata, backup, privacy, and release checks, but the five-case real gate completed `0/5`. Read-only diagnosis established the same earliest failure in all five runs: PDF parsing and parser validation succeeded, then `extract_paper_dossier` failed before producing output because the configured DeepSeek provider returned HTTP 402 for insufficient balance.

The acceptance runner also classified every non-successful prepare workflow as `evidence_ambiguous`, obscuring a provider outage. This is a secondary observability defect, not the reason the model call failed.

The host already has an isolated Ollama container with `qwen3:8b`, GPU execution, a successful synthetic readiness probe, a valid Dify model registration, deterministic profile generation, and an already-generated corrected regression DSL.

## Decision

Use the existing Ollama profile as the recommended failover for the isolated candidate. Keep the same candidate App and fixed workflow metadata, but publish the deterministic Ollama graph through the guarded release boundary after a fresh backup. Do not modify or publish any production App or the six protected user-owned DSLs.

Separately change the acceptance runner so a prepare workflow whose terminal status is not `succeeded` returns the existing privacy-safe `service_unavailable` category before preview/token parsing. Successful prepare responses that lack a valid protocol preview/validation result or token remain `evidence_ambiguous`.

## Release and readiness contract

Before publication:

1. Docker, Dify, paper-parser, repro-runner, PostgreSQL, Redis, sandbox, plugin daemon, and Ollama must be healthy; the runner must have zero queued/running jobs.
2. The candidate must still have App `17fe51d4-091f-4729-87ee-3c0a2e920918`, draft `912d4e05-494c-4302-a189-788a59c6c0c2`, published `88e7e41b-2ee0-40d5-b7cc-4cc2b6453931`, graph `sha256:e753393838ab1b8073c727f355c5985a4fd0e17de975342f72942242f7443b40`, and metadata `sha256:97f0389ff657384392ff4c2ae8aa7ea94c2b9113fb8b4d6b83c0398d524b1d6a`.
3. `qwen3:8b` must be present locally and Dify's Ollama LLM registration must be valid.
4. A synthetic, non-sensitive readiness probe must succeed through both the local Ollama endpoint and Dify's configured model boundary. The Dify probe runs inside `docker-api-1` under the Flask App context, resolves the candidate App tenant, obtains `langgenius/ollama/ollama` / `qwen3:8b` through `ModelManager.for_tenant(...).get_model_instance(...)`, and invokes one non-streaming `UserPromptMessage` with a 120-second host timeout. Success requires a non-empty completion. Persist only status, provider/model/mode, duration presence, response length, and response hash; never persist prompt or response text.
5. The canonical Ollama DSL must match its deterministic builder output and use only `langgenius/ollama/ollama`, `qwen3:8b`, chat mode, with thinking disabled for structured output. The canonical release manifest remains limited to the repository's existing strict schema; profile-specific assertions belong in separate safe readiness/profile evidence and deterministic tests.

Publication must create and independently verify a backup of the current corrected DeepSeek graph and fixed metadata, then save and publish the Ollama graph with unchanged metadata. Dify may retain the existing draft UUID. Acceptance requires a new active published UUID, distinct draft/active-published UUIDs, equal draft/published Ollama graph digests, equal fixed metadata digests, a verified backup, and an empty snapshot drift result.

## Acceptance contract

Preserve the failed DeepSeek corpus evidence under a timestamped ignored name. Run the same five pinned cases with no code, DSL, metadata, provider configuration, or workflow changes between cases.

The gate remains:

- five safe terminal results;
- at least four completed cases;
- zero false strict-comparability results;
- zero unknown failure categories;
- a clean privacy scan;
- zero queued/running runner jobs after execution;
- exact post-publication identities/digests and no candidate drift.

If the Ollama graph exposes another generalized defect or model-quality failure, preserve privacy-safe aggregate evidence and stop. Do not add prompt fallbacks that invent paper evidence, protocol fields, metrics, or strict comparability.

## Safety and rollback

- No provider secret, API token, paper text, CSV row, prompt, raw model response, or raw workflow payload may be recorded.
- Readiness probes use only synthetic content.
- The existing corrected DeepSeek publication remains restorable through the new explicit backup.
- A failed guarded publication must roll back through the existing release boundary and record only safe identities/digests.
- Docker recovery remains limited to the proven non-destructive stale-socket parent-directory rename when exact preconditions are met.

## Alternatives rejected

- Waiting indefinitely for DeepSeek capacity does not complete the user's requested flow and leaves the candidate externally fragile.
- Reclassifying the error alone improves diagnostics but cannot make the model call succeed.
- Installing or inventing another model is unnecessary because the repository and Dify already support a tested local Ollama profile.
- Hot-editing the live graph would bypass deterministic generation, review, backup, and rollback guarantees.
