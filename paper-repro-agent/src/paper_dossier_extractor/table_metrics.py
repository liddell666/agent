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
_PERFORMANCE_CAPTION_PATTERN = re.compile(
    r"(?i)\b(?:on|of)\s+(?P<dataset>[A-Za-z][A-Za-z0-9 ()/-]{1,100}?)\s+"
    r"data\s*set\b.*?\bvia\s+"
    r"(?P<split>hold-out validation|tenfold cross-validation)"
)
_PERFORMANCE_HEADER_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9])R\s+RMSE\b")
_PERFORMANCE_ROW_PATTERN = re.compile(
    r"(?i)(?P<evidence>\bI\.\s+Single\s+"
    r"(?:[A-Za-z+*()/-]+\s+){1,8}"
    r"0?\.\d+\s+(?P<rmse>\d+(?:\.\d+)))"
)
_MODEL_PERFORMANCE_HEADER_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])Model\s+MSE\s+R\s*2(?![A-Za-z0-9])"
)
_MODEL_PERFORMANCE_ROW_PATTERN = re.compile(
    r"(?i)(?P<evidence>\b(?P<model>gradient boosting regressor|"
    r"random forest regressor|RF regressor|linear regression)\s+"
    r"\d+(?:\.\d+)?\s+(?P<r2>(?:0?\.\d+|1(?:\.0+)?)))"
)
_SET_SPLIT_PATTERN = re.compile(r"(?i)\b(training|test|validation)\s+set\b")
_MODEL_ALIASES = {
    "gradient boosting regressor": "gradient_boosting",
    "random forest regressor": "random_forest",
    "rf regressor": "random_forest",
    "linear regression": "linear_regression",
}


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
            if metric_name is not None and split_match is not None and row_match is not None:
                value = float(row_match.group("value"))
                if not math.isfinite(value):
                    continue
                if metric_name in {"MAE", "RMSE"} and value < 0:
                    continue
                dataset = row_match.group("dataset")
                split = split_match.group(1).casefold()
                model = None
                evidence = row_match.group(0)
            else:
                caption_match = _PERFORMANCE_CAPTION_PATTERN.search(source)
                performance_row = _PERFORMANCE_ROW_PATTERN.search(source)
                if (
                    caption_match is not None
                    and performance_row is not None
                    and _PERFORMANCE_HEADER_PATTERN.search(source) is not None
                ):
                    metric_name = "RMSE"
                    value = float(performance_row.group("rmse"))
                    if not math.isfinite(value) or value < 0:
                        continue
                    dataset = caption_match.group("dataset").strip()
                    split = caption_match.group("split").casefold()
                    model = None
                    evidence = performance_row.group("evidence")
                else:
                    model_row = _MODEL_PERFORMANCE_ROW_PATTERN.search(source)
                    set_split = _SET_SPLIT_PATTERN.search(page.text)
                    if (
                        model_row is None
                        or set_split is None
                        or _MODEL_PERFORMANCE_HEADER_PATTERN.search(source) is None
                    ):
                        continue
                    metric_name = "R2"
                    value = float(model_row.group("r2"))
                    if not math.isfinite(value) or not 0 <= value <= 1:
                        continue
                    dataset = None
                    split = set_split.group(1).casefold()
                    model = _MODEL_ALIASES[model_row.group("model").casefold()]
                    evidence = model_row.group("evidence")
            return (
                PartialDossier(
                    metrics=(
                        PartialMetric(
                            name=metric_name,
                            reported_value=value,
                            dataset=dataset,
                            split=split,
                            model=model,
                            evidence=(
                                PartialEvidence(
                                    page=page.page,
                                    source_text=evidence,
                                ),
                            ),
                        ),
                    ),
                ),
            )
    return ()
