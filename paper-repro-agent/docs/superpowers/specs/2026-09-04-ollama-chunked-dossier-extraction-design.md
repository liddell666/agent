# Ollama chunked paper-dossier extraction design

Date: 2026-09-04

## Status and scope

This design improves long-paper evidence recall and structured-output reliability
for the isolated Dify application `Paper comparison Regression Candidate`. It
keeps the existing `prepare -> run` user contract and the local Ollama model
`qwen3:8b`, but replaces the single dossier-generation call in the prepare path
with bounded page selection, chunk extraction, deterministic merging, and strict
evidence validation.

The change is limited to the Ollama regression candidate and a new local-only
extraction service. It must not modify the DeepSeek profile, the legacy
`Paper comparison V3.1 PDF CSV` application, the experiment runner's numerical
semantics, or any existing published rollback application.

The triggering live run parsed the PDF successfully but the dossier model used
all 3,072 completion tokens and stopped with `finish_reason=length`. The root
JSON was therefore incomplete and dossier validation failed at `$`. A local
failure helper displayed `paper_parse_failed`, which did not describe the actual
failure stage. This design addresses both the output truncation and the misleading
diagnostic boundary.

## Goals

- Increase recall of regression datasets, methods, MAE, RMSE, and R² evidence in
  long or table-heavy papers.
- Prevent one oversized completion from invalidating an otherwise parseable
  paper.
- Preserve original PDF page identities and verify every retained quotation
  against the corresponding parsed page.
- Keep all paper content and model execution local.
- Return stable stage-specific errors without exposing paper text, model output,
  credentials, or protocol values.
- Preserve deterministic experiment, comparison, privacy, and rollback
  behavior after dossier preparation succeeds.

## Non-goals

- Changing the local model or increasing its context beyond 16,384 tokens.
- Adding an automatic DeepSeek fallback.
- Using embeddings as the sole evidence-selection mechanism.
- Supporting classification, non-PDF papers, or new experiment metrics in this
  release.
- Weakening schema, evidence, ambiguity, or strict-comparability checks.
- Claiming that numeric similarity proves strict paper reproduction.

## Architecture

The current user-visible two-stage flow remains unchanged:

```text
prepare: paper PDF + training CSV -> reviewable protocol draft and token
run:     confirmed token + identical CSV -> experiment and comparison report
```

Only the dossier portion of `prepare` changes:

```text
paper PDF
  -> paper-parser
  -> paper-dossier-extractor
       -> normalize parsed pages
       -> select candidate pages
       -> build bounded chunks
       -> call local Ollama for each chunk
       -> split and retry failed chunks
       -> deterministically merge partial dossiers
       -> verify citations against source pages
  -> existing final dossier validation
  -> dataset diagnosis
  -> protocol draft creation
```

`paper-dossier-extractor` is a new local service with one clear responsibility:
turn page-addressable parser JSON into a validated partial-paper dossier by
orchestrating local Ollama calls. It is reachable only by service name on the
Dify Docker network and has no host or public port. It calls
`http://ollama:11434` directly so dynamic splitting, bounded retries, timing,
and finish-reason handling are implemented in ordinary testable Python rather
than a large static Dify graph.

The service receives parser JSON, not the original PDF or CSV. It does not
persist parser text, prompts, completions, or extracted evidence after returning
the response. The existing parser and runner authentication boundaries remain
unchanged. Dify calls `POST http://paper-dossier-extractor:8002/v1/extract-dossier`
with `X-Extractor-Token`. The Dify secret `DIFY_EXTRACTOR_API_TOKEN` and service
secret `PAPER_DOSSIER_EXTRACTOR_API_TOKEN` must contain exactly the same bytes.
Neither secret may appear in a DSL export, log, report, screenshot, or test
fixture.

## Candidate-page selection

Candidate selection scans every parsed page locally without calling a model.
Selection uses normalized Unicode text, element kinds, section cues, and bounded
term dictionaries. It includes:

- pages containing MAE, RMSE, R², coefficient-of-determination, and maintained
  Chinese or English aliases;
- pages identified as results, evaluation, experiment, dataset, data, methods,
  methodology, models, or equivalent maintained Chinese headings;
- pages containing tables, captions, or metric-like finite numeric expressions
  near supported metric names;
- the first two valid pages for title, abstract, and task context;
- the immediate preceding and following valid page of each selected evidence
  page, when present, for cross-page sentences and tables.

