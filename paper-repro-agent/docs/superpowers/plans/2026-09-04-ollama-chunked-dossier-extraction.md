# Ollama Chunked Paper-Dossier Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the isolated Ollama regression candidate's single long-paper completion with a bounded local extraction service that selects relevant pages, chunks them, retries truncation by bisection, deterministically merges evidence, and reports the correct failure stage.

**Architecture:** A new stateless `paper_dossier_extractor` FastAPI service receives the existing page-aware parser JSON, selects and chunks candidate pages, calls `qwen3:8b` through Ollama, and validates and merges partial dossiers in Python. Only the Ollama regression DSL calls this service; the DeepSeek workflow, legacy V3.1 app, experiment runner, and run branch remain unchanged.

**Tech Stack:** Python 3.12, FastAPI 0.141.1, Pydantic 2.13.4, `urllib.request`, Ollama `/api/chat`, Docker Compose, Dify 1.16 workflow YAML, pytest 9.1.1.

## Global Constraints

- Implement only the approved design in `docs/superpowers/specs/2026-09-04-ollama-chunked-dossier-extraction-design.md`.
- Keep `qwen3:8b`, `num_ctx=16384`, `num_predict=1536`, `temperature=0`, and `think=false` for chunk extraction.
- Limit each chunk's serialized source material to exactly 8,192 UTF-8 bytes, at most four selected pages, and one overlapping selected page when consecutive batches cover consecutive source pages.
- Retain at most 32 selected pages, at most 12 Ollama calls, and at most 1,200 seconds wall-clock time per extraction request.
- Preserve original one-based PDF page numbers; never renumber, invent, average, recalculate, or silently choose conflicting evidence.
- Keep paper text, prompts, completions, PDF bytes, CSV rows, credentials, cookies, extractor tokens, and protocol tokens out of logs and evidence artifacts.
- Use `DIFY_EXTRACTOR_API_TOKEN` in Dify and `PAPER_DOSSIER_EXTRACTOR_API_TOKEN` in the service; require byte-identical values and transmit them only in `X-Extractor-Token`.
- Expose the extractor only on Docker network `docker_default` as `paper-dossier-extractor:8002`; do not publish a host port.
- Modify and publish only `Paper comparison Regression Candidate`; leave DeepSeek artifacts, legacy V3.1, and the six protected prepare/merged/multimodel DSL files unchanged.
- Preserve unrelated tracked and untracked workspace changes. Never reset, clean, or stage files outside the exact task paths.

## File Structure

Create these focused units:

- `src/paper_dossier_extractor/config.py`: bounded runtime settings.
- `src/paper_dossier_extractor/schemas.py`: source-page, chunk, partial-dossier, diagnostics, and API contracts.
- `src/paper_dossier_extractor/selection.py`: deterministic page normalization and candidate ranking.
- `src/paper_dossier_extractor/chunking.py`: UTF-8-safe clipping, page grouping, and overlap.
- `src/paper_dossier_extractor/ollama.py`: sanitized Ollama transport and completion metadata.
- `src/paper_dossier_extractor/extraction.py`: partial parsing, call/time budget, and bisection retry.
- `src/paper_dossier_extractor/merge.py`: deterministic merge, ambiguity handling, and exact-page citation membership.
- `src/paper_dossier_extractor/service.py`: end-to-end orchestration without persistence.
- `src/paper_dossier_extractor/api.py`: authenticated FastAPI boundary and safe errors.
- `Dockerfile.extractor`: minimal non-root runtime using the existing pinned repro dependencies.
- `tests/paper_dossier_extractor/`: unit, service, API, and privacy tests.

Modify these existing units:

- `compose.yaml`: add the internal-only extractor service.
- `.env.example`: document the extractor service secret without a real value.
- `scripts/build_regression_dsl.py`: replace only the Ollama candidate's dossier LLM node with the extractor HTTP path.
- `tests/test_dify_regression_code.py`: test safe response normalization and stage-specific errors.
- `tests/test_dify_regression_dsl.py`: test Ollama-only graph wiring and protected-profile stability.
- `scripts/check_ollama_readiness.py`: add the production extractor boundary probe.
- `tests/test_ollama_readiness.py` and `tests/test_ollama_readiness_contract.py`: enforce the new envelope and privacy-safe evidence.
- `docs/release-workflow.md`: add deployment, health, acceptance, and rollback instructions.

---

### Task 0: Checkpoint the already verified localized regression fix

**Files:**
- Modify only by committing existing changes: `scripts/build_regression_dsl.py`
- Modify only by committing existing changes: `tests/test_dify_regression_code.py`
- Modify only by committing existing changes: `dify/paper-comparison-regression-workflow.yml`
- Modify only by committing existing changes: `dify/paper-comparison-regression-workflow-ollama.yml`
- Modify only by committing existing changes: `docs/release-workflow.md`

**Interfaces:**
- Consumes: Existing working-tree fix that normalizes localized `回归` to canonical `regression`.
- Produces: A clean committed baseline for files that the new Ollama extraction work will edit.

- [ ] **Step 1: Review the exact existing diff and verify no unrelated path is included**

Run:

```powershell
git diff -- paper-repro-agent/scripts/build_regression_dsl.py paper-repro-agent/tests/test_dify_regression_code.py paper-repro-agent/dify/paper-comparison-regression-workflow.yml paper-repro-agent/dify/paper-comparison-regression-workflow-ollama.yml paper-repro-agent/docs/release-workflow.md
```

