"""Deterministic, safe helpers for the Dify paper-comparison workflow."""

import json
import math


_COMPARE_FIELDS = (
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
_DEFAULT_CLOSE_THRESHOLD = 0.05
_DEFAULT_PARTIAL_THRESHOLD = 0.10


def _object(value, fallback):
    """Return only a JSON object, never an unsafe raw payload."""

    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, dict) else fallback


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _static_error(code, message, stage):
    return {"code": code, "message": message, "stage": stage}


def _threshold_values(close_threshold, partial_threshold):
    try:
        close_value = float(close_threshold)
        partial_value = float(partial_threshold)
    except (TypeError, ValueError):
        return -1.0, -1.0
    if not math.isfinite(close_value) or not math.isfinite(partial_value):
        return -1.0, -1.0
    return close_value, partial_value


def _is_valid_thresholds(close_value, partial_value):
    return 0 < close_value < partial_value <= 1


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _safe_errors(value, fallback):
    errors = value.get("errors") if isinstance(value, dict) else None
    if not isinstance(errors, list):
        return fallback
    safe = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        code = error.get("code")
        message = error.get("message")
        if isinstance(code, str) and isinstance(message, str):
            safe.append({"code": code, "message": message})
    return safe or fallback


def parse_dossier_response(dossier_response_json):
    """Normalize the parse-dossier response for a Dify conditional branch."""

    dossier = _object(dossier_response_json, {})
    ok = dossier.get("valid") is True and isinstance(dossier.get("metrics"), list)
    errors = [] if ok else _safe_errors(
        dossier,
        [{"code": "invalid_dossier_response", "message": "Dossier response is invalid."}],
    )
    return {
        "dossier_ok": ok,
        "dossier_json": _json(dossier) if ok else "{}",
        "dossier_errors": _json(errors),
    }


def validate_thresholds(
    close_threshold=_DEFAULT_CLOSE_THRESHOLD, partial_threshold=_DEFAULT_PARTIAL_THRESHOLD
):
    """Validate inclusive similarity bands before any deterministic scoring."""

    close_value, partial_value = _threshold_values(close_threshold, partial_threshold)
    ok = _is_valid_thresholds(close_value, partial_value)
    errors = [] if ok else [
        {
            "code": "invalid_similarity_thresholds",
            "message": "Thresholds must satisfy 0 < close < partial <= 1.",
        }
    ]
    return {
        "thresholds_ok": ok,
        "close_threshold": close_value,
        "partial_threshold": partial_value,
        "threshold_errors": _json(errors),
    }


def build_comparison_request(dossier_json, experiment_json):
    """Build the narrow compare-result payload without display or evidence data."""

    dossier = _object(dossier_json, {})
    experiment = _object(experiment_json, {})
    experiment_id = experiment.get("experiment_id")
    reported = []
    metrics = dossier.get("metrics")
    if isinstance(metrics, list):
        for metric in metrics:
            if not isinstance(metric, dict) or metric.get("ambiguous") is True:
                continue
            name = metric.get("normalized_name") or metric.get("name")
            if (
                not isinstance(name, str)
                or not name.strip()
                or metric.get("reported_value") is None
            ):
                continue
            item = {"name": name}
            for field in _COMPARE_FIELDS:
                value = metric.get(field)
                if value is not None:
                    item[field] = value
            reported.append(item)

    ok = isinstance(experiment_id, str) and bool(experiment_id.strip()) and bool(reported)
    request = {"experiment_id": experiment_id, "reported_metrics": reported} if ok else {}
    errors = [] if ok else [
        {
            "code": "invalid_comparison_request",
            "message": "Experiment ID and unambiguous metrics are required.",
        }
    ]
    return {
        "comparison_request_ok": ok,
        "comparison_request_json": _json(request),
        "comparison_request_errors": _json(errors),
    }