Selection order is deterministic. Duplicate pages are removed while preserving
ascending original page order. At most 32 candidate pages are retained. When the
uncapped set exceeds 32 pages, supported-metric pages rank first, then result and
table pages, dataset pages, method pages, and context-only pages. Ties use the
original page number. The response records `candidate_pages_capped`; it never
silently claims complete coverage after capping.

If no evidence candidate is found, the first two pages plus evenly spaced pages
from the document are selected, and `no_explicit_regression_evidence_candidate`
is recorded. This fallback supports task identification but cannot manufacture a
usable paper metric.

## Chunk construction and Ollama envelope

Short and long papers use one chunking implementation. A short paper naturally
produces one chunk; a long paper produces several.

- A chunk contains at most four selected pages.
- Serialized source material in one chunk is limited to exactly 8,192 bytes of
  UTF-8 text. The release is blocked unless prompt-envelope tests prove that
  constant fits together with the fixed prompt, template overhead, and completion
  reserve inside 16,384 tokens.
- Consecutive chunks overlap by one selected page when both contain consecutive
  source pages.
- Every element carries its original one-based PDF page number. Pages are never
  renumbered.
- Source text is clipped by UTF-8 bytes without splitting a code point. Head and
  tail text are retained with an explicit truncation marker.

Each chunk call uses:

- model `qwen3:8b`;
- `num_ctx=16384`;
- `num_predict=1536`;
- `temperature=0`;
- `think=false`;
- a compact JSON-only partial-dossier schema.

The partial schema contains only candidate title/task fields, datasets, methods,
supported regression metrics, gaps, and page-addressable evidence. It contains
no Markdown report or free-form analysis. Field counts and evidence excerpt
lengths are bounded so the output cannot expand indefinitely.

Before implementation is considered complete, a readiness test must prove the
exact prompt, source, template-overhead, and completion envelope fits within
16,384 tokens through both direct Ollama and the production service path. The
approximate 8 KiB source target is not itself a release proof.

## Retry and time bounds

The extractor permits at most 12 Ollama calls and 20 minutes of wall-clock work
for one request. Calls execute sequentially to match the current single-user
local resource envelope.

When a chunk returns `finish_reason=length`, an empty response, malformed JSON,
or a schema-invalid top-level value, the service does not repeat the same request.
It bisects the chunk by original page order and submits each non-empty half once.
A one-page chunk cannot be divided again and becomes a terminal failed chunk.

The service stops safely when the call or time budget is exhausted. It returns
the failed chunk identifiers and page ranges with one of these stable codes:

- `qwen_chunk_truncated`
- `qwen_chunk_invalid`
- `qwen_chunk_empty`
- `qwen_chunk_timeout`
- `qwen_extraction_budget_exceeded`

A failed required chunk prevents creation of a ready protocol. Partial facts may
be included only in bounded diagnostic counts, never as a successful final
dossier.

## Deterministic merge and conflict handling

Model output is never used as the final merge authority. Python code performs
normalization, merge, conflict detection, and citation validation.

- Title candidates are accepted only from the first two pages. Conflicting
  non-empty titles remain an explicit conflict.
- Dataset and method entries merge by normalized name. Evidence from distinct
  valid pages is retained and duplicate quotations are removed.
- Metrics merge by normalized metric name, dataset, split, and model qualifier.
- Supported names normalize to `mae`, `rmse`, or `r2`; unsupported metrics remain
  outside the numerical comparison path.
- Equal finite numeric values merge. Distinct values for the same qualified key
  are retained as separate candidates and marked `ambiguous_metric`.
- The merger never averages, recalculates, chooses the most plausible value, or
  changes a paper-reported sign, precision, unit, or qualifier.
- An explicit regression task statement may produce `task_type=regression`.
  Missing or conflicting evidence produces `task_type=uncertain`, not a guess.

Every retained citation must have a positive page within the parser's page
range, a non-empty excerpt, and a whitespace-normalized excerpt that occurs on
that exact normalized source page. A citation that fails membership validation
is removed and records `citation_source_mismatch`. A fact that consequently has
no required evidence is removed. No model-generated quotation is trusted solely
because it has a valid-looking page number.

The merged dossier is serialized canonically so the same parser input, model
partials, and configuration produce byte-identical output.

## Dify contract and diagnostics

The candidate's existing Start variables and `prepare -> run` contract do not
change. The prepare path replaces the single Ollama LLM node with one authenticated
HTTP request to `paper-dossier-extractor`, followed by the existing final dossier
validator. The run path, protocol token, CSV identity binding, model suite, and
comparison nodes remain unchanged.

