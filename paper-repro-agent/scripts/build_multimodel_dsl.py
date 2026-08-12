from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DSL = PROJECT_ROOT / "dify" / "paper-comparison-workflow.yml"
TARGET_DSL = PROJECT_ROOT / "dify" / "paper-comparison-multimodel-workflow.yml"
PREPARE_DSL = PROJECT_ROOT / "dify" / "paper-comparison-prepare-workflow.yml"
DEFAULT_MODELS = [
    "logistic_regression",
    "random_forest",
    "xgboost",
    "lightgbm",
    "svm",
    "knn",
    "mlp",
]
PROTOCOL_CONFIRMATION_ID = "1900000000038"
POLL_CONFIRMED_JOB_ID = "1900000000039"
PROTOCOL_FAILURE_ID = "1900000000040"
PROTOCOL_OK_ID = "1900000000041"


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


def _embedded_experiment_helper_code(entrypoint: str) -> str:
    helper_path = PROJECT_ROOT / "dify" / "code" / "experiment_workflow.py"
    helper = helper_path.read_text(encoding="utf-8").rstrip()
    return helper + "\n\n" + entrypoint.strip() + "\n"


def _protocol_confirmation_code() -> str:
    return _embedded_experiment_helper_code("""import json


def main(
    protocol_token: str,
    confirm_protocol: bool,
    target_column: str,
    models_json_text: str,
    cv_folds_text: str,
    optimization_metric_text: str,
    test_size: float,
    random_state: int,
) -> dict:
    # The shared helper emits protocol_not_confirmed for the explicit false branch.
    return normalize_protocol_confirmation(
        protocol_token,
        confirm_protocol,
        confirmed_options={
            "target_column": target_column,
            "models_json": models_json_text,
            "cv_folds": cv_folds_text,
            "optimization_metric": optimization_metric_text,
            "test_size": test_size,
            "random_state": random_state,
        },
    )
""")


def _poll_confirmed_job_code() -> str:
    return _embedded_experiment_helper_code("""def main(manifest_json: str, training_csv) -> dict:

    # The helper submits to /v1/jobs and polls /result with bounded retries.
    return poll_job_until_terminal(
        "http://repro-runner:8001",
        manifest_json,
        training_csv=training_csv,
    )
""")


def _protocol_failure_code() -> str:
    return """import json


def main(dossier_json: str, validation_json: str, protocol_errors: str) -> dict:
    try:
        dossier = json.loads(dossier_json) if isinstance(dossier_json, str) else {}
    except (TypeError, json.JSONDecodeError):
        dossier = {}
    try:
        validation = json.loads(validation_json) if isinstance(validation_json, str) else {}
    except (TypeError, json.JSONDecodeError):
        validation = {}
    try:
        errors = json.loads(protocol_errors) if isinstance(protocol_errors, str) else []
    except (TypeError, json.JSONDecodeError):
        errors = []
    if not isinstance(dossier, dict):
        dossier = {}
    if not isinstance(validation, dict):
        validation = {}
    if not isinstance(errors, list):
        errors = []
    safe_errors = [
        item for item in errors
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    ] or [{"code": "protocol_not_confirmed", "message": "Protocol confirmation is required."}]
    experiment = {"status": "failed", "errors": safe_errors}
    comparison = {"items": [], "errors": [{"code": "comparison_not_run", "message": "Comparison did not run."}]}
    assessment = {"strict_status": "not_comparable", "approximate_status": "insufficient_metrics", "items": []}
    return {
        "dossier_json": json.dumps(dossier, ensure_ascii=False, separators=(",", ":")),
        "validation_json": json.dumps(validation, ensure_ascii=False, separators=(",", ":")),
        "experiment_json": json.dumps(experiment, ensure_ascii=False, separators=(",", ":")),
        "comparison_json": json.dumps(comparison, ensure_ascii=False, separators=(",", ":")),
        "assessment_json": json.dumps(assessment, ensure_ascii=False, separators=(",", ":")),
        "markdown_report": "Protocol confirmation is required before the asynchronous experiment can run.",
    }
"""


