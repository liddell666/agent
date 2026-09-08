"""Conservative deterministic fallback for flattened regression metric tables."""

from __future__ import annotations

import math
import re

from .schemas import PageChunk, PartialDossier, PartialEvidence, PartialMetric

_METRIC_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9])(?:mae|mean absolute error|mean absolute deviation|"
    r"rmse|root mean squared error|root mean square error|r²|r2|r-squared|"
    r"coefficient of determination)(?![a-z0-9])"
)
_SPLIT_PATTERN = re.compile(
    r"(?i)\b(training|test|validation|development)\s+dataset\b"
)
_STRUCTURAL_ROW_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?P<dataset>[A-Za-z][A-Za-z0-9_-]{1,63})\s+"
    r"(?P<rows>\d+)\s+(?P<features>\d+)\s+"
    r"(?P<value>[-+]?(?:\d+\.\d+|\.\d+)(?:[eE][-+]?\d+)?)"
)


def _metric_name(source: str) -> str | None:
    match = _METRIC_PATTERN.search(source)
    if match is None:
        return None
    value = match.group(0).casefold()
    if value in {"mae", "mean absolute error", "mean absolute deviation"}:
        return "MAE"
    if value in {"rmse", "root mean squared error", "root mean square error"}:
        return "RMSE"
    return "R2"


def metric_table_fallbacks(
    chunks: tuple[PageChunk, ...],
) -> tuple[PartialDossier, ...]:
    """Return at most one source-verifiable metric from a structural table row."""

    for chunk in chunks:
        for page in chunk.pages:
            if not any(kind in {"table", "caption"} for kind in page.kinds):
                continue
            source = page.table_text
            if source is None:
                continue
            metric_name = _metric_name(source)
            split_match = _SPLIT_PATTERN.search(source)
            row_match = _STRUCTURAL_ROW_PATTERN.search(source)
            if metric_name is None or split_match is None or row_match is None:
                continue
            value = float(row_match.group("value"))
            if not math.isfinite(value):
                continue
            if metric_name in {"MAE", "RMSE"} and value < 0:
                continue
            return (
                PartialDossier(
                    metrics=(
                        PartialMetric(
                            name=metric_name,
                            reported_value=value,
                            dataset=row_match.group("dataset"),
                            split=split_match.group(1).casefold(),
                            model=None,
                            evidence=(
                                PartialEvidence(
                                    page=page.page,
                                    source_text=row_match.group(0),
                                ),
                            ),
                        ),
                    ),
                ),
            )
    return ()
