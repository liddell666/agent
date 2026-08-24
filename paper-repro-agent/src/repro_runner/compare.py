"""Comparison of baseline metrics with values reported by a paper."""

from __future__ import annotations

from collections.abc import Iterable

from repro_runner.dossier import normalize_metric_name, parse_reported_value
from repro_runner.schemas import (
    ComparisonItem,
    ComparisonResponse,
    ExperimentResult,
    ExperimentSuiteResult,
    ReportedMetricInput,
    SuiteComparisonItem,
    SuiteComparisonResponse,
)


# V2 calculates every public metric from the held-out test fold.
_INDEPENDENT_DATASET = "test"
_INDEPENDENT_SPLIT = "test"
_SUPPORTED_METRICS = {
    "roc_auc",
    "accuracy",
    "balanced_accuracy",
    "precision",
    "recall",
    "f1",
    "mae",
    "rmse",
    "r2",
}


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
            result.dataset.dataset_id,
            result.split_provenance,
        )
        comparable = reason is None
        absolute_difference, relative_difference = _difference_fields(
            paper_value, independent_value
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


def compare_suite_metrics(
    result: ExperimentSuiteResult,
    reported: Iterable[ReportedMetricInput | dict[str, object]],
) -> SuiteComparisonResponse:
    """Compare paper metrics against every successful model in a suite result."""
    reported_metrics = [ReportedMetricInput.model_validate(candidate) for candidate in reported]
    successful_models = [
        item
        for item in result.results
        if item.status == "succeeded" and item.metrics is not None
    ]
    metric_values = {
        item.model: _metric_values_from_metrics(item.metrics) for item in successful_models
    }
    available_metrics = {
        metric_name for values in metric_values.values() for metric_name in values
    }
    items = []
    items_by_reported_metric: list[tuple[ReportedMetricInput, list[SuiteComparisonItem]]] = []
    for metric in reported_metrics:
        metric_key = _normalize_metric_name(metric.name)
        paper_value = _parse_number(metric.reported_value)
        metric_items: list[SuiteComparisonItem] = []
        selected_models = successful_models
        if metric.model is not None:
            selected_models = [
                item for item in successful_models if item.model == metric.model
            ]
            if not selected_models:
                metric_items.append(
                    SuiteComparisonItem(
                        model=metric.model,
                        name=metric.name,
                        paper_value=paper_value,
                        independent_value=None,
                        absolute_difference=None,
                        relative_difference=None,
                        comparable=False,
                        reason="requested model is not available in the suite result",
                    )
                )
                items.extend(metric_items)
                items_by_reported_metric.append((metric, metric_items))
                continue
        for model_result in selected_models:
            independent_value = metric_values[model_result.model].get(metric_key)
            reason = (
                None
                if metric_key in available_metrics
                else "metric name is not supported"
            )
            if reason is None:
                reason = _comparison_reason(
                    metric,
                    metric_key,
                    paper_value,
                    independent_value,
                    result.dataset.dataset_id,
                    result.split_provenance,
                    independent_threshold=result.config.threshold,
                )
            comparable = reason is None
            absolute_difference, relative_difference = _difference_fields(
                paper_value, independent_value
            )
            metric_items.append(
                SuiteComparisonItem(
                    model=model_result.model,
                    name=metric.name,
                    paper_value=paper_value,
                    independent_value=independent_value,
                    absolute_difference=absolute_difference,
                    relative_difference=relative_difference,
                    comparable=comparable,
                    reason=reason,
                )
            )
        items.extend(metric_items)
        items_by_reported_metric.append((metric, metric_items))

    paper_reference_metric, reference_items = _suite_reference_items(
        items_by_reported_metric, metric_values
    )

    return SuiteComparisonResponse(
        experiment_id=result.experiment_id,
        items=items,
        paper_reference_metric=paper_reference_metric,
        paper_closeness_ranking=_paper_closeness_ranking(
            reference_items, result.performance_ranking
        ),
    )


def _metric_values(result: ExperimentResult) -> dict[str, float]:
    return _metric_values_from_metrics(result.metrics)


def _metric_values_from_metrics(metrics) -> dict[str, float]:
    values = metrics.model_dump(exclude={"confusion_matrix"})
    return {name: float(value) for name, value in values.items()}


def _normalize_metric_name(name: str) -> str:
    return normalize_metric_name(name)


def _parse_number(value: float | str | None) -> float | None:
    return parse_reported_value(value)


def _comparison_reason(
    metric: ReportedMetricInput,
    metric_key: str,
    paper_value: float | None,
    independent_value: float | None,
    dataset_id: str,
    split_provenance,
    independent_threshold: float | None = None,
) -> str | None:
    if metric_key not in _SUPPORTED_METRICS and independent_value is None:
        return "metric name is not supported"
    if paper_value is None:
        return "reported value is not numeric"
    if split_provenance is None:
        return "independent experiment is a legacy artifact without split provenance"
    if metric.dataset is None or metric.split is None:
        return "paper metric is missing dataset or split qualifiers"
    if metric.dataset_id is None:
        return "paper metric is missing dataset identity"
    if metric.dataset_id != dataset_id:
        return "paper metric dataset identity differs from the independent dataset"
    if _normalized_qualifier(metric.dataset) != _INDEPENDENT_DATASET:
        return "paper metric dataset differs from the independent test dataset"
    if _normalized_qualifier(metric.split) != _INDEPENDENT_SPLIT:
        return "paper metric split differs from the independent test split"
    if metric.test_digest is None:
        return "paper metric is missing held-out test digest"
    if metric.test_digest != split_provenance.test_digest:
        return "paper metric held-out test digest differs from the independent test split"
    if metric.test_size is None:
        return "paper metric is missing test_size provenance"
    if metric.test_size != split_provenance.test_size:
        return "paper metric test_size differs from the independent test split"
    if metric.random_state is None:
        return "paper metric is missing random_state provenance"
    if metric.random_state != split_provenance.random_state:
        return "paper metric random_state differs from the independent test split"
    if metric.train_rows is None:
        return "paper metric is missing train_rows provenance"
    if metric.train_rows != split_provenance.train_rows:
        return "paper metric train_rows differs from the independent test split"
    if metric.test_rows is None:
        return "paper metric is missing test_rows provenance"
    if metric.test_rows != split_provenance.test_rows:
        return "paper metric test_rows differs from the independent test split"
    if (
        metric.threshold is not None
        and independent_threshold is not None
        and metric.threshold != independent_threshold
    ):
        return "paper metric threshold differs from the independent run"
    return None


def _difference_fields(
    paper_value: float | None, independent_value: float | None
) -> tuple[float | None, float | None]:
    absolute_difference = None
    relative_difference = None
    if paper_value is not None and independent_value is not None:
        absolute_difference = _round_public(abs(independent_value - paper_value))
        if paper_value != 0:
            relative_difference = _round_public(
                (independent_value - paper_value) / abs(paper_value)
            )
    return absolute_difference, relative_difference


def _suite_reference_items(
    items_by_reported_metric: list[tuple[ReportedMetricInput, list[SuiteComparisonItem]]],
    metric_values: dict[str, dict[str, float]],
) -> tuple[str | None, list[SuiteComparisonItem]]:
    if not metric_values:
        return None, []
    supported_metrics = {
        metric_name for values in metric_values.values() for metric_name in values
    }
    for metric, items in items_by_reported_metric:
        metric_key = _normalize_metric_name(metric.name)
        if metric_key in supported_metrics:
            return metric_key, items
    return None, []


def _paper_closeness_ranking(
    reference_items: Iterable[SuiteComparisonItem],
    performance_ranking: list[str],
) -> list[str]:
    """Order only successful models with a finite difference for the reference."""
    rank = {model: index for index, model in enumerate(performance_ranking)}
    differences = {
        item.model: item.absolute_difference
        for item in reference_items
        if item.absolute_difference is not None
    }
    return sorted(
        differences,
        key=lambda model: (differences[model], rank.get(model, len(rank)), model),
    )


def _normalized_qualifier(value: str) -> str:
    return value.strip().casefold()


def _round_public(value: float) -> float:
    return round(float(value), 6)
