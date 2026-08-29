# Ollama provider failover implementation plan

## Goal

Correct provider-failure classification, publish the deterministic Ollama regression profile to the isolated candidate through the guarded release boundary, and rerun the fixed five-case real acceptance gate.

## Task 1: TDD provider-failure classification

**Modify:** `tests/real_regression_acceptance/test_runner.py`, `src/real_regression_acceptance/runner.py`

1. Add a RED test whose prepare outcome has `status="failed"`, a safe run ID, and outputs containing a fake remote-error/secret sentinel that must never be serialized.
2. Require `failure_code="service_unavailable"`, exactly one client call, a prepare checkpoint containing only allowlisted fields, and no confirm call.
3. Keep a succeeded prepare response with missing/invalid preview or token classified as `evidence_ambiguous`.
4. Implement the smallest ordering change: branch on non-successful prepare status before parsing successful output contracts.
5. Run focused runner/evidence tests and the full suite; commit only the TDD change.

## Task 2: Verify deterministic Ollama release candidate offline

**Read:** `dify/paper-comparison-regression-workflow-ollama.yml`, builders, profile and regression DSL tests

1. Regenerate both regression profile artifacts into a temporary directory and compare the Ollama artifact structurally with the tracked canonical DSL.
2. Verify the comparison request still consumes validated dossier output.
3. Build an ignored canonical release manifest using exactly the repository's existing strict schema: `schema_version`, `git_commit`, `worktree_clean`, source/DSL/graph digests, workflow kind/version, and Dify App/draft/published/rollback IDs. Do not add metadata, protected-file, or profile fields to this manifest.
4. Pin the fixed metadata digest through `ExpectedReleaseIdentity.draft_metadata_digest`. Record that digest, protected-file hashes, provider/model/mode, thinking behavior, and dependency assertions in a separate ignored safe profile-verification evidence file backed by the existing deterministic tests.
5. Confirm the six protected user-owned DSL hashes and tracked Git cleanliness.
6. Run focused profile/DSL/release-boundary tests and review both evidence files; create no tracked commit.

## Task 3: Run synthetic provider readiness gates

1. Require all services healthy and runner idle.
2. Require local `qwen3:8b` presence and Dify Ollama model validity.
3. Invoke `qwen3:8b` locally through `POST http://127.0.0.1:11434/api/generate` with `stream=false`, a 120-second timeout, and minimal synthetic content. Require `done=true` and a non-empty response.
4. Invoke the configured Dify path inside `docker-api-1` with `/app/api/.venv/bin/python`: enter `app.app_context()`, resolve the candidate App tenant, call `ModelManager.for_tenant(...).get_model_instance(tenant_id, "langgenius/ollama/ollama", ModelType.LLM, "qwen3:8b")`, then `invoke_llm` with one synthetic `UserPromptMessage`, `stream=False`, thinking disabled, and a 120-second host timeout. Suppress routine HTTP logs. Require a non-empty completion.
5. Store only safe readiness status, provider/model/mode, duration presence, response length, and response hash in ignored evidence. Add a small reusable safe probe script or helper with unit tests so the invocation, timeout, allowlist, and no-raw-text behavior are reviewable and repeatable.
6. Stop before publication on any failure. Do not persist prompt/response text, raw provider errors, credentials, or plugin dispatch identifiers.

## Task 4: Guarded publication to the isolated candidate

1. Repeat exact preflight identities and digests from the design, service health, and runner idle checks.
2. Create and independently verify a new explicit backup of the current corrected DeepSeek graph and fixed metadata.
3. Publish the manifest-pinned Ollama graph with unchanged metadata using the existing guarded release service. Accept only a `published` outcome and commit the SQLAlchemy transaction only after that result. Treat `rolled_back` or any exception as task failure, preserve only safe rollback evidence, and do not claim the Ollama version is active.
4. Independently read back the post-publish draft and active published layers.
5. Require a new active published UUID, distinct draft/published UUIDs, equal Ollama graph digests, equal fixed metadata digests, verified backup, and `{"drift":[]}`.
6. Record only safe publication evidence. Do not touch production Apps or protected DSLs.

## Task 5: Rerun the five-case real gate

1. Preserve the current failed DeepSeek evidence as a timestamped ignored sibling.
2. Repeat service, runner-idle, provider-readiness, candidate-identity, and drift preflight.
3. Run the exact pinned five-case command with the candidate key only in the child process environment.
4. Evaluate, parse JSON, and privacy-scan evidence.
5. Require at least four completed cases, zero false strict results, zero unknown failures, runner idle, fixed identities/digests, and no drift.
6. On generalized failure, stop without inline patching or republishing.

## Task 6: Document, fully verify, review, and integrate

1. Record only safe aggregate outcomes, final IDs/digests, evidence paths, readiness evidence, and rerun commands in `docs/release-workflow.md`.
2. Run the full test suite, deterministic DSL tests, release checker, privacy checks, `git diff --check`, and clean-tree checks.
3. Commit documentation, perform per-task and whole-branch reviews, then integrate only after every gate passes.
4. Preserve all six protected user-owned DSL changes in the main checkout throughout integration.
