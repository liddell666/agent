"""Deterministic partial-dossier merging with exact-page evidence checks."""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass, field

from .schemas import (
    FinalDossier,
    FinalEvidence,
    FinalFact,
    FinalMetric,
    PartialDossier,
    PartialEvidence,
    PartialFact,
    PartialMetric,
    SourcePage,
)

_METRIC_ALIASES = {
    "mae": "mae",
    "mean absolute error": "mae",
    "mean absolute deviation": "mae",
    "平均绝对误差": "mae",
    "rmse": "rmse",
    "root mean squared error": "rmse",
    "root mean square error": "rmse",
    "均方根误差": "rmse",
    "r2": "r2",
    "r squared": "r2",
    "coefficient of determination": "r2",
    "决定系数": "r2",
}

_MODEL_ALIASES = {
    "linear regression": "linear_regression",
    "random forest": "random_forest",
    "gradient boosting": "gradient_boosting",
    "xgboost": "xgboost",
    "xg boost": "xgboost",
}


def normalize_name(value: str) -> str:
    """Return the stable comparison form used for fact and qualifier names."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return " ".join(normalized.split())


def normalize_optional(value: str | None) -> str | None:
    """Normalize an optional merge qualifier, retaining ``None`` for blank input."""

    if value is None:
        return None
    normalized = normalize_name(value)
    return normalized or None


def normalize_metric(value: str) -> str | None:
    """Map only the supported metric aliases to their final schema names."""

    return _METRIC_ALIASES.get(normalize_name(value))


def normalize_evidence(value: str) -> str:
    """Normalize evidence only for exact-page membership and deduplication."""

    return " ".join(unicodedata.normalize("NFKC", value).split())


@dataclass(frozen=True, slots=True)
class MergeResult:
    """A validated dossier plus bounded, content-free merge diagnostics."""

    dossier: FinalDossier
    warnings: tuple[str, ...]
    rejected_citation_count: int
    canonical_json: str

    @property
    def rejected_citations(self) -> int:
        """Compatibility alias for callers using the shorter count name."""

        return self.rejected_citation_count


@dataclass(slots=True)
class _MergeState:
    warnings: set[str] = field(default_factory=set)
    rejected_citation_count: int = 0

    def reject_citation(self) -> None:
        self.rejected_citation_count += 1
        self.warnings.add("citation_source_mismatch")


@dataclass(slots=True)
class _FactRecord:
    key: str
    names: list[str] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)
    evidence: dict[tuple[int, str], FinalEvidence] = field(default_factory=dict)


@dataclass(slots=True)
class _MetricRecord:
    key: tuple[str, str | None, str | None, str | None]
    value: float | None
    dataset_values: list[str] = field(default_factory=list)
    split_values: list[str] = field(default_factory=list)
    model_value: str | None = None
    evidence: dict[tuple[int, str], FinalEvidence] = field(default_factory=dict)


def _clean_text(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


def _choose_text(values: list[str], *, allow_empty: bool = False) -> str:
    candidates = [_clean_text(value) for value in values]
    non_empty = [value for value in candidates if value]
    if non_empty:
        candidates = non_empty
    elif not allow_empty:
        candidates = []
    if not candidates:
        return ""
    return min(candidates, key=lambda value: (normalize_name(value), value))


def _choose_optional(values: list[str]) -> str | None:
    chosen = _choose_text(values)
    return chosen or None


def _source_page_texts(source_pages: tuple[SourcePage, ...]) -> dict[int, str]:
    return {
        page.page: normalize_evidence(page.text)
        for page in sorted(source_pages, key=lambda item: (item.page, item.text))
    }


def _validated_evidence(
    evidence: PartialEvidence,
    source_page_texts: dict[int, str],
    state: _MergeState,
) -> FinalEvidence | None:
    normalized_excerpt = normalize_evidence(evidence.source_text)
    source_text = source_page_texts.get(evidence.page)
    if not normalized_excerpt or source_text is None:
        state.reject_citation()
        return None
    if normalized_excerpt not in source_text:
        state.reject_citation()
        return None
    return FinalEvidence(page=evidence.page, source_text=evidence.source_text)


def _add_evidence(
    evidence_by_key: dict[tuple[int, str], FinalEvidence], evidence: FinalEvidence
) -> None:
    key = (evidence.page, normalize_evidence(evidence.source_text))
    current = evidence_by_key.get(key)
    if current is None or evidence.source_text < current.source_text:
        evidence_by_key[key] = evidence


def _sorted_evidence(
    evidence_by_key: dict[tuple[int, str], FinalEvidence],
) -> tuple[FinalEvidence, ...]:
    return tuple(
        evidence_by_key[key]
        for key in sorted(
            evidence_by_key,
            key=lambda item: (item[0], item[1], evidence_by_key[item].source_text),
        )
    )


def _first_evidence_page(
    evidence_by_key: dict[tuple[int, str], FinalEvidence],
) -> int:
    return min(key[0] for key in evidence_by_key)


def _merge_facts(
    facts: tuple[PartialFact, ...],
    source_page_texts: dict[int, str],
    state: _MergeState,
) -> tuple[FinalFact, ...]:
    records: dict[str, _FactRecord] = {}
    for fact in facts:
        key = normalize_name(fact.name)
        valid_evidence = [
            validated
            for evidence in fact.evidence
            if (validated := _validated_evidence(evidence, source_page_texts, state))
            is not None
        ]
        if not valid_evidence:
            continue

        record = records.setdefault(key, _FactRecord(key=key))
        record.names.append(fact.name)
        record.descriptions.append(fact.description)
        for evidence in valid_evidence:
            _add_evidence(record.evidence, evidence)

    ordered_records = sorted(
        records.values(),
        key=lambda record: (
            record.key,
            _first_evidence_page(record.evidence),
            _choose_text(record.names),
        ),
    )
    return tuple(
        FinalFact(
            name=_choose_text(record.names),
            description=_choose_text(record.descriptions, allow_empty=True),
            evidence=_sorted_evidence(record.evidence),
        )
        for record in ordered_records
    )


def _normalized_model(value: str | None) -> str | None:
    normalized = normalize_optional(value)
    if normalized is None:
        return None
    return _MODEL_ALIASES.get(normalized)


def _metric_key_sort_key(
    key: tuple[str, str | None, str | None, str | None],
) -> tuple[tuple[int, str], ...]:
    return tuple(
        (0, part) if part is not None else (1, "")
        for part in key
    )


def _merge_metrics(
    metrics: tuple[PartialMetric, ...],
    source_page_texts: dict[int, str],
    state: _MergeState,
) -> tuple[FinalMetric, ...]:
    records: dict[
        tuple[str, str | None, str | None, str | None],
        dict[float | None, _MetricRecord],
    ] = {}

    for metric in metrics:
        metric_name = normalize_metric(metric.name)
        valid_evidence = [
            validated
            for evidence in metric.evidence
            if (validated := _validated_evidence(evidence, source_page_texts, state))
            is not None
        ]
        if metric_name is None:
            state.warnings.add("unsupported_metric")
            continue
        if not valid_evidence:
            continue

        if metric.reported_value is not None and not math.isfinite(
            metric.reported_value
        ):
            state.warnings.add("non_finite_metric")
            continue

        dataset_key = normalize_optional(metric.dataset)
        split_key = normalize_optional(metric.split)
        model_key = normalize_optional(metric.model)
        model_value = _normalized_model(metric.model)
        if model_key is not None and model_value is None:
            state.warnings.add("unsupported_model")
            continue

        key = (metric_name, dataset_key, split_key, model_key)
        by_value = records.setdefault(key, {})
        record = by_value.get(metric.reported_value)
        if record is None:
            record = _MetricRecord(
                key=key,
                value=metric.reported_value,
                model_value=model_value,
            )
            by_value[metric.reported_value] = record
        if metric.dataset is not None:
            record.dataset_values.append(metric.dataset)
        if metric.split is not None:
            record.split_values.append(metric.split)
        for evidence in valid_evidence:
            _add_evidence(record.evidence, evidence)

    merged: list[FinalMetric] = []
    for key in sorted(records, key=_metric_key_sort_key):
        by_value = records[key]
        finite_values = [value for value in by_value if value is not None]
        if len(finite_values) > 1:
            state.warnings.add("ambiguous_metric")
        for record in sorted(
            by_value.values(),
            key=lambda item: (
                item.value is None,
                item.value if item.value is not None else 0.0,
                _first_evidence_page(item.evidence),
                tuple(sorted(item.evidence)),
            ),
        ):
            merged.append(
                FinalMetric(
                    name=record.key[0],
                    reported_value=record.value,
                    model=record.model_value,
                    dataset=_choose_optional(record.dataset_values),
                    split=_choose_optional(record.split_values),
                    evidence=_sorted_evidence(record.evidence),
                )
            )
    return tuple(merged)


def _merge_titles(
    partials: tuple[PartialDossier, ...],
    source_page_texts: dict[int, str],
    state: _MergeState,
) -> str:
    candidates: list[tuple[int, str, str]] = []
    for partial in partials:
        validated = [
            validated_evidence
            for evidence in partial.title_evidence
            if (validated_evidence := _validated_evidence(
                evidence, source_page_texts, state
            ))
            is not None
        ]
        if partial.title is None or not validated:
            continue
        title = _clean_text(partial.title)
        normalized_title = normalize_name(title)
        if not normalized_title:
            continue
        for evidence in validated:
            if evidence.page in (1, 2):
                candidates.append((evidence.page, normalized_title, title))

    distinct_titles = {candidate[1] for candidate in candidates}
    if len(distinct_titles) > 1:
        state.warnings.add("ambiguous_title")
    if not candidates:
        return "未提取到标题"
    return min(candidates, key=lambda candidate: candidate)[2]


def _task_is_regression(
    partials: tuple[PartialDossier, ...],
    source_page_texts: dict[int, str],
    state: _MergeState,
) -> bool:
    supports_regression = False
    for partial in partials:
        valid_evidence = [
            validated_evidence
            for evidence in partial.task_evidence
            if (validated_evidence := _validated_evidence(
                evidence, source_page_texts, state
            ))
            is not None
        ]
        if partial.task_type == "regression" and valid_evidence:
            supports_regression = True
    return supports_regression


def _merge_gaps(partials: tuple[PartialDossier, ...], title: str) -> tuple[str, ...]:
    gaps = {
        gap.strip()
        for partial in partials
        for gap in partial.gaps
        if gap.strip()
    }
    if title == "未提取到标题":
        gaps.add("paper_title_not_extracted")
    return tuple(sorted(gaps))


def merge_partials(
    partials: tuple[PartialDossier, ...],
    source_pages: tuple[SourcePage, ...],
) -> MergeResult:
    """Merge bounded partial dossiers and retain only exact-page evidence."""

    state = _MergeState()
    source_page_texts = _source_page_texts(source_pages)
    title = _merge_titles(partials, source_page_texts, state)
    datasets = _merge_facts(
        tuple(fact for partial in partials for fact in partial.datasets),
        source_page_texts,
        state,
    )
    methods = _merge_facts(
        tuple(fact for partial in partials for fact in partial.methods),
        source_page_texts,
        state,
    )
    metrics = _merge_metrics(
        tuple(metric for partial in partials for metric in partial.metrics),
        source_page_texts,
        state,
    )
    research_problem = _choose_text(
        [
            partial.research_problem
            for partial in partials
            if partial.research_problem is not None
        ]
    )
    dossier = FinalDossier(
        title=title,
        research_problem=research_problem,
        task_type=(
            "regression"
            if _task_is_regression(partials, source_page_texts, state)
            else "uncertain"
        ),
        datasets=datasets,
        methods=methods,
        metrics=metrics,
        gaps=_merge_gaps(partials, title),
    )
    canonical_json = json.dumps(
        dossier.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return MergeResult(
        dossier=dossier,
        warnings=tuple(sorted(state.warnings)),
        rejected_citation_count=state.rejected_citation_count,
        canonical_json=canonical_json,
    )
