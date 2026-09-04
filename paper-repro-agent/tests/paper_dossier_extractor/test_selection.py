from paper_parser.schemas import PaperElement, ParsedPaper

from paper_dossier_extractor.selection import normalize_pages, select_candidate_pages


def _paper(elements: list[PaperElement], page_count: int = 40) -> ParsedPaper:
    return ParsedPaper(
        document_id="synthetic-document",
        file_name="synthetic.pdf",
        page_count=page_count,
        markdown="",
        elements=elements,
        warnings=[],
    )


def _page(page: int, text: str, kind: str = "text") -> PaperElement:
    return PaperElement(kind=kind, page=page, text=text)


def test_normalize_pages_nfkc_collapses_whitespace_and_duplicate_elements() -> None:
    paper = _paper(
        [
            _page(1, "  Ｔｉｔｌｅ\n  of   paper  "),
            _page(1, "First page continuation"),
            _page(2, "Abstract"),
            _page(41, "outside the parsed page range"),
        ],
        page_count=40,
    )

    pages = normalize_pages(paper)

    assert [page.page for page in pages] == [1, 2]
    assert pages[0].text == "Title of paper First page continuation"
    assert pages[0].kinds == ("text",)


def test_selection_keeps_context_metrics_tables_captions_neighbors_and_unique_pages() -> None:
    elements = [
        _page(1, "Paper title"),
        _page(2, "Abstract and regression task context"),
        _page(9, "Prior discussion"),
        _page(10, "Results: RMSE = 2.0"),
        _page(10, "Repeated metric element", "text"),
        _page(11, "Continuation of the result"),
        _page(12, "Benchmark comparison", "table"),
        _page(13, "Figure caption for the evaluation", "caption"),
        _page(19, "Previous method context"),
        _page(20, "Methods: random forest configuration"),
        _page(21, "Following method context"),
    ]
    elements.extend(
        _page(page, f"General discussion on page {page}")
        for page in range(3, 41)
        if page not in {9, 10, 11, 12, 13, 19, 20, 21}
    )

    selection = select_candidate_pages(_paper(elements), limit=32)
    pages = selection.pages
    by_page = {item.page: item for item in pages}

    assert [item.page for item in pages] == sorted({item.page for item in pages})
    assert len(pages) == len({item.page for item in pages})
    assert {1, 2, 9, 10, 11, 12, 13, 19, 20, 21} <= set(by_page)
    assert "supported_metric" in by_page[10].reasons
    assert "table" in by_page[12].reasons
    assert "caption" in by_page[13].reasons
    assert by_page[10].priority > by_page[20].priority
    assert selection.warnings == ()


def test_selection_caps_thirty_three_uncapped_pages_by_priority_then_page() -> None:
    elements = [_page(page, f"Results RMSE = {page}.0") for page in range(1, 34)]

    selection = select_candidate_pages(_paper(elements), limit=32)

    assert [item.page for item in selection.pages] == sorted(
        {item.page for item in selection.pages}
    )
    assert any("supported_metric" in item.reasons for item in selection.pages)
    assert selection.warnings == ("candidate_pages_capped",)
    assert selection.uncapped_count == 33
    assert len(selection.pages) == 32
    assert [item.page for item in selection.pages] == list(range(1, 33))


def test_no_match_fallback_keeps_required_and_evenly_spaced_pages() -> None:
    paper = _paper([_page(page, f"Unrelated prose on page {page}") for page in range(1, 41)])

    selection = select_candidate_pages(paper, limit=8)
    selected = [item.page for item in selection.pages]
    interior = [page for page in selected if page not in {1, 2, 40}]

    assert selected[:2] == [1, 2]
    assert selected[-1] == 40
    assert len(interior) == 5
    gaps = [right - left for left, right in zip(interior, interior[1:])]
    assert max(gaps) - min(gaps) <= 1
    assert selection.warnings == ("no_explicit_regression_evidence_candidate",)
