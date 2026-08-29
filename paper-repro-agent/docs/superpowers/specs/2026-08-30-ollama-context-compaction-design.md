# Ollama context compaction design

Date: 2026-08-30

## Status and scope

The isolated Dify candidate app reaches every LLM node successfully with Ollama, but all five real regression cases fail closed at `validate_paper_dossier`. The captured prompts contain exactly 2,050 input tokens while the installed Ollama provider defaults to `num_ctx=2048`. The model emits prose rather than the required top-level JSON object even though each completion ends normally with `finish_reason=stop`. This is input-context truncation, not a permissive-parser problem and not completion truncation.

This change is limited to the isolated Ollama regression candidate. It must not modify the six protected user-owned DSL files, weaken the dossier validator, add JSON repair, or change DeepSeek runtime semantics.

## Fixed production envelope

The Ollama LLM node will pin these provider parameters:

- model: `qwen3:8b`
- `think=false`
- `num_ctx=16384`
- `num_predict=2048`
- temperature remains `0.1`

The parser payload injected into the prompt is limited to 7,301 UTF-8 bytes. The budget is derived conservatively as follows:

| Component | Reserved amount |
| --- | ---: |
| Ollama context | 16,384 tokens |
| Fixed system prompt | 4,475 UTF-8 bytes |
| Maximum protocol notes | 2,048 UTF-8 bytes |
| Chat-template overhead | 512 tokens |
| Completion reserve | 2,048 tokens |
| Parser payload remainder | 7,301 UTF-8 bytes |

The local model metadata pins `tokenizer.ggml.model=gpt2` and `tokenizer.ggml.pre=qwen2`. This is a byte-level BPE: each ordinary content token consumes a non-empty byte sequence, so the token count of valid UTF-8 content cannot exceed its UTF-8 byte count. The app input accepts at most 512 Unicode scalar values for notes, and each scalar encodes to at most four UTF-8 bytes, proving the 2,048-byte notes bound.

The production Dify node renders exactly one message with role `system`; `parsed_json` and `protocol_notes` are substitutions inside that message, not additional chat messages. On 2026-08-30 the pinned local `qwen3:8b` `/api/chat` boundary and the Dify Ollama model boundary both reported seven prompt tokens for the same empty one-system-message structure. The 512-token chat-template allowance therefore contains a measured 505-token safety margin. Every pre-publication readiness run must repeat both the empty-structure measurement (`prompt_tokens <= 512`) and the fully rendered maximum-shape measurement (`prompt_tokens <= 14,336`, leaving `num_predict=2048`). A tokenizer/provider/model/version change invalidates this proof and must fail readiness until the envelope is redesigned.

Consequently fixed prompt bytes, note bytes, and payload bytes are conservative content-token upper bounds, while the separately measured 512-token chat-template allowance covers inserted special/template tokens. The 2,048-token completion reserve cannot be borrowed by the input.

The fixed prompt byte count is calculated from `build_prepare_dsl("ollama")` after removing the two runtime substitutions (`parsed_json` and `protocol_notes`). Tests must fail if that count or the arithmetic changes without an intentional budget update.

## Context compaction contract

`dify/code/validate_parser.py` remains the single pure parser-response validator. It gains an optional context payload budget whose default is disabled. Existing validation and workflow-size compaction behavior are unchanged when the option is disabled. The Ollama builder activates the fixed 7,301-byte budget in the embedded code; the DeepSeek builder does not.

If the validated compact JSON already fits the context budget, it is returned byte-for-byte unchanged. Otherwise context compaction creates one compact JSON object containing:

- original `document_id`, `file_name`, and `page_count` values;
- an empty `markdown` field to avoid duplicating evidence text;
- page-addressable `elements`, each with `kind="text"`, the original positive page identity, and a non-empty excerpt;
- original parser warnings plus exactly one compaction warning, `parser_output_compacted_for_llm_context`.

The result must serialize with `ensure_ascii=false` and compact separators and must be no larger than 7,301 UTF-8 bytes. It must never synthesize reported values, metric names, citations, page identities, or evidence.

Only elements with a positive, non-boolean integer page and non-empty string text are eligible. Eligible text is grouped by page in original element order, while page identities are sorted numerically.

