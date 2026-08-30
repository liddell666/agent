# Ollama real regression gate recovery design

Date: 2026-08-30

## Status and scope

The isolated Ollama regression candidate is published with the context-compaction
release, but its fixed five-case real acceptance gate reports three
`experiment_failed` cases and two `evidence_ambiguous` cases. Read-only Dify run
inspection proves that these labels come from two workflow contract defects rather
than failed model-suite execution:

- energy-efficiency, concrete-strength, and appliances-energy each completed a
  successful regression suite with four successful model results and a test digest,
  then failed while building the paper comparison request because the evidence-safe
  dossier retained `task_type="uncertain"`;
- wine-quality-red and real-estate-valuation reached the dossier LLM, consumed the
  full `num_predict=2048` allowance, ended with `finish_reason=length`, and therefore
  produced a truncated non-JSON response that the existing validator correctly
  rejected at `$`.

This change repairs those two boundaries without weakening citation validation,
repairing malformed model output, inventing a paper task type, changing runner
semantics, or modifying the six protected prepare/merged/multimodel DSL files.

## Comparison task-type authority

The dossier and the confirmed execution protocol answer different questions:

- the dossier describes only what the paper explicitly supports and may therefore
  retain `task_type="uncertain"`;
- the confirmed manifest and resulting experiment describe what the isolated
  candidate actually executed and remain strictly `task_type="regression"`.

`build_suite_comparison_request` will accept a validated dossier task type of
either `regression` or `uncertain` when, and only when, the experiment suite has
`task_type="regression"`, a valid experiment ID, and at least one supported,
unambiguous MAE, RMSE, or R² paper metric with a safe numeric value. It will continue
to reject an explicitly contradictory dossier task type such as `classification`,
a non-regression experiment, a malformed experiment ID, or a dossier without a
usable regression metric.

This rule does not rewrite the dossier or claim that the paper explicitly identifies
its task as regression. It uses the confirmed experiment as the execution authority
only for deciding whether a regression result may be compared with compatible
paper-reported metrics. Existing qualifier sanitization, provenance handling,
failure codes, and secret-safe terminal output remain unchanged.

The generalized regression comparison contract is provider-independent, so the
builder and both generated regression artifacts will carry it. Only the isolated
Ollama candidate will be published during this recovery.

## Closed Ollama token envelope

The Ollama dossier node will retain `qwen3:8b`, `think=false`, temperature `0.1`,
and `num_ctx=16384`. Its completion allowance will increase from 2,048 to 3,072
tokens. To preserve the existing conservative proof without increasing model memory,
the parser payload budget will decrease from 7,301 to 6,277 UTF-8 bytes:

| Component | Reserved amount |
| --- | ---: |
| Ollama context | 16,384 tokens |
| Fixed system prompt | 4,475 UTF-8 bytes |
| Maximum protocol notes | 2,048 UTF-8 bytes |
| Parser payload | 6,277 UTF-8 bytes |
| Chat-template overhead | 512 tokens |
| Completion reserve | 3,072 tokens |

The conservative input bound is `4,475 + 2,048 + 6,277 + 512 = 13,312`
tokens. Adding the 3,072-token completion reserve equals the 16,384-token context
exactly. The tokenizer assumptions, UTF-8 byte argument, deterministic context
compaction algorithm, and 32-page work bound from the preceding context-compaction
design remain unchanged.

The readiness contract changes its full-prompt ceiling from 14,336 to 13,312 input
tokens and requires the exact `num_predict=3072` provider parameter through both
the direct Ollama and Dify model boundaries. The empty-template ceiling remains 512.

## Fail-closed behavior

The workflow will not add JSON repair, continuation calls, retries, or permissive
parsing. If a completion still ends before a complete top-level dossier object, the
existing validator continues to reject it. If the smaller parser payload cannot
retain positive page-addressable evidence, parser validation continues to fail
before the LLM. Explicitly contradictory paper task types and unsupported or
ambiguous metrics continue to block comparison with
`invalid_regression_comparison_request`.

No raw paper text, CSV content, prompts, completions, backend error bodies,
credentials, protocol tokens, or arbitrary model output may be added to evidence or
diagnostic logs.

## Builder and artifact boundary

The source of truth remains `scripts/build_regression_dsl.py` plus the existing
shared parser validator and readiness modules. The implementation will:

- update the regression comparison helper in the builder;
- update the Ollama-only parser payload and completion budgets;
- regenerate the two builder-owned regression artifacts so source and generated
  output remain exact;
- leave DeepSeek model parameters and parser context behavior unchanged;
- leave all six protected prepare/merged/multimodel DSL artifacts byte-identical.

Offline verification must record the new generated digests and prove that only the
two regression artifacts differ among generated DSL files. Publication tooling must
still verify the fixed candidate app, draft, published, graph, and metadata identities
before mutation and create a rollback backup before publishing only the Ollama graph.

## Test and release gates

Implementation follows test-driven development. RED tests must prove:

- an `uncertain` validated dossier with a supported unambiguous regression metric
  and a successful regression experiment produces a comparison request;
- an explicitly non-regression dossier remains rejected;
- a non-regression experiment, invalid experiment ID, missing metric, unsupported
  metric, ambiguous metric, and unsafe numeric value remain rejected;
- the dossier object is not rewritten and only existing sanitized comparison fields
  are emitted;
- Ollama pins `num_ctx=16384`, `num_predict=3072`, and a 6,277-byte parser budget;
- the exact envelope arithmetic closes at 16,384 tokens and the readiness helper
  enforces a 13,312-token full-prompt ceiling;
- DeepSeek model/context settings remain unchanged;
- generated artifacts match the builder and protected DSL digests do not change.

Before publication, focused tests, the full local suite, artifact regeneration checks,
privacy scans, candidate identity checks, runner-idle checks, and both readiness
boundaries must pass. After guarded publication, independent graph/metadata readback
must pass before rerunning the exact five registered real cases.

The release succeeds only when at least four of five cases are `completed`, with
zero `false_strict`, zero `unknown`, clean privacy and drift checks, and an idle
runner. Any remaining case keeps its evidence-backed fail-closed classification;
the gate is never weakened to manufacture completion.
