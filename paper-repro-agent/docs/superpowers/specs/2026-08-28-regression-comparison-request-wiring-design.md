# Regression Comparison Request Wiring Design

## Status and objective

The isolated regression candidate completes real-paper parsing, protocol
confirmation, dataset validation, and runner execution, but all five real
acceptance cases terminate with `invalid_regression_comparison_request`. This
change restores the validated-dossier boundary used by the comparison request
without weakening any evidence, privacy, or strict-comparability gate.

The change remains candidate-only. It must not modify or promote a production
Dify application, alter the six user-owned workflow DSL files, or reuse a
failed workflow version as the new rollback point.

## Evidence and root cause

The 2026-08-28 live run produced five terminal `experiment_failed` records and
zero completed cases. Privacy scanning was clean, the runner ended with no
queued or running jobs, and the candidate retained its fixed graph and metadata
digests.

Read-only inspection of the live `build_suite_comparison_request` executions
found 156 extracted regression metrics across the corpus. Sixty-five metrics
had `supported=true`, every metric had a reported value and normalized name,
and every suite input had an experiment ID. The comparison request still
contained no reported metrics because its `dossier_json` input is wired to the
raw LLM dossier node. Real papers describe the task type in natural language,
such as `回归预测（Regression）`, while the request builder deliberately accepts
only the canonical value `regression`.

The existing `validate_paper_dossier` node already validates citations,
normalizes metric names, sets `supported` and `ambiguous`, and emits
`validated_json`. Unit tests exercise that validated value directly, but the
generated graph does not connect it to the request builder. The defect is a
missing graph-wiring contract, not a missing metric parser or an overly strict
runtime validator.

## Options considered

1. **Rewire the request builder to `validate_paper_dossier.validated_json`
   (selected).** This restores the intended trust boundary, is deterministic,
   and preserves fail-closed behavior. The change is limited to the regression
   DSL builder and its generated regression DSL artifacts.
2. Normalize arbitrary dossier task labels inside the request builder. This
   duplicates validation logic and lets unvalidated raw LLM output cross the
   comparison boundary, so it is rejected.
3. Force the LLM prompt to emit exactly `regression`. This remains
   nondeterministic and would not protect against future prompt/model drift, so
   it is rejected.

## Design

`scripts/build_regression_dsl.py` will set the
`build_suite_comparison_request.dossier_json` selector to the
`validate_paper_dossier` node's `validated_json` output. Graph generation will
resolve nodes by stable title rather than relying on a newly duplicated magic
ID. The pure request-builder code remains unchanged.

The regression graph validator will assert all of the following:

- the validator is the sole dossier source for the comparison request builder;
- the selected output is exactly `validated_json`;
- the experiment input remains sourced from the safe parsed suite result;
- the success path still passes through `comparison_request_ok?` before the
  comparison HTTP request;
- no raw parser response, PDF text, CSV rows, protocol token, or secret is added
  to workflow outputs or safe acceptance evidence.

Both generated regression profiles must receive the same wiring change and
remain byte-deterministic. No classification, merged, prepare, multimodel, or
user-owned DSL is regenerated.

## Tests

Development follows red-green-refactor:

1. Add a DSL test that builds both regression profiles and fails unless the
   request builder selects `validate_paper_dossier.validated_json`.
2. Add a graph-validation mutation case that rewires the request builder to
   the raw dossier and requires `_validate_regression_graph` to reject it.
3. Run the focused regression DSL/code tests, regenerate only the two canonical
   regression DSL artifacts, and prove deterministic regeneration.
4. Run the complete repository test suite and the four-layer release checker.

The existing natural-metric and privacy tests remain authoritative. No test may
make the request builder accept a noncanonical raw task type.

## Candidate release and live verification

After all offline tests pass, publication uses the existing digest-checked
candidate release process:

1. read the current candidate identities without mutation;
2. create and verify a new explicit rollback backup from the current published
   candidate;
3. save and publish the corrected candidate graph with unchanged metadata;
4. independently read back the post-publish draft and active published UUIDs
   and exact graph and metadata digests;
5. run the five digest-pinned real cases with no code or configuration changes
   between cases;
6. require at least four completed cases, zero unknown failure categories, zero
   false strict-comparability claims, a clean privacy scan, no active runner
   jobs, and no post-run candidate drift.

Dify's normal sync-draft behavior may update the existing draft workflow row in
place, so publication does not require the draft UUID to change. Release
identity verification instead requires that the active published UUID differs
from the pre-change published UUID, that the draft and active published UUIDs
are distinct, and that both layers have the candidate graph digest and fixed
metadata digest. The explicit backup must independently retain the pre-change
graph and fixed metadata digests.

If the corrected graph reaches the comparison service but exposes another
generalized defect, preserve the safe failure distribution and stop again. Do
not add a fallback that manufactures paper metrics or strict-comparability
evidence.

## Completion criteria

The fix is complete only when the new candidate version passes the offline
release gates and the real five-case corpus gate, the operating documentation
records aggregate results and new immutable identities, all task-owned commits
are integrated safely, and the six user-owned DSL changes remain unstaged and
untouched.
