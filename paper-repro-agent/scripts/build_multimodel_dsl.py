from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DSL = PROJECT_ROOT / "dify" / "paper-comparison-workflow.yml"
TARGET_DSL = PROJECT_ROOT / "dify" / "paper-comparison-multimodel-workflow.yml"
DEFAULT_MODELS = [
    "logistic_regression",
    "random_forest",
    "xgboost",
    "lightgbm",
    "svm",
    "knn",
    "mlp",
]


def _load_source() -> dict:
    return yaml.safe_load(SOURCE_DSL.read_text(encoding="utf-8"))


def _nodes(document: dict) -> list[dict]:
    return document["workflow"]["graph"]["nodes"]


def _by_title(document: dict) -> dict[str, dict]:
    return {node["data"]["title"]: node for node in _nodes(document)}


def _suite_normalize_code() -> str:
    return """import json

KNOWN_MODELS = (
    "logistic_regression",
    "random_forest",
    "xgboost",
    "lightgbm",
    "svm",
    "knn",
    "mlp",
)
KNOWN_METRICS = {"roc_auc", "f1", "recall", "balanced_accuracy"}

def safe_models_json(models_json):
    default = json.dumps(list(KNOWN_MODELS), ensure_ascii=False)
    try:
        parsed = json.loads(models_json) if isinstance(models_json, str) else models_json
    except (TypeError, json.JSONDecodeError):
        return default
    if not isinstance(parsed, list) or not parsed:
        return default
    values = []
    seen = set()
    for item in parsed:
        if not isinstance(item, str):
            return default
        name = item.strip()
        if name not in KNOWN_MODELS or name in seen:
            return default
        seen.add(name)
        values.append(name)
    return json.dumps(values, ensure_ascii=False)

def safe_int_text(value, default, minimum, maximum):
    if isinstance(value, bool):
        return str(default)
    try:
        if isinstance(value, float):
            if not value.is_integer():
                return str(default)
            parsed = int(value)
        elif isinstance(value, int):
            parsed = value
        elif isinstance(value, str):
            text = value.strip()
            if not text or text != str(int(text)):
                return str(default)
            parsed = int(text)
        else:
            return str(default)
    except (TypeError, ValueError):
        return str(default)
    return str(parsed if minimum <= parsed <= maximum else default)

def safe_metric_text(value):
    if not isinstance(value, str):
        return "roc_auc"
    metric = value.strip()
    return metric if metric in KNOWN_METRICS else "roc_auc"

def safe_bool_text(value):
    if value is True or (isinstance(value, str) and value.strip().casefold() == "true"):
        return "true"
    if value is False or (isinstance(value, str) and value.strip().casefold() == "false"):
        return "false"
    return "false"

def main(models_json, cv_folds, optimization_metric, n_iter, use_gpu, drop_duplicates):
    return {
        "models_json_text": safe_models_json(models_json),
        "cv_folds_text": safe_int_text(cv_folds, 5, 3, 10),
        "optimization_metric_text": safe_metric_text(optimization_metric),
        "n_iter_text": safe_int_text(n_iter, 8, 1, 32),
        "use_gpu_text": safe_bool_text(use_gpu),
        "drop_duplicates_text": safe_bool_text(drop_duplicates),
    }
"""


def _suite_parse_code() -> str:
    return """import json

def main(body: str) -> dict:
    try:
        experiment = json.loads(body) if isinstance(body, str) else body
    except (TypeError, json.JSONDecodeError):
        experiment = {"status": "failed", "errors": [{"code": "invalid_suite_response", "message": "Suite response could not be read."}]}
    if not isinstance(experiment, dict):
        experiment = {"status": "failed", "errors": [{"code": "invalid_suite_response", "message": "Suite response must be an object."}]}
    errors = experiment.get("errors")
    if not isinstance(errors, list):
        errors = []
    ok = (
        isinstance(experiment.get("experiment_id"), str)
        and bool(experiment.get("experiment_id", "").strip())
        and experiment.get("status") in {"succeeded", "partial"}
        and isinstance(experiment.get("results"), list)
    )
    if not ok and not errors:
        errors = [{"code": "suite_experiment_rejected", "message": "The suite did not return a successful result."}]
    normalized = {**experiment, "errors": errors}
    return {
        "experiment_ok": ok,
        "experiment_json": json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
        "experiment_errors": json.dumps(errors, ensure_ascii=False, separators=(",", ":")),
    }
"""


