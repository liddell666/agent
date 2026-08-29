import hashlib
import json

import pytest

import dify.code.validate_parser as parser_validator


CONTEXT_WARNING = "parser_output_compacted_for_llm_context"
CONTEXT_MARKER = "\n...[truncated for LLM context]...\n"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_payload(**updates: object) -> dict:
    payload = {
        "document_id": "doc-context",
        "file_name": "paper.pdf",
        "page_count": 3,
        "markdown": "# Paper\n\nEvidence",
        "elements": [
            {"kind": "text", "page": 1, "text": "page one evidence"},
            {"kind": "text", "page": 2, "text": "page two evidence"},
            {"kind": "text", "page": 3, "text": "page three evidence"},
        ],
        "warnings": ["original warning"],
    }
    payload.update(updates)
    return payload


@pytest.mark.parametrize(
    ("payload", "expected_digest"),
    [
        (
            {
                "document_id": "doc-fit",
                "file_name": "fit.pdf",
                "page_count": 2,
                "markdown": "# Minimal Paper\n\nAUC: 0.91",
                "elements": [
                    {"page": 1, "text": "Dataset alpha."},
                    {"page": 2, "text": "AUC 0.91"},
                ],
                "warnings": ["table structure disabled"],
            },
            "67744a7bd5d7f6aaaf0decf2392cf020e1b71c0de166dd0b72ccf1da17770b37",
        ),
        (
            {
                "document_id": "doc-unicode",
                "file_name": "paper.pdf",
                "page_count": 1,
                "markdown": "中文🙂e\u0301",
                "elements": [
                    {"kind": "text", "page": 1, "text": "证据🙂e\u0301"},
                ],
                "warnings": ["unicode-warning"],
            },
            "bc2cfd5878f33f722101ed5fc66711e5023b87fbca808291583b031f6b7e44d4",
        ),
    ],
)
def test_default_compaction_golden_fitting_and_multibyte(
    payload: dict, expected_digest: str
) -> None:
    assert _sha256(parser_validator._compact_payload(payload)) == expected_digest


def test_default_compaction_golden_workflow_limit() -> None:
    payload = {
        "document_id": "doc-large",
        "file_name": "large.pdf",
        "page_count": 1,
        "markdown": "段落" * 180_100,
        "elements": [{"kind": "text", "page": 1, "text": "retained evidence"}],
        "warnings": ["original"],
    }

    serialized = parser_validator._compact_payload(payload)

    assert _sha256(serialized) == (
        "92a25ea5f6ed3a9800eb69c48e012be5c4fdb28f5d13ca0c8e5958e20222231c"
    )
    assert json.loads(serialized)["warnings"] == [
        "original",
        "parser_output_compacted_for_workflow_limit",
    ]


def test_default_validation_failure_golden() -> None:
    assert parser_validator.main("{", 200) == {
        "parsed_json": "",
        "parser_warnings": ["解析器响应不是有效 JSON。"],
        "can_continue": False,
    }


def test_context_budget_exact_fit_is_unchanged_and_one_byte_over_activates() -> None:
    payload = _valid_payload(
        markdown="M" * 2_000,
        elements=[
            {"kind": "text", "page": page, "text": f"page-{page}-" + "x" * 300}
            for page in range(1, 4)
        ],
    )
    original = parser_validator._serialized(payload)
    exact_budget = len(original.encode("utf-8"))

    assert (
        parser_validator._compact_payload(
            payload, context_budget_bytes=exact_budget
        )
        == original
    )

    compacted = parser_validator._compact_payload(
        payload, context_budget_bytes=exact_budget - 1
    )
    assert compacted != original
    assert len(compacted.encode("utf-8")) <= exact_budget - 1
    assert json.loads(compacted)["warnings"].count(CONTEXT_WARNING) == 1


def test_context_clip_uses_utf8_bytes_fixed_marker_and_odd_byte_goes_to_tail() -> None:
    value = "甲🙂乙é丙XYZ尾🙂" * 3
    limit = len(CONTEXT_MARKER.encode("utf-8")) + 13

    clipped = parser_validator._clip_text_utf8(value, limit)

    # Six bytes go to the head and seven to the tail. Complete code points leave
    # only "甲" at the head, while the seven-byte suffix is exactly "尾🙂".
    assert clipped == "甲" + CONTEXT_MARKER + "尾🙂"
    assert len(clipped.encode("utf-8")) <= limit
    assert clipped.encode("utf-8").decode("utf-8") == clipped


def test_context_compaction_groups_orders_and_filters_positive_pages() -> None:
    payload = _valid_payload(
        page_count=99,
        markdown="metric-only-in-markdown=" + "9" * 10_000,
        elements=[
            {"kind": "text", "page": 3, "text": "third"},
            {"kind": "text", "page": 1, "text": "first-a"},
            {"kind": "text", "page": True, "text": "boolean-page"},
            {"kind": "text", "page": 0, "text": "zero-page"},
            {"kind": "text", "page": -1, "text": "negative-page"},
            {"kind": "text", "page": "2", "text": "string-page"},
            {"kind": "text", "page": 1, "text": "first-b"},
            {"kind": "text", "page": 2, "text": "second"},
            {"kind": "text", "page": 4, "text": "   "},
        ],
        warnings=["original", CONTEXT_WARNING, CONTEXT_WARNING],
    )

    serialized = parser_validator._compact_payload(
        payload, context_budget_bytes=7_301
    )
    compacted = json.loads(serialized)

    assert compacted["document_id"] == "doc-context"
    assert compacted["file_name"] == "paper.pdf"
    assert compacted["page_count"] == 99
    assert compacted["markdown"] == ""
    assert [element["page"] for element in compacted["elements"]] == [1, 2, 3]
    assert compacted["elements"][0]["text"].index("first-a") < compacted[
        "elements"
    ][0]["text"].index("first-b")
    assert all(element["kind"] == "text" for element in compacted["elements"])
    assert all(element["text"] for element in compacted["elements"])
    assert compacted["warnings"] == ["original", CONTEXT_WARNING]
    assert len(serialized.encode("utf-8")) <= 7_301
    for forbidden in (
        "metric-only-in-markdown",
        "boolean-page",
        "zero-page",
        "negative-page",
        "string-page",
    ):
        assert forbidden not in serialized


