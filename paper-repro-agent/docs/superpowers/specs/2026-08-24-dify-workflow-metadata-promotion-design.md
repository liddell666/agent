# Dify Workflow Metadata Promotion Design

## Problem

The verified candidate and production Dify applications have identical application mode and empty app-model configuration, but their workflow metadata differs. The candidate has two environment variables while production has one, and their normalized feature payloads also have different digests.

The current `publish_verified_graph(...)` release boundary copies only `graph_dict`. `DifyReleaseService.save_draft(...)` intentionally reuses the production draft's features, environment variables, and conversation variables. Publishing the candidate graph through that path would therefore create a hybrid release rather than the exact E2E-tested candidate.

## Goals

- Promote the candidate graph together with its normalized features, environment variables, and conversation variables.
- Add optimistic concurrency protection for the production draft metadata as well as its graph.
- Verify backup, active publication, and rollback metadata in addition to graph digests.
- Preserve existing graph-only callers and public result fields.
- Keep credentials and variable values out of logs, manifests, and error messages.
- Perform no production mutation until focused and full regression tests pass.

## Design

Introduce an immutable `WorkflowReleaseMetadata` value containing deep-copied `features`, `environment_variables`, and `conversation_variables`. A pure `workflow_metadata_digest(...)` function will hash a stable JSON representation of those three fields without exposing their values.

`ExpectedReleaseIdentity` gains an optional `draft_metadata_digest`. `ReleaseState` gains draft and published metadata plus their digests. Existing callers that omit the new identity field retain graph-only concurrency behavior.

`publish_verified_graph(...)` gains an optional keyword-only `candidate_metadata`. When omitted, the existing behavior remains unchanged. When supplied, the orchestrator will:

1. Require the inspected production draft metadata digest to match `draft_metadata_digest` before any write.
2. Validate the explicit backup's graph and metadata against the inspected production draft.
3. Pass the candidate metadata to `save_draft(...)` together with the candidate graph.
4. Require both the active graph digest and active metadata digest to match the candidate before reporting `published`.
5. On any mismatch or exception, restore the explicit backup and require both restored digests to match the pre-release production state.

The Dify adapter will pass candidate features and variables directly to `WorkflowService.sync_draft_workflow(...)`. Its graph-only path continues to use the current production draft as the metadata source. The legacy rollback fallback continues to use the recorded backup workflow as both graph and metadata source; Dify's complete restore service remains preferred when available.

## Result Contract

Existing fields such as `candidate_digest`, `published_digest`, and `restored_digest` remain unchanged. Metadata-aware releases additionally return:

- `candidate_metadata_digest`
- `published_metadata_digest` on success
- `failed_published_metadata_digest` and `restored_metadata_digest` after rollback

No raw feature configuration or workflow variable value is included in the result.

## Testing

TDD regressions will cover:

- metadata digest determinism and input immutability;
- concurrency rejection before backup when production metadata changed;
- exact candidate metadata propagation through the Dify adapter;
- successful graph-plus-metadata publication;
- post-publish metadata mismatch causing explicit rollback;
- backup metadata mismatch causing explicit rollback;
- rollback verification rejecting incomplete metadata restoration;
- unchanged behavior for existing graph-only callers.

After focused tests and the complete repository suite pass, the live promotion will re-inspect candidate and production identities, build a production-identity canonical manifest, publish through the enhanced boundary, commit the Dify transaction, and perform an independent post-publish read. Any `rolled_back` result is a failed release and will not be reported as production success.

## Non-Goals

- Copying application identity, name, icon, tenant, owner, API/site flags, or credentials.
- Modifying the candidate application.
- Changing the Task-5 manifest schema.
- Selecting rollback versions by recency.
