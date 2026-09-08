"""UTF-8-safe, bounded construction of source-page chunks."""

from __future__ import annotations

import json

from .schemas import CandidateSelection, PageChunk, SourcePage

TRUNCATION_MARKER = "\n...[truncated for chunk budget]...\n"
MAX_PAGES_PER_CHUNK = 4
MAX_SOURCE_BYTES = 8_192


def clip_utf8(value: str, limit: int) -> str:
    """Clip a string by UTF-8 bytes while retaining a head and tail."""

    if limit < 0:
        raise ValueError("limit must be non-negative")

    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value

    marker = TRUNCATION_MARKER.encode("utf-8")
    remaining = limit - len(marker)
    if remaining < 0:
        raise ValueError("limit is too small for the truncation marker")

    head_limit = remaining // 2
    tail_limit = remaining - head_limit
    head = (
        encoded[:head_limit].decode("utf-8", errors="ignore")
        if head_limit
        else ""
    )
    tail = (
        encoded[-tail_limit:].decode("utf-8", errors="ignore")
        if tail_limit
        else ""
    )
    return head + TRUNCATION_MARKER + tail


def serialize_page(page: SourcePage) -> str:
    """Serialize one page as deterministic UTF-8 JSON source material."""

    return json.dumps(
        {"page": page.page, "kinds": list(page.kinds), "text": page.text},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def serialize_pages(pages: tuple[SourcePage, ...] | list[SourcePage]) -> str:
    """Serialize a chunk as newline-delimited page records."""

    return "\n".join(serialize_page(page) for page in pages)


def _serialized_bytes(pages: tuple[SourcePage, ...] | list[SourcePage]) -> int:
    return len(serialize_pages(pages).encode("utf-8"))


def _as_source_page(page: SourcePage) -> SourcePage:
    return SourcePage(
        page=page.page,
        text=page.text,
        kinds=page.kinds,
        table_text=page.table_text,
    )


def _fit_page(page: SourcePage, limit: int) -> SourcePage:
    if _serialized_bytes([page]) <= limit:
        return page

    marker_bytes = len(TRUNCATION_MARKER.encode("utf-8"))
    if _serialized_bytes(
        [
            SourcePage(
                page=page.page,
                text=TRUNCATION_MARKER,
                kinds=page.kinds,
                table_text=page.table_text,
            )
        ]
    ) > limit:
        raise ValueError("max_source_bytes is too small for a serialized page")

    encoded_length = len(page.text.encode("utf-8"))
    low = marker_bytes
    high = encoded_length
    best: SourcePage | None = None

    while low <= high:
        text_limit = (low + high) // 2
        clipped = clip_utf8(page.text, text_limit)
        candidate = SourcePage(
            page=page.page,
            text=clipped,
            kinds=page.kinds,
            table_text=page.table_text,
        )
        if _serialized_bytes([candidate]) <= limit:
            best = candidate
            low = text_limit + 1
        else:
            high = text_limit - 1

    if best is None:
        clipped = clip_utf8(page.text, marker_bytes)
        best = SourcePage(
            page=page.page,
            text=clipped,
            kinds=page.kinds,
            table_text=page.table_text,
        )

    return best


def _unique_pages(selection: CandidateSelection) -> tuple[SourcePage, ...]:
    pages_by_number: dict[int, SourcePage] = {}
    for candidate in sorted(selection.pages, key=lambda item: item.page):
        pages_by_number.setdefault(candidate.page, _as_source_page(candidate))
    return tuple(pages_by_number.values())


def build_chunks(
    selection: CandidateSelection,
    max_pages: int = MAX_PAGES_PER_CHUNK,
    max_source_bytes: int = MAX_SOURCE_BYTES,
) -> tuple[PageChunk, ...]:
    """Build stable, bounded chunks with overlap only across consecutive pages."""

    if not 1 <= max_pages <= MAX_PAGES_PER_CHUNK:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES_PER_CHUNK}")
    if not 1 <= max_source_bytes <= MAX_SOURCE_BYTES:
        raise ValueError(f"max_source_bytes must be between 1 and {MAX_SOURCE_BYTES}")

    pages = tuple(_fit_page(page, max_source_bytes) for page in _unique_pages(selection))
    chunks: list[PageChunk] = []
    next_index = 0
    previous: PageChunk | None = None

    while next_index < len(pages):
        overlap_page: SourcePage | None = None
        if (
            previous is not None
            and max_pages > 1
            and pages[next_index].page == previous.pages[-1].page + 1
        ):
            overlap_page = previous.pages[-1]

        chosen: list[SourcePage] = [overlap_page] if overlap_page is not None else []
        consumed = 0
        index = next_index

        while index < len(pages) and len(chosen) < max_pages:
            candidate = pages[index]
            trial = chosen + [candidate]
            if _serialized_bytes(trial) <= max_source_bytes:
                chosen.append(candidate)
                consumed += 1
                index += 1
                continue

            if overlap_page is not None and consumed == 0:
                overlap_page = None
                chosen = []
                continue
            break

        if consumed == 0:
            if overlap_page is not None:
                overlap_page = None
                chosen = []
                while next_index < len(pages) and len(chosen) < max_pages:
                    candidate = pages[next_index]
                    if _serialized_bytes(chosen + [candidate]) > max_source_bytes:
                        raise ValueError("unable to fit a page within max_source_bytes")
                    chosen.append(candidate)
                    consumed += 1
                    next_index += 1
                    break
            else:
                raise ValueError("unable to fit a page within max_source_bytes")

        source_bytes = _serialized_bytes(chosen)
        chunk = PageChunk(
            chunk_id=f"chunk-{len(chunks) + 1:03d}",
            pages=tuple(chosen),
            source_bytes=source_bytes,
        )
        chunks.append(chunk)
        next_index += consumed
        previous = chunk

    return tuple(chunks)
