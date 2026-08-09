"""Normalization helpers for metrics reported in research papers."""

from __future__ import annotations

import math


SUPPORTED_METRICS = frozenset(
    {
        "roc_auc",
        "accuracy",
        "balanced_accuracy",
        "precision",
        "recall",
        "f1",
    }
)

_METRIC_ALIASES = {
    "auc": "roc_auc",
    "roc_auc": "roc_auc",
}
_PERCENT_SUFFIXES = ("%", "％", "锛卄")


def normalize_metric_name(name: str) -> str:
    """Return a stable metric key from a paper's display name."""
    normalized = "_".join(
        name.strip().casefold().replace("-", " ").replace("_", " ").split()
    )
    return _METRIC_ALIASES.get(normalized, normalized)


def parse_reported_value(value: object) -> float | None:
    """Parse a finite paper-reported metric value without implicit rescaling."""
    if value is None or isinstance(value, bool):
        return None

    percent = False
    if isinstance(value, str):
        numeric_text = value.strip()
        if not numeric_text:
            return None
        for suffix in _PERCENT_SUFFIXES:
            if numeric_text.endswith(suffix):
                percent = True
                numeric_text = numeric_text[: -len(suffix)].strip()
                break
        if not numeric_text:
            return None
        value = numeric_text

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if percent:
        parsed /= 100
    return round(parsed, 6)