def _reordered_start_variables(start_node: dict) -> list[dict]:
    existing = {item["variable"]: deepcopy(item) for item in start_node["data"]["variables"]}
    protocol_token = {
        "default": "",
        "hint": "Short-lived token returned by the prepare workflow.",
        "label": "protocol_token",
        "max_length": 100000,
        "options": [],
        "placeholder": "",
        "required": False,
        "type": "paragraph",
        "variable": "protocol_token",
    }
    confirm_protocol = {
        "default": False,
        "hint": "Run only after reviewing and confirming the protocol preview.",
        "label": "confirm_protocol",
        "options": [],
        "placeholder": "",
        "required": False,
        "type": "checkbox",
        "variable": "confirm_protocol",
    }
    return [
        existing["paper_dossier_json"],
        existing["training_csv"],
        protocol_token,
        confirm_protocol,
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


def _clone_code_node(template: dict, node_id: str, title: str, code: str, outputs: dict, variables: list[dict], x: int, y: int) -> dict:
    node = deepcopy(template)
    node["id"] = node_id
    node["position"] = {"x": x, "y": y}
    node["positionAbsolute"] = {"x": x, "y": y}
    node["data"]["title"] = title
    node["data"]["code"] = code
    node["data"]["outputs"] = outputs
    node["data"]["variables"] = variables
    return node


def _clone_if_node(template: dict, node_id: str, title: str, variable_selector: list[str], x: int, y: int) -> dict:
    node = deepcopy(template)
    node["id"] = node_id
    node["position"] = {"x": x, "y": y}
    node["positionAbsolute"] = {"x": x, "y": y}
    node["data"]["title"] = title
    node["data"]["cases"] = [
        {
            "case_id": "true",
            "conditions": [
                {
                    "comparison_operator": "is",
                    "id": f"{node_id}-condition",
                    "value": True,
                    "varType": "boolean",
                    "variable_selector": variable_selector,
                }
            ],
            "id": "true",
            "logical_operator": "and",
        }
    ]
    return node


def _make_edge(document: dict, source: str, source_handle: str, target: str) -> dict:
    nodes = {node["id"]: node for node in _nodes(document)}
    source_type = nodes[source]["data"]["type"]
    target_type = nodes[target]["data"]["type"]
    return {
        "data": {
            "isInIteration": False,
            "isInLoop": False,
            "sourceType": source_type,
            "targetType": target_type,
        },
        "id": f"{source}-{source_handle}-{target}-target",
        "source": source,
        "sourceHandle": source_handle,
        "target": target,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
    }


def _add_protocol_path(document: dict, nodes: dict[str, dict]) -> None:
    start = nodes["Start"]
    normalize = nodes["normalize_experiment_inputs"]
    parse_suite = nodes["parse_experiment_response"]
    template = normalize
    confirmation = _clone_code_node(
        template,
        PROTOCOL_CONFIRMATION_ID,
        "normalize_protocol_confirmation",
        _protocol_confirmation_code(),
        {
            "manifest_json": {"children": None, "type": "string"},
            "protocol_errors": {"children": None, "type": "string"},
            "protocol_ok": {"children": None, "type": "boolean"},
        },
        [
            {"value_selector": [start["id"], "protocol_token"], "value_type": "string", "variable": "protocol_token"},
            {"value_selector": [start["id"], "confirm_protocol"], "value_type": "boolean", "variable": "confirm_protocol"},
            {"value_selector": [start["id"], "target_column"], "value_type": "string", "variable": "target_column"},
            {"value_selector": [normalize["id"], "models_json_text"], "value_type": "string", "variable": "models_json_text"},
            {"value_selector": [normalize["id"], "cv_folds_text"], "value_type": "string", "variable": "cv_folds_text"},
            {"value_selector": [normalize["id"], "optimization_metric_text"], "value_type": "string", "variable": "optimization_metric_text"},
            {"value_selector": [start["id"], "test_size"], "value_type": "number", "variable": "test_size"},
            {"value_selector": [start["id"], "random_state"], "value_type": "number", "variable": "random_state"},
        ],
        3210,
        -360,
    )
    poll = _clone_code_node(
        template,
        POLL_CONFIRMED_JOB_ID,
        "poll_confirmed_job",
        _poll_confirmed_job_code(),
        {
            "experiment_errors": {"children": None, "type": "string"},
            "experiment_json": {"children": None, "type": "string"},
            "experiment_ok": {"children": None, "type": "boolean"},
        },
        [
            {"value_selector": [confirmation["id"], "manifest_json"], "value_type": "string", "variable": "manifest_json"},
            {"value_selector": [start["id"], "training_csv"], "value_type": "file", "variable": "training_csv"},
        ],
        3540,
        -360,
    )
    protocol_ok = _clone_if_node(
        nodes["experiment_ok?"],
        PROTOCOL_OK_ID,
        "protocol_ok?",
        [confirmation["id"], "protocol_ok"],
        3375,
        -360,
    )
    failure = _clone_code_node(
        nodes["normalize_experiment_http_failure"],
        PROTOCOL_FAILURE_ID,
        "protocol_confirmation_failure",
        _protocol_failure_code(),
        {
            "dossier_json": {"children": None, "type": "string"},
            "validation_json": {"children": None, "type": "string"},
            "experiment_json": {"children": None, "type": "string"},
            "comparison_json": {"children": None, "type": "string"},
            "assessment_json": {"children": None, "type": "string"},
            "markdown_report": {"children": None, "type": "string"},
        },
        [
            {"value_selector": [nodes["parse_dossier_response"]["id"], "dossier_json"], "value_type": "string", "variable": "dossier_json"},
            {"value_selector": [nodes["parse_validation_response"]["id"], "validation_json"], "value_type": "string", "variable": "validation_json"},
            {"value_selector": [confirmation["id"], "protocol_errors"], "value_type": "string", "variable": "protocol_errors"},
        ],
        3705,
        -360,
    )
    document["workflow"]["graph"]["nodes"].extend([confirmation, protocol_ok, poll, failure])

    parse_suite["data"]["variables"] = [
        {"value_selector": [poll["id"], "experiment_json"], "value_type": "string", "variable": "body"}
    ]
    for edge in document["workflow"]["graph"]["edges"]:
        if edge["source"] == nodes["run_experiment"]["id"] or edge["target"] == nodes["run_experiment"]["id"]:
            edge["_remove"] = True
    document["workflow"]["graph"]["edges"] = [
        edge for edge in document["workflow"]["graph"]["edges"] if not edge.pop("_remove", False)
    ]
    edges = document["workflow"]["graph"]["edges"]
    edges.extend(
        [
            _make_edge(document, normalize["id"], "source", confirmation["id"]),
            _make_edge(document, confirmation["id"], "source", protocol_ok["id"]),
            _make_edge(document, protocol_ok["id"], "true", poll["id"]),
            _make_edge(document, protocol_ok["id"], "false", failure["id"]),
            _make_edge(document, poll["id"], "source", parse_suite["id"]),
            _make_edge(document, failure["id"], "source", "1900000000031"),
        ]
    )
    for title in (
        "normalize_experiment_http_failure",
        "aggregate_dossier_json",
        "aggregate_validation_json",
        "aggregate_experiment_json",
        "aggregate_comparison_json",
        "aggregate_assessment_json",
        "aggregate_markdown_report",
    ):
        if title == "normalize_experiment_http_failure":
            node = nodes[title]
            node["data"]["variables"] = [
                {"value_selector": [poll["id"], "experiment_json"], "value_type": "string", "variable": "experiment_json"},
            ]
            continue
        node = nodes[title]
        variable_name = {
            "aggregate_dossier_json": "dossier_json",
            "aggregate_validation_json": "validation_json",
            "aggregate_experiment_json": "experiment_json",
            "aggregate_comparison_json": "comparison_json",
            "aggregate_assessment_json": "assessment_json",
            "aggregate_markdown_report": "markdown_report",
        }[title]
        node["data"]["variables"].append([failure["id"], variable_name])


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

    _add_protocol_path(document, nodes)
    document["workflow"]["name"] = "paper-comparison-multimodel-workflow"
    return document


def _prepare_code() -> str:
    return _embedded_experiment_helper_code("""def main(dossier_response_json: str, diagnosis_response_json: str, target_column: str, protocol_notes: str) -> dict:
    return prepare_protocol_artifacts(
        dossier_response_json,
        diagnosis_response_json,
        target_column=target_column,
        protocol_notes=protocol_notes,
    )
""")


def build_prepare_dsl() -> dict:
    source = _load_source()
    source_nodes = _by_title(source)
    start_id = "2900000000001"
    dossier_id = "2900000000002"
    diagnosis_id = "2900000000003"
    prepare_id = "2900000000004"
    output_id = "2900000000005"

    start = deepcopy(source_nodes["Start"])
    start["id"] = start_id
    start["position"] = {"x": 100, "y": 300}
    start["positionAbsolute"] = {"x": 100, "y": 300}
    start["data"]["variables"] = [
        {
            "default": "",
            "hint": "Upload the paper PDF for dossier parsing.",
            "label": "paper_pdf",
            "max_length": 0,
            "options": [],
            "placeholder": "",
            "required": True,
            "type": "file",
            "variable": "paper_pdf",
        },
        {
            "default": "",
            "hint": "Upload a UTF-8 tabular binary-classification CSV.",
            "label": "training_csv",
            "max_length": 0,
            "options": [],
            "placeholder": "",
            "required": True,
            "type": "file",
            "variable": "training_csv",
        },
        {
            "default": "",
            "hint": "Optional protocol notes; only a digest is retained in the token.",
            "label": "protocol_notes",
            "max_length": 512,
            "options": [],
            "placeholder": "",
            "required": False,
            "type": "paragraph",
            "variable": "protocol_notes",
        },
        {
            "default": "",
            "hint": "Optional target-column suggestion; confirm it in the preview.",
            "label": "target_column",
            "max_length": 128,
            "options": [],
            "placeholder": "Y_cls",
            "required": False,
            "type": "text-input",
            "variable": "target_column",
        },
    ]

    dossier = deepcopy(source_nodes["parse_dossier"])
    dossier["id"] = dossier_id
    dossier["position"] = {"x": 600, "y": 200}
    dossier["positionAbsolute"] = {"x": 600, "y": 200}
    dossier["data"]["body"]["data"] = [
        {"file": [start_id, "paper_pdf"], "id": "key-value-1", "key": "file", "type": "file", "value": ""},
        {"id": "key-value-2", "key": "metric_overrides_json", "type": "text", "value": "[]"},
    ]

    diagnosis = deepcopy(source_nodes["validate_dataset"])
    diagnosis["id"] = diagnosis_id
    diagnosis["position"] = {"x": 600, "y": 420}
    diagnosis["positionAbsolute"] = {"x": 600, "y": 420}
    diagnosis["data"]["title"] = "diagnose_dataset"
    diagnosis["data"]["url"] = "http://repro-runner:8001/v1/diagnose-dataset"
    diagnosis["data"]["body"]["data"] = [
        {"file": [start_id, "training_csv"], "id": "key-value-1", "key": "file", "type": "file", "value": ""},
        {"id": "key-value-2", "key": "target_column", "type": "text", "value": f"{{{{#{start_id}.target_column#}}}}"},
    ]

    prepare = _clone_code_node(
        source_nodes["normalize_experiment_inputs"],
        prepare_id,
        "prepare_protocol_artifacts",
        _prepare_code(),
        {
            "protocol_preview_json": {"children": None, "type": "string"},
            "protocol_token": {"children": None, "type": "string"},
        },
        [
            {"value_selector": [dossier_id, "body"], "value_type": "string", "variable": "dossier_response_json"},
            {"value_selector": [diagnosis_id, "body"], "value_type": "string", "variable": "diagnosis_response_json"},
            {"value_selector": [start_id, "target_column"], "value_type": "string", "variable": "target_column"},
            {"value_selector": [start_id, "protocol_notes"], "value_type": "string", "variable": "protocol_notes"},
        ],
        1100,
        300,
    )
    output = deepcopy(source_nodes["Output"])
    output["id"] = output_id
    output["position"] = {"x": 1500, "y": 300}
    output["positionAbsolute"] = {"x": 1500, "y": 300}
    output["data"]["outputs"] = [
        {"value_selector": [prepare_id, "protocol_preview_json"], "value_type": "string", "variable": "protocol_preview_json"},
        {"value_selector": [prepare_id, "protocol_token"], "value_type": "string", "variable": "protocol_token"},
    ]

    document = {
        "app": {
            "description": "Prepare a safe protocol preview before confirmed asynchronous training.",
            "icon": "🧪",
            "icon_background": "#E4FBCC",
            "icon_type": "emoji",
            "mode": "workflow",
            "name": "paper-comparison-prepare-workflow",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [],
        "kind": source.get("kind", "app"),
        "version": source.get("version", "0.7.0"),
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [],
            "features": deepcopy(source["workflow"]["features"]),
            "graph": {
                "edges": [],
                "nodes": [start, dossier, diagnosis, prepare, output],
                "viewport": {"x": 0, "y": 0, "zoom": 0.8},
            },
            "rag_pipeline_variables": [],
            "name": "paper-comparison-prepare-workflow",
        },
    }
    document["workflow"]["graph"]["edges"] = [
        _make_edge(document, start_id, "source", dossier_id),
        _make_edge(document, dossier_id, "source", diagnosis_id),
        _make_edge(document, diagnosis_id, "source", prepare_id),
        _make_edge(document, prepare_id, "source", output_id),
    ]
    return document


def write_multimodel_dsl(path: Path) -> None:
    content = yaml.safe_dump(
        build_multimodel_dsl(),
        allow_unicode=True,
        sort_keys=False,
        width=4096,
    )
    path.write_text(content, encoding="utf-8")


def write_prepare_dsl(path: Path) -> None:
    content = yaml.safe_dump(
        build_prepare_dsl(),
        allow_unicode=True,
        sort_keys=False,
        width=4096,
    )
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    write_multimodel_dsl(TARGET_DSL)
    write_prepare_dsl(PREPARE_DSL)