def _suite_request_code() -> str:
    return """import json
import math
import re

ALIASES = {"auc": "roc_auc", "roc_auc": "roc_auc"}
SUPPORTED = {"roc_auc", "accuracy", "balanced_accuracy", "precision", "recall", "f1"}
MANUAL_OVERRIDE_QUALIFIERS = {"\u5949\u8282\u53bf\uff08\u5168\u57df\u6a21\u578b\uff09", "\u6d4b\u8bd5\u96c6"}
EXPERIMENT_ID_RE = re.compile(r"^exp-[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

def object_or_empty(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

def safe_number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None

def metric_name(metric):
    candidate = metric.get("normalized_name") or metric.get("name")
    if not isinstance(candidate, str):
        return None
    normalized = "_".join(candidate.strip().casefold().replace("-", " ").split())
    normalized = ALIASES.get(normalized, normalized)
    return normalized if normalized in SUPPORTED else None

def safe_qualifier(value):
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
    if candidate in MANUAL_OVERRIDE_QUALIFIERS:
        return candidate
    allowed_punctuation = " ._()/-\uff08\uff09"
    return candidate if all(char.isalnum() or char in allowed_punctuation for char in candidate) else None

def safe_digest(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if SHA256_RE.fullmatch(candidate) else None

def safe_experiment_id(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if EXPERIMENT_ID_RE.fullmatch(candidate) else None

def safe_score(value):
    parsed = safe_number(value)
    return parsed if parsed is not None and 0 <= parsed <= 1 else None

def safe_fraction(value):
    parsed = safe_number(value)
    return parsed if parsed is not None and 0 < parsed < 1 else None

def safe_int(value, minimum, maximum):
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
            text = value.strip()
            if not text:
                return None
            parsed = int(text)
        else:
            return None
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None

def main(dossier_json: str, experiment_json: str) -> dict:
    dossier = object_or_empty(dossier_json)
    suite = object_or_empty(experiment_json)
    reported = []
    for metric in dossier.get("metrics", []):
        if (
            not isinstance(metric, dict)
            or metric.get("ambiguous") is True
            or metric.get("supported") is not True
        ):
            continue
        name = metric_name(metric)
        reported_value = safe_score(metric.get("reported_value"))
        if name is None or reported_value is None:
            continue
        item = {"name": name, "reported_value": reported_value}
        for key, validator, raw in (
            ("dataset", safe_qualifier, metric.get("dataset")),
            ("split", safe_qualifier, metric.get("split")),
            ("dataset_id", safe_digest, metric.get("dataset_id")),
            ("test_size", safe_fraction, metric.get("test_size")),
            ("random_state", lambda value: safe_int(value, 0, 2147483647), metric.get("random_state")),
            ("train_rows", lambda value: safe_int(value, 1, 1000000000), metric.get("train_rows")),
            ("test_rows", lambda value: safe_int(value, 1, 1000000000), metric.get("test_rows")),
            ("test_digest", safe_digest, metric.get("test_digest")),
        ):
            value = validator(raw)
            if value is not None:
                item[key] = value
        reported.append(item)
    experiment_id = safe_experiment_id(suite.get("experiment_id"))
    ok = isinstance(experiment_id, str) and bool(experiment_id.strip()) and bool(reported)
    request = {"experiment_id": experiment_id, "reported_metrics": reported} if ok else {}
    errors = [] if ok else [{"code": "invalid_suite_comparison_request", "message": "Suite experiment ID and unambiguous metrics are required."}]
    return {
        "suite_comparison_request_ok": ok,
        "suite_comparison_request_json": json.dumps(request, ensure_ascii=False, separators=(",", ":")),
        "suite_comparison_request_errors": json.dumps(errors, ensure_ascii=False, separators=(",", ":")),
    }
"""