Expected: only localized task-type normalization, regenerated regression artifacts, and the matching release record appear.

- [ ] **Step 2: Run the already established regression and release tests**

Run:

```powershell
cd D:\Documents\arcgis\paper-repro-agent
python -m pytest tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py tests/test_workflow_release_integrity.py -q
```

Expected: `91 passed`.

- [ ] **Step 3: Commit only the five baseline paths**

Run:

```powershell
cd D:\Documents\arcgis
git add -- paper-repro-agent/scripts/build_regression_dsl.py paper-repro-agent/tests/test_dify_regression_code.py paper-repro-agent/dify/paper-comparison-regression-workflow.yml paper-repro-agent/dify/paper-comparison-regression-workflow-ollama.yml paper-repro-agent/docs/release-workflow.md
git commit -m "fix: normalize localized regression task type"
```

Expected: one commit containing exactly those five paths; unrelated ArcGIS and workspace files remain unstaged.

---

### Task 1: Define extractor settings and immutable contracts

**Files:**
- Create: `src/paper_dossier_extractor/__init__.py`
- Create: `src/paper_dossier_extractor/config.py`
- Create: `src/paper_dossier_extractor/schemas.py`
- Create: `tests/paper_dossier_extractor/__init__.py`
- Create: `tests/paper_dossier_extractor/test_config.py`
- Create: `tests/paper_dossier_extractor/test_schemas.py`

**Interfaces:**
- Consumes: `paper_parser.schemas.ParsedPaper` and `PaperDossier`.
- Produces: `Settings`, `SourcePage`, `CandidatePage`, `CandidateSelection`, `PageChunk`, `OllamaCompletion`, `PartialDossier`, `FinalDossier`, `ExtractionDiagnostics`, and `ExtractionResponse`.

- [ ] **Step 1: Write failing settings and schema tests**

Add tests with these exact assertions:

```python
from pydantic import ValidationError
import pytest

from paper_dossier_extractor.config import Settings
from paper_dossier_extractor.schemas import ExtractionDiagnostics, SourcePage


def test_settings_pin_approved_limits() -> None:
    settings = Settings(api_token="x" * 32)
    assert settings.ollama_base_url == "http://ollama:11434"
    assert settings.ollama_model == "qwen3:8b"
    assert settings.num_ctx == 16_384
    assert settings.num_predict == 1_536
    assert settings.max_chunk_source_bytes == 8_192
    assert settings.max_pages_per_chunk == 4
    assert settings.max_candidate_pages == 32
    assert settings.max_ollama_calls == 12
    assert settings.request_timeout_seconds == 1_200
    assert settings.max_concurrent_extractions == 1


def test_source_page_requires_original_positive_page() -> None:
    with pytest.raises(ValidationError):
        SourcePage(page=0, text="Results: RMSE 2.0", kinds=["text"])


def test_diagnostics_forbid_source_content() -> None:
    fields = set(ExtractionDiagnostics.model_fields)
    assert fields == {
        "mode", "page_count", "candidate_page_count", "initial_chunk_count",
        "ollama_call_count", "successful_chunk_count", "split_retry_count",
        "failed_chunk_count", "elapsed_seconds", "warnings", "errors",
        "failed_page_ranges",
    }
```

- [ ] **Step 2: Run tests and confirm the package is absent**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_config.py tests/paper_dossier_extractor/test_schemas.py -q
```

Expected: collection fails with `ModuleNotFoundError: paper_dossier_extractor`.

- [ ] **Step 3: Add the bounded settings contract**

Implement `config.py` with these exact fields:

```python
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAPER_DOSSIER_EXTRACTOR_", extra="ignore"
    )
    api_token: str = Field(min_length=32)
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:8b"
    num_ctx: int = 16_384
    num_predict: int = 1_536
    max_chunk_source_bytes: int = 8_192
    max_pages_per_chunk: int = 4
    max_candidate_pages: int = 32
    max_ollama_calls: int = 12
    request_timeout_seconds: int = 1_200
    ollama_call_timeout_seconds: int = 360
    max_concurrent_extractions: int = 1


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Add strict immutable schemas**

Implement every model with `ConfigDict(extra="forbid", frozen=True)`. Use these exact public signatures:

```python
class SourcePage(BaseModel):
    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    kinds: tuple[str, ...] = ()

class CandidatePage(SourcePage):
    priority: int = Field(ge=0)
    reasons: tuple[str, ...] = ()

class CandidateSelection(BaseModel):
    pages: tuple[CandidatePage, ...]
    uncapped_count: int = Field(ge=0)
    warnings: tuple[str, ...] = ()

class PageChunk(BaseModel):
    chunk_id: str = Field(pattern=r"^chunk-[0-9]{3}(?:\.[12])*$")
    pages: tuple[SourcePage, ...] = Field(min_length=1, max_length=4)
    source_bytes: int = Field(ge=1, le=8_192)

class OllamaCompletion(BaseModel):
    text: str
    finish_reason: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)

class PartialEvidence(BaseModel):
    page: int = Field(ge=1)
    source_text: str = Field(min_length=1, max_length=320)

class PartialFact(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    evidence: tuple[PartialEvidence, ...] = Field(min_length=1, max_length=3)

class PartialMetric(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    reported_value: float | None = None
    dataset: str | None = Field(default=None, max_length=200)
    split: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    evidence: tuple[PartialEvidence, ...] = Field(min_length=1, max_length=3)

class PartialDossier(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    title_evidence: tuple[PartialEvidence, ...] = Field(default=(), max_length=1)
    research_problem: str | None = Field(default=None, max_length=1_000)
    task_type: Literal["regression", "uncertain"] = "uncertain"
    task_evidence: tuple[PartialEvidence, ...] = Field(default=(), max_length=2)
    datasets: tuple[PartialFact, ...] = Field(default=(), max_length=12)
    methods: tuple[PartialFact, ...] = Field(default=(), max_length=20)
    metrics: tuple[PartialMetric, ...] = Field(default=(), max_length=40)
    gaps: tuple[str, ...] = Field(default=(), max_length=20)

class FinalEvidence(BaseModel):
    page: int = Field(ge=1)
    source_text: str = Field(min_length=1, max_length=320)
    source: Literal["paper"] = "paper"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

class FinalFact(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    evidence: tuple[FinalEvidence, ...] = Field(min_length=1)

class FinalMetric(BaseModel):
    name: Literal["mae", "rmse", "r2"]
    reported_value: float | None = None
    model: Literal[
        "linear_regression", "random_forest", "gradient_boosting", "xgboost"
    ] | None = None
    dataset: str | None = Field(default=None, max_length=200)
    split: str | None = Field(default=None, max_length=100)
    evidence: tuple[FinalEvidence, ...] = Field(min_length=1)

class FinalDossier(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    research_problem: str = Field(default="", max_length=1_000)
    task_type: Literal["regression", "uncertain"]
    datasets: tuple[FinalFact, ...] = ()
    methods: tuple[FinalFact, ...] = ()
    metrics: tuple[FinalMetric, ...] = ()
    gaps: tuple[str, ...] = ()
```

Add `ExtractionError(code: str, page_range: tuple[int, int] | None)`, the exact diagnostics fields asserted above, and `ExtractionResponse(ok: bool, dossier: FinalDossier | None, diagnostics: ExtractionDiagnostics)` with a validator requiring a dossier exactly when `ok` is true. `FinalDossier` is extractor-owned because its optional metric model qualifier is required by the existing suite-comparison contract but is absent from `paper_parser.schemas.PaperDossier`; do not widen the shared parser schema for this isolated candidate.

- [ ] **Step 5: Run tests and commit the contracts**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_config.py tests/paper_dossier_extractor/test_schemas.py -q
```

Expected: all tests pass.

Commit:

```powershell
git add src/paper_dossier_extractor tests/paper_dossier_extractor
git commit -m "feat: define local dossier extractor contracts"
```

---

### Task 2: Select candidate pages and build bounded overlapping chunks

**Files:**
- Create: `src/paper_dossier_extractor/selection.py`
- Create: `src/paper_dossier_extractor/chunking.py`
- Create: `tests/paper_dossier_extractor/test_selection.py`
- Create: `tests/paper_dossier_extractor/test_chunking.py`

**Interfaces:**
- Consumes: `ParsedPaper`, `Settings`, `SourcePage`, and `CandidateSelection`.
- Produces: `normalize_pages(paper: ParsedPaper) -> tuple[SourcePage, ...]`, `select_candidate_pages(paper: ParsedPaper, limit: int = 32) -> CandidateSelection`, and `build_chunks(selection: CandidateSelection, max_pages: int = 4, max_source_bytes: int = 8192) -> tuple[PageChunk, ...]`.

- [ ] **Step 1: Write failing page-selection tests**

Cover exact behavior with a 40-page synthetic `ParsedPaper`: first two pages are retained, `RMSE = 2.0` outranks a Methods page, table/caption kinds add selection reasons, neighbors are retained, duplicate pages collapse, final pages are ascending, and a 33-page result records `candidate_pages_capped` with length 32.

Use this core assertion:

```python
selection = select_candidate_pages(paper, limit=32)
assert [item.page for item in selection.pages] == sorted(
    {item.page for item in selection.pages}
)
assert any("supported_metric" in item.reasons for item in selection.pages)
assert selection.warnings == ("candidate_pages_capped",)
assert len(selection.pages) == 32
```

Also prove the no-match fallback retains pages 1, 2, the last page, evenly spaced interior pages, and warning `no_explicit_regression_evidence_candidate`.

- [ ] **Step 2: Run selection tests and verify RED**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_selection.py -q
```

Expected: FAIL because `selection.py` does not exist.

- [ ] **Step 3: Implement deterministic normalization and ranking**

Use compiled, case-insensitive normalized term groups with these public priorities:

```python
PRIORITY_METRIC = 500
PRIORITY_RESULT_TABLE = 400
PRIORITY_DATASET = 300
PRIORITY_METHOD = 200
PRIORITY_CONTEXT = 100

METRIC_TERMS = (
    "mae", "mean absolute error", "mean absolute deviation",
    "rmse", "root mean squared error", "root mean square error",
    "r²", "r2", "r-squared", "coefficient of determination",
    "平均绝对误差", "均方根误差", "决定系数",
)
```

Build page text from eligible parser elements in original order, normalize with Unicode NFKC plus collapsed whitespace, score every page, add immediate valid neighbors, then cap by `(-priority, page)` before restoring ascending page order. Never use model output during selection.

- [ ] **Step 4: Write failing UTF-8 chunk tests**

Tests must prove:

```python
chunks = build_chunks(selection, max_pages=4, max_source_bytes=8_192)
assert all(len(chunk.pages) <= 4 for chunk in chunks)
assert all(chunk.source_bytes <= 8_192 for chunk in chunks)
assert [chunk.chunk_id for chunk in chunks] == ["chunk-001", "chunk-002"]
assert chunks[0].pages[-1].page == chunks[1].pages[0].page
assert all("�" not in page.text for chunk in chunks for page in chunk.pages)
```

Add exact-fit, one-byte-over, multibyte Chinese, one oversized page, and non-consecutive-page cases. Non-consecutive batches do not add artificial overlap.

- [ ] **Step 5: Implement UTF-8-safe clipping and chunk construction**

Implement these helpers:

```python
TRUNCATION_MARKER = "\n...[truncated for chunk budget]...\n"

def clip_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    marker = TRUNCATION_MARKER.encode("utf-8")
    remaining = limit - len(marker)
    head_limit = remaining // 2
    tail_limit = remaining - head_limit
    head = encoded[:head_limit].decode("utf-8", errors="ignore")
    tail = encoded[-tail_limit:].decode("utf-8", errors="ignore")
    return head + TRUNCATION_MARKER + tail
```

Serialize each page with its page number and kinds when calculating the exact source-byte budget. If one page exceeds the budget, clip its text to the largest complete-code-point value whose serialized representation is at most 8,192 bytes. Generate stable IDs in construction order.

- [ ] **Step 6: Run focused tests and commit selection/chunking**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_selection.py tests/paper_dossier_extractor/test_chunking.py -q
```

Expected: all tests pass.

Commit:

```powershell
git add src/paper_dossier_extractor/selection.py src/paper_dossier_extractor/chunking.py tests/paper_dossier_extractor/test_selection.py tests/paper_dossier_extractor/test_chunking.py
git commit -m "feat: select and chunk paper evidence pages"
```

---

### Task 3: Add the sanitized Ollama transport and prompt envelope

**Files:**
- Create: `src/paper_dossier_extractor/ollama.py`
- Create: `tests/paper_dossier_extractor/test_ollama.py`

**Interfaces:**
- Consumes: `PageChunk` and `Settings`.
- Produces: `build_messages(chunk: PageChunk) -> list[dict[str, str]]` and `OllamaClient.complete(chunk: PageChunk) -> OllamaCompletion`.

- [ ] **Step 1: Write failing transport and prompt tests**

Use a fake `urlopen` and assert the exact request boundary:

```python
assert payload["model"] == "qwen3:8b"
assert payload["stream"] is False
assert payload["think"] is False
assert payload["options"] == {
    "num_ctx": 16_384,
    "num_predict": 1_536,
    "temperature": 0,
}
assert request.full_url == "http://ollama:11434/api/chat"
assert "RMSE = 2.0" not in caplog.text
```

Test success metadata, HTTP failure, timeout, malformed response, missing model response, and non-finite token counts. Public exceptions expose only stable codes: `ollama_unavailable`, `ollama_timeout`, or `ollama_invalid_response`.

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_ollama.py -q
```

Expected: FAIL because `ollama.py` does not exist.

- [ ] **Step 3: Implement the compact JSON-only prompt**

The system prompt must contain these requirements verbatim:

```text
Return exactly one compact JSON object and no Markdown or analysis.
Use only the supplied page objects. Preserve their original page numbers.
Extract only title, research_problem, regression task evidence, datasets,
methods, MAE, RMSE, and R2. Copy a short exact source_text from its page.
Put title support in title_evidence and task-type support in task_evidence.
Do not infer a value, dataset, split, model, quotation, or page number.
If evidence is absent, omit the fact and add a short gap.
```

The user message is canonical compact JSON containing only `chunk_id` and the chunk's page objects. The prompt contains no protocol, CSV, secret, or prior chunk output.

- [ ] **Step 4: Implement `OllamaClient` with sanitized errors**

Use `urllib.request.Request`, `urlopen(..., timeout=settings.ollama_call_timeout_seconds)`, `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`, and `json.loads`. Return only completion text, finish reason, and token counts. Do not log request or response bodies. Convert transport and decoding exceptions to an `OllamaError(code)` that stringifies only its code.

- [ ] **Step 5: Run and commit the transport**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_ollama.py -q
```

Expected: all tests pass.

Commit:

```powershell
git add src/paper_dossier_extractor/ollama.py tests/paper_dossier_extractor/test_ollama.py
git commit -m "feat: add bounded local ollama extraction client"
```

---

### Task 4: Parse partial dossiers and bisect failed chunks within fixed budgets

**Files:**
- Create: `src/paper_dossier_extractor/extraction.py`
- Create: `tests/paper_dossier_extractor/test_extraction.py`

**Interfaces:**
- Consumes: ordered `PageChunk` values, `OllamaClient`, monotonic clock, maximum 12 calls, and 1,200-second deadline.
- Produces: `extract_chunks(chunks: tuple[PageChunk, ...], client: OllamaClient, settings: Settings, clock: Callable[[], float] = time.monotonic) -> ChunkExtractionResult`.

- [ ] **Step 1: Write failing success and bisection tests**

Use a scripted fake client and assert:

```python
result = extract_chunks((four_page_chunk,), client, settings)
assert [item.chunk_id for item in result.successes] == ["chunk-001.1", "chunk-001.2"]
assert result.call_count == 3
assert result.split_retry_count == 1
assert result.failures == ()
```

The first response has `finish_reason="length"`; each half returns valid JSON. Add tests for malformed JSON, empty output, schema-invalid output, one-page terminal failure, call 13 rejection, and deadline expiry using a deterministic fake clock.

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_extraction.py -q
```