def parse_comparison_response(comparison_response_json):
    """Normalize a compare-result response for an explicit Dify success branch."""

    comparison = _object(comparison_response_json, {})
    experiment_id = comparison.get("experiment_id")
    ok = isinstance(experiment_id, str) and bool(experiment_id.strip()) and isinstance(
        comparison.get("items"), list
    )
    errors = [] if ok else _safe_errors(
        comparison,
        [{"code": "invalid_comparison_response", "message": "Comparison response is invalid."}],
    )
    return {
        "comparison_ok": ok,
        "comparison_json": _json(comparison) if ok else "{}",
        "comparison_errors": _json(errors),
    }


def score_approximate_similarity(
    comparison_json,
    close_threshold=_DEFAULT_CLOSE_THRESHOLD,
    partial_threshold=_DEFAULT_PARTIAL_THRESHOLD,
):
    """Grade metric proximity while keeping strict comparability independent."""

    close_value, partial_value = _threshold_values(close_threshold, partial_threshold)
    if not _is_valid_thresholds(close_value, partial_value):
        assessment = {
            "strict_status": "not_comparable",
            "approximate_status": "insufficient_metrics",
            "close_threshold": close_value,
            "partial_threshold": partial_value,
            "items": [],
            "errors": [
                {
                    "code": "invalid_similarity_thresholds",
                    "message": "Thresholds must satisfy 0 < close < partial <= 1.",
                }
            ],
        }
        return {"assessment_json": _json(assessment)}

    comparison = _object(comparison_json, {"items": []})
    graded = []
    items = comparison.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            paper_value = _number(item.get("paper_value"))
            independent_value = _number(item.get("independent_value"))
            if paper_value is None or independent_value is None:
                continue
            raw_difference = (
                item.get("absolute_difference")
                if paper_value == 0
                else item.get("relative_difference")
            )
            difference = _number(raw_difference)
            if difference is None:
                continue
            difference = round(abs(difference), 6)
            if difference <= close_value:
                grade = "highly_similar"
            elif difference <= partial_value:
                grade = "partially_similar"
            else:
                grade = "materially_different"
            graded.append(
                {
                    "name": item.get("name") if isinstance(item.get("name"), str) else None,
                    "paper_value": paper_value,
                    "independent_value": independent_value,
                    "difference_for_grade": difference,
                    "grade": grade,
                    "comparable": item.get("comparable") is True,
                    "reason": item.get("reason") if isinstance(item.get("reason"), str) else None,
                }
            )

    comparable_count = sum(item["comparable"] for item in graded)
    if not graded or comparable_count == 0:
        strict_status = "not_comparable"
    elif comparable_count == len(graded):
        strict_status = "strictly_comparable"
    else:
        strict_status = "partially_comparable"

    grades = [item["grade"] for item in graded]
    if not grades:
        approximate_status = "insufficient_metrics"
    elif "materially_different" in grades:
        approximate_status = "materially_different"
    elif all(grade == "highly_similar" for grade in grades):
        approximate_status = "highly_similar"
    else:
        approximate_status = "partially_similar"

    return {
        "assessment_json": _json(
            {
                "strict_status": strict_status,
                "approximate_status": approximate_status,
                "close_threshold": close_value,
                "partial_threshold": partial_value,
                "items": graded,
            }
        )
    }


def _report_json(value):
    parsed = _object(value, {})
    if isinstance(value, str) and parsed:
        return value, parsed
    if isinstance(value, str) and value.strip() == "{}":
        return value, parsed
    return _json(parsed), parsed


def _pages(evidence):
    if not isinstance(evidence, list):
        return ""
    pages = []
    for item in evidence:
        page = item.get("page") if isinstance(item, dict) else None
        if isinstance(page, int) and not isinstance(page, bool) and page > 0:
            pages.append(page)
    return ", ".join("p.{0}".format(page) for page in sorted(set(pages)))


def _approximate_label(status):
    return {
        "highly_similar": "高度接近",
        "partially_similar": "部分接近",
        "materially_different": "存在实质差异",
        "insufficient_metrics": "指标不足",
    }.get(status, "指标不足")