def _suite_comparison_parse_code() -> str:
    return """import json

def main(comparison_response_json: str) -> dict:
    try:
        comparison = json.loads(comparison_response_json) if isinstance(comparison_response_json, str) else comparison_response_json
    except (TypeError, json.JSONDecodeError):
        comparison = {}
    experiment_id = comparison.get("experiment_id") if isinstance(comparison, dict) else None
    ok = isinstance(experiment_id, str) and bool(experiment_id.strip()) and isinstance(comparison.get("items"), list)
    errors = comparison.get("errors") if isinstance(comparison, dict) else None
    if not isinstance(errors, list):
        errors = [{"code": "invalid_comparison_response", "message": "Comparison response is invalid."}]
    return {
        "comparison_ok": ok,
        "comparison_json": json.dumps(comparison, ensure_ascii=False, separators=(",", ":")) if ok else "{}",
        "comparison_errors": json.dumps([] if ok else errors, ensure_ascii=False, separators=(",", ":")),
    }
"""


def _suite_report_code() -> str:
    return """import json
import re

SAFE_ERROR_MESSAGES = {"bounded_failure"}
SAFE_ERROR_REDACTION = "details redacted for privacy."
SAFE_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

def object_or_empty(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}

def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

def report_json(value):
    parsed = object_or_empty(value)
    if isinstance(value, str) and (parsed or value.strip() == "{}"):
        return value, parsed
    return encoded(parsed), parsed

def report_value(value):
    if value is None or value == "":
        return "unavailable"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("\\r", " ").replace("\\n", " ")

def safe_error_code(value):
    if not isinstance(value, str):
        return "unavailable"
    candidate = value.strip()
    return candidate if SAFE_ERROR_CODE_RE.fullmatch(candidate) else "unavailable"

def safe_error_message(value):
    if not isinstance(value, str):
        return SAFE_ERROR_REDACTION
    candidate = value.strip()
    return candidate if candidate in SAFE_ERROR_MESSAGES else SAFE_ERROR_REDACTION

def results_list(suite):
    values = suite.get("results")
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []

def first_by_model(items):
    mapped = {}
    if not isinstance(items, list):
        return mapped
    for item in items:
        if not isinstance(item, dict):
            continue
        model = item.get("model")
        if isinstance(model, str) and model not in mapped:
            mapped[model] = item
    return mapped

def ranking_list(value):
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

def main(dossier_json: str, validation_json: str, experiment_json: str, comparison_json: str, assessment_json: str) -> dict:
    dossier_string, dossier = report_json(dossier_json)
    validation_string, validation = report_json(validation_json)
    experiment_string, experiment = report_json(experiment_json)
    comparison_string, comparison = report_json(comparison_json)
    assessment_string, assessment = report_json(assessment_json)
    dataset = validation.get("dataset") if isinstance(validation.get("dataset"), dict) else {}
    if not dataset and isinstance(experiment.get("dataset"), dict):
        dataset = experiment.get("dataset")
    split = experiment.get("split_provenance") if isinstance(experiment.get("split_provenance"), dict) else {}
    config = experiment.get("config") if isinstance(experiment.get("config"), dict) else {}
    title = dossier.get("title") if isinstance(dossier.get("title"), str) else "Unnamed paper"
    performance_ranking = ranking_list(experiment.get("performance_ranking"))
    paper_distance_ranking = ranking_list(comparison.get("paper_distance_ranking") or assessment.get("paper_distance_ranking"))
    comparison_by_model = first_by_model(comparison.get("items"))
    assessment_by_model = first_by_model(assessment.get("items"))
    lines = [
        "# Multi-model comparison report",
        "",
        f"paper: {report_value(title)}",
        f"suite experiment id: {report_value(experiment.get('experiment_id') or comparison.get('experiment_id'))}",
        "shared dataset/split summary:",
        f"- rows={report_value(dataset.get('rows'))}, effective_rows={report_value(dataset.get('effective_rows'))}, features={report_value(dataset.get('features'))}, target={report_value(dataset.get('target') or config.get('target_column'))}",
        f"- test_size={report_value(split.get('test_size', config.get('test_size')))}, random_state={report_value(split.get('random_state', config.get('random_state')))}, train_rows={report_value(split.get('train_rows'))}, test_rows={report_value(split.get('test_rows'))}",
        f"- cv_folds={report_value(config.get('cv_folds'))}, optimization_metric={report_value(config.get('optimization_metric'))}, n_iter={report_value(config.get('n_iter'))}, use_gpu={report_value(config.get('use_gpu'))}",
        "",
    ]
    if performance_ranking:
        lines.append("performance ranking: " + " > ".join(performance_ranking))
    if paper_distance_ranking:
        lines.append("paper-distance ranking: " + " > ".join(paper_distance_ranking))
    if performance_ranking or paper_distance_ranking:
        lines.append("")
    lines.append("## model summaries")
    for item in results_list(experiment):
        model = item.get("model")
        if not isinstance(model, str):
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        comparison_item = comparison_by_model.get(model, {})
        assessment_item = assessment_by_model.get(model, {})
        error = item.get("error") if isinstance(item.get("error"), dict) else {}
        lines.extend([
            f"### {model}",
            f"- status: {report_value(item.get('status'))}",
            f"- cv_best_score: {report_value(item.get('cv_best_score'))}",
            f"- auc={report_value(metrics.get('roc_auc'))}, accuracy={report_value(metrics.get('accuracy'))}, f1={report_value(metrics.get('f1'))}, recall={report_value(metrics.get('recall'))}",
            f"- paper_value={report_value(comparison_item.get('paper_value'))}, absolute_difference={report_value(comparison_item.get('absolute_difference'))}, relative_difference={report_value(comparison_item.get('relative_difference'))}",
            f"- comparison_reason={report_value(comparison_item.get('reason'))}, approximate_grade={report_value(assessment_item.get('grade'))}",
        ])
        if error:
            lines.append(f"- safe_error={safe_error_code(error.get('code'))}: {safe_error_message(error.get('message'))}")
    if not results_list(experiment):
        lines.append("- no suite results were available.")
    if performance_ranking or paper_distance_ranking:
        lines.extend(["", "## rankings"])
        if performance_ranking:
            lines.append("- performance ranking: " + " > ".join(performance_ranking))
        if paper_distance_ranking:
            lines.append("- paper-distance ranking: " + " > ".join(paper_distance_ranking))
    incomparable = sorted(
        {
            item.get("reason")
            for item in comparison_by_model.values()
            if isinstance(item.get("reason"), str) and item.get("comparable") is not True
        }
    )
    if incomparable:
        lines.extend([
            "",
            "numeric similarity is not strict reproduction when provenance does not match.",
            "not strict reproduction: " + "; ".join(incomparable),
        ])
    return {
        "dossier_json": dossier_string,
        "validation_json": validation_string,
        "experiment_json": experiment_string,
        "comparison_json": comparison_string,
        "assessment_json": assessment_string,
        "markdown_report": "\\n".join(lines),
    }
"""


