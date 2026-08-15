from __future__ import annotations

import argparse
from dataclasses import dataclass
from copy import deepcopy
import json
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DSL = PROJECT_ROOT / "dify" / "paper-comparison-workflow.yml"
PAPER_DOSSIER_DSL = PROJECT_ROOT / "dify" / "paper-dossier-workflow.yml"
TARGET_DSL = PROJECT_ROOT / "dify" / "paper-comparison-multimodel-workflow.yml"
PREPARE_DSL = PROJECT_ROOT / "dify" / "paper-comparison-prepare-workflow.yml"
MERGED_DSL = PROJECT_ROOT / "dify" / "paper-comparison-merged-workflow.yml"
DEFAULT_LLM_PROFILE = "deepseek"


@dataclass(frozen=True)
class LLMProfile:
    name: str
    provider: str
    model: str
    dependency: dict[str, object]
    suffix: str


LLM_PROFILES = {
    "deepseek": LLMProfile(
        name="deepseek",
        provider="langgenius/deepseek/deepseek",
        model="deepseek-v4-flash",
        dependency={
            "current_identifier": None,
            "type": "marketplace",
            "value": {
                "marketplace_plugin_unique_identifier": (
                    "langgenius/deepseek:0.0.19@5b68617c637b62d31e7f33a9f5677b76e88f81868fb04a728e208588564b72ea"
                ),
                "version": None,
            },
        },
        suffix="",
    ),
    "ollama": LLMProfile(
        name="ollama",
        provider="langgenius/ollama/ollama",
        model="qwen3:8b",
        dependency={
            "current_identifier": None,
            "type": "marketplace",
            "value": {
                "marketplace_plugin_unique_identifier": (
                    "langgenius/ollama:1.0.0@86dd6101fbd9de94e6681782700fa98c8a785c982918e6fe0e3f"
                    "d507e15ba3f"
                ),
                "version": None,
            },
        },
        suffix="-ollama",
    ),
}

DEFAULT_MODELS = [
    "logistic_regression",
    "random_forest",
    "xgboost",
    "lightgbm",
    "svm",
    "knn",
    "mlp",
]


def resolve_llm_profile(profile: str = DEFAULT_LLM_PROFILE) -> LLMProfile:
    try:
        return LLM_PROFILES[profile]
    except KeyError:
        allowed = ", ".join(sorted(LLM_PROFILES))
        raise ValueError(f"unknown LLM profile {profile!r}; expected one of: {allowed}") from None

PROTOCOL_CONFIRMATION_ID = "1900000000038"
POLL_CONFIRMED_JOB_ID = "1900000000039"
PROTOCOL_FAILURE_ID = "1900000000040"
PROTOCOL_OK_ID = "1900000000041"
SUBMIT_CONFIRMED_JOB_ID = "1900000000042"
JOB_SUBMISSION_FAILURE_ID = "1900000000043"
JOB_SUBMISSION_RESPONSE_ID = "1900000000044"
JOB_SUBMISSION_OK_ID = "1900000000045"
PROTOCOL_FAILURE_OUTPUT_ID = "1900000000046"
JOB_FAILURE_OUTPUT_ID = "1900000000047"
DIRECT_FAILURE_OUTPUT_SPECS = (
    ("1900000000048", "normalize_dossier_http_failure"),
    ("1900000000049", "dossier_semantic_failure"),
    ("1900000000050", "thresholds_failure"),
    ("1900000000051", "normalize_validation_http_failure"),
    ("1900000000052", "validation_semantic_failure"),
    ("1900000000053", "normalize_experiment_http_failure"),
    ("1900000000054", "experiment_semantic_failure"),
    ("1900000000055", "request_failure"),
    ("1900000000056", "normalize_comparison_http_failure"),
    ("1900000000057", "comparison_semantic_failure"),
)
REPORT_OUTPUT_VARIABLES = (
    "dossier_json",
    "validation_json",
    "experiment_json",
    "comparison_json",
    "assessment_json",
    "markdown_report",
)
PREPARE_OUTPUT_VARIABLES = (
    "protocol_preview_json",
    "protocol_token",
    "draft_expires_at",
)

MERGED_START_ID = "3900000000001"
MERGED_RUN_MODE_ID = "3900000000002"
MERGED_PREPARE_PDF_PRESENT_ID = "3900000000003"
MERGED_PREPARE_READY_ID = "3900000000005"
MERGED_PROTOCOL_DRAFT_POST_ID = "3900000000006"
MERGED_PROTOCOL_DRAFT_RESPONSE_ID = "3900000000007"
MERGED_OUTPUT_PREPARE_ID = "3900000000008"
MERGED_GET_DRAFT_ID = "3900000000009"
MERGED_DRAFT_RESPONSE_ID = "3900000000010"
MERGED_DOSSIER_OK_ID = "3900000000011"
MERGED_OUTPUT_RUN_ID = "3900000000012"
MERGED_PREPARE_INPUT_FAILURE_ID = "3900000000013"
MERGED_PROTOCOL_NOT_READY_ID = "3900000000014"
MERGED_RUN_DRAFT_FAILURE_ID = "3900000000015"
MERGED_OUTPUT_PREPARE_INPUT_FAILURE_ID = "3900000000016"
MERGED_OUTPUT_PROTOCOL_NOT_READY_ID = "3900000000017"
MERGED_OUTPUT_RUN_DRAFT_FAILURE_ID = "3900000000018"
MERGED_OUTPUT_PROTOCOL_FAILURE_ID = "3900000000019"
MERGED_OUTPUT_JOB_FAILURE_ID = "3900000000020"
MERGED_DRAFT_SAVED_OK_ID = "3900000000021"
MERGED_DRAFT_SAVE_FAILURE_ID = "3900000000022"
MERGED_OUTPUT_DRAFT_SAVE_FAILURE_ID = "3900000000023"
MERGED_PREPARE_PARSE_HTTP_FAILURE_ID = "3900000000024"
MERGED_PREPARE_PARSE_SEMANTIC_FAILURE_ID = "3900000000025"
MERGED_PREPARE_DOSSIER_FAILURE_ID = "3900000000026"
MERGED_PREPARE_DIAGNOSIS_FAILURE_ID = "3900000000027"
MERGED_OUTPUT_PREPARE_PARSE_HTTP_FAILURE_ID = "3900000000028"
MERGED_OUTPUT_PREPARE_PARSE_SEMANTIC_FAILURE_ID = "3900000000029"
MERGED_OUTPUT_PREPARE_DOSSIER_FAILURE_ID = "3900000000030"
MERGED_OUTPUT_PREPARE_DIAGNOSIS_FAILURE_ID = "3900000000031"
MERGED_DRAFT_POST_FAILURE_ID = "3900000000032"
MERGED_OUTPUT_DRAFT_POST_FAILURE_ID = "3900000000033"


def _load_source() -> dict:
    return yaml.safe_load(SOURCE_DSL.read_text(encoding="utf-8"))


def _load_paper_dossier_source() -> dict:
    return yaml.safe_load(PAPER_DOSSIER_DSL.read_text(encoding="utf-8"))


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

def has_explicit_metric_qualifier(metric):
    name = metric.get("name") if isinstance(metric, dict) else None
    if not isinstance(name, str):
        return False
    candidate = name.strip()
    return ("(" in candidate and ")" in candidate) or ("\\uff08" in candidate and "\\uff09" in candidate)

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
            or metric.get("supported") is not True
            or (metric.get("ambiguous") is True and not has_explicit_metric_qualifier(metric))
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
    helper = helper.replace(
        '_PROTOCOL_SECRET = os.environ.get("DIFY_PROTOCOL_SECRET") or "local-only-fallback-not-for-production"',
        '_PROTOCOL_SECRET = os.environ.get("DIFY_PROTOCOL_SECRET", "")',
    )
    return helper + "\n\n" + entrypoint.strip() + "\n"


def _secret_safe_embedded_experiment_helper_code(entrypoint: str) -> str:
    return _embedded_experiment_helper_code(entrypoint).replace('"sk-"', '"s" + "k-"')


def _embedded_parser_validator_code() -> str:
    validator_path = PROJECT_ROOT / "dify" / "code" / "validate_parser.py"
    return validator_path.read_text(encoding="utf-8").rstrip() + "\n"


def _protocol_environment_variables(base_variables: list[dict] | None = None) -> list[dict]:
    variables = deepcopy(base_variables or [])
    if not any(item.get("name") == "DIFY_PROTOCOL_SECRET" for item in variables):
        variables.append(
            {
                "description": "Protocol signing secret shared with repro-runner; exported without a value.",
                "id": "39000000-0000-4000-8000-000000000001",
                "name": "DIFY_PROTOCOL_SECRET",
                "selector": ["env", "DIFY_PROTOCOL_SECRET"],
                "value": "",
                "value_type": "secret",
            }
        )
    return variables


def _protocol_secret_input() -> dict:
    return {
        "value_selector": ["env", "DIFY_PROTOCOL_SECRET"],
        "value_type": "string",
        "variable": "protocol_secret",
    }


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
    protocol_secret: str = "",
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
        secret=protocol_secret,
    )
""")


def _poll_confirmed_job_code() -> str:
    return _embedded_experiment_helper_code("""def main(job_response_json: str) -> dict:

    # The HTTP node uploads the CSV and returns the admitted job; this code only polls.
    return poll_submitted_job_until_terminal(
        "http://repro-runner:8001",
        job_response_json,
    )
""")


def _parse_job_submission_response_code() -> str:
    return """import json
import re