Compaction is deterministic:

1. Start with every eligible page and a per-page excerpt ceiling of 1,400 UTF-8 bytes.
2. Clip each page by UTF-8 bytes without splitting a Unicode code point. The marker is exactly `\n...[truncated for LLM context]...\n`. After reserving marker bytes, assign `floor(remaining/2)` bytes to the head and all odd remainder bytes to the tail. Each side keeps the longest complete-code-point prefix/suffix within its allocation.
3. For a fixed page set, test every common per-page ceiling in strict descending integer order from 1,400 through 160 bytes and take the first serialized object that fits. The finite scan does not assume monotonic serialized length.
4. If no ceiling fits, reduce the selected page count strictly one at a time (`k=n, n-1, ..., 1`) and repeat the descending ceiling scan. For `k` selected pages from `n`, use indices `floor(i*(n-1)/(k-1))` for `i=0..k-1`; `k=1` selects the first page. Thus any multi-page sample always retains the first and last eligible pages and evenly spans the interior.

If no positive page-addressable excerpt exists, or even the minimum one-page object cannot fit, parser validation fails closed and does not invoke the LLM.

The existing 360,000-character workflow compaction remains the default fallback for non-Ollama profiles. When the smaller Ollama context budget is active, context compaction runs directly against the validated source payload, so its own warning is not confused with `parser_output_compacted_for_workflow_limit`.

## Builder and artifact boundary

`scripts/build_multimodel_dsl.py` owns profile activation:

- Ollama embeds the 7,301-byte context budget and pins `num_ctx`/`num_predict`.
- DeepSeek keeps the option disabled and preserves the previous prompt/model behavior.
- Only `dify/paper-comparison-regression-workflow-ollama.yml` may be regenerated for this change.
- The six protected prepare/merged/multimodel DSL files must remain untouched and unstaged.

Offline verification must assert the generated Ollama graph digest and profile metadata and must assert no protected-path drift.

## Readiness and release gates

The readiness helper must exercise the production envelope before publication, through both local Ollama and Dify's model boundary. Both probes use one system message containing the actual fixed production prompt with a deterministic 7,301-byte synthetic parser substitution and a 512-character/2,048-byte synthetic notes substitution. The direct probe uses `/api/chat` (not `/api/generate`) with `options.num_ctx=16384` and `options.num_predict=2048`; the Dify probe uses `SystemPromptMessage` and the same model parameters. Each boundary first measures the empty one-system-message overhead and then the full maximum shape. Evidence records status, duration, content/input byte lengths, prompt-token count, parameter values, and SHA-256 digests only; no prompt or completion content.

Publication remains guarded and isolated:

1. Read-only health checks pass and the runner has no queued/running job.
2. Candidate app/draft/published identities and pre-change graph/metadata digests match their fixed values.
3. A rollback backup is created before the draft is changed.
4. Only the isolated candidate is imported/published.
5. Post-publication graph and metadata match the new offline evidence.
6. The exact five real regression cases run through the existing safe acceptance tool.
7. At least four of five cases complete, with zero false-strict results and zero unknown results; privacy, drift, and runner-idle gates also pass.

If any gate fails, retain fail-closed classification and the rollback evidence. Do not reset Docker, delete data, clear containers, or modify unrelated apps.

## Test boundary

RED tests must cover:

- exact budget arithmetic and fixed-prompt byte count;
- exact-fit payload unchanged and one-byte-over activation;
- UTF-8 byte limits with multibyte text and no broken code points;
- deterministic grouping, original in-page order, sorted positive pages, first/last preservation, and evenly spaced sampling;
- head/tail excerpts and non-empty page-addressable output;
- fail-closed behavior when no eligible page can be retained;
- exactly one context-compaction warning and no synthesized evidence;
- default/DeepSeek serialization unchanged;
- Ollama-only builder parameters and embedded budget;
- tokenizer metadata and 512-character notes bound, empty-template overhead, and full production-shaped readiness probes through both boundaries with content-free evidence;
- protected DSL paths unchanged.

No real publication occurs until focused tests, the full suite, offline artifact verification, and independent review all pass.