def _reordered_start_variables(start_node: dict) -> list[dict]:
    existing = {item["variable"]: deepcopy(item) for item in start_node["data"]["variables"]}
    return [
        existing["paper_dossier_json"],
        existing["training_csv"],
        existing["metric_overrides_json"],
        existing["target_column"],
        existing["test_size"],
        existing["random_state"],
        {
            "default": json.dumps(DEFAULT_MODELS),
            "hint": "",
            "label": "models_json",
            "max_length": 100000,
            "options": [],
            "placeholder": "",
            "required": False,
            "type": "paragraph",
            "variable": "models_json",
        },
        {
            "default": 5,
            "hint": "",
            "label": "cv_folds",
            "options": [],
            "placeholder": "",
            "required": False,
            "type": "number",
            "variable": "cv_folds",
        },
        {
            "default": "roc_auc",
            "hint": "",
            "label": "optimization_metric",
            "max_length": 100000,
            "options": [],
            "placeholder": "",
            "required": False,
            "type": "text-input",
            "variable": "optimization_metric",
        },
        {
            "default": 8,
            "hint": "",
            "label": "n_iter",
            "options": [],
            "placeholder": "",
            "required": False,
            "type": "number",
            "variable": "n_iter",
        },
        {
            "default": False,
            "hint": "",
            "label": "use_gpu",
            "options": [],
            "placeholder": "",
            "required": False,
            "type": "checkbox",
            "variable": "use_gpu",
        },
        existing["drop_duplicates"],
        existing["close_threshold"],
        existing["partial_threshold"],
    ]


