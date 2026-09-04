"""Stateless orchestration for authenticated paper-dossier extraction."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from paper_parser.schemas import ParsedPaper

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
    PageChunk,
)
from .selection import normalize_pages, select_candidate_pages


def _mode(chunks: tuple[PageChunk, ...]) -> str:
    return "single" if len(chunks) <= 1 else "chunked"


def _stable_warnings(*groups: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({warning for group in groups for warning in group}))


def _diagnostics(
    paper: ParsedPaper,
    selection: CandidateSelection,
    chunks: tuple[PageChunk, ...],
    extracted: ChunkExtractionResult,
    settings: Settings,
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
        source_bytes=extracted.source_bytes,
        source_sha256=extracted.source_sha256,
        prompt_tokens=extracted.prompt_tokens,
        completion_tokens=extracted.completion_tokens,
        num_ctx=settings.num_ctx,
        num_predict=settings.num_predict,
        max_chunk_source_bytes=settings.max_chunk_source_bytes,
        max_ollama_calls=min(settings.max_ollama_calls, MAX_OLLAMA_CALLS),
        warnings=_stable_warnings(selection.warnings, warnings),
        errors=errors,
        failed_page_ranges=tuple(failure.page_range for failure in extracted.failures),
    )


def failed_response(
    paper: ParsedPaper,
    selection: CandidateSelection,
    chunks: tuple[PageChunk, ...],
    extracted: ChunkExtractionResult,
    settings: Settings,
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
            settings,
            elapsed_seconds=elapsed_seconds,
        ),
    )


def successful_response(
    paper: ParsedPaper,
    selection: CandidateSelection,
    chunks: tuple[PageChunk, ...],
    extracted: ChunkExtractionResult,
    merged: MergeResult,
    settings: Settings,
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
            settings,
            elapsed_seconds=elapsed_seconds,
            warnings=merged.warnings,
        ),
    )


def extract_dossier(
    paper: ParsedPaper,
    settings: Settings,
    client: OllamaClient,
    clock: Callable[[], float] = time.monotonic,
) -> ExtractionResponse:
    """Extract and merge a dossier without retaining request data."""

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
        return failed_response(
            paper,
            selection,
            chunks,
            extracted,
            settings,
            clock() - started,
        )
    merged = merge_partials(
        tuple(item.partial for item in extracted.successes), source_pages
    )
    return successful_response(
        paper,
        selection,
        chunks,
        extracted,
        merged,
        settings,
        clock() - started,
    )