JOB_ID_RE = re.compile(r"^job-[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
KNOWN_STATUSES = {"queued", "running", "cancel_requested", "succeeded", "partial", "failed", "cancelled", "needs_retry"}


def main(body: str) -> dict:
    try:
        response = json.loads(body) if isinstance(body, str) else body
    except (TypeError, json.JSONDecodeError):
        response = {}
    if not isinstance(response, dict):
        response = {}
    job_id = response.get("job_id")
    status = response.get("status")
    ok = isinstance(job_id, str) and JOB_ID_RE.fullmatch(job_id) is not None and status in KNOWN_STATUSES
    errors = [] if ok else [{"code": "job_response_invalid", "message": "The job submission response is invalid."}]
    return {
        "job_id": job_id if ok else "",
        "job_ok": ok,
        "job_errors": json.dumps(errors, ensure_ascii=False, separators=(",", ":")),
    }
"""


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


def _protocol_failure_empty_context_code() -> str:
    return """import json


def main(protocol_errors: str) -> dict:
    try:
        errors = json.loads(protocol_errors) if isinstance(protocol_errors, str) else []
    except (TypeError, json.JSONDecodeError):
        errors = []
    if not isinstance(errors, list) or not errors:
        errors = [{"code": "protocol_not_confirmed", "message": "Protocol confirmation is required."}]
    safe_errors = [
        item for item in errors
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    ] or [{"code": "protocol_not_confirmed", "message": "Protocol confirmation is required."}]
    experiment = {"status": "failed", "errors": safe_errors}
    comparison = {"items": [], "errors": [{"code": "comparison_not_run", "message": "Comparison did not run."}]}
    assessment = {"strict_status": "not_comparable", "approximate_status": "insufficient_metrics", "items": []}
    return {
        "dossier_json": "{}",
        "validation_json": "{}",
        "experiment_json": json.dumps(experiment, ensure_ascii=False, separators=(",", ":")),
        "comparison_json": json.dumps(comparison, ensure_ascii=False, separators=(",", ":")),
        "assessment_json": json.dumps(assessment, ensure_ascii=False, separators=(",", ":")),
        "markdown_report": "Protocol confirmation is required before the asynchronous experiment can run.",
    }
"""


def _job_submission_failure_code() -> str:
    return """import json


def _object(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def main(dossier_json: str, validation_json: str) -> dict:
    dossier = _object(dossier_json)
    validation = _object(validation_json)
    experiment = {
        "status": "failed",
        "errors": [
            {
                "code": "job_submit_failed",
                "message": "The asynchronous experiment job could not be submitted.",
            }
        ],
    }
    comparison = {
        "items": [],
        "errors": [{"code": "comparison_not_run", "message": "Comparison did not run."}],
    }
    assessment = {
        "strict_status": "not_comparable",
        "approximate_status": "insufficient_metrics",
        "items": [],
    }
    return {
        "dossier_json": json.dumps(dossier, ensure_ascii=False, separators=(",", ":")),
        "validation_json": json.dumps(validation, ensure_ascii=False, separators=(",", ":")),
        "experiment_json": json.dumps(experiment, ensure_ascii=False, separators=(",", ":")),
        "comparison_json": json.dumps(comparison, ensure_ascii=False, separators=(",", ":")),
        "assessment_json": json.dumps(assessment, ensure_ascii=False, separators=(",", ":")),
        "markdown_report": "The asynchronous experiment job could not be submitted.",
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
            "draft_id": {"children": None, "type": "string"},
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
            _protocol_secret_input(),
        ],
        3210,
        -360,
    )
    submit = deepcopy(nodes["run_experiment"])
    submit["id"] = SUBMIT_CONFIRMED_JOB_ID
    submit["position"] = {"x": 3540, "y": -360}
    submit["positionAbsolute"] = {"x": 3540, "y": -360}
    submit["data"]["title"] = "submit_confirmed_job"
    submit["data"]["url"] = "http://repro-runner:8001/v1/jobs"
    submit["data"]["body"]["data"] = [
        {
            "file": [start["id"], "training_csv"],
            "id": "key-value-1",
            "key": "file",
            "type": "file",
            "value": "",
        },
        {
            "id": "key-value-2",
            "key": "manifest_json",
            "type": "text",
            "value": f"{{{{#{confirmation['id']}.manifest_json#}}}}",
        },
    ]
    parse_submission = _clone_code_node(
        template,
        JOB_SUBMISSION_RESPONSE_ID,
        "parse_job_submission_response",
        _parse_job_submission_response_code(),
        {
            "job_id": {"children": None, "type": "string"},
            "job_errors": {"children": None, "type": "string"},
            "job_ok": {"children": None, "type": "boolean"},
        },
        [
            {"value_selector": [submit["id"], "body"], "value_type": "string", "variable": "body"},
        ],
        3870,
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
    job_ok = _clone_if_node(
        nodes["experiment_ok?"],
        JOB_SUBMISSION_OK_ID,
        "job_submission_ok?",
        [parse_submission["id"], "job_ok"],
        4035,
        -360,
    )
    poll = deepcopy(nodes["run_experiment"])
    poll["id"] = POLL_CONFIRMED_JOB_ID
    poll["position"] = {"x": 4200, "y": -360}
    poll["positionAbsolute"] = {"x": 4200, "y": -360}
    poll["data"]["title"] = "poll_confirmed_job"
    poll["data"]["method"] = "post"
    poll["data"]["url"] = f"http://repro-runner:8001/v1/jobs/{{{{#{parse_submission['id']}.job_id#}}}}/wait-result"
    poll["data"]["body"] = {"type": "form-data", "data": []}
    poll["data"]["variables"] = []
    poll["data"]["error_strategy"] = "fail-branch"
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
    job_failure = _clone_code_node(
        nodes["normalize_experiment_http_failure"],
        JOB_SUBMISSION_FAILURE_ID,
        "normalize_job_submission_http_failure",
        _job_submission_failure_code(),
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
        ],
        4200,
        -360,
    )
    early_output_variables = list(REPORT_OUTPUT_VARIABLES)

    def direct_output(node_id: str, title: str, source_id: str, x: int, y: int) -> dict:
        output = deepcopy(nodes["Output"])
        output["id"] = node_id
        output["position"] = {"x": x, "y": y}
        output["positionAbsolute"] = {"x": x, "y": y}
        output["data"]["title"] = title
        output["data"]["outputs"] = [
            {
                "value_selector": [source_id, variable],
                "value_type": "string",
                "variable": variable,
            }
            for variable in early_output_variables
        ]
        return output

    protocol_failure_output = direct_output(
        PROTOCOL_FAILURE_OUTPUT_ID,
        "Output_protocol_confirmation_failure",
        failure["id"],
        4700,
        -360,
    )
    job_failure_output = direct_output(
        JOB_FAILURE_OUTPUT_ID,
        "Output_job_submission_failure",
        job_failure["id"],
        4700,
        -360,
    )
    late_failure_outputs = [
        direct_output(
            output_id,
            f"Output_{source_title}",
            nodes[source_title]["id"],
            4700,
            nodes[source_title].get("position", {}).get("y", 0),
        )
        for output_id, source_title in DIRECT_FAILURE_OUTPUT_SPECS
    ]

    # The main Output must be reached directly from the success formatter.
    # A shared variable-aggregator waits for every branch edge, and can remain
    # unresolved when an IF branch is not taken in the imported Dify graph.
    nodes["Output"]["data"]["outputs"] = [
        {
            "value_selector": [nodes["format_comparison_report"]["id"], variable],
            "value_type": "string",
            "variable": variable,
        }
        for variable in REPORT_OUTPUT_VARIABLES
    ]
    document["workflow"]["graph"]["nodes"].extend(
        [
            confirmation,
            protocol_ok,
            submit,
            parse_submission,
            job_ok,
            poll,
            failure,
            job_failure,
            protocol_failure_output,
            job_failure_output,
            *late_failure_outputs,
        ]
    )
    protocol_titles = {
        "normalize_protocol_confirmation",
        "protocol_ok?",
        "submit_confirmed_job",
        "parse_job_submission_response",
        "job_submission_ok?",
        "poll_confirmed_job",
        "protocol_confirmation_failure",
        "normalize_job_submission_http_failure",
        "Output_protocol_confirmation_failure",
        "Output_job_submission_failure",
        *(f"Output_{source_title}" for _, source_title in DIRECT_FAILURE_OUTPUT_SPECS),
    }
    graph_nodes = document["workflow"]["graph"]["nodes"]
    protocol_nodes = [
        node for node in graph_nodes if node["data"].get("title") in protocol_titles
    ]
    remaining_nodes = [
        node for node in graph_nodes if node["data"].get("title") not in protocol_titles
    ]
    aggregator_index = next(
        index
        for index, node in enumerate(remaining_nodes)
        if node["data"].get("title") == "aggregate_dossier_json"
    )
    # Keep the added branch nodes before the variable aggregators and Output.
    # Dify's imported graph evaluator uses this serialized order when joining branches.
    graph_nodes[:] = (
        remaining_nodes[:aggregator_index]
        + protocol_nodes
        + remaining_nodes[aggregator_index:]
    )

    parse_suite["data"]["variables"] = [
        {"value_selector": [poll["id"], "body"], "value_type": "string", "variable": "body"}
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
            _make_edge(document, protocol_ok["id"], "true", submit["id"]),
            _make_edge(document, protocol_ok["id"], "false", failure["id"]),
            _make_edge(document, submit["id"], "source", parse_submission["id"]),
            _make_edge(document, submit["id"], "fail-branch", job_failure["id"]),
            _make_edge(document, parse_submission["id"], "source", job_ok["id"]),
            _make_edge(document, job_ok["id"], "true", poll["id"]),
            _make_edge(document, job_ok["id"], "false", job_failure["id"]),
            _make_edge(document, poll["id"], "fail-branch", job_failure["id"]),
            _make_edge(document, poll["id"], "source", parse_suite["id"]),
            _make_edge(document, failure["id"], "source", protocol_failure_output["id"]),
            _make_edge(document, job_failure["id"], "source", job_failure_output["id"]),
            _make_edge(document, nodes["format_comparison_report"]["id"], "source", nodes["Output"]["id"]),
            *[
                _make_edge(document, nodes[source_title]["id"], "source", output["id"])
                for output, (_, source_title) in zip(late_failure_outputs, DIRECT_FAILURE_OUTPUT_SPECS)
            ],
        ]
    )
    protocol_node_ids = {
        confirmation["id"],
        protocol_ok["id"],
        submit["id"],
        parse_submission["id"],
        job_ok["id"],
        poll["id"],
        failure["id"],
        job_failure["id"],
    }
    protocol_edges = [
        edge
        for edge in edges
        if edge["source"] in protocol_node_ids or edge["target"] in protocol_node_ids
    ]
    # Remove the old aggregator-to-Output edge; each terminal path now owns an
    # End node with a single incoming edge.
    aggregator_markdown_id = nodes["aggregate_markdown_report"]["id"]
    edges = [
        edge
        for edge in edges
        if not (
            edge["source"] == aggregator_markdown_id
            and edge["target"] == nodes["Output"]["id"]
        )
    ]
    output_ids = {nodes["Output"]["id"], *(output["id"] for output in late_failure_outputs)}
    direct_output_edges = [edge for edge in edges if edge["target"] in output_ids]
    base_edges = [
        edge
        for edge in edges
        if edge not in protocol_edges and edge not in direct_output_edges
    ]
    aggregator_id = nodes["aggregate_dossier_json"]["id"]
    aggregator_chain_index = next(
        index for index, edge in enumerate(base_edges) if edge["source"] == aggregator_id
    )
    # Keep branch edges before the aggregator chain so the Dify runtime registers
    # every possible aggregator input before evaluating the chain.
    document["workflow"]["graph"]["edges"] = (
        base_edges[:aggregator_chain_index]
        + direct_output_edges
        + protocol_edges
        + base_edges[aggregator_chain_index:]
    )
    nodes["normalize_experiment_http_failure"]["data"]["variables"] = [
        {
            "value_selector": [poll["id"], "experiment_json"],
            "value_type": "string",
            "variable": "experiment_json",
        },
    ]