def test_context_compaction_caps_at_32_and_uses_evenly_spaced_pages() -> None:
    page_count = 64
    payload = _valid_payload(
        page_count=page_count,
        markdown="source markdown " * 2_000,
        elements=[
            {"kind": "text", "page": page, "text": f"p{page}:" + "x" * 400}
            for page in range(1, page_count + 1)
        ],
    )

    serialized = parser_validator._compact_payload(
        payload, context_budget_bytes=7_301
    )
    compacted = json.loads(serialized)
    pages = [element["page"] for element in compacted["elements"]]
    expected = [1 + (index * (page_count - 1)) // 31 for index in range(32)]

    assert pages == expected
    assert len(pages) == 32
    assert pages[0] == 1
    assert pages[-1] == page_count
    assert len(serialized.encode("utf-8")) <= 7_301


def test_context_compaction_evenly_resamples_when_budget_reduces_page_count() -> None:
    candidate_pages = list(range(1, 11))
    payload = _valid_payload(
        page_count=10,
        markdown="source markdown " * 1_000,
        elements=[
            {"kind": "text", "page": page, "text": f"p{page}:" + "x" * 400}
            for page in candidate_pages
        ],
    )

    serialized = parser_validator._compact_payload(
        payload, context_budget_bytes=900
    )
    compacted = json.loads(serialized)
    pages = [element["page"] for element in compacted["elements"]]
    selected_count = len(pages)
    assert 1 < selected_count < len(candidate_pages)
    expected = [
        candidate_pages[(index * (len(candidate_pages) - 1)) // (selected_count - 1)]
        for index in range(selected_count)
    ]

    assert pages == expected
    assert pages[0] == candidate_pages[0]
    assert pages[-1] == candidate_pages[-1]
    assert len(serialized.encode("utf-8")) <= 900


def test_context_compaction_resamples_only_after_the_32_page_candidate_cap() -> None:
    page_count = 64
    payload = _valid_payload(
        page_count=page_count,
        markdown="source markdown " * 1_000,
        elements=[
            {"kind": "text", "page": page, "text": f"p{page}:" + "x" * 400}
            for page in range(1, page_count + 1)
        ],
    )

    serialized = parser_validator._compact_payload(
        payload, context_budget_bytes=1_000
    )
    compacted = json.loads(serialized)
    pages = [element["page"] for element in compacted["elements"]]
    candidates = [
        1 + (index * (page_count - 1)) // 31 for index in range(32)
    ]
    expected = [
        candidates[(index * (len(candidates) - 1)) // (len(pages) - 1)]
        for index in range(len(pages))
    ]

    assert pages == expected == [1, 21, 41, 64]
    assert len(serialized.encode("utf-8")) <= 1_000


@pytest.mark.parametrize(
    "payload,budget",
    [
        (
            _valid_payload(
                markdown="valid markdown " * 1_000,
                elements=[
                    {"page": 0, "text": "zero"},
                    {"page": -1, "text": "negative"},
                    {"page": True, "text": "boolean"},
                    {"page": "1", "text": "string"},
                    {"page": 1, "text": "   "},
                ],
            ),
            7_301,
        ),
        (_valid_payload(markdown="M" * 10_000), 32),
    ],
)
def test_context_compaction_fails_closed_without_eligible_or_feasible_page(
    payload: dict, budget: int
) -> None:
    result = parser_validator.main(
        json.dumps(payload, ensure_ascii=False),
        200,
        context_budget_bytes=budget,
    )

    assert result["can_continue"] is False
    assert result["parsed_json"] == ""
    assert result["parser_warnings"]


def test_context_compaction_tens_of_thousands_of_pages_is_bounded_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_count = 20_000
    payload = _valid_payload(
        page_count=page_count,
        markdown="source",
        elements=[
            {"kind": "text", "page": page, "text": "x"}
            for page in range(1, page_count + 1)
        ],
    )
    real_dumps = parser_validator.json.dumps
    dump_calls = 0

    def counted_dumps(*args: object, **kwargs: object) -> str:
        nonlocal dump_calls
        dump_calls += 1
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(parser_validator.json, "dumps", counted_dumps)
    first = parser_validator._compact_payload(payload, context_budget_bytes=7_301)
    first_call_count = dump_calls
    dump_calls = 0
    second = parser_validator._compact_payload(payload, context_budget_bytes=7_301)
    second_call_count = dump_calls

    compacted = json.loads(first)
    pages = [element["page"] for element in compacted["elements"]]
    expected = [1 + (index * (page_count - 1)) // 31 for index in range(32)]
    assert first == second
    assert pages == expected
    assert len(pages) <= 32
    # One source serialization plus the design's 32 floor checks and 1,241
    # descending-ceiling checks; implementations may return earlier.
    assert first_call_count <= 1_274
    assert second_call_count <= 1_274
