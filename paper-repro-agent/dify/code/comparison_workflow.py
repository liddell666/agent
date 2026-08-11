"""Deterministic, safe helpers for the Dify paper-comparison workflow."""

import json
import math
import re


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


_SUITE_METRIC_ALIASES = {"auc": "roc_auc", "roc_auc": "roc_auc"}
_SUITE_SUPPORTED_METRICS = {
    "roc_auc",
    "accuracy",
    "balanced_accuracy",
    "precision",
    "recall",
    "f1",
}
_SAFE_SUITE_ERROR_MESSAGES = {"bounded_failure"}
_SAFE_SUITE_ERROR_REDACTION = "details redacted for privacy."
_SAFE_SUITE_MANUAL_QUALIFIERS = {"\u5949\u8282\u53bf\uff08\u5168\u57df\u6a21\u578b\uff09", "\u6d4b\u8bd5\u96c6"}
_SAFE_SUITE_EXPERIMENT_ID_RE = re.compile(r"^exp-[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_SAFE_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _normalized_metric_name(value):
    if not isinstance(value, str):
        return None
    normalized = "_".join(value.strip().casefold().replace("-", " ").split())
    normalized = _SUITE_METRIC_ALIASES.get(normalized, normalized)
    return normalized if normalized in _SUITE_SUPPORTED_METRICS else None


def _safe_suite_experiment_id(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _SAFE_SUITE_EXPERIMENT_ID_RE.fullmatch(candidate) else None


def _safe_suite_qualifier(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 32:
        return None
    if any(ord(char) < 32 for char in candidate):
        return None
    lowered = candidate.casefold()
    if lowered.startswith("sk-") or "secret_token" in lowered:
        return None
    if "traceback (most recent call last):" in lowered:
        return None
    if candidate in _SAFE_SUITE_MANUAL_QUALIFIERS:
        return candidate
    allowed_punctuation = " ._()/-\uff08\uff09"
    return candidate if all(char.isalnum() or char in allowed_punctuation for char in candidate) else None


def _safe_suite_digest(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _SAFE_SHA256_RE.fullmatch(candidate) else None


def _safe_suite_score(value):
    parsed = _number(value)
    return parsed if parsed is not None and 0 <= parsed <= 1 else None


def _safe_suite_fraction(value):
    parsed = _number(value)
    return parsed if parsed is not None and 0 < parsed < 1 else None


def _safe_suite_int(value, minimum, maximum):
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, float):
            if not value.is_integer():
                return None
            parsed = int(value)
        elif isinstance(value, int):
            parsed = value
        elif isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return None
            parsed = int(candidate)
        else:
            return None
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _safe_suite_error_code(value):
    if not isinstance(value, str):
        return "unavailable"
    candidate = value.strip()
    return candidate if _SAFE_ERROR_CODE_RE.fullmatch(candidate) else "unavailable"


def build_suite_comparison_request(dossier_json, suite_json):
    dossier = _object(dossier_json, {})
    suite = _object(suite_json, {})
    experiment_id = _safe_suite_experiment_id(suite.get("experiment_id"))
    reported = []
    metrics = dossier.get("metrics")
    if isinstance(metrics, list):
        for metric in metrics:
            if (
                not isinstance(metric, dict)
                or metric.get("ambiguous") is True
                or metric.get("supported") is not True
            ):
                continue
            name = _normalized_metric_name(metric.get("normalized_name") or metric.get("name"))
            reported_value = _safe_suite_score(metric.get("reported_value"))
            if name is None or reported_value is None:
                continue
            item = {"name": name, "reported_value": reported_value}
            for key, validator, raw in (
                ("dataset", _safe_suite_qualifier, metric.get("dataset")),
                ("split", _safe_suite_qualifier, metric.get("split")),
                ("dataset_id", _safe_suite_digest, metric.get("dataset_id")),
                ("test_size", _safe_suite_fraction, metric.get("test_size")),
                (
                    "random_state",
                    lambda value: _safe_suite_int(value, 0, 2_147_483_647),
                    metric.get("random_state"),
                ),
                (
                    "train_rows",
                    lambda value: _safe_suite_int(value, 1, 1_000_000_000),
                    metric.get("train_rows"),
                ),
                (
                    "test_rows",
                    lambda value: _safe_suite_int(value, 1, 1_000_000_000),
                    metric.get("test_rows"),
                ),
                ("test_digest", _safe_suite_digest, metric.get("test_digest")),
            ):
                value = validator(raw)
                if value is not None:
                    item[key] = value
            reported.append(item)
    ok = isinstance(experiment_id, str) and bool(experiment_id.strip()) and bool(reported)
    request = {"experiment_id": experiment_id, "reported_metrics": reported} if ok else {}
    errors = [] if ok else [
        {
            "code": "invalid_suite_comparison_request",
            "message": "Suite experiment ID and unambiguous metrics are required.",
        }
    ]
    return {
        "suite_comparison_request_ok": ok,
        "suite_comparison_request_json": _json(request),
        "suite_comparison_request_errors": _json(errors),
    }


def _suite_results(suite):
    results = suite.get("results")
    return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []


def _suite_result_map(suite):
    mapped = {}
    for item in _suite_results(suite):
        model = item.get("model")
        if isinstance(model, str) and model not in mapped:
            mapped[model] = item
    return mapped


def _comparison_map(comparison):
    mapped = {}
    items = comparison.get("items")
    if not isinstance(items, list):
        return mapped
    for item in items:
        if not isinstance(item, dict):
            continue
        model = item.get("model")
        if isinstance(model, str) and model not in mapped:
            mapped[model] = item
    return mapped


def _assessment_map(assessment):
    mapped = {}
    items = assessment.get("items")
    if not isinstance(items, list):
        return mapped
    for item in items:
        if not isinstance(item, dict):
            continue
        model = item.get("model")
        if isinstance(model, str) and model not in mapped:
            mapped[model] = item
    return mapped


def _safe_message(value):
    if not isinstance(value, str):
        return _SAFE_SUITE_ERROR_REDACTION
    candidate = value.strip()
    return candidate if candidate in _SAFE_SUITE_ERROR_MESSAGES else _SAFE_SUITE_ERROR_REDACTION


def _ranking_list(value):
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _suite_metric_value(metrics, key):
    if not isinstance(metrics, dict):
        return None
    return metrics.get(key)


def format_suite_comparison_report(
    dossier_json,
    validation_json,
    suite_json,
    comparison_json,
    assessment_json,
):
    dossier_string, dossier = _report_json(dossier_json)
    validation_string, validation = _report_json(validation_json)
    suite_string, suite = _report_json(suite_json)
    comparison_string, comparison = _report_json(comparison_json)
    assessment_string, assessment = _report_json(assessment_json)

    result_map = _suite_result_map(suite)
    comparison_by_model = _comparison_map(comparison)
    assessment_by_model = _assessment_map(assessment)
    dataset = validation.get("dataset") if isinstance(validation.get("dataset"), dict) else {}
    if not dataset and isinstance(suite.get("dataset"), dict):
        dataset = suite.get("dataset")
    split = suite.get("split_provenance") if isinstance(suite.get("split_provenance"), dict) else {}
    config = suite.get("config") if isinstance(suite.get("config"), dict) else {}
    performance_ranking = _ranking_list(suite.get("performance_ranking"))
    paper_distance_ranking = _ranking_list(
        comparison.get("paper_distance_ranking") or assessment.get("paper_distance_ranking")
    )
    experiment_id = suite.get("experiment_id") or comparison.get("experiment_id") or "unavailable"
    title = dossier.get("title") if isinstance(dossier.get("title"), str) else "Unnamed paper"

    lines = [
        "# Multi-model comparison report",
        "",
        f"paper: {_report_value(title)}",
        f"suite experiment id: {_report_value(experiment_id)}",
        "shared dataset/split summary:",
        f"- rows={_report_value(dataset.get('rows'))}, effective_rows={_report_value(dataset.get('effective_rows'))}, features={_report_value(dataset.get('features'))}, target={_report_value(dataset.get('target') or config.get('target_column'))}",
        f"- test_size={_report_value(split.get('test_size', config.get('test_size')))}, random_state={_report_value(split.get('random_state', config.get('random_state')))}, train_rows={_report_value(split.get('train_rows'))}, test_rows={_report_value(split.get('test_rows'))}",
        f"- cv_folds={_report_value(config.get('cv_folds'))}, optimization_metric={_report_value(config.get('optimization_metric'))}, n_iter={_report_value(config.get('n_iter'))}, use_gpu={_report_value(config.get('use_gpu'))}",
        "",
    ]
    if performance_ranking:
        lines.append("performance ranking: " + " > ".join(performance_ranking))
    if paper_distance_ranking:
        lines.append("paper-distance ranking: " + " > ".join(paper_distance_ranking))
    if performance_ranking or paper_distance_ranking:
        lines.append("")

    lines.append("## model summaries")
    for model in [item.get("model") for item in _suite_results(suite) if isinstance(item.get("model"), str)]:
        result = result_map.get(model, {})
        comparison_item = comparison_by_model.get(model, {})
        assessment_item = assessment_by_model.get(model, {})
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        lines.extend(
            [
                f"### {model}",
                f"- status: {_report_value(result.get('status'))}",
                f"- cv_best_score: {_report_value(result.get('cv_best_score'))}",
                f"- auc={_report_value(_suite_metric_value(metrics, 'roc_auc'))}, accuracy={_report_value(_suite_metric_value(metrics, 'accuracy'))}, f1={_report_value(_suite_metric_value(metrics, 'f1'))}, recall={_report_value(_suite_metric_value(metrics, 'recall'))}",
                f"- paper_value={_report_value(comparison_item.get('paper_value'))}, absolute_difference={_report_value(comparison_item.get('absolute_difference'))}, relative_difference={_report_value(comparison_item.get('relative_difference'))}",
                f"- comparison_reason={_report_value(comparison_item.get('reason'))}, approximate_grade={_report_value(assessment_item.get('grade'))}",
            ]
        )
        if error:
            lines.append(
                f"- safe_error={_safe_suite_error_code(error.get('code'))}: {_safe_message(error.get('message'))}"
            )
    if not _suite_results(suite):
        lines.append("- no suite results were available.")

    if performance_ranking or paper_distance_ranking:
        lines.extend(["", "## rankings"])
        if performance_ranking:
            lines.append("- performance ranking: " + " > ".join(performance_ranking))
        if paper_distance_ranking:
            lines.append("- paper-distance ranking: " + " > ".join(paper_distance_ranking))

    incomparable = [
        item.get("reason")
        for item in comparison_by_model.values()
        if isinstance(item.get("reason"), str) and item.get("comparable") is not True
    ]
    if incomparable:
        lines.extend(
            [
                "",
                "numeric similarity is not strict reproduction when provenance does not match.",
                "not strict reproduction: " + "; ".join(sorted(set(incomparable))),
            ]
        )

    return {
        "dossier_json": dossier_string,
        "validation_json": validation_string,
        "experiment_json": suite_string,
        "comparison_json": comparison_string,
        "assessment_json": assessment_string,
        "markdown_report": "\n".join(lines),
    }


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
            if (
                not isinstance(metric, dict)
                or metric.get("ambiguous") is True
                or metric.get("supported") is not True
            ):
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


def _report_value(value):
    if value is None or value == "":
        return "未提供"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("\r", " ").replace("\n", " ")


def _selected_metrics(metrics):
    if not isinstance(metrics, list):
        return []
    selected = []
    for metric in metrics:
        if (
            not isinstance(metric, dict)
            or metric.get("ambiguous") is True
            or metric.get("supported") is not True
        ):
            continue
        candidate = metric.get("normalized_name") or metric.get("name")
        if (
            isinstance(candidate, str)
            and candidate.strip()
            and metric.get("reported_value") is not None
        ):
            selected.append(metric)
    return selected


def _matching_selected_metric(metrics, name, index):
    if index < len(metrics):
        metric = metrics[index]
        candidate = metric.get("normalized_name") or metric.get("name")
        if candidate == name:
            return metric
    return {}


def _matching_assessment(items, name, index):
    if not isinstance(items, list):
        return {}
    if index < len(items) and isinstance(items[index], dict):
        candidate = items[index]
        if candidate.get("name") == name:
            return candidate
    for item in items:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return {}


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
    experiment_id = experiment.get("experiment_id") or comparison.get("experiment_id") or "未提供"
    dataset = validation.get("dataset") if isinstance(validation.get("dataset"), dict) else {}
    if not dataset and isinstance(experiment.get("dataset"), dict):
        dataset = experiment["dataset"]
    config = experiment.get("config") if isinstance(experiment.get("config"), dict) else {}
    split = (
        experiment.get("split_provenance")
        if isinstance(experiment.get("split_provenance"), dict)
        else {}
    )
    lines = [
        "# 论文指标对比报告",
        "",
        "论文: {0}".format(title),
        "实验 ID: {0}".format(_report_value(experiment_id)),
        "严格可比性: {0}".format(strict_status),
        "近似相似度: {0} ({1})".format(
            _approximate_label(approximate_status), approximate_status
        ),
        "",
        "## 数据与划分摘要",
        "- 数据集: 行数 {0}; 有效行数 {1}; 特征数 {2}; 目标列 {3}; 缺失值 {4}; 重复行 {5}".format(
            _report_value(dataset.get("rows")),
            _report_value(dataset.get("effective_rows")),
            _report_value(dataset.get("features")),
            _report_value(dataset.get("target") or config.get("target_column")),
            _report_value(dataset.get("missing_values")),
            _report_value(dataset.get("duplicate_rows")),
        ),
        "- 划分: test_size {0}; random_state {1}; 训练行 {2}; 测试行 {3}".format(
            _report_value(split.get("test_size", config.get("test_size"))),
            _report_value(split.get("random_state", config.get("random_state"))),
            _report_value(split.get("train_rows")),
            _report_value(split.get("test_rows")),
        ),
        "",
        "## 指标明细",
    ]
    metrics = dossier.get("metrics") if isinstance(dossier.get("metrics"), list) else []
    selected_metrics = _selected_metrics(metrics)
    comparison_items = (
        comparison.get("items") if isinstance(comparison.get("items"), list) else []
    )
    assessment_items = (
        assessment.get("items") if isinstance(assessment.get("items"), list) else []
    )
    if comparison_items:
        for index, item in enumerate(comparison_items):
            if not isinstance(item, dict):
                continue
            name = item.get("name") if isinstance(item.get("name"), str) else "未命名指标"
            metric = _matching_selected_metric(selected_metrics, name, index)
            grade = _matching_assessment(assessment_items, name, index).get("grade")
            pages = _pages(metric.get("evidence"))
            lines.extend(
                [
                    "### {0}".format(_report_value(name)),
                    "- 论文值: {0}; 独立值: {1}; 绝对差: {2}; 相对差: {3}".format(
                        _report_value(item.get("paper_value")),
                        _report_value(item.get("independent_value")),
                        _report_value(item.get("absolute_difference")),
                        _report_value(item.get("relative_difference")),
                    ),
                    "- 严格可比: {0}; 原因: {1}; 近似等级: {2}".format(
                        _report_value(item.get("comparable") is True),
                        _report_value(item.get("reason") or "满足全部严格来源条件"),
                        _report_value(grade),
                    ),
                    "- 论文数据集/划分: {0}/{1}; 来源: {2}; 证据页: {3}".format(
                        _report_value(metric.get("dataset")),
                        _report_value(metric.get("split")),
                        _report_value(metric.get("source") or "未标注来源"),
                        pages or "无页码证据",
                    ),
                ]
            )
    else:
        lines.append("- 没有可用的论文值与独立实验值对。")
    lines.extend(["", "## 论文来源与证据"])
    if metrics:
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            name = metric.get("normalized_name") or metric.get("name") or "未命名指标"
            source = metric.get("source") or "未标注来源"
            pages = _pages(metric.get("evidence"))
            lines.append(
                "- {0}: 来源 {1}; 证据页 {2}".format(
                    _report_value(name), _report_value(source), pages or "无页码证据"
                )
            )
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