Expected: FAIL because `extraction.py` does not exist.

- [ ] **Step 3: Implement strict partial parsing**

Use exact JSON parsing with no fence stripping or substring recovery:

```python
def parse_partial(completion: OllamaCompletion) -> PartialDossier:
    if not completion.text.strip():
        raise ChunkError("qwen_chunk_empty")
    if completion.finish_reason == "length":
        raise ChunkError("qwen_chunk_truncated")
    try:
        value = json.loads(completion.text)
        return PartialDossier.model_validate(value)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        raise ChunkError("qwen_chunk_invalid") from None
```

- [ ] **Step 4: Implement depth-first deterministic bisection**

Process chunks in original order. On a retryable chunk error, split pages at `len(pages) // 2`, create `.1` and `.2` IDs, recalculate source bytes, and process the left half before the right half. Never resubmit a successful chunk. Before every call, reject when `call_count == 12` or elapsed time is at least 1,200 seconds. Map transport timeout to `qwen_chunk_timeout`; map exhausted capacity to `qwen_extraction_budget_exceeded`.

- [ ] **Step 5: Run and commit extraction control**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_extraction.py -q
```

Expected: all tests pass.

Commit:

```powershell
git add src/paper_dossier_extractor/extraction.py tests/paper_dossier_extractor/test_extraction.py
git commit -m "feat: bisect invalid ollama extraction chunks"
```

---

### Task 5: Deterministically merge facts and verify exact-page citations

**Files:**
- Create: `src/paper_dossier_extractor/merge.py`
- Create: `tests/paper_dossier_extractor/test_merge.py`

**Interfaces:**
- Consumes: `tuple[PartialDossier, ...]` and the normalized `tuple[SourcePage, ...]`.
- Produces: `merge_partials(partials: tuple[PartialDossier, ...], source_pages: tuple[SourcePage, ...]) -> MergeResult`, containing a validated `FinalDossier`, warnings, and rejected-citation counts.

- [ ] **Step 1: Write failing merge and citation tests**

Cover duplicate facts, duplicate quotations, equal metrics, conflicting metrics, title restricted to pages 1–2, conflicting titles, regression task evidence versus uncertain task type, whitespace-normalized citation membership, wrong-page quotation rejection, and deterministic output under repeated inputs.

Core assertions:

```python
result = merge_partials(partials, source_pages)
assert result.dossier.metrics[0].name == "rmse"
assert result.dossier.metrics[0].reported_value == 2.0
assert result.dossier.metrics[0].evidence[0].page == 17
assert "citation_source_mismatch" in mismatch_result.warnings
assert [item.reported_value for item in conflict_result.dossier.metrics] == [1.8, 2.0]
assert "ambiguous_metric" in conflict_result.warnings
assert result.canonical_json == repeated.canonical_json
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_merge.py -q
```

Expected: FAIL because `merge.py` does not exist.

- [ ] **Step 3: Implement normalization and citation membership**

Normalize names with Unicode NFKC, case folding, punctuation-to-space conversion, and collapsed whitespace. Normalize source text only for membership comparison; preserve the original excerpt in output. Metric aliases map exactly to `mae`, `rmse`, or `r2`. Require the normalized excerpt to be a substring of the normalized text for the claimed page.

- [ ] **Step 4: Implement stable merge keys and ambiguity**

Use these keys:

```python
fact_key = normalize_name(fact.name)
metric_key = (
    normalize_metric(metric.name),
    normalize_optional(metric.dataset),
    normalize_optional(metric.split),
    normalize_optional(metric.model),
)
evidence_key = (evidence.page, normalize_evidence(evidence.source_text))
```

Sort facts by normalized name then first evidence page. Sort metrics by metric key, numeric value, then first evidence page. Preserve distinct finite values for one metric key and append one `ambiguous_metric` warning. Remove facts that lose all required evidence. Serialize with `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))`.

Accept a title only when its validated `title_evidence` points to page 1 or 2. If distinct validated title candidates remain, choose the candidate with the earliest evidence page and then the normalized lexical value, and record `ambiguous_title`. If none remains, emit the explicit non-factual label `未提取到标题` and gap `paper_title_not_extracted`. Set `task_type=regression` only when at least one validated `task_evidence` quotation supports the model's regression label; otherwise set it to `uncertain`.

- [ ] **Step 5: Run and commit deterministic merging**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_merge.py -q
```

Expected: all tests pass.

Commit:

```powershell
git add src/paper_dossier_extractor/merge.py tests/paper_dossier_extractor/test_merge.py
git commit -m "feat: merge and verify paper dossier evidence"
```

---

### Task 6: Expose the stateless authenticated extraction service

**Files:**
- Create: `src/paper_dossier_extractor/service.py`
- Create: `src/paper_dossier_extractor/api.py`
- Create: `tests/paper_dossier_extractor/test_service.py`
- Create: `tests/paper_dossier_extractor/test_api.py`

**Interfaces:**
- Consumes: `ParsedPaper`, `Settings`, and injectable `OllamaClient`.
- Produces: `extract_dossier(paper: ParsedPaper, settings: Settings, client: OllamaClient, clock: Callable[[], float] = time.monotonic) -> ExtractionResponse`, `GET /healthz`, and authenticated `POST /v1/extract-dossier`.