def build_multimodel_dsl() -> dict:
    document = deepcopy(_load_source())
    nodes = _by_title(document)
    start = nodes["Start"]
    start["data"]["variables"] = _reordered_start_variables(start)

    normalize = nodes["normalize_experiment_inputs"]
    normalize["data"]["title"] = "normalize_suite_inputs"
    normalize["data"]["code"] = _suite_normalize_code()
    normalize["data"]["outputs"] = {
        "models_json_text": {"children": None, "type": "string"},
        "cv_folds_text": {"children": None, "type": "string"},
        "optimization_metric_text": {"children": None, "type": "string"},
        "n_iter_text": {"children": None, "type": "string"},
        "use_gpu_text": {"children": None, "type": "string"},
        "drop_duplicates_text": {"children": None, "type": "string"},
    }
    normalize["data"]["variables"] = [
        {"value_selector": [start["id"], "models_json"], "value_type": "string", "variable": "models_json"},
        {"value_selector": [start["id"], "cv_folds"], "value_type": "number", "variable": "cv_folds"},
        {"value_selector": [start["id"], "optimization_metric"], "value_type": "string", "variable": "optimization_metric"},
        {"value_selector": [start["id"], "n_iter"], "value_type": "number", "variable": "n_iter"},
        {"value_selector": [start["id"], "use_gpu"], "value_type": "boolean", "variable": "use_gpu"},
        {"value_selector": [start["id"], "drop_duplicates"], "value_type": "boolean", "variable": "drop_duplicates"},
    ]

    run_node = nodes["run_experiment"]
    run_node["data"]["title"] = "run_model_suite"
    run_node["data"]["url"] = "http://repro-runner:8001/v1/run-model-suite"
    run_node["data"]["body"]["data"] = [
        run_node["data"]["body"]["data"][0],
        run_node["data"]["body"]["data"][1],
        run_node["data"]["body"]["data"][2],
        run_node["data"]["body"]["data"][3],
        {"id": "key-value-5", "key": "models_json", "type": "text", "value": "{{#1900000000010.models_json_text#}}"},
        {"id": "key-value-6", "key": "cv_folds", "type": "text", "value": "{{#1900000000010.cv_folds_text#}}"},
        {"id": "key-value-7", "key": "optimization_metric", "type": "text", "value": "{{#1900000000010.optimization_metric_text#}}"},
        {"id": "key-value-8", "key": "n_iter", "type": "text", "value": "{{#1900000000010.n_iter_text#}}"},
        {"id": "key-value-9", "key": "use_gpu", "type": "text", "value": "{{#1900000000010.use_gpu_text#}}"},
        {"id": "key-value-10", "key": "drop_duplicates", "type": "text", "value": "{{#1900000000010.drop_duplicates_text#}}"},
        {"id": "key-value-11", "key": "idempotency_key", "type": "text", "value": "{{#sys.workflow_run_id#}}"},
    ]

    parse_suite = nodes["parse_experiment_response"]
    parse_suite["data"]["title"] = "parse_suite_response"
    parse_suite["data"]["code"] = _suite_parse_code()

    build_request = nodes["build_comparison_request"]
    build_request["data"]["title"] = "build_suite_comparison_request"
    build_request["data"]["code"] = _suite_request_code()
    build_request["data"]["outputs"] = {
        "suite_comparison_request_ok": {"children": None, "type": "boolean"},
        "suite_comparison_request_json": {"children": None, "type": "string"},
        "suite_comparison_request_errors": {"children": None, "type": "string"},
    }

    request_ok = nodes["comparison_request_ok?"]
    request_ok["data"]["cases"][0]["conditions"][0]["variable_selector"] = [
        build_request["id"],
        "suite_comparison_request_ok",
    ]

    compare_node = nodes["compare_result"]
    compare_node["data"]["title"] = "compare_model_suite_result"
    compare_node["data"]["url"] = "http://repro-runner:8001/v1/compare-model-suite-result"
    compare_node["data"]["body"]["data"][0]["value"] = "{{#1900000000014.suite_comparison_request_json#}}"

    parse_compare = nodes["parse_comparison_response"]
    parse_compare["data"]["title"] = "parse_suite_comparison_response"
    parse_compare["data"]["code"] = _suite_comparison_parse_code()

    formatter = nodes["format_comparison_report"]
    formatter["data"]["title"] = "format_suite_comparison_report"
    formatter["data"]["code"] = _suite_report_code()

    document["workflow"]["name"] = "paper-comparison-multimodel-workflow"
    return document


def write_multimodel_dsl(path: Path) -> None:
    content = yaml.safe_dump(
        build_multimodel_dsl(),
        allow_unicode=True,
        sort_keys=False,
        width=4096,
    )
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    write_multimodel_dsl(TARGET_DSL)
