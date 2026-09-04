from paper_dossier_extractor.chunking import (
    TRUNCATION_MARKER,
    build_chunks,
    clip_utf8,
    serialize_page,
)
from paper_dossier_extractor.schemas import CandidatePage, CandidateSelection, SourcePage


def _selection(*pages: SourcePage) -> CandidateSelection:
    return CandidateSelection(
        pages=tuple(
            CandidatePage(
                page=page.page,
                text=page.text,
                kinds=page.kinds,
                priority=0,
            )
            for page in pages
        ),
        uncapped_count=len(pages),
    )


def test_build_chunks_limits_pages_bytes_and_overlaps_consecutive_batches() -> None:
    selection = _selection(
        *(SourcePage(page=page, text=f"page {page}") for page in range(1, 6))
    )

    chunks = build_chunks(selection, max_pages=4, max_source_bytes=8_192)

    assert all(len(chunk.pages) <= 4 for chunk in chunks)
    assert all(chunk.source_bytes <= 8_192 for chunk in chunks)
    assert [chunk.chunk_id for chunk in chunks] == ["chunk-001", "chunk-002"]
    assert chunks[0].pages[-1].page == chunks[1].pages[0].page
    assert all("�" not in page.text for chunk in chunks for page in chunk.pages)


def test_exact_serialized_page_budget_keeps_the_complete_page() -> None:
    page = SourcePage(page=7, text="exact fit", kinds=("text",))
    budget = len(serialize_page(page).encode("utf-8"))

    chunks = build_chunks(_selection(page), max_source_bytes=budget)

    assert chunks[0].source_bytes == budget
    assert chunks[0].pages[0].text == page.text


def test_one_byte_over_budget_clips_with_a_marker() -> None:
    page = SourcePage(page=7, text="x" * 200, kinds=("text",))
    budget = len(serialize_page(page).encode("utf-8")) - 1

    chunks = build_chunks(_selection(page), max_source_bytes=budget)

    assert chunks[0].source_bytes <= budget
    assert TRUNCATION_MARKER in chunks[0].pages[0].text
    assert "�" not in chunks[0].pages[0].text


def test_multibyte_text_is_clipped_without_split_code_points() -> None:
    page = SourcePage(page=8, text="中文🙂" * 200, kinds=("text",))

    chunks = build_chunks(_selection(page), max_source_bytes=256)

    assert chunks[0].source_bytes <= 256
    assert "�" not in chunks[0].pages[0].text
    assert TRUNCATION_MARKER in chunks[0].pages[0].text
    chunks[0].pages[0].text.encode("utf-8")


def test_one_oversized_page_becomes_one_bounded_clipped_page() -> None:
    page = SourcePage(page=3, text="long page " * 2_000, kinds=("text",))

    chunks = build_chunks(_selection(page), max_source_bytes=512)

    assert len(chunks) == 1
    assert len(chunks[0].pages) == 1
    assert chunks[0].source_bytes <= 512
    assert TRUNCATION_MARKER in chunks[0].pages[0].text


def test_non_consecutive_page_batches_do_not_add_artificial_overlap() -> None:
    selection = _selection(
        SourcePage(page=1, text="one"),
        SourcePage(page=2, text="two"),
        SourcePage(page=5, text="five"),
        SourcePage(page=6, text="six"),
    )

    chunks = build_chunks(selection, max_pages=2, max_source_bytes=8_192)

    assert [tuple(page.page for page in chunk.pages) for chunk in chunks] == [
        (1, 2),
        (5, 6),
    ]


def test_clip_utf8_retains_head_and_tail_at_a_byte_boundary() -> None:
    value = "开头" + ("中间" * 100) + "结尾"

    clipped = clip_utf8(value, 128)

    assert len(clipped.encode("utf-8")) <= 128
    assert clipped.startswith("开头")
    assert clipped.endswith("结尾")
    assert TRUNCATION_MARKER in clipped
    assert "�" not in clipped
