"""Deterministic page normalization and candidate selection."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from paper_parser.schemas import ParsedPaper

from .schemas import CandidatePage, CandidateSelection, SourcePage

PRIORITY_METRIC = 500
PRIORITY_RESULT_TABLE = 400
PRIORITY_DATASET = 300
PRIORITY_METHOD = 200
PRIORITY_CONTEXT = 100
MAX_CANDIDATE_PAGES = 32
MAX_TABLE_TEXT_BYTES = 8_192

METRIC_TERMS = (
    "mae",
    "mean absolute error",
    "mean absolute deviation",
    # MSE is not emitted as a supported comparison metric, but it is a
    # reliable signal for locating regression performance tables whose R²
    # values should still be retained as source evidence.
    "mse",
    "rmse",
    "root mean squared error",
    "root mean square error",
    "r²",
    "r2",
    "r 2",
    "r-squared",
    "coefficient of determination",
    "平均绝对误差",
    "均方根误差",
    "决定系数",
)

RESULT_TERMS = (
    "result",
    "results",
    "evaluation",
    "evaluations",
    "experiment",
    "experiments",
    "performance",
    "findings",
    "结果",
    "评估",
    "评价",
    "实验",
    "性能",
)

DATASET_TERMS = (
    "dataset",
    "datasets",
    "data set",
    "data sets",
    "data",
    "sample",
    "samples",
    "数据集",
    "数据",
    "样本",
    "数据来源",
)

METHOD_TERMS = (
    "method",
    "methods",
    "methodology",
    "model",
    "models",
    "approach",
    "approaches",
    "algorithm",
    "algorithms",
    "方法",
    "方法学",
    "模型",
    "算法",
)

CONTEXT_TERMS = (
    "title",
    "abstract",
    "introduction",
    "background",
    "conclusion",
    "research problem",
    "task",
    "regression",
    "标题",
    "摘要",
    "引言",
    "背景",
    "结论",
    "研究问题",
    "任务",
    "回归",
)


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _bounded_table_text(value: str) -> str | None:
    encoded = value.encode("utf-8")
    if not encoded:
        return None
    if len(encoded) <= MAX_TABLE_TEXT_BYTES:
        return value
    return encoded[:MAX_TABLE_TEXT_BYTES].decode("utf-8", errors="ignore") or None


def _compile_terms(terms: Iterable[str]) -> re.Pattern[str]:
    normalized_terms = {
        _normalize_text(term).casefold() for term in terms if _normalize_text(term)
    }
    alternatives: list[str] = []
    for term in sorted(normalized_terms, key=lambda item: (-len(item), item)):
        escaped = re.escape(term)
        if re.search(r"[a-z0-9]", term):
            alternatives.append(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
        else:
            alternatives.append(escaped)
    return re.compile("|".join(alternatives), re.IGNORECASE)


METRIC_PATTERN = _compile_terms(METRIC_TERMS)
RESULT_PATTERN = _compile_terms(RESULT_TERMS)
DATASET_PATTERN = _compile_terms(DATASET_TERMS)
METHOD_PATTERN = _compile_terms(METHOD_TERMS)
CONTEXT_PATTERN = _compile_terms(CONTEXT_TERMS)

_REASON_ORDER = {
    "supported_metric": 0,
    "result_section": 1,
    "table": 2,
    "caption": 3,
    "dataset_section": 4,
    "method_section": 5,
    "context_page": 6,
    "fallback_page": 7,
    "neighbor": 8,
}


def normalize_pages(paper: ParsedPaper) -> tuple[SourcePage, ...]:
    """Build one normalized, page-addressable source page per valid PDF page."""

    texts: dict[int, list[tuple[int, int, str]]] = {}
    table_texts: dict[int, list[tuple[int, int, str]]] = {}
    kinds: dict[int, list[str]] = {}

    for index, element in enumerate(paper.elements):
        page_number = element.page
        if page_number is None or page_number > paper.page_count:
            continue

        text = _normalize_text(element.text)
        if not text:
            continue

        priority = (
            0
            if METRIC_PATTERN.search(text)
            else 1
            if element.kind == "table"
            else 2
            if element.kind == "caption"
            else 3
        )
        texts.setdefault(page_number, []).append((priority, index, text))
        if element.kind in {"table", "caption"}:
            table_texts.setdefault(page_number, []).append((priority, index, text))
        page_kinds = kinds.setdefault(page_number, [])
        if element.kind not in page_kinds:
            page_kinds.append(element.kind)

    return tuple(
        SourcePage(
            page=page_number,
            text=" ".join(
                text for _priority, _index, text in sorted(texts[page_number])
            ),
            kinds=tuple(kinds[page_number]),
            table_text=_bounded_table_text(
                " ".join(
                    text
                    for _priority, _index, text in sorted(
                        table_texts.get(page_number, ())
                    )
                )
            ),
        )
        for page_number in sorted(texts)
    )


def _page_signals(page: SourcePage) -> tuple[int, tuple[str, ...], bool]:
    text = page.text
    reasons: list[str] = []
    explicit = False

    if METRIC_PATTERN.search(text):
        reasons.append("supported_metric")
        explicit = True
    if RESULT_PATTERN.search(text):
        reasons.append("result_section")
        explicit = True
    if "table" in page.kinds:
        reasons.append("table")
        explicit = True
    if "caption" in page.kinds:
        reasons.append("caption")
        explicit = True
    if DATASET_PATTERN.search(text):
        reasons.append("dataset_section")
        explicit = True
    if METHOD_PATTERN.search(text):
        reasons.append("method_section")
        explicit = True
    if CONTEXT_PATTERN.search(text):
        reasons.append("context_page")

    if "supported_metric" in reasons:
        priority = PRIORITY_METRIC
    elif any(reason in reasons for reason in ("result_section", "table", "caption")):
        priority = PRIORITY_RESULT_TABLE
    elif "dataset_section" in reasons:
        priority = PRIORITY_DATASET
    elif "method_section" in reasons:
        priority = PRIORITY_METHOD
    elif "context_page" in reasons:
        priority = PRIORITY_CONTEXT
    else:
        priority = 0

    return priority, tuple(reasons), explicit


def _fallback_indices(page_count: int, limit: int) -> tuple[int, ...]:
    if page_count <= limit:
        return tuple(range(page_count))
    if limit == 1:
        return (0,)
    if limit == 2:
        return (0, 1) if page_count > 1 else (0,)

    required = [0, 1, page_count - 1]
    slots = limit - len(required)
    interior = range(2, page_count - 1)
    interior_count = page_count - 3
    selected = list(required)

    for offset in range(1, slots + 1):
        target = offset * (interior_count + 1) / (slots + 1)
        index = 2 + int(target + 0.5) - 1
        index = max(2, min(page_count - 2, index))
        if index not in selected:
            selected.append(index)

    if len(selected) < limit:
        for index in interior:
            if index not in selected:
                selected.append(index)
                if len(selected) == limit:
                    break

    return tuple(sorted(selected[:limit]))


def _ordered_reasons(reasons: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(reasons), key=lambda reason: (_REASON_ORDER.get(reason, 99), reason)))


def select_candidate_pages(
    paper: ParsedPaper, limit: int = MAX_CANDIDATE_PAGES
) -> CandidateSelection:
    """Select evidence-bearing pages using only deterministic local signals."""

    if limit < 1:
        raise ValueError("limit must be positive")

    effective_limit = min(limit, MAX_CANDIDATE_PAGES)
    pages = normalize_pages(paper)
    if not pages:
        return CandidateSelection(
            pages=(),
            uncapped_count=0,
            warnings=("no_explicit_regression_evidence_candidate",),
        )

    signals = {page.page: _page_signals(page) for page in pages}
    explicit_pages = {
        page.page for page in pages if signals[page.page][2]
    }

    if not explicit_pages:
        fallback = _fallback_indices(len(pages), effective_limit)
        candidates = []
        for index in fallback:
            page = pages[index]
            reasons = ["fallback_page"]
            if index < 2:
                reasons.append("context_page")
            priority = PRIORITY_CONTEXT if index < 2 else 0
            candidates.append(
                CandidatePage(
                    page=page.page,
                    text=page.text,
                    kinds=page.kinds,
                    table_text=page.table_text,
                    priority=priority,
                    reasons=_ordered_reasons(reasons),
                )
            )
        return CandidateSelection(
            pages=tuple(candidates),
            uncapped_count=len(candidates),
            warnings=("no_explicit_regression_evidence_candidate",),
        )

    reasons_by_page: dict[int, set[str]] = {}
    priorities_by_page: dict[int, int] = {}

    def add_candidate(page: SourcePage, priority: int, reasons: Iterable[str]) -> None:
        reasons_by_page.setdefault(page.page, set()).update(reasons)
        priorities_by_page[page.page] = max(
            priority, priorities_by_page.get(page.page, 0)
        )

    for page in pages:
        priority, reasons, _ = signals[page.page]
        if priority:
            add_candidate(page, priority, reasons)

    for page in pages[:2]:
        add_candidate(page, PRIORITY_CONTEXT, ("context_page",))

    page_index = {page.page: index for index, page in enumerate(pages)}
    for page_number in sorted(explicit_pages):
        index = page_index[page_number]
        for neighbor_index in (index - 1, index + 1):
            if 0 <= neighbor_index < len(pages):
                add_candidate(pages[neighbor_index], 0, ("neighbor",))

    candidates_by_page = {
        page.page: CandidatePage(
            page=page.page,
            text=page.text,
            kinds=page.kinds,
            table_text=page.table_text,
            priority=priorities_by_page.get(page.page, 0),
            reasons=_ordered_reasons(reasons_by_page.get(page.page, ())),
        )
        for page in pages
        if page.page in reasons_by_page
    }

    uncapped_count = len(candidates_by_page)
    warnings: tuple[str, ...] = ()
    retained = tuple(candidates_by_page.values())
    if uncapped_count > effective_limit:
        retained = tuple(
            sorted(retained, key=lambda item: (-item.priority, item.page))[:effective_limit]
        )
        warnings = ("candidate_pages_capped",)

    return CandidateSelection(
        pages=tuple(sorted(retained, key=lambda item: item.page)),
        uncapped_count=uncapped_count,
        warnings=warnings,
    )