def format_comparison_report(
    dossier_json, validation_json, experiment_json, comparison_json, assessment_json
):
    """Return the six workflow strings and a report that avoids reproduction claims."""

    dossier_string, dossier = _report_json(dossier_json)
    validation_string, validation = _report_json(validation_json)
    experiment_string, experiment = _report_json(experiment_json)
    comparison_string, comparison = _report_json(comparison_json)
    assessment_string, assessment = _report_json(assessment_json)

    title = dossier.get("title") if isinstance(dossier.get("title"), str) else "未命名论文"
    strict_status = assessment.get("strict_status", "not_comparable")
    approximate_status = assessment.get("approximate_status", "insufficient_metrics")
    lines = [
        "# 论文指标对比报告",
        "",
        "论文: {0}".format(title),
        "实验 ID: {0}".format(experiment.get("experiment_id") or comparison.get("experiment_id") or "未提供"),
        "严格可比性: {0}".format(strict_status),
        "近似相似度: {0}".format(_approximate_label(approximate_status)),
        "",
        "## 论文来源与证据",
    ]
    metrics = dossier.get("metrics")
    if isinstance(metrics, list) and metrics:
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            name = metric.get("name") if isinstance(metric.get("name"), str) else "未命名指标"
            source = metric.get("source") if isinstance(metric.get("source"), str) else "未标注来源"
            pages = _pages(metric.get("evidence"))
            lines.append("- {0}: 来源 {1}{2}".format(name, source, "；证据 " + pages if pages else "；无页码证据"))
    else:
        lines.append("- 未提供可展示的论文指标证据。")
    lines.extend(
        [
            "",
            "近似指标一致不等于严格复现。",
            "严格可比性和近似相似度是独立结论。",
        ]
    )
    return {
        "dossier_json": dossier_string,
        "validation_json": validation_string,
        "experiment_json": experiment_string,
        "comparison_json": comparison_string,
        "assessment_json": assessment_string,
        "markdown_report": "\n".join(lines),
    }


def normalize_dossier_http_failure():
    """Produce a complete static terminal payload when dossier parsing is unavailable."""

    dossier = {
        "valid": False,
        "errors": [_static_error("dossier_service_unavailable", "Dossier service request failed.", "parse_dossier")],
    }
    validation = {"valid": False, "errors": [_static_error("validation_not_run", "Dataset validation did not run.", "validate_dataset")]}
    experiment = {"status": "failed", "errors": [_static_error("experiment_not_run", "Experiment did not run.", "run_experiment")]}
    comparison = {"items": [], "errors": [_static_error("comparison_not_run", "Comparison did not run.", "compare_result")]}
    assessment = {
        "strict_status": "not_comparable",
        "approximate_status": "insufficient_metrics",
        "close_threshold": _DEFAULT_CLOSE_THRESHOLD,
        "partial_threshold": _DEFAULT_PARTIAL_THRESHOLD,
        "items": [],
    }
    return {
        "dossier_json": _json(dossier),
        "validation_json": _json(validation),
        "experiment_json": _json(experiment),
        "comparison_json": _json(comparison),
        "assessment_json": _json(assessment),
        "markdown_report": "论文档案服务暂时不可用，未执行后续比较。",
    }


def normalize_comparison_http_failure(dossier_json, validation_json, experiment_json):
    """Preserve completed prior objects and add a static comparison failure."""

    dossier = _object(dossier_json, {})
    validation = _object(validation_json, {})
    experiment = _object(experiment_json, {})
    experiment_id = experiment.get("experiment_id")
    comparison = {
        "experiment_id": experiment_id if isinstance(experiment_id, str) else None,
        "items": [],
        "errors": [
            _static_error(
                "comparison_service_unavailable",
                "Comparison service request failed.",
                "compare_result",
            )
        ],
    }
    assessment = {
        "strict_status": "not_comparable",
        "approximate_status": "insufficient_metrics",
        "close_threshold": _DEFAULT_CLOSE_THRESHOLD,
        "partial_threshold": _DEFAULT_PARTIAL_THRESHOLD,
        "items": [],
    }
    return {
        "dossier_json": _json(dossier),
        "validation_json": _json(validation),
        "experiment_json": _json(experiment),
        "comparison_json": _json(comparison),
        "assessment_json": _json(assessment),
        "markdown_report": "比较服务暂时不可用，未生成近似相似度结论。",
    }