- [ ] **Step 1: Write failing orchestration tests**

Prove a short paper reports `mode="single"`, a long paper reports `mode="chunked"`, successful extraction returns a dossier, any required failed chunk returns `ok=false` and no dossier, candidate warnings survive, diagnostics contain counts only, and neither `caplog` nor serialized diagnostics contains source text or model output.

- [ ] **Step 2: Run service tests and verify RED**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_service.py -q
```

Expected: FAIL because `service.py` does not exist.

- [ ] **Step 3: Implement orchestration**

Implement this exact order:

```python
def extract_dossier(paper, settings, client, clock=time.monotonic):
    started = clock()
    source_pages = normalize_pages(paper)
    selection = select_candidate_pages(paper, settings.max_candidate_pages)
    chunks = build_chunks(
        selection,
        settings.max_pages_per_chunk,
        settings.max_chunk_source_bytes,
    )
    extracted = extract_chunks(chunks, client, settings, clock)
    if extracted.failures:
        return failed_response(paper, selection, chunks, extracted, clock() - started)
    merged = merge_partials(
        tuple(item.partial for item in extracted.successes), source_pages
    )
    return successful_response(
        paper, selection, chunks, extracted, merged, clock() - started
    )
```

No request data is written to disk or module-level caches.

- [ ] **Step 4: Write failing API authentication and sanitization tests**

Assert public health, 401 for missing/wrong tokens, constant-time valid-token comparison, 422 for invalid parser JSON, 429 for a second concurrent extraction, 502 for Ollama failure, and 200 for semantic extraction failure with `ok=false`. Verify exception responses contain stable codes and a random request ID but no source content or traceback.

- [ ] **Step 5: Implement the FastAPI boundary**

Use `secrets.compare_digest`, one `asyncio.Semaphore(1)`, and `run_in_threadpool`. The request body is a strict `ParsedPaper`; the response model is `ExtractionResponse`. Log only `request_id`, terminal code, counts, and elapsed seconds. Health returns:

```json
{"status":"ok","service":"paper-dossier-extractor","model":"qwen3:8b"}
```

- [ ] **Step 6: Run and commit service/API tests**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor -q
```

Expected: all extractor tests pass.

Commit:

```powershell
git add src/paper_dossier_extractor/service.py src/paper_dossier_extractor/api.py tests/paper_dossier_extractor/test_service.py tests/paper_dossier_extractor/test_api.py
git commit -m "feat: expose local paper dossier extractor"
```

---

### Task 7: Package the extractor as an internal-only Docker service

**Files:**
- Create: `Dockerfile.extractor`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Create: `tests/paper_dossier_extractor/test_compose_contract.py`

**Interfaces:**
- Consumes: `paper_dossier_extractor.api:app`, `requirements-repro.lock`, Docker network `docker_default`, and deployment secrets.
- Produces: healthy service `paper-dossier-extractor:8002` reachable by Dify and Ollama with no host port.

- [ ] **Step 1: Write failing Compose contract tests**

Parse Compose YAML and assert:

```python
service = compose["services"]["paper-dossier-extractor"]
assert service["container_name"] == "paper-dossier-extractor"
assert "ports" not in service
assert service["networks"]["dify"]["aliases"] == ["paper-dossier-extractor"]
assert service["environment"]["PAPER_DOSSIER_EXTRACTOR_OLLAMA_BASE_URL"] == "http://ollama:11434"
assert service["mem_limit"] == "2g"
assert service["cpus"] == 2
assert service["cap_drop"] == ["ALL"]
assert service["security_opt"] == ["no-new-privileges:true"]
```

Also assert `.env.example` includes a value name but no usable secret.

