"""Bounded, deterministic orchestration for partial Ollama dossier extraction."""

from __future__ import annotations

import hashlib
import json
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from .chunking import serialize_pages
from .config import Settings
from .ollama import OllamaClient, OllamaError, call_budget
from .schemas import OllamaCompletion, PageChunk, PartialDossier

MAX_OLLAMA_CALLS = 12
MAX_EXTRACTION_SECONDS = 1_200

_CHUNK_ERROR_CODES = frozenset(
    {
        "qwen_chunk_empty",
        "qwen_chunk_invalid",
        "qwen_chunk_timeout",
        "qwen_chunk_truncated",
        "qwen_extraction_budget_exceeded",
    }
)
_RETRYABLE_CHUNK_ERRORS = frozenset(
    {
        "qwen_chunk_empty",
        "qwen_chunk_invalid",
        "qwen_chunk_timeout",
        "qwen_chunk_truncated",
    }
)


class ChunkError(RuntimeError):
    """A stable extraction error that never carries model or transport data."""

    def __init__(self, code: str) -> None:
        if code not in _CHUNK_ERROR_CODES:
            raise ValueError("unsupported chunk error code")
        self.code = code
        super().__init__(code)

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class ChunkSuccess:
    """A validated partial dossier associated with the exact submitted chunk."""

    chunk_id: str
    partial: PartialDossier

    @property
    def dossier(self) -> PartialDossier:
        """Compatibility alias for callers that call a partial a dossier."""

        return self.partial


@dataclass(frozen=True, slots=True)
class ChunkFailure:
    """Safe metadata for one terminal or budget-blocked chunk."""

    chunk_id: str
    code: str
    page_range: tuple[int, int]


@dataclass(frozen=True, slots=True)
class ChunkExtractionResult:
    """Ordered extraction outcomes and bounded execution diagnostics."""

    successes: tuple[ChunkSuccess, ...]
    failures: tuple[ChunkFailure, ...]
    call_count: int
    split_retry_count: int
    elapsed_seconds: float
    source_bytes: int | None
    source_sha256: str | None
    prompt_tokens: int | None
    completion_tokens: int | None


def parse_partial(completion: OllamaCompletion) -> PartialDossier:
    """Parse one complete JSON-only partial dossier without recovery heuristics."""

    if not completion.text.strip():
        raise ChunkError("qwen_chunk_empty")
    if completion.finish_reason == "length":
        raise ChunkError("qwen_chunk_truncated")
    try:
        value = json.loads(completion.text)
        return PartialDossier.model_validate(value)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        raise ChunkError("qwen_chunk_invalid") from None


def _page_range(chunk: PageChunk) -> tuple[int, int]:
    pages = tuple(page.page for page in chunk.pages)
    return min(pages), max(pages)


def _split_chunk(chunk: PageChunk) -> tuple[PageChunk, PageChunk] | None:
    midpoint = len(chunk.pages) // 2
    if midpoint == 0 or midpoint == len(chunk.pages):
        return None

    children: list[PageChunk] = []
    for suffix, pages in (
        (".1", chunk.pages[:midpoint]),
        (".2", chunk.pages[midpoint:]),
    ):
        source_bytes = len(serialize_pages(pages).encode("utf-8"))
        children.append(
            PageChunk(
                chunk_id=f"{chunk.chunk_id}{suffix}",
                pages=pages,
                source_bytes=source_bytes,
            )
        )
    return children[0], children[1]


def _client_error(error: OllamaError) -> ChunkError:
    if error.code == "ollama_timeout":
        return ChunkError("qwen_chunk_timeout")
    return ChunkError("qwen_chunk_invalid")


def extract_chunks(
    chunks: tuple[PageChunk, ...],
    client: OllamaClient,
    settings: Settings,
    clock: Callable[[], float] = time.monotonic,
) -> ChunkExtractionResult:
    """Extract chunks in depth-first order within fixed call and time budgets."""

    started_at = clock()
    call_limit = min(settings.max_ollama_calls, MAX_OLLAMA_CALLS)
    deadline_seconds = min(settings.request_timeout_seconds, MAX_EXTRACTION_SECONDS)
    successes: list[ChunkSuccess] = []
    failures: list[ChunkFailure] = []
    call_count = 0
    split_retry_count = 0
    max_source_bytes: int | None = None
    max_source_sha256: str | None = None
    max_prompt_tokens: int | None = None
    max_completion_tokens: int | None = None

    def observe_call(chunk: PageChunk) -> None:
        nonlocal max_source_bytes, max_source_sha256
        if max_source_bytes is None or chunk.source_bytes > max_source_bytes:
            max_source_bytes = chunk.source_bytes
            max_source_sha256 = hashlib.sha256(
                serialize_pages(chunk.pages).encode("utf-8")
            ).hexdigest()

    def observe_tokens(completion: OllamaCompletion) -> None:
        nonlocal max_prompt_tokens, max_completion_tokens
        if max_prompt_tokens is None or completion.prompt_tokens > max_prompt_tokens:
            max_prompt_tokens = completion.prompt_tokens
        if (
            max_completion_tokens is None
            or completion.completion_tokens > max_completion_tokens
        ):
            max_completion_tokens = completion.completion_tokens

    def budget_exceeded(chunk: PageChunk) -> None:
        failures.append(
            ChunkFailure(
                chunk_id=chunk.chunk_id,
                code="qwen_extraction_budget_exceeded",
                page_range=_page_range(chunk),
            )
        )

    def process(chunk: PageChunk) -> None:
        nonlocal call_count, split_retry_count

        elapsed = clock() - started_at
        if call_count >= call_limit or elapsed >= deadline_seconds:
            budget_exceeded(chunk)
            return

        call_count += 1
        observe_call(chunk)
        error: ChunkError | None = None
        try:
            with call_budget(deadline_seconds - elapsed):
                completion = client.complete(chunk)
            observe_tokens(completion)
            partial = parse_partial(completion)
        except OllamaError as caught:
            error = _client_error(caught)
        except (TimeoutError, socket.timeout):
            error = ChunkError("qwen_chunk_timeout")
        except ChunkError as caught:
            error = caught
        else:
            successes.append(ChunkSuccess(chunk_id=chunk.chunk_id, partial=partial))
            return

        assert error is not None
        if error.code not in _RETRYABLE_CHUNK_ERRORS:
            failures.append(
                ChunkFailure(
                    chunk_id=chunk.chunk_id,
                    code=error.code,
                    page_range=_page_range(chunk),
                )
            )
            return

        children = _split_chunk(chunk)
        if children is None:
            failures.append(
                ChunkFailure(
                    chunk_id=chunk.chunk_id,
                    code=error.code,
                    page_range=_page_range(chunk),
                )
            )
            return

        split_retry_count += 1
        process(children[0])
        process(children[1])
        return

    for chunk in chunks:
        process(chunk)

    elapsed_seconds = max(0.0, clock() - started_at)
    return ChunkExtractionResult(
        successes=tuple(successes),
        failures=tuple(failures),
        call_count=call_count,
        split_retry_count=split_retry_count,
        elapsed_seconds=elapsed_seconds,
        source_bytes=max_source_bytes,
        source_sha256=max_source_sha256,
        prompt_tokens=max_prompt_tokens,
        completion_tokens=max_completion_tokens,
    )