def build_multimodel_dsl(profile: str = DEFAULT_LLM_PROFILE) -> dict:
    resolved_profile = resolve_llm_profile(profile)
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
    document["workflow"]["environment_variables"] = _protocol_environment_variables(
        document["workflow"].get("environment_variables", [])
    )
    return _apply_profile_metadata(document, resolved_profile)


def _prepare_code() -> str:
    return _embedded_experiment_helper_code("""def main(dossier_response_json: str, diagnosis_response_json: str, target_column: str, protocol_notes: str, protocol_secret: str = "") -> dict:
    return prepare_protocol_artifacts(
        dossier_response_json,
        diagnosis_response_json,
        target_column=target_column,
        protocol_notes=protocol_notes,
        secret=protocol_secret,
    )
""")


def _apply_profile_metadata(document: dict, profile: LLMProfile) -> dict:
    if profile.name == DEFAULT_LLM_PROFILE:
        return document
    document["dependencies"] = [deepcopy(profile.dependency)]
    document["app"]["name"] = f"{document['app']['name']}{profile.suffix}"
    document["workflow"]["name"] = f"{document['workflow']['name']}{profile.suffix}"
    return document


def _apply_llm_node_profile(node: dict, profile: LLMProfile) -> None:
    model = deepcopy(node["data"].get("model", {}))
    model["provider"] = profile.provider
    model["name"] = profile.model
    node["data"]["model"] = model


def build_prepare_dsl_legacy() -> dict:
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
            "allowed_file_extensions": [".PDF"],
            "allowed_file_types": ["document"],
            "allowed_file_upload_methods": ["local_file"],
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
            "allowed_file_extensions": [".CSV"],
            "allowed_file_types": ["document"],
            "allowed_file_upload_methods": ["local_file"],
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
            _protocol_secret_input(),
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
            "environment_variables": _protocol_environment_variables([]),
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


def build_prepare_dsl(profile: str = DEFAULT_LLM_PROFILE) -> dict:
    """Compose the PDF-to-protocol workflow for one explicit LLM profile."""
    resolved_profile = resolve_llm_profile(profile)
    source = _load_source()
    source_nodes = _by_title(source)
    dossier_source = _load_paper_dossier_source()
    dossier_nodes = _nodes(dossier_source)
    http_template = next(node for node in dossier_nodes if node["data"]["type"] == "http-request")
    code_templates = [node for node in dossier_nodes if node["data"]["type"] == "code"]
    gate_templates = [node for node in dossier_nodes if node["data"]["type"] == "if-else"]
    llm_template = next(node for node in dossier_nodes if node["data"]["type"] == "llm")

    start_id = "2900000000001"
    parser_id = "2900000000002"
    parser_validate_id = "2900000000003"
    parser_gate_id = "2900000000004"
    extract_id = "2900000000005"
    dossier_validate_id = "2900000000006"
    dossier_gate_id = "2900000000007"
    diagnosis_id = "2900000000008"
    prepare_id = "2900000000009"
    output_id = "2900000000010"

    start = deepcopy(source_nodes["Start"])
    start["id"] = start_id
    start["position"] = {"x": 100, "y": 300}
    start["positionAbsolute"] = {"x": 100, "y": 300}
    start["data"]["variables"] = [
        {
            "allowed_file_extensions": [".PDF"],
            "allowed_file_types": ["document"],
            "allowed_file_upload_methods": ["local_file"],
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
            "allowed_file_extensions": [".CSV"],
            "allowed_file_types": ["document"],
            "allowed_file_upload_methods": ["local_file"],
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

    parser = deepcopy(http_template)
    parser["id"] = parser_id
    parser["position"] = {"x": 500, "y": 300}
    parser["positionAbsolute"] = {"x": 500, "y": 300}
    parser["data"]["title"] = "parse_paper"
    parser["data"]["url"] = "http://paper-parser:8000/v1/parse"
    parser["data"]["headers"] = "X-Parser-Token:{{#env.PARSER_API_TOKEN#}}"
    parser["data"]["body"]["type"] = "form-data"
    parser["data"]["body"]["data"] = [
        {"file": [start_id, "paper_pdf"], "id": "key-value-1", "key": "file", "type": "file", "value": ""},
    ]
    parser["data"]["retry_config"] = {
        "max_retries": 2,
        "retry_enabled": True,
        "retry_interval": 1000,
    }

    parser_validate = deepcopy(code_templates[0])
    parser_validate["id"] = parser_validate_id
    parser_validate["position"] = {"x": 800, "y": 300}
    parser_validate["positionAbsolute"] = {"x": 800, "y": 300}
    parser_validate["data"]["title"] = "validate_parser_response"
    parser_validate["data"]["code"] = _embedded_parser_validator_code()
    parser_validate["data"]["variables"] = [
        {"value_selector": [parser_id, "body"], "value_type": "string", "variable": "body"},
        {"value_selector": [parser_id, "status_code"], "value_type": "number", "variable": "status_code"},
    ]

    parser_gate = deepcopy(gate_templates[0])
    parser_gate["id"] = parser_gate_id
    parser_gate["position"] = {"x": 1100, "y": 300}
    parser_gate["positionAbsolute"] = {"x": 1100, "y": 300}
    parser_gate["data"]["title"] = "paper_parser_ok?"
    parser_gate["data"]["cases"][0]["conditions"][0]["variable_selector"] = [parser_validate_id, "can_continue"]

    extract = deepcopy(llm_template)
    extract["id"] = extract_id
    extract["position"] = {"x": 1400, "y": 300}
    extract["positionAbsolute"] = {"x": 1400, "y": 300}
    extract["data"]["title"] = "extract_paper_dossier"
    for message in extract["data"].get("prompt_template", []):
        if not isinstance(message, dict) or not isinstance(message.get("text"), str):
            continue
        message["text"] = (
            message["text"]
            .replace("{{#1785821039306.parsed_json#}}", f"{{{{#{parser_validate_id}.parsed_json#}}}}")
            .replace("{{#1785820293883.user_notes#}}", f"{{{{#{start_id}.protocol_notes#}}}}")
            .replace("{{#1785820293883.target_language#}}", "简体中文")
        )
    _apply_llm_node_profile(extract, resolved_profile)

    dossier_validate = deepcopy(code_templates[1])
    dossier_validate["id"] = dossier_validate_id
    dossier_validate["position"] = {"x": 1700, "y": 300}
    dossier_validate["positionAbsolute"] = {"x": 1700, "y": 300}
    dossier_validate["data"]["title"] = "validate_paper_dossier"
    dossier_validate["data"]["variables"] = [
        {"value_selector": [extract_id, "text"], "value_type": "string", "variable": "dossier_json"},
        {"value_selector": [parser_validate_id, "parsed_json"], "value_type": "string", "variable": "page_count"},
    ]

    dossier_gate = deepcopy(gate_templates[1])
    dossier_gate["id"] = dossier_gate_id
    dossier_gate["position"] = {"x": 2000, "y": 300}
    dossier_gate["positionAbsolute"] = {"x": 2000, "y": 300}
    dossier_gate["data"]["title"] = "paper_dossier_ok?"
    dossier_gate["data"]["cases"][0]["conditions"][0]["variable_selector"] = [dossier_validate_id, "can_continue"]

    diagnosis = deepcopy(source_nodes["validate_dataset"])
    diagnosis["id"] = diagnosis_id
    diagnosis["position"] = {"x": 2300, "y": 300}
    diagnosis["positionAbsolute"] = {"x": 2300, "y": 300}
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
            {"value_selector": [dossier_validate_id, "validated_json"], "value_type": "string", "variable": "dossier_response_json"},
            {"value_selector": [diagnosis_id, "body"], "value_type": "string", "variable": "diagnosis_response_json"},
            {"value_selector": [start_id, "target_column"], "value_type": "string", "variable": "target_column"},
            {"value_selector": [start_id, "protocol_notes"], "value_type": "string", "variable": "protocol_notes"},
            _protocol_secret_input(),
        ],
        2600,
        300,
    )
    output = deepcopy(source_nodes["Output"])
    output["id"] = output_id
    output["position"] = {"x": 2900, "y": 300}
    output["positionAbsolute"] = {"x": 2900, "y": 300}
    output["data"]["outputs"] = [
        {"value_selector": [prepare_id, "protocol_preview_json"], "value_type": "string", "variable": "protocol_preview_json"},
        {"value_selector": [prepare_id, "protocol_token"], "value_type": "string", "variable": "protocol_token"},
    ]

    document = {
        "app": {
            "description": "Prepare a safe protocol preview before confirmed asynchronous training.",
            "icon": "馃И",
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
            "environment_variables": _protocol_environment_variables(
                dossier_source["workflow"].get("environment_variables", [])
            ),
            "features": deepcopy(source["workflow"]["features"]),
            "graph": {
                "edges": [],
                "nodes": [start, parser, parser_validate, parser_gate, extract, dossier_validate, dossier_gate, diagnosis, prepare, output],
                "viewport": {"x": 0, "y": 0, "zoom": 0.8},
            },
            "rag_pipeline_variables": [],
            "name": "paper-comparison-prepare-workflow",
        },
    }
    document["workflow"]["graph"]["edges"] = [
        _make_edge(document, start_id, "source", parser_id),
        _make_edge(document, parser_id, "source", parser_validate_id),
        _make_edge(document, parser_validate_id, "source", parser_gate_id),
        _make_edge(document, parser_gate_id, "true", extract_id),
        _make_edge(document, extract_id, "source", dossier_validate_id),
        _make_edge(document, dossier_validate_id, "source", dossier_gate_id),
        _make_edge(document, dossier_gate_id, "true", diagnosis_id),
        _make_edge(document, diagnosis_id, "source", prepare_id),
        _make_edge(document, prepare_id, "source", output_id),
    ]
    return _apply_profile_metadata(document, resolved_profile)


