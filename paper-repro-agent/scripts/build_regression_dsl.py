from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_multimodel_dsl import (
    DEFAULT_LLM_PROFILE,
    LLM_PROFILES,
    _secret_safe_embedded_experiment_helper_code,
    build_merged_dsl,
    resolve_llm_profile,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET_DSL = PROJECT_ROOT / "dify" / "paper-comparison-regression-workflow.yml"
REGRESSION_MODELS = (
    "linear_regression",
    "random_forest",
    "gradient_boosting",
    "xgboost",
)
REGRESSION_METRICS = ("mae", "rmse", "r2")
WORKFLOW_VERSION = "regression-1.0.0"


def _nodes(document: dict) -> list[dict]:
    return document["workflow"]["graph"]["nodes"]


def _by_title(document: dict) -> dict[str, dict]:
    return {node["data"]["title"]: node for node in _nodes(document)}


def _task_form_field() -> dict:
    return {
        "id": "key-value-task-type",
        "key": "task_type",
        "type": "text",
        "value": "regression",
    }


def _set_explicit_regression_inputs(document: dict) -> None:
    nodes = _by_title(document)
    start = nodes["Start"]
    variables = start["data"]["variables"]
    task_input = {
        "default": "regression",
        "hint": "This isolated candidate always executes continuous-target regression.",
        "label": "task_type",
        "options": ["regression"],
        "placeholder": "",
        "required": True,
        "type": "select",
        "variable": "task_type",
    }
    variables[:] = [item for item in variables if item.get("variable") != "task_type"]
    variables.insert(1, task_input)
    by_name = {item["variable"]: item for item in variables}
    by_name["training_csv"]["hint"] = "Upload a UTF-8 tabular regression CSV. Raw rows are never returned."
    by_name["target_column"].update(
        {
            "default": "target",
            "hint": "Continuous numeric response column confirmed in the frozen protocol.",
            "placeholder": "target",
        }
    )
    by_name["models_json"].update(
        {
            "default": json.dumps(list(REGRESSION_MODELS)),
            "hint": "Regression-only model allowlist.",
        }
    )
    by_name["optimization_metric"].update(
        {
            "default": "rmse",
            "hint": "One of mae, rmse, or r2.",
        }
    )
    for title in ("diagnose_dataset", "validate_dataset"):
        fields = nodes[title]["data"]["body"]["data"]
        fields[:] = [item for item in fields if item.get("key") != "task_type"]
        fields.append(_task_form_field())


def _normalize_code() -> str:
    models = repr(REGRESSION_MODELS)
    metrics = repr(REGRESSION_METRICS)
    return f'''import json

KNOWN_MODELS = {models}
KNOWN_METRICS = {metrics}

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
        return "rmse"
    metric = value.strip()
    return metric if metric in KNOWN_METRICS else "rmse"

def safe_bool_text(value):
    if value is True or (isinstance(value, str) and value.strip().casefold() == "true"):
        return "true"
    if value is False or (isinstance(value, str) and value.strip().casefold() == "false"):
        return "false"
    return "false"

def main(models_json, cv_folds, optimization_metric, n_iter, use_gpu, drop_duplicates):
    return {{
        "models_json_text": safe_models_json(models_json),
        "cv_folds_text": safe_int_text(cv_folds, 5, 3, 10),
        "optimization_metric_text": safe_metric_text(optimization_metric),
        "n_iter_text": safe_int_text(n_iter, 8, 1, 32),
        "use_gpu_text": safe_bool_text(use_gpu),
        "drop_duplicates_text": safe_bool_text(drop_duplicates),
        "task_type_text": "regression",
    }}
'''


def _rewrite_regression_helper_constants(code: str) -> str:
    old_models = '''_SUITE_MODEL_NAMES = (
    "logistic_regression",
    "random_forest",
    "xgboost",
    "lightgbm",
    "svm",
    "knn",
    "mlp",
)'''
    new_models = "_SUITE_MODEL_NAMES = " + repr(REGRESSION_MODELS)
    code = code.replace(old_models, new_models)
    code = code.replace(
        '_SUITE_WORKFLOW_VERSION = "multimodel-0.8.0"',
        f'_SUITE_WORKFLOW_VERSION = "{WORKFLOW_VERSION}"',
    )
    code = code.replace(
        '_SUITE_METRICS = {"roc_auc", "f1", "recall", "balanced_accuracy"}',
        "_SUITE_METRICS = " + repr(REGRESSION_METRICS),
    )
    return code


def _regression_experiment_helper_code(entrypoint: str) -> str:
    return _rewrite_regression_helper_constants(
        _secret_safe_embedded_experiment_helper_code(entrypoint)
    )


def _regression_protocol_overrides() -> str:
    return f'''_BASE_MANIFEST_DRAFT = _manifest_draft

def _manifest_draft(diagnosis, target_column, protocol_notes):
    draft, dataset, notes = _BASE_MANIFEST_DRAFT(diagnosis, target_column, protocol_notes)
    recommended = diagnosis.get("recommended_options") if isinstance(diagnosis.get("recommended_options"), dict) else {{}}
    draft["task_type"] = "regression"
    draft["sampling_strategy"] = "original"
    draft["optimization_metric"] = recommended.get("optimization_metric") if recommended.get("optimization_metric") in _SUITE_METRICS else "rmse"
    draft["threshold"] = None
    draft["models"] = list(_SUITE_MODEL_NAMES)
    draft["workflow_version"] = "{WORKFLOW_VERSION}"
    canonical = dict(draft)
    canonical["manifest_id"] = ""
    draft["manifest_id"] = "sha256:" + hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
    return draft, dataset, notes

def _validate_manifest(manifest):
    if not isinstance(manifest, dict):
        return ["protocol_payload_invalid"]
    errors = []
    if _safe_sha(manifest.get("manifest_id")) is None:
        errors.append("protocol_payload_invalid")
    if _safe_sha(manifest.get("dataset_id")) is None:
        errors.append("protocol_dataset_missing")
    target = _safe_text(manifest.get("target_column"), 128)
    raw_features = manifest.get("feature_columns")
    features = _safe_list(raw_features)
    if target is None:
        errors.append("protocol_target_missing")
    if not isinstance(raw_features, list) or not features or target in features or len(features) != len(raw_features):
        errors.append("protocol_features_missing")
    if manifest.get("task_type") != "regression":
        errors.append("protocol_options_invalid")
    if manifest.get("missing_policy") not in {{"reject", "drop_rows", "impute"}}:
        errors.append("protocol_options_invalid")
    if manifest.get("sampling_strategy") != "original":
        errors.append("protocol_options_invalid")
    if manifest.get("comparison_mode") not in {{"paper_comparable", "real_world"}}:
        errors.append("protocol_options_invalid")
    if _safe_number(manifest.get("test_size"), 0.1, 0.5) is None:
        errors.append("protocol_options_invalid")
    if _safe_int(manifest.get("random_state"), 0) is None:
        errors.append("protocol_options_invalid")
    if _safe_int(manifest.get("cv_folds"), 3, 10) is None:
        errors.append("protocol_options_invalid")
    if manifest.get("optimization_metric") not in _SUITE_METRICS:
        errors.append("protocol_options_invalid")
    if manifest.get("threshold") is not None:
        errors.append("protocol_options_invalid")
    models = manifest.get("models")
    if not isinstance(models, list) or not models or any(model not in _SUITE_MODEL_NAMES for model in models) or len(models) != len(set(models)):
        errors.append("protocol_options_invalid")
    if manifest.get("workflow_version") != "{WORKFLOW_VERSION}":
        errors.append("protocol_options_invalid")
    return list(dict.fromkeys(errors))
'''


def _prepare_protocol_code() -> str:
    return _regression_experiment_helper_code(
        _regression_protocol_overrides()
        + '''

def _private_preview(value):
    try:
        preview = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        preview = {}
    if not isinstance(preview, dict):
        preview = {}
    manifest = preview.get("manifest_draft") if isinstance(preview.get("manifest_draft"), dict) else {}
    dataset = preview.get("dataset") if isinstance(preview.get("dataset"), dict) else {}
    safe_dataset = {}
    for key in ("rows", "effective_rows", "features", "missing_values", "duplicate_rows"):
        number = _safe_int(dataset.get(key), 0, 1_000_000_000)
        if number is not None:
            safe_dataset[key] = number
    dataset_id = _safe_sha(dataset.get("dataset_id"))
    if dataset_id is not None:
        safe_dataset["dataset_id"] = dataset_id
    return {
        "protocol_version": preview.get("protocol_version"),
        "expires_in_seconds": preview.get("expires_in_seconds"),
        "protocol_notes_digest": preview.get("protocol_notes_digest"),
        "dataset": safe_dataset,
        "paper_summary": {"task_type": "regression"},
        "manifest_draft": manifest,
        "unresolved_protocol_fields": [
            item for item in preview.get("unresolved_protocol_fields", [])
            if isinstance(item, str) and item in _SAFE_ERROR_CODES
        ],
    }

def main(dossier_response_json: str, diagnosis_response_json: str, target_column: str, protocol_notes: str, protocol_secret: str = "") -> dict:
    result = prepare_protocol_artifacts(
        dossier_response_json,
        diagnosis_response_json,
        target_column=target_column,
        protocol_notes=protocol_notes,
        secret=protocol_secret,
    )
    result["protocol_preview_json"] = _json(_private_preview(result.get("protocol_preview_json")))
    return result
'''
    )


def _confirmation_code() -> str:
    return _regression_experiment_helper_code(
        _regression_protocol_overrides()
        + '''

def main(
    protocol_token: str,
    confirm_protocol: bool,
    target_column: str,
    models_json_text: str,
    cv_folds_text: str,
    optimization_metric_text: str,
    test_size: float,
    random_state: int,
    protocol_secret: str = "",
) -> dict:
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
        secret=protocol_secret,
    )
'''
    )


def _suite_parse_code() -> str:
    return '''import json

SAFE_ERROR = {"code": "invalid_regression_suite_response", "message": "Regression suite response is invalid."}

def main(body: str) -> dict:
    try:
        experiment = json.loads(body) if isinstance(body, str) else body
    except (TypeError, json.JSONDecodeError):
        experiment = {}
    config = experiment.get("config") if isinstance(experiment, dict) and isinstance(experiment.get("config"), dict) else {}
    ok = (
        isinstance(experiment, dict)
        and isinstance(experiment.get("experiment_id"), str)
        and bool(experiment.get("experiment_id", "").strip())
        and experiment.get("status") in {"succeeded", "partial"}
        and experiment.get("task_type") == "regression"
        and config.get("task_type") == "regression"
        and isinstance(experiment.get("results"), list)
    )
    normalized = experiment if ok else {"status": "failed", "task_type": "regression", "errors": [SAFE_ERROR]}
    return {
        "experiment_ok": ok,
        "experiment_json": json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
        "experiment_errors": json.dumps([] if ok else [SAFE_ERROR], ensure_ascii=False, separators=(",", ":")),
    }
'''


def _comparison_request_code() -> str:
    models = repr(REGRESSION_MODELS)
    return f'''import json
import math
import re
import unicodedata

KNOWN_MODELS = {models}
ALIASES = {{
    "mae": "mae",
    "mean_absolute_error": "mae",
    "mean_absolute_deviation": "mae",
    "rmse": "rmse",
    "root_mean_squared_error": "rmse",
    "root_mean_square_error": "rmse",
    "r2": "r2",
    "r_squared": "r2",
    "coefficient_of_determination": "r2",
}}
EXPERIMENT_ID_RE = re.compile(r"^exp-[A-Za-z0-9][A-Za-z0-9-]{{0,127}}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{{64}}$")

def object_or_empty(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {{}}
    return parsed if isinstance(parsed, dict) else {{}}

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
    display = unicodedata.normalize("NFKC", candidate.strip())
    if "(" in display:
        display = display.split("(", 1)[0].strip()
    normalized = "_".join(display.casefold().replace("-", " ").replace("_", " ").split())
    return ALIASES.get(normalized)

def safe_reported(metric, value):
    parsed = safe_number(value)
    if parsed is None:
        return None
    if metric in {{"mae", "rmse"}} and parsed < 0:
        return None
    if metric == "r2" and parsed > 1:
        return None
    return parsed

def safe_qualifier(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 32 or any(ord(char) < 32 for char in candidate):
        return None
    allowed = " ._()/-（）"
    return candidate if all(char.isalnum() or char in allowed for char in candidate) else None

def safe_digest(value):
    return value if isinstance(value, str) and SHA256_RE.fullmatch(value.strip()) else None

def safe_fraction(value):
    parsed = safe_number(value)
    return parsed if parsed is not None and 0 < parsed < 1 else None

def safe_int(value, minimum, maximum):
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if str(parsed) == str(value).strip() and minimum <= parsed <= maximum else None

def main(dossier_json: str, experiment_json: str) -> dict:
    dossier = object_or_empty(dossier_json)
    suite = object_or_empty(experiment_json)
    reported = []
    if dossier.get("task_type") == "regression" and suite.get("task_type") == "regression":
        metrics = dossier.get("metrics") if isinstance(dossier.get("metrics"), list) else []
        for metric in metrics:
            if not isinstance(metric, dict) or metric.get("supported") is not True or metric.get("ambiguous") is True:
                continue
            name = metric_name(metric)
            value = safe_reported(name, metric.get("reported_value")) if name else None
            if name is None or value is None:
                continue
            item = {{"name": name, "reported_value": value}}
            model = metric.get("model")
            if model in KNOWN_MODELS:
                item["model"] = model
            for key, validator, raw in (
                ("dataset", safe_qualifier, metric.get("dataset")),
                ("split", safe_qualifier, metric.get("split")),
                ("dataset_id", safe_digest, metric.get("dataset_id")),
                ("test_size", safe_fraction, metric.get("test_size")),
                ("random_state", lambda item: safe_int(item, 0, 2147483647), metric.get("random_state")),
                ("train_rows", lambda item: safe_int(item, 1, 1000000000), metric.get("train_rows")),
                ("test_rows", lambda item: safe_int(item, 1, 1000000000), metric.get("test_rows")),
                ("test_digest", safe_digest, metric.get("test_digest")),
            ):
                safe = validator(raw)
                if safe is not None:
                    item[key] = safe
            reported.append(item)
    experiment_id = suite.get("experiment_id")
    ok = isinstance(experiment_id, str) and EXPERIMENT_ID_RE.fullmatch(experiment_id) is not None and bool(reported)
    request = {{"experiment_id": experiment_id, "reported_metrics": reported}} if ok else {{}}
    errors = [] if ok else [{{"code": "invalid_regression_comparison_request", "message": "Regression experiment and supported metrics are required."}}]
    return {{
        "suite_comparison_request_ok": ok,
        "suite_comparison_request_json": json.dumps(request, ensure_ascii=False, separators=(",", ":")),
        "suite_comparison_request_errors": json.dumps(errors, ensure_ascii=False, separators=(",", ":")),
    }}
'''


def _comparison_parse_code() -> str:
    models = repr(REGRESSION_MODELS)
    metrics = repr(REGRESSION_METRICS)
    return f'''import json
import math
import re

KNOWN_MODELS = {models}
KNOWN_METRICS = {metrics}
EXPERIMENT_ID_RE = re.compile(r"^exp-[A-Za-z0-9][A-Za-z0-9-]{{0,127}}$")

def number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None

def main(comparison_response_json: str) -> dict:
    try:
        raw = json.loads(comparison_response_json) if isinstance(comparison_response_json, str) else comparison_response_json
    except (TypeError, json.JSONDecodeError):
        raw = {{}}
    experiment_id = raw.get("experiment_id") if isinstance(raw, dict) else None
    raw_items = raw.get("items") if isinstance(raw, dict) else None
    ok = isinstance(experiment_id, str) and EXPERIMENT_ID_RE.fullmatch(experiment_id) is not None and isinstance(raw_items, list)
    safe = {{"experiment_id": experiment_id, "items": [], "paper_closeness_ranking": []}} if ok else {{}}
    if ok:
        for raw_item in raw_items:
            if not isinstance(raw_item, dict) or raw_item.get("model") not in KNOWN_MODELS or raw_item.get("name") not in KNOWN_METRICS:
                continue
            item = {{"model": raw_item["model"], "name": raw_item["name"], "comparable": raw_item.get("comparable") is True}}
            for key in ("paper_value", "independent_value", "absolute_difference", "relative_difference"):
                value = number(raw_item.get(key))
                if value is not None:
                    item[key] = value
            if isinstance(raw_item.get("reason"), str):
                item["reason"] = "details redacted for privacy."
            safe["items"].append(item)
        for model in raw.get("paper_closeness_ranking", []):
            if model in KNOWN_MODELS and model not in safe["paper_closeness_ranking"]:
                safe["paper_closeness_ranking"].append(model)
    errors = [] if ok else [{{"code": "invalid_regression_comparison_response", "message": "Regression comparison response is invalid."}}]
    return {{
        "comparison_ok": ok,
        "comparison_json": json.dumps(safe, ensure_ascii=False, separators=(",", ":")) if ok else "{{}}",
        "comparison_errors": json.dumps(errors, ensure_ascii=False, separators=(",", ":")),
    }}
'''


def _report_code() -> str:
    models = repr(REGRESSION_MODELS)
    metrics = repr(REGRESSION_METRICS)
    return f'''import json
import math
import re

KNOWN_MODELS = {models}
KNOWN_METRICS = {metrics}
SAFE_STATUSES = {{"queued", "running", "succeeded", "partial", "failed", "cancelled", "needs_retry"}}
SAFE_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{{0,63}}$")
SAFE_ID_RE = re.compile(r"^(?:exp|job)-[A-Za-z0-9][A-Za-z0-9-]{{0,127}}$")
SHA_RE = re.compile(r"^sha256:[0-9a-f]{{64}}$")

def object_or_empty(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {{}}
    return parsed if isinstance(parsed, dict) else {{}}

def number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None

def safe_id(value):
    return value if isinstance(value, str) and SAFE_ID_RE.fullmatch(value) else None

def safe_ranking(value):
    ranking = []
    for model in value if isinstance(value, list) else []:
        if model in KNOWN_MODELS and model not in ranking:
            ranking.append(model)
    return ranking

def safe_metric_values(value):
    metrics = {{}}
    if not isinstance(value, dict):
        return metrics
    for name in KNOWN_METRICS:
        parsed = number(value.get(name))
        if parsed is not None and (name == "r2" or parsed >= 0) and (name != "r2" or parsed <= 1):
            metrics[name] = parsed
    return metrics

def safe_dossier(raw):
    metrics = []
    for item in raw.get("metrics", []) if isinstance(raw.get("metrics"), list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("normalized_name") or item.get("name")
        if isinstance(name, str):
            name = name.casefold().replace("²", "2")
        if name not in KNOWN_METRICS:
            continue
        value = number(item.get("reported_value"))
        safe = {{"name": name}}
        if value is not None:
            safe["reported_value"] = value
        if item.get("model") in KNOWN_MODELS:
            safe["model"] = item["model"]
        metrics.append(safe)
    return {{"task_type": "regression", "metrics": metrics}}

def safe_validation(raw):
    dataset = raw.get("dataset") if isinstance(raw.get("dataset"), dict) else {{}}
    safe_dataset = {{}}
    for key in ("rows", "effective_rows", "features", "missing_values", "duplicate_rows"):
        value = number(dataset.get(key))
        if value is not None and value >= 0:
            safe_dataset[key] = int(value) if value.is_integer() else value
    if isinstance(dataset.get("dataset_id"), str) and SHA_RE.fullmatch(dataset["dataset_id"]):
        safe_dataset["dataset_id"] = dataset["dataset_id"]
    return {{"valid": raw.get("valid") is True, "task_type": "regression", "dataset": safe_dataset}}

def safe_suite(raw):
    config = raw.get("config") if isinstance(raw.get("config"), dict) else {{}}
    safe_config = {{"task_type": "regression"}}
    if config.get("optimization_metric") in KNOWN_METRICS:
        safe_config["optimization_metric"] = config["optimization_metric"]
    for key in ("cv_folds", "n_iter", "n_seeds", "random_state", "test_size"):
        value = number(config.get(key))
        if value is not None:
            safe_config[key] = int(value) if value.is_integer() else value
    results = []
    for item in raw.get("results", []) if isinstance(raw.get("results"), list) else []:
        if not isinstance(item, dict) or item.get("model") not in KNOWN_MODELS:
            continue
        result = {{
            "model": item["model"],
            "status": item.get("status") if item.get("status") in SAFE_STATUSES else "failed",
            "metrics": safe_metric_values(item.get("metrics")),
        }}
        if isinstance(item.get("error"), dict):
            code = item["error"].get("code")
            result["error"] = {{
                "code": code if isinstance(code, str) and SAFE_ERROR_CODE_RE.fullmatch(code) else "unavailable",
                "message": "bounded_failure" if item["error"].get("message") == "bounded_failure" else "details redacted for privacy.",
            }}
        results.append(result)
    safe = {{
        "task_type": "regression",
        "config": safe_config,
        "performance_ranking": safe_ranking(raw.get("performance_ranking")),
        "results": results,
    }}
    for key in ("experiment_id", "job_id"):
        value = safe_id(raw.get(key))
        if value is not None:
            safe[key] = value
    status = raw.get("job_status") if raw.get("job_status") in SAFE_STATUSES else raw.get("status")
    safe["status"] = status if status in SAFE_STATUSES else "failed"
    return safe

def safe_comparison(raw):
    items = []
    for item in raw.get("items", []) if isinstance(raw.get("items"), list) else []:
        if not isinstance(item, dict) or item.get("model") not in KNOWN_MODELS or item.get("name") not in KNOWN_METRICS:
            continue
        safe = {{"model": item["model"], "name": item["name"], "comparable": item.get("comparable") is True}}
        for key in ("paper_value", "independent_value", "absolute_difference", "relative_difference"):
            value = number(item.get(key))
            if value is not None:
                safe[key] = value
        if isinstance(item.get("reason"), str):
            safe["reason"] = "details redacted for privacy."
        items.append(safe)
    return {{
        "experiment_id": safe_id(raw.get("experiment_id")),
        "paper_closeness_ranking": safe_ranking(raw.get("paper_closeness_ranking")),
        "items": items,
    }}

def safe_assessment(raw):
    safe = {{}}
    if raw.get("strict_status") in {{"not_comparable", "strictly_comparable", "partially_comparable"}}:
        safe["strict_status"] = raw["strict_status"]
    if raw.get("approximate_status") in {{"insufficient_metrics", "highly_similar", "partially_similar", "materially_different"}}:
        safe["approximate_status"] = raw["approximate_status"]
    safe["items"] = []
    for item in raw.get("items", []) if isinstance(raw.get("items"), list) else []:
        if not isinstance(item, dict) or item.get("model") not in KNOWN_MODELS or item.get("name") not in KNOWN_METRICS:
            continue
        safe["items"].append({{"model": item["model"], "name": item["name"], "grade": item.get("grade") if item.get("grade") in {{"highly_similar", "partially_similar", "materially_different"}} else "unavailable"}})
    return safe

def format_number(value):
    parsed = number(value)
    return "unavailable" if parsed is None else "{{0:.3f}}".format(parsed)

def main(dossier_json: str, validation_json: str, experiment_json: str, comparison_json: str, assessment_json: str) -> dict:
    dossier = safe_dossier(object_or_empty(dossier_json))
    validation = safe_validation(object_or_empty(validation_json))
    suite = safe_suite(object_or_empty(experiment_json))
    comparison = safe_comparison(object_or_empty(comparison_json))
    assessment = safe_assessment(object_or_empty(assessment_json))
    performance = suite["performance_ranking"]
    closeness = comparison["paper_closeness_ranking"]
    lines = [
        "# Regression comparison report",
        "",
        "task type: regression",
        "suite experiment id: " + str(suite.get("experiment_id") or "unavailable"),
        "job id: " + str(suite.get("job_id") or "unavailable"),
        "status: " + str(suite.get("status") or "unavailable"),
        "metric direction: lower MAE/RMSE is better; higher R² is better",
    ]
    if performance:
        lines.append("performance ranking: " + " > ".join(performance))
    if closeness:
        lines.append("paper-closeness ranking: " + " > ".join(closeness))
    lines.extend(["", "## model summaries"])
    for item in suite["results"]:
        values = item.get("metrics", {{}})
        lines.extend([
            "### " + item["model"],
            "- status: " + item["status"],
            "- MAE=" + format_number(values.get("mae")) + ", RMSE=" + format_number(values.get("rmse")) + ", R²=" + format_number(values.get("r2")),
        ])
        if isinstance(item.get("error"), dict):
            lines.append("- safe_error=" + item["error"]["code"] + ": " + item["error"]["message"])
    if not suite["results"]:
        lines.append("- no regression suite results were available.")
    if performance or closeness:
        lines.extend(["", "## rankings"])
    if performance:
        lines.append("- performance ranking: " + " > ".join(performance))
    if closeness:
        lines.append("- paper-closeness ranking: " + " > ".join(closeness))
    return {{
        "dossier_json": json.dumps(dossier, ensure_ascii=False, separators=(",", ":")),
        "validation_json": json.dumps(validation, ensure_ascii=False, separators=(",", ":")),
        "experiment_json": json.dumps(suite, ensure_ascii=False, separators=(",", ":")),
        "comparison_json": json.dumps(comparison, ensure_ascii=False, separators=(",", ":")),
        "assessment_json": json.dumps(assessment, ensure_ascii=False, separators=(",", ":")),
        "markdown_report": "\\n".join(lines),
    }}
'''


def _replace_regression_code_nodes(document: dict) -> None:
    nodes = _by_title(document)
    normalize = nodes["normalize_suite_inputs"]
    normalize["data"]["code"] = _normalize_code()
    normalize["data"]["outputs"]["task_type_text"] = {"children": None, "type": "string"}
    nodes["prepare_protocol_artifacts"]["data"]["code"] = _prepare_protocol_code()
    nodes["normalize_protocol_confirmation"]["data"]["code"] = _confirmation_code()
    nodes["parse_suite_response"]["data"]["code"] = _suite_parse_code()
    nodes["build_suite_comparison_request"]["data"]["code"] = _comparison_request_code()
    nodes["parse_suite_comparison_response"]["data"]["code"] = _comparison_parse_code()
    nodes["format_suite_comparison_report"]["data"]["code"] = _report_code()
    for node in _nodes(document):
        data = node.get("data", {})
        if data.get("type") == "code" and "logistic_regression" in data.get("code", ""):
            data["code"] = _rewrite_regression_helper_constants(data["code"])


def _set_regression_metadata(document: dict, profile: str) -> None:
    resolved = resolve_llm_profile(profile)
    suffix = resolved.suffix
    document["app"].update(
        {
            "description": "Isolated continuous-target regression candidate with MAE, RMSE, and R² reporting.",
            "name": f"Paper comparison Regression Candidate{suffix}",
        }
    )
    document["workflow"]["name"] = f"paper-comparison-regression-workflow{suffix}"
    document["candidate_metadata"] = {
        "task_type": "regression",
        "workflow_version": WORKFLOW_VERSION,
    }


def _validate_regression_graph(document: dict) -> None:
    serialized = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    if "logistic_regression" in serialized:
        raise ValueError("classification model leaked into regression candidate")
    for model in REGRESSION_MODELS:
        if model not in serialized:
            raise ValueError(f"regression model is missing: {model}")
    for metric in REGRESSION_METRICS:
        if metric not in serialized:
            raise ValueError(f"regression metric is missing: {metric}")
    nodes = _by_title(document)
    for title in ("diagnose_dataset", "validate_dataset"):
        fields = {item.get("key"): item for item in nodes[title]["data"]["body"]["data"]}
        if fields.get("task_type", {}).get("value") != "regression":
            raise ValueError(f"{title} does not force regression")
    for node in _nodes(document):
        data = node.get("data", {})
        if data.get("type") == "code":
            compile(data["code"], f"<regression-dify:{data.get('title')}>", "exec")


def build_regression_dsl(profile: str = DEFAULT_LLM_PROFILE) -> dict:
    """Return a deterministic regression-only clone without writing files."""

    document = deepcopy(build_merged_dsl(profile))
    _set_regression_metadata(document, profile)
    _set_explicit_regression_inputs(document)
    _replace_regression_code_nodes(document)
    _validate_regression_graph(document)
    return document


def _profile_path(profile: str, output_root: Path) -> Path:
    suffix = resolve_llm_profile(profile).suffix
    return output_root / f"{TARGET_DSL.stem}{suffix}{TARGET_DSL.suffix}"


def _write_dsl(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        yaml.safe_dump(document, stream, allow_unicode=True, sort_keys=False, width=10_000)


def write_profile_dsl(
    profile: str = DEFAULT_LLM_PROFILE,
    *,
    output_root: Path = PROJECT_ROOT / "dify",
) -> Path:
    path = _profile_path(profile, output_root)
    _write_dsl(path, build_regression_dsl(profile))
    return path


def write_all_profile_dsls(
    *, output_root: Path = PROJECT_ROOT / "dify"
) -> tuple[Path, ...]:
    return tuple(
        write_profile_dsl(profile, output_root=output_root)
        for profile in sorted(LLM_PROFILES)
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=sorted(LLM_PROFILES), default=DEFAULT_LLM_PROFILE)
    parser.add_argument("--all-profiles", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dify")
    return parser


if __name__ == "__main__":
    arguments = _build_parser().parse_args()
    if arguments.all_profiles:
        write_all_profile_dsls(output_root=arguments.output_dir)
    else:
        write_profile_dsl(arguments.profile, output_root=arguments.output_dir)
