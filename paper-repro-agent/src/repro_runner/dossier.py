"""Normalization helpers for metrics reported in research papers."""

from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import ValidationError

from paper_parser.schemas import PaperDossier
from repro_runner.schemas import (
    DossierEvidence,
    DossierMetric,
    DossierParseResponse,
    ReportedMetricInput,
    ValidationErrorItem,
)


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


_OVERRIDE_FIELDS = (
    "reported_value",
    "dataset",
    "split",
    "dataset_id",
    "test_size",
    "random_state",
    "train_rows",
    "test_rows",
    "test_digest",
)


def _invalid(code: str, message: str) -> DossierParseResponse:
    return DossierParseResponse(
        valid=False,
        errors=[ValidationErrorItem(code=code, message=message)],
    )


def _metrics_from_dossier(dossier: PaperDossier) -> list[DossierMetric]:
    return [
        DossierMetric(
            name=metric.name,
            normalized_name=normalize_metric_name(metric.name),
            supported=normalize_metric_name(metric.name) in SUPPORTED_METRICS,
            reported_value=parse_reported_value(metric.reported_value),
            dataset=metric.dataset,
            split=metric.split,
            source="paper_dossier",
            evidence=[
                DossierEvidence.model_validate(item.model_dump())
                for item in metric.evidence
            ],
        )
        for metric in dossier.metrics
    ]


def _apply_overrides_and_mark_ambiguity(
    metrics: list[DossierMetric], overrides: list[ReportedMetricInput]
) -> list[str]:
    grouped: dict[str, list[DossierMetric]] = {}
    for metric in metrics:
        grouped.setdefault(metric.normalized_name, []).append(metric)
    for group in grouped.values():
        if len(group) > 1:
            for metric in group:
                metric.ambiguous = True

    for override in overrides:
        candidates = list(grouped.get(normalize_metric_name(override.name), []))
        if len(candidates) > 1:
            if "dataset" in override.model_fields_set:
                candidates = [
                    item for item in candidates if item.dataset == override.dataset
                ]
            if "split" in override.model_fields_set:
                candidates = [item for item in candidates if item.split == override.split]
        if len(candidates) != 1:
            continue

        selected = candidates[0]
        for field in _OVERRIDE_FIELDS:
            if field in override.model_fields_set:
                value = getattr(override, field)
                if field == "reported_value":
                    value = parse_reported_value(value)
                setattr(selected, field, value)
        selected.source = "manual_override"
        selected.ambiguous = False

    return ["ambiguous_metric"] if any(item.ambiguous for item in metrics) else []


def parse_dossier(
    filename: str, content: bytes, overrides_json: str = "[]"
) -> DossierParseResponse:
    if Path(filename).suffix.casefold() != ".json":
        return _invalid(
            "invalid_dossier_extension",
            "The dossier file must use the .json extension.",
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return _invalid(
            "invalid_dossier_encoding", "The dossier file must be UTF-8 encoded."
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _invalid("invalid_dossier_json", "The dossier file is not valid JSON.")
    try:
        dossier = PaperDossier.model_validate(payload)
    except ValidationError:
        return _invalid(
            "invalid_dossier_schema",
            "The dossier does not match the PaperDossier schema.",
        )
    if not dossier.metrics:
        return _invalid(
            "no_reported_metrics", "The dossier does not contain reported metrics."
        )
    try:
        override_payload = json.loads(overrides_json or "[]")
        if not isinstance(override_payload, list):
            raise ValueError
        overrides = [
            ReportedMetricInput.model_validate(item) for item in override_payload
        ]
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        return _invalid(
            "invalid_metric_overrides",
            "Metric overrides must be a valid JSON array.",
        )

    metrics = _metrics_from_dossier(dossier)
    return DossierParseResponse(
        valid=True,
        title=dossier.title,
        metrics=metrics,
        warnings=_apply_overrides_and_mark_ambiguity(metrics, overrides),
        errors=[],
    )