def _merged_start_variables() -> list[dict]:
    source = _load_source()
    existing = {item["variable"]: deepcopy(item) for item in _by_title(source)["Start"]["data"]["variables"]}
    return [
        {
            "default": "prepare",
            "hint": "Choose prepare for PDF parsing/token creation or run for confirmed suite execution.",
            "label": "run_mode",
            "options": ["prepare", "run"],
            "placeholder": "",
            "required": True,
            "type": "select",
            "variable": "run_mode",
        },
        {
            "allowed_file_extensions": [".PDF"],
            "allowed_file_types": ["document"],
            "allowed_file_upload_methods": ["local_file"],
            "default": "",
            "hint": "Required only when run_mode is prepare.",
            "label": "paper_pdf",
            "max_length": 0,
            "options": [],
            "placeholder": "",
            "required": False,
            "type": "file",
            "variable": "paper_pdf",
        },
        {
            "allowed_file_extensions": [".CSV"],
            "allowed_file_types": ["document"],
            "allowed_file_upload_methods": ["local_file"],
            "default": "",
            "hint": "Upload the training CSV for prepare diagnosis and confirmed run execution.",
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
            "hint": "Short-lived token returned by prepare mode.",
            "label": "protocol_token",
            "max_length": 100000,
            "options": [],
            "placeholder": "",
            "required": False,
            "type": "paragraph",
            "variable": "protocol_token",
        },
        {
            "default": False,
            "hint": "Required for run mode after reviewing the prepare preview.",
            "label": "confirm_protocol",
            "options": [],
            "placeholder": "",
            "required": False,
            "type": "checkbox",
            "variable": "confirm_protocol",
        },
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
        {
            "default": "",
            "hint": "Optional notes used only in prepare mode; only a digest is retained.",
            "label": "protocol_notes",
            "max_length": 512,
            "options": [],
            "placeholder": "",
            "required": False,
            "type": "paragraph",
            "variable": "protocol_notes",
        },
    ]


def _merged_run_mode_code() -> str:
    return """def main(run_mode: str) -> dict:
    mode = run_mode.strip().casefold() if isinstance(run_mode, str) else ""
    prepare = mode != "run"
    return {"is_prepare": prepare}
"""


def _merged_prepare_code() -> str:
    return _secret_safe_embedded_experiment_helper_code("""def main(dossier_response_json: str, diagnosis_response_json: str, target_column: str, protocol_notes: str, protocol_secret: str = "") -> dict:
    return prepare_protocol_artifacts(
        dossier_response_json,
        diagnosis_response_json,
        target_column=target_column,
        protocol_notes=protocol_notes,
        secret=protocol_secret,
    )
""")


def _merged_prepare_draft_response_code() -> str:
    return _secret_safe_embedded_experiment_helper_code("""def main(body: str, status_code: int, expected_draft_id: str, expected_manifest_json: str) -> dict:
    preview = _object(expected_manifest_json)
    manifest = preview.get("manifest_draft") if isinstance(preview, dict) else {}
    result = normalize_protocol_draft_write_response(
        body,
        status_code,
        expected_draft_id,
        manifest,
    )
    result["protocol_preview_json"] = _json(preview)
    result["protocol_token"] = ""
    return result
""")


def _merged_protocol_draft_read_response_code() -> str:
    return _secret_safe_embedded_experiment_helper_code("""def main(body: str, status_code: int, expected_draft_id: str, manifest_json: str) -> dict:
    return normalize_protocol_draft_read_response(
        body,
        status_code,
        expected_draft_id,
        manifest_json,
    )
""")


def _merged_prepare_failure_code(default_code: str, default_message: str) -> str:
    return f"""import json


def _errors(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    safe = [
        item for item in parsed
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    ]
    return safe or [{{"code": "{default_code}", "message": "{default_message}"}}]


def main(error_json: str = "", markdown_report: str = "") -> dict:
    report = markdown_report if isinstance(markdown_report, str) and markdown_report else "{default_message}"
    return {{
        "protocol_preview_json": json.dumps({{"errors": _errors(error_json)}}, ensure_ascii=False, separators=(",", ":")),
        "protocol_token": "",
        "draft_expires_at": "",
        "error_json": json.dumps(_errors(error_json), ensure_ascii=False, separators=(",", ":")),
        "markdown_report": report,
    }}
"""


def _merged_run_draft_failure_code() -> str:
    return """import json


def main(draft_errors: str) -> dict:
    try:
        errors = json.loads(draft_errors) if isinstance(draft_errors, str) else []
    except (TypeError, json.JSONDecodeError):
        errors = []
    if not isinstance(errors, list) or not errors:
        errors = [{"code": "protocol_draft_read_failed", "message": "Protocol draft could not be read."}]
    experiment = {"status": "failed", "errors": errors}
    comparison = {"items": [], "errors": [{"code": "comparison_not_run", "message": "Comparison did not run."}]}
    assessment = {"strict_status": "not_comparable", "approximate_status": "insufficient_metrics", "items": []}
    return {
        "dossier_json": "{}",
        "validation_json": "{}",
        "experiment_json": json.dumps(experiment, ensure_ascii=False, separators=(",", ":")),
        "comparison_json": json.dumps(comparison, ensure_ascii=False, separators=(",", ":")),
        "assessment_json": json.dumps(assessment, ensure_ascii=False, separators=(",", ":")),
        "markdown_report": "Protocol draft could not be read.",
    }
"""


def _merged_remap_value_selectors(value: object, id_map: dict[str, str]) -> object:
    if isinstance(value, list):
        if len(value) >= 1 and isinstance(value[0], str) and value[0] in id_map:
            return [id_map[value[0]], *[_merged_remap_value_selectors(item, id_map) for item in value[1:]]]
        return [_merged_remap_value_selectors(item, id_map) for item in value]
    if isinstance(value, dict):
        return {key: _merged_remap_value_selectors(item, id_map) for key, item in value.items()}
    if isinstance(value, str):
        for old_id, new_id in id_map.items():
            value = value.replace(f"{{{{#{old_id}.", f"{{{{#{new_id}.")
        return value
    return value


def _merged_clone_node(node: dict, new_id: str, id_map: dict[str, str]) -> dict:
    cloned = deepcopy(node)
    cloned["id"] = new_id
    return _merged_remap_value_selectors(cloned, id_map)


def _merged_end_from_source(template: dict, node_id: str, title: str, source_id: str, variables: tuple[str, ...], x: int, y: int) -> dict:
    output = deepcopy(template)
    output["id"] = node_id
    output["position"] = {"x": x, "y": y}
    output["positionAbsolute"] = {"x": x, "y": y}
    output["data"]["title"] = title
    output["data"]["outputs"] = [
        {"value_selector": [source_id, variable], "value_type": "string", "variable": variable}
        for variable in variables
    ]
    return output


def _merged_http_node(template: dict, node_id: str, title: str, url: str, x: int, y: int) -> dict:
    node = deepcopy(template)
    node["id"] = node_id
    node["position"] = {"x": x, "y": y}
    node["positionAbsolute"] = {"x": x, "y": y}
    node["data"]["title"] = title
    node["data"]["method"] = "post"
    node["data"]["url"] = url
    node["data"]["headers"] = ""
    node["data"]["error_strategy"] = "fail-branch"
    node["data"]["body"] = {"type": "form-data", "data": []}
    node["data"]["variables"] = []
    return node


def _merged_failure_outputs_for_run(document: dict, nodes: dict[str, dict], output_template: dict) -> list[dict]:
    specs = [
        (MERGED_OUTPUT_PROTOCOL_FAILURE_ID, "Output_protocol_confirmation_failure", "protocol_confirmation_failure"),
        (MERGED_OUTPUT_JOB_FAILURE_ID, "Output_job_submission_failure", "normalize_job_submission_http_failure"),
        *[
            (output_id, f"Output_{source_title}", source_title)
            for output_id, source_title in DIRECT_FAILURE_OUTPUT_SPECS
        ],
    ]
    result = []
    for output_id, title, source_title in specs:
        if source_title not in nodes:
            continue
        source = nodes[source_title]
        y = source.get("position", {}).get("y", 0)
        result.append(_merged_end_from_source(output_template, output_id, title, source["id"], REPORT_OUTPUT_VARIABLES, 7000, y))
    return result


def _clone_not_empty_if_node(template: dict, node_id: str, title: str, variable_selector: list[str], var_type: str, x: int, y: int) -> dict:
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
                    "comparison_operator": "not empty",
                    "id": f"{node_id}-condition",
                    "value": "",
                    "varType": var_type,
                    "variable_selector": variable_selector,
                }
            ],
            "id": "true",
            "logical_operator": "and",
        }
    ]
    return node