- [ ] **Step 2: Run contract tests and verify RED**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor/test_compose_contract.py -q
```

Expected: FAIL because the service is absent.

- [ ] **Step 3: Add a minimal non-root image**

`Dockerfile.extractor` must install `requirements-repro.lock`, copy `src`, create an unprivileged `app` user, expose 8002, and run:

```dockerfile
CMD ["uvicorn", "paper_dossier_extractor.api:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
```

- [ ] **Step 4: Add the Compose service and environment template**

Use a health check against `http://localhost:8002/healthz`, `restart: unless-stopped`, `init: true`, `mem_limit: 2g`, `cpus: 2`, read-only source mount consistent with the current local development pattern, and no host port. Add:

```dotenv
PAPER_DOSSIER_EXTRACTOR_API_TOKEN=replace-with-the-same-random-value-used-by-dify-of-at-least-32-characters
PAPER_DOSSIER_EXTRACTOR_OLLAMA_BASE_URL=http://ollama:11434
```

- [ ] **Step 5: Validate Compose, build, and run the focused tests**

Run:

```powershell
docker compose config --quiet
docker compose build paper-dossier-extractor
python -m pytest tests/paper_dossier_extractor/test_compose_contract.py -q
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit packaging**

```powershell
git add Dockerfile.extractor compose.yaml .env.example tests/paper_dossier_extractor/test_compose_contract.py
git commit -m "build: package internal dossier extractor"
```

---

### Task 8: Wire only the Ollama regression candidate to the extractor

**Files:**
- Modify: `scripts/build_regression_dsl.py`
- Modify: `tests/test_dify_regression_code.py`
- Modify: `tests/test_dify_regression_dsl.py`
- Modify: `dify/paper-comparison-regression-workflow-ollama.yml`

**Interfaces:**
- Consumes: parser validator `parsed_json`, Dify secret `DIFY_EXTRACTOR_API_TOKEN`, and `ExtractionResponse`.
- Produces: an Ollama-only HTTP node `extract_paper_dossier_chunks`, a code node `normalize_extractor_response`, stage-correct failure outputs, and the existing validated dossier input contract.

- [ ] **Step 1: Record protected and DeepSeek artifact hashes**

Run a read-only SHA-256 capture for:

```text
dify/paper-comparison-regression-workflow.yml
dify/paper-comparison-prepare-workflow.yml
dify/paper-comparison-prepare-workflow-ollama.yml
dify/paper-comparison-multimodel-workflow.yml
dify/paper-comparison-multimodel-workflow-ollama.yml
dify/paper-comparison-merged-workflow.yml
dify/paper-comparison-merged-workflow-ollama.yml
```

Store only path and digest in ignored `.live-artifacts/pre-extractor-protected-digests.json`.

- [ ] **Step 2: Write failing code-node tests**

Add tests for `normalize_extractor_response(body, status_code)`:

```python
assert success == {
    "dossier_json": canonical_dossier_json,
    "extraction_diagnostics_json": canonical_diagnostics_json,
    "can_continue": True,
    "error_code": "",
}
assert truncated["can_continue"] is False
assert truncated["error_code"] == "qwen_chunk_truncated"
assert "paper_parse_failed" not in json.dumps(truncated)
```

Test HTTP 401, 429, 502, malformed JSON, `ok=false`, and success. Inputs containing fake paper text or tokens must not be copied into failure output.

- [ ] **Step 3: Run code tests and verify RED**

Run:

```powershell
python -m pytest tests/test_dify_regression_code.py -k extractor -q
```

Expected: FAIL because the response normalizer is absent.

- [ ] **Step 4: Implement the embedded normalizer**

Add `_extractor_response_normalizer_code()` to the builder. It accepts only bounded status, dossier, diagnostics, and stable errors. It returns the four fields asserted above, truncates warning/error lists to 50 codes, and never includes raw HTTP bodies in an error response.

- [ ] **Step 5: Write failing Ollama-only graph tests**

Assert the Ollama graph:

- declares secret `DIFY_EXTRACTOR_API_TOKEN`;
- has no `extract_paper_dossier` LLM node;
- posts validated parser JSON to `http://paper-dossier-extractor:8002/v1/extract-dossier`;
- sends `X-Extractor-Token` through a secret selector;
- routes `normalize_extractor_response.can_continue=true` to the existing final dossier validator;
- routes extraction failure to a terminal prepare error with the exact extractor code;
- leaves every run-branch node and edge behaviorally identical.

Assert the DeepSeek regression graph retains its current LLM node and is byte-identical after Ollama generation.

- [ ] **Step 6: Run graph tests and verify RED**

Run:

```powershell
python -m pytest tests/test_dify_regression_dsl.py -k extractor -q
```

Expected: FAIL because the Ollama graph still uses the single LLM node.

- [ ] **Step 7: Implement profile-specific graph replacement**

In `build_regression_dsl(profile)`, branch only when `profile == "ollama"`. Replace the dossier LLM node with the authenticated HTTP node and insert the response normalizer. Preserve existing node IDs where compatibility allows; otherwise use deterministic fixed IDs and update all affected edges through one mapping. Add diagnostics to the prepare preview without adding paper text.

- [ ] **Step 8: Regenerate only the Ollama regression artifact and prove isolation**

Run:

```powershell
python scripts/build_regression_dsl.py --profile ollama
python -m pytest tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py -q
```

Expected: tests pass. Recompute protected hashes and require all recorded paths except `paper-comparison-regression-workflow-ollama.yml` to be unchanged.

- [ ] **Step 9: Commit candidate wiring**

```powershell
git add scripts/build_regression_dsl.py tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py dify/paper-comparison-regression-workflow-ollama.yml
git commit -m "feat: route ollama dossier extraction through chunks"
```

---

### Task 9: Extend readiness, privacy, and regression gates

**Files:**
- Modify: `scripts/check_ollama_readiness.py`
- Modify: `tests/test_ollama_readiness.py`
- Modify: `tests/test_ollama_readiness_contract.py`
- Create: `tests/paper_dossier_extractor/test_privacy.py`

**Interfaces:**
- Consumes: live Ollama and extractor health boundaries and the fixed production prompt envelope.
- Produces: content-free readiness evidence with `direct_ollama`, `extractor_boundary`, exact parameters, counts, durations, and hashes.

- [ ] **Step 1: Write failing readiness contract tests**

Require the readiness document to include:

```python
assert result["status"] == "ready"
assert result["probes"]["extractor_boundary"]["status"] == "ready"
assert result["probes"]["extractor_boundary"]["num_ctx"] == 16_384
assert result["probes"]["extractor_boundary"]["num_predict"] == 1_536
assert result["probes"]["extractor_boundary"]["max_chunk_source_bytes"] == 8_192
assert result["probes"]["extractor_boundary"]["max_ollama_calls"] == 12
assert "prompt" not in serialized
assert "completion" not in serialized
assert "source_text" not in serialized
```

Also fail readiness if the measured maximum production prompt plus 1,536 completion tokens exceeds 16,384.

- [ ] **Step 2: Run readiness tests and verify RED**

Run:

```powershell
python -m pytest tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py -q
```

Expected: FAIL because the extractor boundary is absent.

- [ ] **Step 3: Implement extractor readiness probing**

Probe `GET http://paper-dossier-extractor:8002/healthz` from the Dify network and execute one synthetic maximum-shape extraction through the service using only generated page text. Record status, elapsed time, source byte count, prompt/completion token counts, configuration values, and SHA-256 digests. Never record generated source or completion content.

- [ ] **Step 4: Add privacy regression tests**

Inject sentinel strings into source pages, model output, a fake token, and an exception. Capture API responses, logs, diagnostics, readiness JSON, and Dify failure output. Assert no sentinel appears and no key name suggests raw prompt/completion storage.

- [ ] **Step 5: Run focused gates and commit**

Run:

```powershell
python -m pytest tests/paper_dossier_extractor tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py -q
```

Expected: all tests pass.

Commit:

```powershell
git add scripts/check_ollama_readiness.py tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py tests/paper_dossier_extractor/test_privacy.py
git commit -m "test: gate chunked ollama extraction readiness"
```

---

### Task 10: Verify locally, publish the isolated candidate, and record acceptance

**Files:**
- Modify: `docs/release-workflow.md`
- Create ignored evidence: `.live-artifacts/ollama-chunked-extractor-readiness.json`
- Create ignored evidence: `.live-artifacts/ollama-chunked-extractor-acceptance.json`

**Interfaces:**
- Consumes: healthy parser, extractor, Ollama, runner, exact candidate identity, generated Ollama DSL, and the user's previously failing long-paper case.
- Produces: guarded candidate publication, privacy-safe acceptance evidence, exact rollback identity, and updated operator documentation.

- [ ] **Step 1: Run static and full repository verification**

Run:

```powershell
python -m pytest -q
git diff --check
docker compose config --quiet
```

Expected: the full suite passes, diff check is empty, and Compose validation exits 0.

- [ ] **Step 2: Build and start only the new extractor service**

Run:

```powershell
docker compose up -d --build paper-dossier-extractor
docker inspect paper-dossier-extractor --format '{{.State.Health.Status}}'
```

Expected: `healthy`. Do not restart parser, runner, Dify database, Redis, Weaviate, or experiment storage.

- [ ] **Step 3: Run content-free readiness**

Run:

```powershell
python scripts/check_ollama_readiness.py --output .live-artifacts/ollama-chunked-extractor-readiness.json
```

Expected: aggregate `ready`, direct Ollama `ready`, extractor boundary `ready`, exact 16,384/1,536/8,192/12 parameters, and no content fields.

- [ ] **Step 4: Capture candidate and rollback identities before mutation**

Read the candidate app UUID, draft UUID, active published workflow UUID, graph digest, metadata digest, and current runner activity. Abort if the app is not `17fe51d4-091f-4729-87ee-3c0a2e920918`, identities differ from the immediately reviewed live state, or the runner has a queued/running job. Create an explicit Dify rollback version before changing the draft.

- [ ] **Step 5: Import, verify, and publish only the candidate graph**

Apply the generated `dify/paper-comparison-regression-workflow-ollama.yml` graph to the candidate draft, re-enter `DIFY_EXTRACTOR_API_TOKEN` only through the Dify secret UI, read back the draft graph, and require its behavioral digest to equal the generated Ollama DSL. Publish once, then require the active graph and metadata digests to match the reviewed draft.

- [ ] **Step 6: Rerun the previously failing long-paper prepare case**

Use the same user-authorized PDF and regression CSV without copying their contents into logs. Require parser success, extraction `ok=true`, no whole-document `finish_reason=length`, a valid merged dossier, valid citation membership, a ready protocol preview, at most 12 Ollama calls, and at most 1,200 seconds.

- [ ] **Step 7: Complete the confirmed four-model run**

Before the protocol expires, run with the exact same CSV bytes, `confirm_protocol=true`, `cv_folds=3`, `n_iter=1`, and models `linear_regression`, `random_forest`, `gradient_boosting`, and `xgboost`. Require four successful model results, finite MAE/RMSE/R², shared split provenance, both rankings, and separate strict and approximate conclusions.

- [ ] **Step 8: Write privacy-safe acceptance evidence**

Record only app/workflow/run IDs, graph/source digests, service/model parameters, page/chunk/call counts, durations, model statuses, comparison counts/statuses, warning/error codes, and booleans proving privacy scans. Do not record filenames, local paths, page text, prompts, completions, PDF bytes, CSV rows, tokens, cookies, or secrets.

- [ ] **Step 9: Update documentation and run final verification**

Document startup, health, two-stage use, long-paper diagnostics, error meanings, exact rollback ID, and the accepted run. Run:

```powershell
python -m pytest tests/paper_dossier_extractor tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py tests/test_ollama_readiness.py tests/test_ollama_readiness_contract.py tests/test_workflow_release_integrity.py -q
git diff --check
```

Expected: all focused tests pass and diff check is empty.

- [ ] **Step 10: Commit release documentation**

```powershell
git add docs/release-workflow.md
git commit -m "docs: record chunked ollama extraction release"
```

If any health, identity, privacy, drift, time, call-count, citation, experiment, or comparison gate fails, restore the exact recorded rollback workflow and retain the failed evidence as aggregate codes and counts only.
