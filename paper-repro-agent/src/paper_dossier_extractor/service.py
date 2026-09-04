"""Stateless orchestration for authenticated paper-dossier extraction."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from paper_parser.schemas import PaperElement, ParsedPaper

from .chunking import build_chunks
from .config import Settings
from .extraction import MAX_OLLAMA_CALLS, ChunkExtractionResult, extract_chunks
from .merge import MergeResult, merge_partials
from .ollama import OllamaClient
from .schemas import (
    CandidateSelection,
    ExtractionDiagnostics,
    ExtractionError,
    ExtractionResponse,
    ExtractorReadinessResponse,
    PageChunk,
)
from .selection import normalize_pages, select_candidate_pages

_READINESS_PAGE_TEXT = (
    "Synthetic readiness results for a regression dataset and method. "
    "RMSE is reported for the regression task. "
    + "x" * 20_000
)


def _mode(chunks: tuple[PageChunk, ...]) -> str:
    return "single" if len(chunks) <= 1 else "chunked"


def _stable_warnings(*groups: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({warning for group in groups for warning in group}))


def _diagnostics(
    paper: ParsedPaper,
    selection: CandidateSelection,
    chunks: tuple[PageChunk, ...],
    extracted: ChunkExtractionResult,
    *,
    elapsed_seconds: float,
    warnings: Iterable[str] = (),
) -> ExtractionDiagnostics:
    errors = tuple(
        ExtractionError(code=failure.code, page_range=failure.page_range)
        for failure in extracted.failures
    )
    return ExtractionDiagnostics(
        mode=_mode(chunks),
        page_count=paper.page_count,
        candidate_page_count=len(selection.pages),
        initial_chunk_count=len(chunks),
        ollama_call_count=extracted.call_count,
        successful_chunk_count=len(extracted.successes),
        split_retry_count=extracted.split_retry_count,
        failed_chunk_count=len(extracted.failures),
        elapsed_seconds=max(0.0, elapsed_seconds),
        warnings=_stable_warnings(selection.warnings, warnings),
        errors=errors,
        failed_page_ranges=tuple(failure.page_range for failure in extracted.failures),
    )


def _readiness_paper() -> ParsedPaper:
    return ParsedPaper(
        document_id="readiness-synthetic-document",
        file_name="readiness-synthetic.pdf",
        page_count=1,
        markdown="",
        elements=[PaperElement(kind="text", page=1, text=_READINESS_PAGE_TEXT)],
        warnings=[],
    )


def _execute_pipeline(
    paper: ParsedPaper,
    settings: Settings,
    client: OllamaClient,
    clock: Callable[[], float],
) -> tuple[CandidateSelection, tuple[PageChunk, ...], ChunkExtractionResult, MergeResult | None, float]:
    started = clock()
    source_pages = normalize_pages(paper)
    selection = select_candidate_pages(paper, settings.max_candidate_pages)
    chunks = build_chunks(
        selection,
        settings.max_pages_per_chunk,
        settings.max_chunk_source_bytes,
    )
    extracted = extract_chunks(chunks, client, settings, clock)
    elapsed_seconds = clock() - started
    if extracted.failures:
        return selection, chunks, extracted, None, elapsed_seconds
    merged = merge_partials(
        tuple(item.partial for item in extracted.successes), source_pages
    )
    return selection, chunks, extracted, merged, elapsed_seconds


def failed_response(
    paper: ParsedPaper,
    selection: CandidateSelection,
    chunks: tuple[PageChunk, ...],
    extracted: ChunkExtractionResult,
    elapsed_seconds: float,
) -> ExtractionResponse:
    """Build a safe response when at least one required chunk failed."""

    return ExtractionResponse(
        ok=False,
        dossier=None,
        diagnostics=_diagnostics(
            paper,
            selection,
            chunks,
            extracted,
            elapsed_seconds=elapsed_seconds,
        ),
    )


def successful_response(
    paper: ParsedPaper,
    selection: CandidateSelection,
    chunks: tuple[PageChunk, ...],
    extracted: ChunkExtractionResult,
    merged: MergeResult,
    elapsed_seconds: float,
) -> ExtractionResponse:
    """Build a safe response from a validated deterministic merge."""

    return ExtractionResponse(
        ok=True,
        dossier=merged.dossier,
        diagnostics=_diagnostics(
            paper,
            selection,
            chunks,
            extracted,
            elapsed_seconds=elapsed_seconds,
            warnings=merged.warnings,
        ),
    )


def extract_readiness_probe(
    settings: Settings,
    client: OllamaClient,
    clock: Callable[[], float] = time.monotonic,
) -> ExtractorReadinessResponse:
    paper = _readiness_paper()
    selection, chunks, extracted, merged, elapsed_seconds = _execute_pipeline(
        paper,
        settings,
        client,
        clock,
    )
    effective_max_ollama_calls = min(settings.max_ollama_calls, MAX_OLLAMA_CALLS)
    if merged is None:
        raise RuntimeError("synthetic readiness extraction failed")
    if (
        extracted.source_bytes is None
        or extracted.source_sha256 is None
        or extracted.prompt_tokens is None
        or extracted.completion_tokens is None
    ):
        raise RuntimeError("synthetic readiness evidence incomplete")
    if extracted.prompt_tokens + settings.num_predict > settings.num_ctx:
        raise RuntimeError("synthetic readiness prompt exceeds reserved context")
    if (
        len(selection.pages) <= 0
        or len(chunks) <= 0
        or extracted.call_count <= 0
        or len(extracted.successes) <= 0
        or extracted.call_count > effective_max_ollama_calls
        or len(extracted.failures) != 0
    ):
        raise RuntimeError("synthetic readiness counters invalid")

    return ExtractorReadinessResponse(
        status="ready",
        service="paper-dossier-extractor",
        model=settings.ollama_model,
        source_bytes=extracted.source_bytes,
        source_sha256=extracted.source_sha256,
        prompt_tokens=extracted.prompt_tokens,
        completion_tokens=extracted.completion_tokens,
        num_ctx=settings.num_ctx,
        num_predict=settings.num_predict,
        max_chunk_source_bytes=settings.max_chunk_source_bytes,
        max_ollama_calls=effective_max_ollama_calls,
        page_count=paper.page_count,
        candidate_page_count=len(selection.pages),
        initial_chunk_count=len(chunks),
        ollama_call_count=extracted.call_count,
        successful_chunk_count=len(extracted.successes),
        split_retry_count=extracted.split_retry_count,
        failed_chunk_count=len(extracted.failures),
        elapsed_seconds=max(0.0, elapsed_seconds),
    )


def extract_dossier(
    paper: ParsedPaper,
    settings: Settings,
    client: OllamaClient,
    clock: Callable[[], float] = time.monotonic,
) -> ExtractionResponse:
    """Extract and merge a dossier without retaining request data."""

    selection, chunks, extracted, merged, elapsed_seconds = _execute_pipeline(
        paper,
        settings,
        client,
        clock,
    )
    if merged is None:
        return failed_response(
            paper,
            selection,
            chunks,
            extracted,
            elapsed_seconds,
        )
    return successful_response(
        paper,
        selection,
        chunks,
        extracted,
        merged,
        elapsed_seconds,
    )
