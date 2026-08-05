"""Comparison of baseline metrics with values reported by a paper."""

from __future__ import annotations

from collections.abc import Iterable
import math

from repro_runner.schemas import (
    ComparisonItem,
    ComparisonResponse,
    ExperimentResult,
    ReportedMetricInput,
)


# V2 calculates every public metric from the held-out test fold.
_INDEPENDENT_DATASET = "test"
_INDEPENDENT_SPLIT = "test"
_METRIC_ALIASES = {"auc": "roc_auc", "roc_auc": "roc_auc"}


def compare_metrics(
    result: ExperimentResult,
    reported: Iterable[ReportedMetricInput | dict[str, object]],
) -> ComparisonResponse:
    """Compare named paper metrics to the experiment's held-out-test metrics.

    An arithmetic difference is useful context even when the paper did not
    identify a compatible dataset and split, but such a pair is never marked
    comparable.
    """
    independent = _metric_values(result)
    items = []
    for candidate in reported:
        metric = ReportedMetricInput.model_validate(candidate)
        metric_key = _normalize_metric_name(metric.name)
        paper_value = _parse_number(metric.reported_value)
        independent_value = independent.get(metric_key)
        reason = _comparison_reason(metric, metric_key, paper_value, independent_value)
        comparable = reason is None
        absolute_difference = None
        relative_difference = None
        if paper_value is not None and independent_value is not None:
            absolute_difference = _round_public(independent_value - paper_value)
            if paper_value != 0:
                relative_difference = _round_public(
                    (independent_value - paper_value) / abs(paper_value)
                )

        items.append(
            ComparisonItem(
                name=metric.name,
                paper_value=paper_value,
                independent_value=independent_value,
                absolute_difference=absolute_difference,
                relative_difference=relative_difference,
                comparable=comparable,
                reason=reason,
            )
        )
    return ComparisonResponse(experiment_id=result.experiment_id, items=items)


def _metric_values(result: ExperimentResult) -> dict[str, float]:
    values = result.metrics.model_dump(exclude={"confusion_matrix"})
    return {name: float(value) for name, value in values.items()}


def _normalize_metric_name(name: str) -> str:
    normalized = "_".join(name.strip().casefold().replace("-", " ").split())
    return _METRIC_ALIASES.get(normalized, normalized)


def _parse_number(value: float | str | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return _round_public(parsed) if math.isfinite(parsed) else None


def _comparison_reason(
    metric: ReportedMetricInput,
    metric_key: str,
    paper_value: float | None,
    independent_value: float | None,
) -> str | None:
    if metric_key not in _METRIC_ALIASES.values() and independent_value is None:
        return "metric name is not supported"
    if paper_value is None:
        return "reported value is not numeric"
    if metric.dataset is None or metric.split is None:
        return "paper metric is missing dataset or split qualifiers"
    if _normalized_qualifier(metric.dataset) != _INDEPENDENT_DATASET:
        return "paper metric dataset differs from the independent test dataset"
    if _normalized_qualifier(metric.split) != _INDEPENDENT_SPLIT:
        return "paper metric split differs from the independent test split"
    return None


def _normalized_qualifier(value: str) -> str:
    return value.strip().casefold()


def _round_public(value: float) -> float:
    return round(float(value), 6)