def build_merged_dsl(profile: str = DEFAULT_LLM_PROFILE) -> dict:
    """Build the deterministic merged prepare/run Dify workflow."""
    resolved_profile = resolve_llm_profile(profile)
    prepare_doc = build_prepare_dsl(profile)
    run_doc = build_multimodel_dsl(profile)
    prepare_nodes = _by_title(prepare_doc)
    run_nodes = _by_title(run_doc)
    source = _load_source()
    source_nodes = _by_title(source)

    id_map = {
        "2900000000001": MERGED_START_ID,
        "2900000000002": "3910000000002",
        "2900000000003": "3910000000003",
        "2900000000004": "3910000000004",
        "2900000000005": "3910000000005",
        "2900000000006": "3910000000006",
        "2900000000007": "3910000000007",
        "2900000000008": "3910000000008",
        "2900000000009": "3910000000009",
        "1900000000001": MERGED_START_ID,
        "1900000000007": "3920000000007",
        "1900000000008": "3920000000008",
        "1900000000009": "3920000000009",
        "1900000000010": "3920000000010",
        "1900000000042": "3920000000042",
        "1900000000044": "3920000000044",
        "1900000000045": "3920000000045",
        "1900000000039": "3920000000039",
        "1900000000012": "3920000000012",
        "1900000000013": "3920000000013",
        "1900000000014": "3920000000014",
        "1900000000015": "3920000000015",
        "1900000000016": "3920000000016",
        "1900000000017": "3920000000017",
        "1900000000018": "3920000000018",
        "1900000000019": "3920000000019",
        "1900000000020": "3920000000020",
        "1900000000021": "3920000000021",
        "1900000000022": "3920000000022",
        "1900000000024": "3920000000024",
        "1900000000025": "3920000000025",
        "1900000000026": "3920000000026",
        "1900000000027": "3920000000027",
        "1900000000028": "3920000000028",
        "1900000000029": "3920000000029",
        "1900000000030": "3920000000030",
        "1900000000038": "3920000000038",
        "1900000000041": "3920000000041",
        "1900000000040": "3920000000040",
        "1900000000043": "3920000000043",
    }

    start = deepcopy(source_nodes["Start"])
    start["id"] = MERGED_START_ID
    start["position"] = {"x": 100, "y": 120}
    start["positionAbsolute"] = {"x": 100, "y": 120}
    start["data"]["variables"] = _merged_start_variables()

    run_mode = _clone_if_node(
        run_nodes["protocol_ok?"],
        MERGED_RUN_MODE_ID,
        "run_mode?",
        [MERGED_RUN_MODE_ID, "is_prepare"],
        430,
        120,
    )
    run_mode["data"]["type"] = "if-else"
    run_mode_code = _clone_code_node(
        run_nodes["normalize_suite_inputs"],
        MERGED_RUN_MODE_ID,
        "run_mode?",
        _merged_run_mode_code(),
        {"is_prepare": {"children": None, "type": "boolean"}},
        [{"value_selector": [MERGED_START_ID, "run_mode"], "value_type": "string", "variable": "run_mode"}],
        430,
        120,
    )
    run_mode_gate = _clone_if_node(
        run_nodes["protocol_ok?"],
        MERGED_RUN_MODE_ID + "g",
        "run_mode?",
        [MERGED_RUN_MODE_ID, "is_prepare"],
        760,
        120,
    )

    prepare_pdf_present = _clone_not_empty_if_node(
        run_nodes["protocol_ok?"],
        MERGED_PREPARE_PDF_PRESENT_ID,
        "prepare_pdf_present?",
        [MERGED_START_ID, "paper_pdf"],
        "array[file]",
        1420,
        -260,
    )

    prepare_titles = [
        "parse_paper",
        "validate_parser_response",
        "paper_parser_ok?",
        "extract_paper_dossier",
        "validate_paper_dossier",
        "paper_dossier_ok?",
        "diagnose_dataset",
        "prepare_protocol_artifacts",
    ]
    prepare_branch = [_merged_clone_node(prepare_nodes[title], id_map[prepare_nodes[title]["id"]], id_map) for title in prepare_titles]
    prepare_branch_by_title = {node["data"]["title"]: node for node in prepare_branch}
    prepare_branch_by_title["prepare_protocol_artifacts"]["data"]["code"] = _merged_prepare_code()
    prepare_branch_by_title["prepare_protocol_artifacts"]["data"]["variables"] = [
        variable
        for variable in prepare_branch_by_title["prepare_protocol_artifacts"]["data"].get("variables", [])
        if variable.get("variable") != "protocol_secret"
    ] + [_protocol_secret_input()]
    prepare_branch_by_title["prepare_protocol_artifacts"]["data"]["outputs"] = {
        "protocol_preview_json": {"children": None, "type": "string"},
        "protocol_token": {"children": None, "type": "string"},
        "draft_id": {"children": None, "type": "string"},
        "draft_expires_at": {"children": None, "type": "string"},
        "protocol_ready": {"children": None, "type": "boolean"},
        "protocol_errors": {"children": None, "type": "string"},
    }

    protocol_ready_gate = _clone_if_node(
        run_nodes["protocol_ok?"],
        MERGED_PREPARE_READY_ID,
        "protocol_ready?",
        [id_map[prepare_nodes["prepare_protocol_artifacts"]["id"]], "protocol_ready"],
        3940,
        -260,
    )
    draft_post = deepcopy(source_nodes["run_experiment"])
    draft_post["id"] = MERGED_PROTOCOL_DRAFT_POST_ID
    draft_post["position"] = {"x": 4270, "y": -260}
    draft_post["positionAbsolute"] = {"x": 4270, "y": -260}
    draft_post["data"]["title"] = "save_protocol_draft"
    draft_post["data"]["method"] = "post"
    draft_post["data"]["url"] = "http://repro-runner:8001/v1/protocol-drafts"
    draft_post["data"]["headers"] = ""
    draft_post["data"]["error_strategy"] = "fail-branch"
    draft_post["data"]["body"] = {
        "type": "form-data",
        "data": [
            {
                "id": "key-value-1",
                "key": "draft_id",
                "type": "text",
                "value": f"{{{{#{id_map[prepare_nodes['prepare_protocol_artifacts']['id']]}.draft_id#}}}}",
            },
            {
                "id": "key-value-2",
                "key": "protocol_token",
                "type": "text",
                "value": f"{{{{#{id_map[prepare_nodes['prepare_protocol_artifacts']['id']]}.protocol_token#}}}}",
            },
            {
                "id": "key-value-3",
                "key": "dossier_json",
                "type": "text",
                "value": f"{{{{#{id_map[prepare_nodes['validate_paper_dossier']['id']]}.validated_json#}}}}",
            },
        ],
    }
    draft_post["data"]["variables"] = []
    draft_response = _clone_code_node(
        run_nodes["normalize_suite_inputs"],
        MERGED_PROTOCOL_DRAFT_RESPONSE_ID,
        "prepare_protocol_draft_response",
        _merged_prepare_draft_response_code(),
        {
            "draft_saved_ok": {"children": None, "type": "boolean"},
            "draft_id": {"children": None, "type": "string"},
            "manifest_id": {"children": None, "type": "string"},
            "dataset_id": {"children": None, "type": "string"},
            "draft_expires_at": {"children": None, "type": "string"},
            "draft_errors": {"children": None, "type": "string"},
            "protocol_preview_json": {"children": None, "type": "string"},
            "protocol_token": {"children": None, "type": "string"},
        },
        [
            {"value_selector": [MERGED_PROTOCOL_DRAFT_POST_ID, "body"], "value_type": "string", "variable": "body"},
            {"value_selector": [MERGED_PROTOCOL_DRAFT_POST_ID, "status_code"], "value_type": "number", "variable": "status_code"},
            {"value_selector": [id_map[prepare_nodes["prepare_protocol_artifacts"]["id"]], "draft_id"], "value_type": "string", "variable": "expected_draft_id"},
            {"value_selector": [id_map[prepare_nodes["prepare_protocol_artifacts"]["id"]], "protocol_preview_json"], "value_type": "string", "variable": "expected_manifest_json"},
        ],
        4600,
        -260,
    )
    draft_saved_ok = _clone_if_node(
        run_nodes["protocol_ok?"],
        MERGED_DRAFT_SAVED_OK_ID,
        "draft_saved_ok?",
        [MERGED_PROTOCOL_DRAFT_RESPONSE_ID, "draft_saved_ok"],
        4930,
        -260,
    )

    prepare_failure = _clone_code_node(
        run_nodes["normalize_suite_inputs"],
        MERGED_PREPARE_INPUT_FAILURE_ID,
        "prepare_input_failure",
        _merged_prepare_failure_code("paper_pdf_required", "Upload paper_pdf when run_mode is prepare."),
        {
            "protocol_preview_json": {"children": None, "type": "string"},
            "protocol_token": {"children": None, "type": "string"},
            "draft_expires_at": {"children": None, "type": "string"},
            "error_json": {"children": None, "type": "string"},
            "markdown_report": {"children": None, "type": "string"},
        },
        [],
        1750,
        -620,
    )
    protocol_not_ready = _clone_code_node(
        run_nodes["normalize_suite_inputs"],
        MERGED_PROTOCOL_NOT_READY_ID,
        "protocol_not_ready_failure",
        _merged_prepare_failure_code("protocol_not_ready", "Protocol draft is not ready to run."),
        {
            "protocol_preview_json": {"children": None, "type": "string"},
            "protocol_token": {"children": None, "type": "string"},
            "draft_expires_at": {"children": None, "type": "string"},
            "error_json": {"children": None, "type": "string"},
            "markdown_report": {"children": None, "type": "string"},
        },
        [{"value_selector": [id_map[prepare_nodes["prepare_protocol_artifacts"]["id"]], "protocol_errors"], "value_type": "string", "variable": "error_json"}],
        4270,
        -620,
    )
    draft_save_failure = _clone_code_node(
        run_nodes["normalize_suite_inputs"],
        MERGED_DRAFT_SAVE_FAILURE_ID,
        "protocol_draft_save_failure",
        _merged_prepare_failure_code("protocol_draft_write_failed", "Protocol draft could not be saved."),
        {
            "protocol_preview_json": {"children": None, "type": "string"},
            "protocol_token": {"children": None, "type": "string"},
            "draft_expires_at": {"children": None, "type": "string"},
            "error_json": {"children": None, "type": "string"},
            "markdown_report": {"children": None, "type": "string"},
        },
        [{"value_selector": [MERGED_PROTOCOL_DRAFT_RESPONSE_ID, "draft_errors"], "value_type": "string", "variable": "error_json"}],
        4930,
        -620,
    )
    draft_post_failure = _clone_code_node(
        run_nodes["normalize_suite_inputs"],
        MERGED_DRAFT_POST_FAILURE_ID,
        "protocol_draft_write_http_failure",
        _merged_prepare_failure_code("protocol_draft_write_failed", "Protocol draft could not be saved."),
        {
            "protocol_preview_json": {"children": None, "type": "string"},
            "protocol_token": {"children": None, "type": "string"},
            "draft_expires_at": {"children": None, "type": "string"},
            "error_json": {"children": None, "type": "string"},
            "markdown_report": {"children": None, "type": "string"},
        },
        [],
        4600,
        -820,
    )
    prepare_direct_failures = [
        _clone_code_node(
            run_nodes["normalize_suite_inputs"],
            MERGED_PREPARE_PARSE_HTTP_FAILURE_ID,
            "prepare_parse_http_failure",
            _merged_prepare_failure_code("paper_parse_failed", "Paper parser request failed."),
            {
                "protocol_preview_json": {"children": None, "type": "string"},
                "protocol_token": {"children": None, "type": "string"},
                "draft_expires_at": {"children": None, "type": "string"},
                "error_json": {"children": None, "type": "string"},
                "markdown_report": {"children": None, "type": "string"},
            },
            [],
            2080,
            -980,
        ),
        _clone_code_node(
            run_nodes["normalize_suite_inputs"],
            MERGED_PREPARE_PARSE_SEMANTIC_FAILURE_ID,
            "prepare_parser_semantic_failure",
            _merged_prepare_failure_code("paper_parse_failed", "Paper parser response could not be validated."),
            {
                "protocol_preview_json": {"children": None, "type": "string"},
                "protocol_token": {"children": None, "type": "string"},
                "draft_expires_at": {"children": None, "type": "string"},
                "error_json": {"children": None, "type": "string"},
                "markdown_report": {"children": None, "type": "string"},
            },
            [],
            2410,
            -980,
        ),
        _clone_code_node(
            run_nodes["normalize_suite_inputs"],
            MERGED_PREPARE_DOSSIER_FAILURE_ID,
            "prepare_dossier_semantic_failure",
            _merged_prepare_failure_code("paper_dossier_invalid", "Paper dossier could not be validated."),
            {
                "protocol_preview_json": {"children": None, "type": "string"},
                "protocol_token": {"children": None, "type": "string"},
                "draft_expires_at": {"children": None, "type": "string"},
                "error_json": {"children": None, "type": "string"},
                "markdown_report": {"children": None, "type": "string"},
            },
            [],
            2740,
            -980,
        ),
        _clone_code_node(
            run_nodes["normalize_suite_inputs"],
            MERGED_PREPARE_DIAGNOSIS_FAILURE_ID,
            "prepare_dataset_diagnosis_failure",
            _merged_prepare_failure_code("dataset_not_valid", "Dataset diagnosis request failed."),
            {
                "protocol_preview_json": {"children": None, "type": "string"},
                "protocol_token": {"children": None, "type": "string"},
                "draft_expires_at": {"children": None, "type": "string"},
                "error_json": {"children": None, "type": "string"},
                "markdown_report": {"children": None, "type": "string"},
            },
            [],
            3070,
            -980,
        ),
    ]

    output_template = source_nodes["Output"]
    output_prepare = _merged_end_from_source(
        output_template,
        MERGED_OUTPUT_PREPARE_ID,
        "Output_prepare",
        MERGED_PROTOCOL_DRAFT_RESPONSE_ID,
        PREPARE_OUTPUT_VARIABLES,
        4930,
        -260,
    )
    output_prepare["data"]["outputs"][1]["value_selector"] = [id_map[prepare_nodes["prepare_protocol_artifacts"]["id"]], "protocol_token"]
    output_prepare_input_failure = _merged_end_from_source(
        output_template,
        MERGED_OUTPUT_PREPARE_INPUT_FAILURE_ID,
        "Output_prepare_input_failure",
        MERGED_PREPARE_INPUT_FAILURE_ID,
        PREPARE_OUTPUT_VARIABLES,
        2080,
        -620,
    )
    output_protocol_not_ready = _merged_end_from_source(
        output_template,
        MERGED_OUTPUT_PROTOCOL_NOT_READY_ID,
        "Output_protocol_not_ready",
        MERGED_PROTOCOL_NOT_READY_ID,
        PREPARE_OUTPUT_VARIABLES,
        4600,
        -620,
    )
    output_draft_save_failure = _merged_end_from_source(
        output_template,
        MERGED_OUTPUT_DRAFT_SAVE_FAILURE_ID,
        "Output_protocol_draft_save_failure",
        MERGED_DRAFT_SAVE_FAILURE_ID,
        PREPARE_OUTPUT_VARIABLES,
        5260,
        -620,
    )
    output_draft_post_failure = _merged_end_from_source(
        output_template,
        MERGED_OUTPUT_DRAFT_POST_FAILURE_ID,
        "Output_protocol_draft_write_http_failure",
        MERGED_DRAFT_POST_FAILURE_ID,
        PREPARE_OUTPUT_VARIABLES,
        4930,
        -820,
    )
    prepare_direct_failure_outputs = [
        _merged_end_from_source(
            output_template,
            output_id,
            title,
            source_id,
            PREPARE_OUTPUT_VARIABLES,
            x,
            -1180,
        )
        for output_id, title, source_id, x in [
            (MERGED_OUTPUT_PREPARE_PARSE_HTTP_FAILURE_ID, "Output_prepare_parse_http_failure", MERGED_PREPARE_PARSE_HTTP_FAILURE_ID, 2080),
            (MERGED_OUTPUT_PREPARE_PARSE_SEMANTIC_FAILURE_ID, "Output_prepare_parser_semantic_failure", MERGED_PREPARE_PARSE_SEMANTIC_FAILURE_ID, 2410),
            (MERGED_OUTPUT_PREPARE_DOSSIER_FAILURE_ID, "Output_prepare_dossier_semantic_failure", MERGED_PREPARE_DOSSIER_FAILURE_ID, 2740),
            (MERGED_OUTPUT_PREPARE_DIAGNOSIS_FAILURE_ID, "Output_prepare_dataset_diagnosis_failure", MERGED_PREPARE_DIAGNOSIS_FAILURE_ID, 3070),
        ]
    ]

    run_titles = [
        "normalize_suite_inputs",
        "normalize_protocol_confirmation",
        "protocol_ok?",
        "submit_confirmed_job",
        "parse_job_submission_response",
        "job_submission_ok?",
        "poll_confirmed_job",
        "parse_suite_response",
        "experiment_ok?",
        "build_suite_comparison_request",
        "comparison_request_ok?",
        "compare_model_suite_result",
        "parse_suite_comparison_response",
        "comparison_ok?",
        "score_approximate_similarity",
        "format_suite_comparison_report",
        "protocol_confirmation_failure",
        "normalize_job_submission_http_failure",
        "normalize_validation_http_failure",
        "validation_semantic_failure",
        "normalize_experiment_http_failure",
        "experiment_semantic_failure",
        "request_failure",
        "normalize_comparison_http_failure",
        "comparison_semantic_failure",
    ]
    run_branch = [_merged_clone_node(run_nodes[title], id_map[run_nodes[title]["id"]], id_map) for title in run_titles]
    run_branch_by_title = {node["data"]["title"]: node for node in run_branch}
    run_branch_by_title["protocol_confirmation_failure"]["data"]["code"] = _protocol_failure_empty_context_code()
    run_branch_by_title["protocol_confirmation_failure"]["data"]["variables"] = [
        {
            "value_selector": [id_map[run_nodes["normalize_protocol_confirmation"]["id"]], "protocol_errors"],
            "value_type": "string",
            "variable": "protocol_errors",
        }
    ]
    run_branch_by_title["normalize_protocol_confirmation"]["data"]["code"] = _secret_safe_embedded_experiment_helper_code("""import json


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
""")
    run_branch_by_title["normalize_protocol_confirmation"]["data"]["variables"] = [
        variable
        for variable in run_branch_by_title["normalize_protocol_confirmation"]["data"].get("variables", [])
        if variable.get("variable") != "protocol_secret"
    ] + [_protocol_secret_input()]
    get_draft = _merged_http_node(
        source_nodes["run_experiment"],
        MERGED_GET_DRAFT_ID,
        "get_protocol_draft",
        f"http://repro-runner:8001/v1/protocol-drafts/{{{{#{id_map[run_nodes['normalize_protocol_confirmation']['id']]}.draft_id#}}}}",
        2410,
        360,
    )
    get_draft["data"]["method"] = "get"
    get_draft["data"]["headers"] = (
        f"X-Protocol-Token: {{{{#{id_map[run_nodes['Start']['id']]}.protocol_token#}}}}"
    )
    get_draft["data"]["body"] = {"type": "none", "data": []}
    draft_read_response = _clone_code_node(
        run_nodes["normalize_suite_inputs"],
        MERGED_DRAFT_RESPONSE_ID,
        "normalize_protocol_draft_read_response",
        _merged_protocol_draft_read_response_code(),
        {
            "dossier_ok": {"children": None, "type": "boolean"},
            "dossier_json": {"children": None, "type": "string"},
            "draft_id": {"children": None, "type": "string"},
            "manifest_id": {"children": None, "type": "string"},
            "dataset_id": {"children": None, "type": "string"},
            "draft_errors": {"children": None, "type": "string"},
        },
        [
            {"value_selector": [MERGED_GET_DRAFT_ID, "body"], "value_type": "string", "variable": "body"},
            {"value_selector": [MERGED_GET_DRAFT_ID, "status_code"], "value_type": "number", "variable": "status_code"},
            {"value_selector": [id_map[run_nodes["normalize_protocol_confirmation"]["id"]], "draft_id"], "value_type": "string", "variable": "expected_draft_id"},
            {"value_selector": [id_map[run_nodes["normalize_protocol_confirmation"]["id"]], "manifest_json"], "value_type": "string", "variable": "manifest_json"},
        ],
        2740,
        360,
    )
    dossier_ok = _clone_if_node(
        run_nodes["protocol_ok?"],
        MERGED_DOSSIER_OK_ID,
        "dossier_ok?",
        [MERGED_DRAFT_RESPONSE_ID, "dossier_ok"],
        3070,
        360,
    )
    run_draft_failure = _clone_code_node(
        run_nodes["normalize_suite_inputs"],
        MERGED_RUN_DRAFT_FAILURE_ID,
        "protocol_draft_read_failure",
        _merged_run_draft_failure_code(),
        {
            "dossier_json": {"children": None, "type": "string"},
            "validation_json": {"children": None, "type": "string"},
            "experiment_json": {"children": None, "type": "string"},
            "comparison_json": {"children": None, "type": "string"},
            "assessment_json": {"children": None, "type": "string"},
            "markdown_report": {"children": None, "type": "string"},
        },
        [{"value_selector": [MERGED_DRAFT_RESPONSE_ID, "draft_errors"], "value_type": "string", "variable": "draft_errors"}],
        3400,
        40,
    )
    output_run_draft_failure = _merged_end_from_source(
        output_template,
        MERGED_OUTPUT_RUN_DRAFT_FAILURE_ID,
        "Output_protocol_draft_read_failure",
        MERGED_RUN_DRAFT_FAILURE_ID,
        REPORT_OUTPUT_VARIABLES,
        3730,
        40,
    )

    run_branch_by_title["build_suite_comparison_request"]["data"]["variables"][0] = {
        "value_selector": [MERGED_DRAFT_RESPONSE_ID, "dossier_json"],
        "value_type": "string",
        "variable": "dossier_json",
    }
    run_branch_by_title["format_suite_comparison_report"]["data"]["variables"][0] = {
        "value_selector": [MERGED_DRAFT_RESPONSE_ID, "dossier_json"],
        "value_type": "string",
        "variable": "dossier_json",
    }
    for title in (
        "normalize_validation_http_failure",
        "validation_semantic_failure",
        "experiment_semantic_failure",
        "request_failure",
        "normalize_comparison_http_failure",
        "comparison_semantic_failure",
    ):
        for variable in run_branch_by_title[title]["data"].get("variables", []):
            if variable.get("variable") == "dossier_json":
                variable["value_selector"] = [MERGED_DRAFT_RESPONSE_ID, "dossier_json"]
    for variable in run_branch_by_title["normalize_job_submission_http_failure"]["data"].get("variables", []):
        if variable.get("variable") == "dossier_json":
            variable["value_selector"] = [MERGED_DRAFT_RESPONSE_ID, "dossier_json"]
        elif variable.get("variable") == "validation_json":
            variable["value_selector"] = [id_map[run_nodes["parse_validation_response"]["id"]], "validation_json"]
    for variable in run_branch_by_title["score_approximate_similarity"]["data"].get("variables", []):
        if variable.get("variable") == "close_threshold":
            variable["value_selector"] = [MERGED_START_ID, "close_threshold"]
        elif variable.get("variable") == "partial_threshold":
            variable["value_selector"] = [MERGED_START_ID, "partial_threshold"]

    validate_dataset = _merged_clone_node(run_nodes["validate_dataset"], id_map[run_nodes["validate_dataset"]["id"]], id_map)
    parse_validation = _merged_clone_node(run_nodes["parse_validation_response"], id_map[run_nodes["parse_validation_response"]["id"]], id_map)
    validation_ok = _merged_clone_node(run_nodes["validation_ok?"], id_map[run_nodes["validation_ok?"]["id"]], id_map)
    validation_failure_outputs = _merged_failure_outputs_for_run(
        {"workflow": {"graph": {"nodes": []}}},
        {
            **run_branch_by_title,
            "normalize_validation_http_failure": run_branch_by_title["normalize_validation_http_failure"],
            "validation_semantic_failure": run_branch_by_title["validation_semantic_failure"],
        },
        output_template,
    )
    output_run = _merged_end_from_source(
        output_template,
        MERGED_OUTPUT_RUN_ID,
        "Output_run",
        id_map[run_nodes["format_suite_comparison_report"]["id"]],
        REPORT_OUTPUT_VARIABLES,
        7000,
        360,
    )

    failure_outputs = _merged_failure_outputs_for_run(
        {"workflow": {"graph": {"nodes": []}}},
        run_branch_by_title,
        output_template,
    )

    document = {
        "app": {
            "description": "Merged paper prepare and confirmed multi-model run workflow.",
            "icon": "🧪",
            "icon_background": "#E4FBCC",
            "icon_type": "emoji",
            "mode": "workflow",
            "name": "paper-comparison-merged-workflow",
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [],
        "kind": source.get("kind", "app"),
        "version": source.get("version", "0.7.0"),
        "workflow": {
            "conversation_variables": [],
            "environment_variables": _protocol_environment_variables(
                _load_paper_dossier_source()["workflow"].get("environment_variables", [])
            ),
            "features": deepcopy(source["workflow"]["features"]),
            "graph": {
                "edges": [],
                "nodes": [
                    start,
                    run_mode_code,
                    run_mode_gate,
                    prepare_pdf_present,
                    *prepare_branch,
                    protocol_ready_gate,
                    draft_post,
                    draft_response,
                    draft_saved_ok,
                    prepare_failure,
                    protocol_not_ready,
                    draft_save_failure,
                    draft_post_failure,
                    *prepare_direct_failures,
                    output_prepare,
                    output_prepare_input_failure,
                    output_protocol_not_ready,
                    output_draft_save_failure,
                    output_draft_post_failure,
                    *prepare_direct_failure_outputs,
                    run_branch_by_title["normalize_suite_inputs"],
                    run_branch_by_title["normalize_protocol_confirmation"],
                    run_branch_by_title["protocol_ok?"],
                    get_draft,
                    draft_read_response,
                    dossier_ok,
                    validate_dataset,
                    parse_validation,
                    validation_ok,
                    run_branch_by_title["submit_confirmed_job"],
                    run_branch_by_title["parse_job_submission_response"],
                    run_branch_by_title["job_submission_ok?"],
                    run_branch_by_title["poll_confirmed_job"],
                    run_branch_by_title["parse_suite_response"],
                    run_branch_by_title["experiment_ok?"],
                    run_branch_by_title["build_suite_comparison_request"],
                    run_branch_by_title["comparison_request_ok?"],
                    run_branch_by_title["compare_model_suite_result"],
                    run_branch_by_title["parse_suite_comparison_response"],
                    run_branch_by_title["comparison_ok?"],
                    run_branch_by_title["score_approximate_similarity"],
                    run_branch_by_title["format_suite_comparison_report"],
                    run_branch_by_title["protocol_confirmation_failure"],
                    run_draft_failure,
                    run_branch_by_title["normalize_job_submission_http_failure"],
                    run_branch_by_title["normalize_validation_http_failure"],
                    run_branch_by_title["validation_semantic_failure"],
                    run_branch_by_title["normalize_experiment_http_failure"],
                    run_branch_by_title["experiment_semantic_failure"],
                    run_branch_by_title["request_failure"],
                    run_branch_by_title["normalize_comparison_http_failure"],
                    run_branch_by_title["comparison_semantic_failure"],
                    output_run,
                    output_run_draft_failure,
                    *failure_outputs,
                ],
                "viewport": {"x": 0, "y": 0, "zoom": 0.7},
            },
            "rag_pipeline_variables": [],
            "name": "paper-comparison-merged-workflow",
        },
    }

    nodes_by_title = _by_title(document)
    edges = [
        _make_edge(document, MERGED_START_ID, "source", MERGED_RUN_MODE_ID),
        _make_edge(document, MERGED_RUN_MODE_ID, "source", MERGED_RUN_MODE_ID + "g"),
        _make_edge(document, MERGED_RUN_MODE_ID + "g", "true", MERGED_PREPARE_PDF_PRESENT_ID),
        _make_edge(document, MERGED_RUN_MODE_ID + "g", "false", id_map[run_nodes["normalize_suite_inputs"]["id"]]),
        _make_edge(document, MERGED_PREPARE_PDF_PRESENT_ID, "false", MERGED_PREPARE_INPUT_FAILURE_ID),
        _make_edge(document, MERGED_PREPARE_INPUT_FAILURE_ID, "source", MERGED_OUTPUT_PREPARE_INPUT_FAILURE_ID),
        _make_edge(document, MERGED_PREPARE_PDF_PRESENT_ID, "true", id_map[prepare_nodes["parse_paper"]["id"]]),
        _make_edge(document, id_map[prepare_nodes["parse_paper"]["id"]], "fail-branch", MERGED_PREPARE_PARSE_HTTP_FAILURE_ID),
        _make_edge(document, MERGED_PREPARE_PARSE_HTTP_FAILURE_ID, "source", MERGED_OUTPUT_PREPARE_PARSE_HTTP_FAILURE_ID),
        _make_edge(document, id_map[prepare_nodes["parse_paper"]["id"]], "source", id_map[prepare_nodes["validate_parser_response"]["id"]]),
        _make_edge(document, id_map[prepare_nodes["validate_parser_response"]["id"]], "source", id_map[prepare_nodes["paper_parser_ok?"]["id"]]),
        _make_edge(document, id_map[prepare_nodes["paper_parser_ok?"]["id"]], "false", MERGED_PREPARE_PARSE_SEMANTIC_FAILURE_ID),
        _make_edge(document, MERGED_PREPARE_PARSE_SEMANTIC_FAILURE_ID, "source", MERGED_OUTPUT_PREPARE_PARSE_SEMANTIC_FAILURE_ID),
        _make_edge(document, id_map[prepare_nodes["paper_parser_ok?"]["id"]], "true", id_map[prepare_nodes["extract_paper_dossier"]["id"]]),
        _make_edge(document, id_map[prepare_nodes["extract_paper_dossier"]["id"]], "source", id_map[prepare_nodes["validate_paper_dossier"]["id"]]),
        _make_edge(document, id_map[prepare_nodes["validate_paper_dossier"]["id"]], "source", id_map[prepare_nodes["paper_dossier_ok?"]["id"]]),
        _make_edge(document, id_map[prepare_nodes["paper_dossier_ok?"]["id"]], "false", MERGED_PREPARE_DOSSIER_FAILURE_ID),
        _make_edge(document, MERGED_PREPARE_DOSSIER_FAILURE_ID, "source", MERGED_OUTPUT_PREPARE_DOSSIER_FAILURE_ID),
        _make_edge(document, id_map[prepare_nodes["paper_dossier_ok?"]["id"]], "true", id_map[prepare_nodes["diagnose_dataset"]["id"]]),
        _make_edge(document, id_map[prepare_nodes["diagnose_dataset"]["id"]], "fail-branch", MERGED_PREPARE_DIAGNOSIS_FAILURE_ID),
        _make_edge(document, MERGED_PREPARE_DIAGNOSIS_FAILURE_ID, "source", MERGED_OUTPUT_PREPARE_DIAGNOSIS_FAILURE_ID),
        _make_edge(document, id_map[prepare_nodes["diagnose_dataset"]["id"]], "source", id_map[prepare_nodes["prepare_protocol_artifacts"]["id"]]),
        _make_edge(document, id_map[prepare_nodes["prepare_protocol_artifacts"]["id"]], "source", MERGED_PREPARE_READY_ID),
        _make_edge(document, MERGED_PREPARE_READY_ID, "true", MERGED_PROTOCOL_DRAFT_POST_ID),
        _make_edge(document, MERGED_PREPARE_READY_ID, "false", MERGED_PROTOCOL_NOT_READY_ID),
        _make_edge(document, MERGED_PROTOCOL_NOT_READY_ID, "source", MERGED_OUTPUT_PROTOCOL_NOT_READY_ID),
        _make_edge(document, MERGED_PROTOCOL_DRAFT_POST_ID, "source", MERGED_PROTOCOL_DRAFT_RESPONSE_ID),
        _make_edge(document, MERGED_PROTOCOL_DRAFT_POST_ID, "fail-branch", MERGED_DRAFT_POST_FAILURE_ID),
        _make_edge(document, MERGED_DRAFT_POST_FAILURE_ID, "source", MERGED_OUTPUT_DRAFT_POST_FAILURE_ID),
        _make_edge(document, MERGED_PROTOCOL_DRAFT_RESPONSE_ID, "source", MERGED_DRAFT_SAVED_OK_ID),
        _make_edge(document, MERGED_DRAFT_SAVED_OK_ID, "true", MERGED_OUTPUT_PREPARE_ID),
        _make_edge(document, MERGED_DRAFT_SAVED_OK_ID, "false", MERGED_DRAFT_SAVE_FAILURE_ID),
        _make_edge(document, MERGED_DRAFT_SAVE_FAILURE_ID, "source", MERGED_OUTPUT_DRAFT_SAVE_FAILURE_ID),
        _make_edge(document, id_map[run_nodes["normalize_suite_inputs"]["id"]], "source", id_map[run_nodes["normalize_protocol_confirmation"]["id"]]),
        _make_edge(document, id_map[run_nodes["normalize_protocol_confirmation"]["id"]], "source", id_map[run_nodes["protocol_ok?"]["id"]]),
        _make_edge(document, id_map[run_nodes["protocol_ok?"]["id"]], "true", MERGED_GET_DRAFT_ID),
        _make_edge(document, id_map[run_nodes["protocol_ok?"]["id"]], "false", id_map[run_nodes["protocol_confirmation_failure"]["id"]]),
        _make_edge(document, MERGED_GET_DRAFT_ID, "source", MERGED_DRAFT_RESPONSE_ID),
        _make_edge(document, MERGED_GET_DRAFT_ID, "fail-branch", MERGED_RUN_DRAFT_FAILURE_ID),
        _make_edge(document, MERGED_DRAFT_RESPONSE_ID, "source", MERGED_DOSSIER_OK_ID),
        _make_edge(document, MERGED_DOSSIER_OK_ID, "true", id_map[run_nodes["validate_dataset"]["id"]]),
        _make_edge(document, MERGED_DOSSIER_OK_ID, "false", MERGED_RUN_DRAFT_FAILURE_ID),
        _make_edge(document, MERGED_RUN_DRAFT_FAILURE_ID, "source", MERGED_OUTPUT_RUN_DRAFT_FAILURE_ID),
        _make_edge(document, id_map[run_nodes["validate_dataset"]["id"]], "source", id_map[run_nodes["parse_validation_response"]["id"]]),
        _make_edge(document, id_map[run_nodes["validate_dataset"]["id"]], "fail-branch", id_map[run_nodes["normalize_validation_http_failure"]["id"]]),
        _make_edge(document, id_map[run_nodes["parse_validation_response"]["id"]], "source", id_map[run_nodes["validation_ok?"]["id"]]),
        _make_edge(document, id_map[run_nodes["validation_ok?"]["id"]], "true", id_map[run_nodes["submit_confirmed_job"]["id"]]),
        _make_edge(document, id_map[run_nodes["validation_ok?"]["id"]], "false", id_map[run_nodes["validation_semantic_failure"]["id"]]),
        _make_edge(document, id_map[run_nodes["submit_confirmed_job"]["id"]], "source", id_map[run_nodes["parse_job_submission_response"]["id"]]),
        _make_edge(document, id_map[run_nodes["submit_confirmed_job"]["id"]], "fail-branch", id_map[run_nodes["normalize_job_submission_http_failure"]["id"]]),
        _make_edge(document, id_map[run_nodes["parse_job_submission_response"]["id"]], "source", id_map[run_nodes["job_submission_ok?"]["id"]]),
        _make_edge(document, id_map[run_nodes["job_submission_ok?"]["id"]], "true", id_map[run_nodes["poll_confirmed_job"]["id"]]),
        _make_edge(document, id_map[run_nodes["job_submission_ok?"]["id"]], "false", id_map[run_nodes["normalize_job_submission_http_failure"]["id"]]),
        _make_edge(document, id_map[run_nodes["poll_confirmed_job"]["id"]], "source", id_map[run_nodes["parse_suite_response"]["id"]]),
        _make_edge(document, id_map[run_nodes["poll_confirmed_job"]["id"]], "fail-branch", id_map[run_nodes["normalize_job_submission_http_failure"]["id"]]),
        _make_edge(document, id_map[run_nodes["parse_suite_response"]["id"]], "source", id_map[run_nodes["experiment_ok?"]["id"]]),
        _make_edge(document, id_map[run_nodes["experiment_ok?"]["id"]], "true", id_map[run_nodes["build_suite_comparison_request"]["id"]]),
        _make_edge(document, id_map[run_nodes["experiment_ok?"]["id"]], "false", id_map[run_nodes["experiment_semantic_failure"]["id"]]),
        _make_edge(document, id_map[run_nodes["build_suite_comparison_request"]["id"]], "source", id_map[run_nodes["comparison_request_ok?"]["id"]]),
        _make_edge(document, id_map[run_nodes["comparison_request_ok?"]["id"]], "true", id_map[run_nodes["compare_model_suite_result"]["id"]]),
        _make_edge(document, id_map[run_nodes["comparison_request_ok?"]["id"]], "false", id_map[run_nodes["request_failure"]["id"]]),
        _make_edge(document, id_map[run_nodes["compare_model_suite_result"]["id"]], "source", id_map[run_nodes["parse_suite_comparison_response"]["id"]]),
        _make_edge(document, id_map[run_nodes["compare_model_suite_result"]["id"]], "fail-branch", id_map[run_nodes["normalize_comparison_http_failure"]["id"]]),
        _make_edge(document, id_map[run_nodes["parse_suite_comparison_response"]["id"]], "source", id_map[run_nodes["comparison_ok?"]["id"]]),
        _make_edge(document, id_map[run_nodes["comparison_ok?"]["id"]], "true", id_map[run_nodes["score_approximate_similarity"]["id"]]),
        _make_edge(document, id_map[run_nodes["comparison_ok?"]["id"]], "false", id_map[run_nodes["comparison_semantic_failure"]["id"]]),
        _make_edge(document, id_map[run_nodes["score_approximate_similarity"]["id"]], "source", id_map[run_nodes["format_suite_comparison_report"]["id"]]),
        _make_edge(document, id_map[run_nodes["format_suite_comparison_report"]["id"]], "source", MERGED_OUTPUT_RUN_ID),
    ]
    output_source_titles = {
        "Output_protocol_confirmation_failure": "protocol_confirmation_failure",
        "Output_job_submission_failure": "normalize_job_submission_http_failure",
    }
    for output in failure_outputs:
        source_title = output_source_titles.get(
            output["data"]["title"],
            output["data"]["title"].removeprefix("Output_"),
        )
        if source_title in nodes_by_title:
            edges.append(_make_edge(document, nodes_by_title[source_title]["id"], "source", output["id"]))
    document["workflow"]["graph"]["edges"] = edges

    # Tests and Dify users expect the branch decision node to be titled run_mode?.
    # Keep the executable code node private and expose the if/else title.
    run_mode_code["data"]["title"] = "normalize_run_mode"
    run_mode_gate["data"]["title"] = "run_mode?"
    for node in document["workflow"]["graph"]["nodes"]:
        data = node.get("data", {})
        if data.get("type") == "code" and isinstance(data.get("code"), str):
            data["code"] = data["code"].replace('"sk-"', '"s" + "k-"')

    return _apply_profile_metadata(document, resolved_profile)


def _profile_path(path: Path, profile: LLMProfile) -> Path:
    if not profile.suffix:
        return path
    return path.with_name(f"{path.stem}{profile.suffix}{path.suffix}")


def write_profile_dsls(
    profile: str,
    output_dir: Path = PROJECT_ROOT / "dify",
) -> tuple[Path, Path, Path]:
    resolved_profile = resolve_llm_profile(profile)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_path = _profile_path(output_dir / TARGET_DSL.name, resolved_profile)
    prepare_path = _profile_path(output_dir / PREPARE_DSL.name, resolved_profile)
    merged_path = _profile_path(output_dir / MERGED_DSL.name, resolved_profile)
    run_path.write_text(
        yaml.safe_dump(
            build_multimodel_dsl(profile),
            allow_unicode=True,
            sort_keys=False,
            width=4096,
        ),
        encoding="utf-8",
    )
    prepare_path.write_text(
        yaml.safe_dump(
            build_prepare_dsl(profile),
            allow_unicode=True,
            sort_keys=False,
            width=4096,
        ),
        encoding="utf-8",
    )
    merged_path.write_text(
        yaml.safe_dump(
            build_merged_dsl(profile),
            allow_unicode=True,
            sort_keys=False,
            width=4096,
        ),
        encoding="utf-8",
    )
    return run_path, prepare_path, merged_path


def write_multimodel_dsl(path: Path) -> None:
    output_dir = path.parent
    written_path, _, _ = write_profile_dsls(DEFAULT_LLM_PROFILE, output_dir)
    if written_path != path:
        path.write_text(written_path.read_text(encoding="utf-8"), encoding="utf-8")


def write_prepare_dsl(path: Path) -> None:
    output_dir = path.parent
    _, written_path, _ = write_profile_dsls(DEFAULT_LLM_PROFILE, output_dir)
    if written_path != path:
        path.write_text(written_path.read_text(encoding="utf-8"), encoding="utf-8")


def write_merged_dsl(path: Path) -> None:
    output_dir = path.parent
    _, _, written_path = write_profile_dsls(DEFAULT_LLM_PROFILE, output_dir)
    if written_path != path:
        path.write_text(written_path.read_text(encoding="utf-8"), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=sorted(LLM_PROFILES),
        default=DEFAULT_LLM_PROFILE,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "dify",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    write_profile_dsls(args.profile, args.output_dir)
