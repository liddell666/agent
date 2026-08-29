# Ollama context compaction implementation plan

Design: `docs/superpowers/specs/2026-08-30-ollama-context-compaction-design.md`

Execution method: test-driven, one reviewed commit per logical task. Never edit or stage the six protected prepare/merged/multimodel DSL files.

## Task 1: Lock the pure context-compaction contract with RED tests

Files:

- Modify: `tests/test_dify_code.py`
- Create if separation improves clarity: a focused context-compaction test module under `tests/`

Steps:

1. Add a golden corpus for the disabled/default path and compare its serialized outputs with snapshots captured from the pre-change implementation, covering fitting, workflow-compacted, multibyte, and validation-failure inputs.
2. Add exact-fit and one-byte-over tests using UTF-8 byte length, including Chinese/multibyte text.
3. Add deterministic page grouping and sampling tests: ignore invalid pages, preserve original in-page order, sort pages, cap candidates at 32, retain first/last, and evenly span interior pages.
4. Add head/tail clipping, non-empty excerpt, warning, original `page_count`, and no-synthesis assertions.
5. Add fail-closed tests for missing eligible page text and impossible minimum budget, plus a tens-of-thousands-of-pages case that proves bounded deterministic output.
6. Run the focused tests and record the expected RED failures.
7. Commit only the RED tests and obtain independent review.

## Task 2: Implement deterministic byte-budgeted compaction

Files:

- Modify: `dify/code/validate_parser.py`
- Modify: `tests/test_dify_code.py` and/or the focused context-compaction test module

Steps:

1. Add a disabled-by-default context budget constant and the fixed context warning.
2. Implement UTF-8-safe head/tail clipping without splitting code points.
3. Implement positive-page collection, a 32-page evenly spaced candidate cap, at most 32 floor-size page-count checks, and one descending 1,400..160-byte ceiling scan for the maximum feasible page set.
4. Make context compaction operate directly on the validated source payload and guarantee serialized bytes do not exceed the active budget.
5. Fail closed when no eligible page-addressable result can be produced.
6. Preserve the current 360,000-character workflow compaction exactly when the context option is disabled.
7. Run focused tests, related parser/DSL tests, and the full suite.
8. Commit and obtain independent code review; correct all findings before continuing.

## Task 3: Wire the Ollama-only production envelope

Files:

- Modify: `scripts/build_multimodel_dsl.py`
- Modify: `tests/test_dify_llm_profiles.py`
- Modify: `tests/test_dify_regression_dsl.py` or a focused builder test file

Steps:

1. Add named constants for `num_ctx=16384`, `num_predict=2048`, fixed prompt bytes 4,475, note bytes 2,048, chat overhead 512, completion reserve 2,048, and payload bytes 7,301.
2. Add a test that recomputes the fixed prompt bytes and budget arithmetic from the built Ollama prompt.
3. Activate the embedded parser context budget only for the Ollama profile.
4. Pin `think=false`, `num_ctx=16384`, and `num_predict=2048` only on Ollama LLM nodes.
5. Assert the default/DeepSeek profile retains prior completion parameters and parser behavior.
6. Run focused and full tests, commit, and obtain independent review.

## Task 4: Strengthen readiness probes for the production envelope

Files:

- Modify: `scripts/check_ollama_readiness.py`
- Modify: `tests/test_ollama_readiness.py`

Steps:

1. Write RED tests requiring both direct and Dify probes to send `num_ctx=16384`, `num_predict=2048`, and `think=false`.
2. Switch the direct boundary to `/api/chat` and the Dify boundary to `SystemPromptMessage`; render the same single production system message with deterministic 7,301-byte parser and 512-character/2,048-byte notes substitutions.
3. Probe the empty one-system-message template overhead and the full maximum shape at both boundaries; assert `prompt_tokens <= 512` for the former and `prompt_tokens <= 14,336` for the latter.
4. Send local parameters at `options.num_ctx=16384`, `options.num_predict=2048`, and top-level `think=false`; send the equivalent Dify model parameters.
5. Keep stdout evidence content-free and add verified tokenizer/profile identity, input byte length, prompt-token count, parameter metadata, and digests.
6. Ensure timeouts and provider errors remain bounded, typed, and fail closed.
7. Run unit tests, then run the live local and Dify probes.
8. Commit and obtain independent review.

## Task 5: Regenerate and verify only the isolated Ollama artifact

Files:

