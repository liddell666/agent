"""Normalization helpers for metrics reported in research papers."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import unicodedata

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
    "accuracy": "accuracy",
    "总精度": "accuracy",
    "准确率": "accuracy",
    "balanced_accuracy": "balanced_accuracy",
    "平衡准确率": "balanced_accuracy",
    "precision": "precision",
    "精确率": "precision",
    "查准率": "precision",
    "recall": "recall",
    "召回率": "recall",
    "查全率": "recall",
    "f1": "f1",
    "f1值": "f1",
    "f1_score": "f1",
}
_PERCENT_SUFFIXES = ("%", "％", "锛卄")
_MODEL_ALIASES = {
    "logistic regression": "logistic_regression",
    "logistic_regression": "logistic_regression",
    "逻辑回归": "logistic_regression",
    "lr": "logistic_regression",
    "random forest": "random_forest",
    "random_forest": "random_forest",
    "随机森林": "random_forest",
    "rf": "random_forest",
    "xgboost": "xgboost",
    "xg boost": "xgboost",
    "lightgbm": "lightgbm",
    "light gbm": "lightgbm",
    "svm": "svm",
    "support vector machine": "svm",
    "支持向量机": "svm",
    "knn": "knn",
    "k nearest neighbors": "knn",
    "k近邻": "knn",
    "mlp": "mlp",
    "neural network": "mlp",
    "neural_network": "mlp",
    "神经网络": "mlp",
}
_THRESHOLD_RE = re.compile(
    r"(?:threshold|cut\s*[-_ ]?off|cutoff|阈值)\s*[:=]?\s*"
    r"(0(?:\.\d+)?|1(?:\.0+)?)",
    re.IGNORECASE,
)


def normalize_metric_name(name: str) -> str:
    """Return a stable metric key from a paper's display name."""
    display_name = unicodedata.normalize("NFKC", name.strip())
    if "(" in display_name:
        display_name = display_name.split("(", 1)[0].strip()
    normalized = "_".join(
        display_name.casefold().replace("-", " ").replace("_", " ").split()
    )
    return _METRIC_ALIASES.get(normalized, normalized)


def extract_metric_qualifiers(name: str) -> tuple[str | None, float | None]:
    """Extract an explicit model and classification threshold from a display label."""
    display_name = unicodedata.normalize("NFKC", name.strip())
    if "(" not in display_name:
        return None, None
    qualifier = display_name.split("(", 1)[1].rsplit(")", 1)[0]
    model = None
    for alias in sorted(_MODEL_ALIASES, key=len, reverse=True):
        if _qualifier_contains_model_alias(qualifier, alias):
            model = _MODEL_ALIASES[alias]
            break

    threshold = None
    match = _THRESHOLD_RE.search(qualifier)
    if match is not None:
        parsed = float(match.group(1))
        if math.isfinite(parsed) and 0.0 <= parsed <= 1.0:
            threshold = round(parsed, 6)
    return model, threshold


def _qualifier_contains_model_alias(qualifier: str, alias: str) -> bool:
    normalized_alias = alias.casefold().replace("_", " ")
    normalized_qualifier = qualifier.casefold().replace("_", " ")
    if normalized_alias.isascii():
        pattern = r"(?<![a-z0-9])" + re.escape(normalized_alias).replace(
            r"\ ", r"\s+"
        ) + r"(?![a-z0-9])"
        return re.search(pattern, normalized_qualifier) is not None
    return normalized_alias in normalized_qualifier


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
    "model",
    "threshold",
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
    parsed_metrics = []
    for metric in dossier.metrics:
        normalized_name = normalize_metric_name(metric.name)
        model, threshold = extract_metric_qualifiers(metric.name)
        parsed_metrics.append(
            DossierMetric(
                name=metric.name,
                normalized_name=normalized_name,
                supported=normalized_name in SUPPORTED_METRICS,
                reported_value=parse_reported_value(metric.reported_value),
                model=model,
                threshold=threshold,
                dataset=metric.dataset,
                split=metric.split,
                source="paper_dossier",
                evidence=[
                    DossierEvidence.model_validate(item.model_dump())
                    for item in metric.evidence
                ],
            )
        )
    return parsed_metrics


def _apply_overrides_and_mark_ambiguity(
    metrics: list[DossierMetric], overrides: list[ReportedMetricInput]
) -> list[str]:
    grouped: dict[tuple[str, str | None, float | None], list[DossierMetric]] = {}
    for metric in metrics:
        key = (metric.normalized_name, metric.model, metric.threshold)
        grouped.setdefault(key, []).append(metric)
    for group in grouped.values():
        if len(group) > 1:
            for metric in group:
                metric.ambiguous = True

    for override in overrides:
        candidates = [
            metric
            for (normalized_name, _model, _threshold), group in grouped.items()
            if normalized_name == normalize_metric_name(override.name)
            for metric in group
        ]
        if len(candidates) > 1:
            if "model" in override.model_fields_set:
                candidates = [item for item in candidates if item.model == override.model]
            if "threshold" in override.model_fields_set:
                candidates = [
                    item for item in candidates if item.threshold == override.threshold
                ]
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
