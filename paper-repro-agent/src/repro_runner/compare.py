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

    An arithmetic difference is useful context even when paper provenance is
    incomplete, but a pair is comparable only if it identifies the same input
    dataset and the held-out test split used by this service.
    """
    independent = _metric_values(result)
    items = []
    for candidate in reported:
        metric = ReportedMetricInput.model_validate(candidate)
        metric_key = _normalize_metric_name(metric.name)
        paper_value = _parse_number(metric.reported_value)
        independent_value = independent.get(metric_key)
        reason = _comparison_reason(
            metric,
            metric_key,
            paper_value,
            independent_value,
            result,
        )
        comparable = reason is None
        absolute_difference = None
        relative_difference = None
        if paper_value is not None and independent_value is not None:
            absolute_difference = _round_public(abs(independent_value - paper_value))
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
    result: ExperimentResult,
) -> str | None:
    if metric_key not in _METRIC_ALIASES.values() and independent_value is None:
        return "metric name is not supported"
    if paper_value is None:
        return "reported value is not numeric"
    if result.split_provenance is None:
        return "independent experiment is a legacy artifact without split provenance"
    if metric.dataset is None or metric.split is None:
        return "paper metric is missing dataset or split qualifiers"
    if metric.dataset_id is None:
        return "paper metric is missing dataset identity"
    if metric.dataset_id != result.dataset.dataset_id:
        return "paper metric dataset identity differs from the independent dataset"
    if _normalized_qualifier(metric.dataset) != _INDEPENDENT_DATASET:
        return "paper metric dataset differs from the independent test dataset"
    if _normalized_qualifier(metric.split) != _INDEPENDENT_SPLIT:
        return "paper metric split differs from the independent test split"
    if metric.test_digest is None:
        return "paper metric is missing held-out test digest"
    if metric.test_digest != result.split_provenance.test_digest:
        return "paper metric held-out test digest differs from the independent test split"
    if metric.test_size is None:
        return "paper metric is missing test_size provenance"
    if metric.test_size != result.split_provenance.test_size:
        return "paper metric test_size differs from the independent test split"
    if metric.random_state is None:
        return "paper metric is missing random_state provenance"
    if metric.random_state != result.split_provenance.random_state:
        return "paper metric random_state differs from the independent test split"
    if metric.train_rows is None:
        return "paper metric is missing train_rows provenance"
    if metric.train_rows != result.split_provenance.train_rows:
        return "paper metric train_rows differs from the independent test split"
    if metric.test_rows is None:
        return "paper metric is missing test_rows provenance"
    if metric.test_rows != result.split_provenance.test_rows:
        return "paper metric test_rows differs from the independent test split"
    return None


def _normalized_qualifier(value: str) -> str:
    return value.strip().casefold()


def _round_public(value: float) -> float:
    return round(float(value), 6)