- Modify: `dify/paper-comparison-regression-workflow-ollama.yml`
- Create/update: privacy-safe offline evidence under `.live-artifacts/`

Steps:

1. Snapshot `git status` and verify these protected files against their pre-change SHA-256 values:
   - `dify/paper-comparison-merged-workflow.yml`: `5e4bcacebb997a8ade036fff352006a05acf3d3296f8a9c9efcaed5aef253b15`
   - `dify/paper-comparison-merged-workflow-ollama.yml`: `f2055cc7d9b06b277c05252dfb3b5489248a11370c0372d56c132f07f35516dc`
   - `dify/paper-comparison-multimodel-workflow.yml`: `9d182407f01c38e8ecf6e60115fac92b258cd441d8fbae0fec11de9c0ed4f1b1`
   - `dify/paper-comparison-multimodel-workflow-ollama.yml`: `db895087afd10762f406b12784185465e4ae93e0a63d1f2b9e3e086e0b5b4bcb`
   - `dify/paper-comparison-prepare-workflow.yml`: `54749c2b649b79e5cd0e5364541d94b0b8a207ce73014310ad320ff66523f5fb`
   - `dify/paper-comparison-prepare-workflow-ollama.yml`: `1066496786d2684b1ac8b5138a954a98937a487522d3991dcf6cac5388beae18`
2. Generate the Ollama regression workflow in memory and verify its parameters, embedded budget, node/edge contract, and canonical digest.
3. Write only the isolated Ollama regression artifact.
4. Recheck that every protected file is byte-identical and unstaged.
5. Run focused DSL tests and the full suite.
6. Commit the single generated artifact plus any required tracked manifest update and obtain independent review.

## Task 6: Guarded publication to the isolated candidate

Files:

- Create/update: content-free publication evidence under `.live-artifacts/`
- Update: `.superpowers/sdd/ollama-failover-progress.md`

Steps:

1. Perform read-only Docker, Dify, paper-parser, repro-runner, Ollama, candidate identity, drift, and runner-idle checks.
2. Run the exact production-sized local and Dify readiness probes.
3. Verify the exact precondition baseline: app `17fe51d4-091f-4729-87ee-3c0a2e920918`, draft `912d4e05-494c-4302-a189-788a59c6c0c2`, published workflow `a63be80d-5340-42ae-a71f-8671961bcee2`, graph `sha256:4101efd0d46989fcf99fa0bacc708f05cfe99fe8dc72a639a24e4886fb057633`, and metadata `sha256:97f0389ff657384392ff4c2ae8aa7ea94c2b9113fb8b4d6b83c0398d524b1d6a`.
4. Create and verify a rollback backup.
5. Import and publish only app `17fe51d4-091f-4729-87ee-3c0a2e920918` using the existing safe publication tooling.
6. Verify new draft/published identities, canonical graph digest, metadata digest, and zero unrelated-app drift.
7. Record privacy-safe evidence and obtain independent review.

## Task 7: Rerun the five-case real acceptance gate

Files:

- Update: `.live-artifacts/real-regression-acceptance.json`
- Create/update: `.live-artifacts/sdd/ollama-context-compaction-real-gate-report.md`
- Update: `.superpowers/sdd/ollama-failover-progress.md`

Steps:

1. Confirm readiness, exact candidate identities/digests, and runner idle immediately before execution.
2. Run the existing five real cases with the existing privacy-safe regression acceptance tool.
3. Require at least 4/5 completed cases, exactly 0 false-strict results, exactly 0 unknown results, a clean privacy scan, no candidate drift, and final runner idle.
4. Inspect only bounded content when needed for a failed gate; keep persistent evidence content-free.
5. Reconfirm the candidate graph/metadata and readiness after execution.
6. Obtain independent execution/evidence review.

## Task 8: Final verification, documentation, and safe integration

Files:

- Modify only the relevant tracked documentation/manifests indicated by verification
- Do not touch protected DSL files

Steps:

1. Run the complete test suite and all repository validation commands.
2. Review `git diff`, protected-file hashes, generated artifact digest, candidate drift, and secrets/privacy scans.
3. Update the progress ledger and operator documentation with final identities, rollback reference, and acceptance result.
4. Obtain final code/release review.
5. If and only if the real gate passes, complete the existing Task 6 documentation/integration work from the provider-failover plan and integrate the feature branch using the repository's safe branch-finishing procedure.
6. If the gate remains failed, stop fail-closed with evidence and do not claim acceptance complete.