Safe extraction diagnostics are included in the protocol preview and failure
output:

- extraction mode: `single` or `chunked`;
- PDF page count;
- candidate-page count;
- initial chunk count;
- Ollama call count;
- successful, split-retried, and failed chunk counts;
- elapsed seconds;
- stable warning and error codes;
- failed original page ranges when applicable.

Diagnostics never include page text, prompts, completions, dossier payloads,
PDF bytes, CSV rows, cookies, credentials, parser tokens, extractor tokens, or
protocol tokens.

The workflow must map failures to their actual stage. A valid parser response
followed by model truncation reports `qwen_chunk_truncated` or the applicable
extraction code. `paper_parse_failed` is reserved for an actual parser request
or parser-response failure.

## Error handling and service lifecycle

The new service exposes `GET /healthz`, which verifies configuration without
echoing secrets. Startup does not eagerly load or copy paper content. Ollama
network failure, model absence, deadline expiry, invalid source JSON, oversized
input, and authentication failure each map to bounded stable errors.

The service is stateless for the first release. A Dify retry restarts extraction
from the deterministic candidate and chunk plan. Request-level idempotency may
reuse a completed in-memory result during one process lifetime, but persistent
prompt or completion caching is outside scope because it would retain paper
content.

Container resource limits, one active extraction, and finite HTTP timeouts are
required. The existing `paper-parser`, `repro-runner`, Dify database, Redis,
Weaviate, and experiment storage must not be restarted or deleted as part of
normal extraction deployment.

## Testing and acceptance

Implementation follows test-driven development. Unit tests cover:

- Unicode normalization and every maintained selection term;
- metric, table, dataset, method, first-page, and neighbor selection;
- deterministic priority capping at 32 pages;
- one-batch short papers and multi-batch long papers;
- four-page, byte-size, overlap, original-page, and UTF-8 clipping rules;
- exact context-envelope arithmetic;
- valid partial parsing and bounded schema limits;
- length, malformed JSON, empty response, timeout, bisection, one-page failure,
  12-call, and 20-minute behavior;
- deterministic dataset, method, and metric merging;
- duplicate removal, conflicting values, and ambiguity preservation;
- exact-page citation membership and mismatch rejection;
- canonical deterministic serialization and privacy-safe diagnostics.

Integration tests use a fake Ollama boundary to prove:

- a short paper completes in one call;
- a long paper uses multiple ordered chunks;
- one truncated chunk is bisected without rerunning successful chunks;
- a permanently invalid required chunk fails closed;
- final Dify outputs distinguish parser and Qwen failures;
- the existing run path receives the same validated dossier contract.

Live acceptance uses the currently failing long-paper case plus maintained
synthetic and real regression cases. Release requires:

- the triggering case no longer ends as one 3,072-token truncated completion;
- every required chunk either returns a complete valid object or reports its
  exact original page range;
- expected MAE, RMSE, and R² fixture evidence is recalled;
- zero retained citations absent from their claimed source page;
- repeat runs produce identical merged output for deterministic recorded partials;
- total extraction stays within the 12-call and 20-minute bounds;
- all four regression models and both comparison conclusions remain valid;
- zero false-strict results and no leakage of paper text or secrets in evidence;
- focused tests, the full repository suite, readiness checks, artifact drift
  checks, and independent graph readback pass.

## Release and rollback

The implementation creates and validates a separate extractor container before
changing the Dify candidate. Publication is limited to
`Paper comparison Regression Candidate`. Before mutation, release tooling records
the exact candidate app, draft, published workflow, graph, and metadata identities
and creates an explicit rollback version.

The DeepSeek artifacts, legacy V3.1 application, six protected user-owned DSL
files, and existing experiment data remain byte-identical or unchanged as
applicable. If service health, privacy checks, graph parity, live acceptance, or
the time/call budget fails, the candidate is restored to the recorded rollback
workflow. Rollback does not delete the new image, parser data, Dify state, or
experiment results.

## Optimization paths after this release

Later improvements may add local BGE-M3 retrieval as a candidate-ranking
accelerator, a larger Qwen model on suitable hardware, or persistent encrypted
chunk caching. Each requires a separate design and acceptance gate. Embedding
retrieval must never become the only route by which supported metric evidence can
reach extraction, and no cloud fallback may run without explicit user approval.
