from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time
import urllib.error
import urllib.request


def normalize_experiment_inputs(drop_duplicates):
    return {"drop_duplicates_text": "true" if drop_duplicates is True else "false"}


def normalize_validation_http_failure():
    validation = {
        "valid": False,
        "errors": [
            {
                "code": "validation_service_unavailable",
                "message": "数据验证服务请求失败，请稍后重试。",
                "stage": "validate_dataset",
            }
        ],
    }
    return {
        "validation_json": json.dumps(validation, ensure_ascii=False),
        "experiment_json": "{}",
        "markdown_summary": "数据验证服务暂时不可用，请稍后重试。",
    }


def normalize_experiment_http_failure(validation_json):
    try:
        validation = (
            json.loads(validation_json)
            if isinstance(validation_json, str)
            else validation_json
        )
    except (TypeError, json.JSONDecodeError):
        validation = {"valid": True}
    if not isinstance(validation, dict):
        validation = {"valid": True}
    experiment = {
        "status": "failed",
        "errors": [
            {
                "code": "experiment_service_unavailable",
                "message": "实验服务请求失败，请稍后重试。",
                "stage": "run_experiment",
            }
        ],
    }
    return {
        "validation_json": json.dumps(validation, ensure_ascii=False),
        "experiment_json": json.dumps(experiment, ensure_ascii=False),
        "markdown_summary": "实验服务暂时不可用，请稍后重试。",
    }


_SUITE_MODEL_NAMES = (
    "logistic_regression",
    "random_forest",
    "xgboost",
    "lightgbm",
    "svm",
    "knn",
    "mlp",
)
_SUITE_WORKFLOW_VERSION = "multimodel-0.8.0"
_SUITE_METRICS = {"roc_auc", "f1", "recall", "balanced_accuracy"}


def _safe_suite_models_json(models_json):
    default = json.dumps(list(_SUITE_MODEL_NAMES), ensure_ascii=False)
    try:
        parsed = json.loads(models_json) if isinstance(models_json, str) else models_json
    except (TypeError, json.JSONDecodeError):
        return default
    if not isinstance(parsed, list) or not parsed:
        return default
    normalized = []
    seen = set()
    for item in parsed:
        if not isinstance(item, str):
            return default
        name = item.strip()
        if name not in _SUITE_MODEL_NAMES or name in seen:
            return default
        seen.add(name)
        normalized.append(name)
    return json.dumps(normalized, ensure_ascii=False)


def _safe_int_text(value, default, minimum, maximum):
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


def _safe_metric_text(value):
    if not isinstance(value, str):
        return "roc_auc"
    metric = value.strip()
    return metric if metric in _SUITE_METRICS else "roc_auc"


def _safe_bool_text(value):
    if value is True or (isinstance(value, str) and value.strip().casefold() == "true"):
        return "true"
    if value is False or (isinstance(value, str) and value.strip().casefold() == "false"):
        return "false"
    return "false"


def normalize_suite_inputs(
    models_json,
    cv_folds,
    optimization_metric,
    n_iter,
    use_gpu,
    drop_duplicates,
):
    return {
        "models_json_text": _safe_suite_models_json(models_json),
        "cv_folds_text": _safe_int_text(cv_folds, 5, 3, 10),
        "optimization_metric_text": _safe_metric_text(optimization_metric),
        "n_iter_text": _safe_int_text(n_iter, 8, 1, 32),
        "use_gpu_text": _safe_bool_text(use_gpu),
        "drop_duplicates_text": _safe_bool_text(drop_duplicates),
    }


_PROTOCOL_TOKEN_VERSION = 1
_PROTOCOL_DEFAULT_TTL_SECONDS = 900
_PROTOCOL_MIN_TTL_SECONDS = 60
_PROTOCOL_MAX_TTL_SECONDS = 3600
_PROTOCOL_SECRET = os.environ.get("DIFY_PROTOCOL_SECRET") or "local-only-fallback-not-for-production"
_PROTOCOL_TOKEN_RE = re.compile(r"^pt1\.([A-Za-z0-9_-]+)\.([0-9a-f]{64})$")
_DRAFT_ID_RE = re.compile(r"^draft-[A-Za-z0-9_-]{8,128}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_JOB_ID_RE = re.compile(r"^job-[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_EXPERIMENT_ID_RE = re.compile(r"^exp-[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_TERMINAL_JOB_STATUSES = {"succeeded", "partial", "failed", "cancelled", "needs_retry"}
_KNOWN_JOB_STATUSES = {"queued", "running", "cancel_requested", *_TERMINAL_JOB_STATUSES}
_SAFE_ERROR_CODES = {
    "protocol_not_confirmed",
    "protocol_token_missing",
    "protocol_token_malformed",
    "protocol_token_expired",
    "protocol_token_tampered",
    "protocol_payload_invalid",
    "protocol_dataset_missing",
    "protocol_target_missing",
    "protocol_target_mismatch",
    "protocol_features_missing",
    "protocol_options_invalid",
    "protocol_draft_not_found",
    "protocol_draft_expired",
    "protocol_draft_token_mismatch",
    "protocol_draft_response_invalid",
    "protocol_draft_read_failed",
    "protocol_draft_write_failed",
    "job_submit_failed",
    "job_submit_rejected",
    "job_response_invalid",
    "job_status_invalid",
    "job_failed",
    "job_cancelled",
    "job_needs_retry",
    "job_result_failed",
    "job_result_invalid",
    "job_transport_failed",
    "poll_timeout",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _object(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_text(value, maximum=128):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > maximum:
        return None
    if any(ord(char) < 32 for char in candidate):
        return None
    lowered = candidate.casefold()
    if lowered.startswith("sk-") or "secret_token" in lowered:
        return None
    if "traceback (most recent call last):" in lowered:
        return None
    return candidate


def _safe_code(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip().casefold()
    return candidate if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", candidate) else None


def _safe_sha(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip().casefold()
    return candidate if _SHA256_RE.fullmatch(candidate) else None


def _safe_draft_id(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _DRAFT_ID_RE.fullmatch(candidate) else None


def _safe_number(value, minimum=None, maximum=None):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if minimum is not None and parsed < minimum:
        return None
    if maximum is not None and parsed > maximum:
        return None
    return parsed


def _safe_int(value, minimum=None, maximum=None):
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    if minimum is not None and parsed < minimum:
        return None
    if maximum is not None and parsed > maximum:
        return None
    return parsed


def _safe_list(value, maximum=256):
    if not isinstance(value, list):
        return []
    result = []
    for raw in value[:maximum]:
        item = _safe_text(raw, 128)
        if item is not None and item not in result:
            result.append(item)
    return result


def _safe_map(value, *, integer=False):
    if not isinstance(value, dict):
        return {}
    result = {}
    for raw_key, raw_value in list(value.items())[:32]:
        key = _safe_text(str(raw_key), 64)
        parsed = _safe_int(raw_value, 0, 1_000_000_000) if integer else _safe_number(raw_value, 0, 1)
        if key is not None and parsed is not None:
            result[key] = parsed
    return result


def _safe_columns(value):
    result = []
    if not isinstance(value, list):
        return result
    for raw in value[:256]:
        if not isinstance(raw, dict):
            continue
        name = _safe_text(raw.get("name"), 128)
        inferred_type = _safe_code(raw.get("inferred_type"))
        if name is None or inferred_type is None:
            continue
        flags = [
            code for item in raw.get("risk_flags", [])[:32]
            if (code := _safe_code(item)) is not None
        ] if isinstance(raw.get("risk_flags"), list) else []
        result.append(
            {
                "name": name,
                "inferred_type": inferred_type,
                "missing_count": _safe_int(raw.get("missing_count"), 0, 1_000_000_000) or 0,
                "unique_count": _safe_int(raw.get("unique_count"), 0, 1_000_000_000) or 0,
                "is_target_candidate": raw.get("is_target_candidate") is True,
                "risk_flags": flags,
            }
        )
    return result


def _safe_metrics(value):
    result = []
    if not isinstance(value, list):
        return result
    for raw in value[:64]:
        if not isinstance(raw, dict):
            continue
        name = _safe_text(raw.get("name") or raw.get("normalized_name"), 64)
        normalized_name = _safe_code(raw.get("normalized_name"))
        if name is None and normalized_name is None:
            continue
        item = {
            "name": name or normalized_name,
            "normalized_name": normalized_name or "unavailable",
            "supported": raw.get("supported") is True,
            "reported_value": _safe_number(raw.get("reported_value"), 0, 1),
        }
        model = raw.get("model")
        if model in _SUITE_MODEL_NAMES:
            item["model"] = model
        threshold = _safe_number(raw.get("threshold"), 0, 1)
        if threshold is not None:
            item["threshold"] = threshold
        for key in ("dataset", "split"):
            value = _safe_text(raw.get(key), 64)
            if value is not None:
                item[key] = value
        source = raw.get("source")
        if source in {"paper_dossier", "manual_override"}:
            item["source"] = source
        evidence = []
        for raw_evidence in raw.get("evidence", [])[:16] if isinstance(raw.get("evidence"), list) else []:
            if not isinstance(raw_evidence, dict):
                continue
            page = _safe_int(raw_evidence.get("page"), 1, 100_000)
            if page is not None:
                evidence.append({"page": page, "source": source or "paper_dossier"})
        if evidence:
            item["evidence"] = evidence
        result.append(item)
    return result


def _safe_dataset(diagnosis):
    raw = diagnosis.get("dataset") if isinstance(diagnosis.get("dataset"), dict) else {}
    result = {}
    for key in ("rows", "effective_rows", "features", "missing_values", "duplicate_rows"):
        value = _safe_int(raw.get(key), 0, 1_000_000_000)
        if value is not None:
            result[key] = value
    target = _safe_text(raw.get("target"), 128)
    if target is not None:
        result["target"] = target
    dataset_id = _safe_sha(raw.get("dataset_id"))
    if dataset_id is not None:
        result["dataset_id"] = dataset_id
    result["class_counts"] = _safe_map(raw.get("class_counts"), integer=True)
    result["class_ratios"] = _safe_map(raw.get("class_ratios"))
    result["column_names"] = _safe_list(raw.get("column_names"))
    column_types = raw.get("column_types")
    result["column_types"] = {
        key: value
        for raw_key, raw_value in list(column_types.items())[:256]
        if (key := _safe_text(str(raw_key), 128)) is not None
        and (value := _safe_code(raw_value)) is not None
    } if isinstance(column_types, dict) else {}
    return result


def _manifest_draft(diagnosis, target_column, protocol_notes):
    dataset = _safe_dataset(diagnosis)
    recommended = diagnosis.get("recommended_options") if isinstance(diagnosis.get("recommended_options"), dict) else {}
    target = _safe_text(target_column, 128) or _safe_text(recommended.get("target_column"), 128) or _safe_text(dataset.get("target"), 128)
    features = _safe_list(recommended.get("feature_columns"))
    if not features:
        features = [name for name in dataset.get("column_names", []) if name != target]
    draft = {
        "manifest_id": "",
        "dataset_id": dataset.get("dataset_id"),
        "target_column": target,
        "feature_columns": features,
        "missing_policy": recommended.get("missing_policy") if recommended.get("missing_policy") in {"reject", "drop_rows", "impute"} else "reject",
        "sampling_strategy": recommended.get("sampling_strategy") if recommended.get("sampling_strategy") in {"original", "class_weight", "balanced_undersample"} else "original",
        "comparison_mode": recommended.get("comparison_mode") if recommended.get("comparison_mode") in {"paper_comparable", "real_world"} else "paper_comparable",
        "test_size": _safe_number(recommended.get("test_size"), 0.1, 0.5) or 0.2,
        "random_state": _safe_int(recommended.get("random_state"), 0, 2_147_483_647) or 42,
        "cv_folds": _safe_int(recommended.get("cv_folds"), 3, 10) or 5,
        "optimization_metric": recommended.get("optimization_metric") if recommended.get("optimization_metric") in _SUITE_METRICS else "roc_auc",
        "threshold": _safe_number(recommended.get("threshold"), 0, 1) if _safe_number(recommended.get("threshold"), 0, 1) is not None else 0.5,
        "models": list(_SUITE_MODEL_NAMES),
        "workflow_version": _SUITE_WORKFLOW_VERSION,
    }
    notes = _safe_text(protocol_notes, 512)
    canonical = dict(draft)
    canonical["manifest_id"] = ""
    draft["manifest_id"] = "sha256:" + hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
    return draft, dataset, notes


def _token_key(secret):
    return (secret if isinstance(secret, str) and secret else _PROTOCOL_SECRET).encode("utf-8")[:256]


def _token_encode(payload, secret):
    encoded = base64.urlsafe_b64encode(_json(payload).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_token_key(secret), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"pt1.{encoded}.{signature}"


def _token_decode(token, secret):
    if not isinstance(token, str):
        return None, "protocol_token_missing"
    match = _PROTOCOL_TOKEN_RE.fullmatch(token.strip())
    if match is None:
        return None, "protocol_token_malformed"
    encoded, supplied = match.groups()
    expected = hmac.new(_token_key(secret), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return None, "protocol_token_tampered"
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode((encoded + padding).encode("ascii")))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None, "protocol_token_malformed"
    return payload if isinstance(payload, dict) else None, None


def _protocol_error(code):
    safe_code = code if code in _SAFE_ERROR_CODES else "protocol_payload_invalid"
    return {"code": safe_code, "message": "The confirmed protocol is not valid."}


def _protocol_unresolved_code(code):
    return {
        "dataset_not_valid": "protocol_payload_invalid",
        "dataset_id": "protocol_dataset_missing",
        "target_column": "protocol_target_missing",
        "feature_columns": "protocol_features_missing",
        "sampling_strategy": "protocol_options_invalid",
    }.get(code, "protocol_payload_invalid")


def _draft_error(code, action):
    safe_code = code if code in _SAFE_ERROR_CODES else "protocol_payload_invalid"
    verb = "saved" if action == "save" else "read"
    return {"code": safe_code, "message": f"Protocol draft could not be {verb}."}


def _new_draft_id():
    return "draft-" + secrets.token_urlsafe(16)


def _validate_manifest(manifest):
    if not isinstance(manifest, dict):
        return ["protocol_payload_invalid"]
    errors = []
    if (
        "workflow_version" in manifest
        and _safe_text(manifest.get("workflow_version"), 128) is None
    ):
        errors.append("protocol_options_invalid")
    if _safe_sha(manifest.get("manifest_id")) is None:
        errors.append("protocol_payload_invalid")
    if _safe_sha(manifest.get("dataset_id")) is None:
        errors.append("protocol_dataset_missing")
    target = _safe_text(manifest.get("target_column"), 128)
    raw_features = manifest.get("feature_columns")
    features = _safe_list(raw_features)
    if target is None:
        errors.append("protocol_target_missing")
    if (
        not isinstance(raw_features, list)
        or not features
        or target in features
        or len(features) != len(raw_features)
    ):
        errors.append("protocol_features_missing")
    if manifest.get("missing_policy") not in {"reject", "drop_rows", "impute"}:
        errors.append("protocol_options_invalid")
    if manifest.get("sampling_strategy") not in {"original", "class_weight", "balanced_undersample"}:
        errors.append("protocol_options_invalid")
    if manifest.get("comparison_mode") not in {"paper_comparable", "real_world"}:
        errors.append("protocol_options_invalid")
    if _safe_number(manifest.get("test_size"), 0.1, 0.5) is None:
        errors.append("protocol_options_invalid")
    if _safe_int(manifest.get("random_state"), 0) is None:
        errors.append("protocol_options_invalid")
    if _safe_int(manifest.get("cv_folds"), 3, 10) is None:
        errors.append("protocol_options_invalid")
    if manifest.get("optimization_metric") not in _SUITE_METRICS:
        errors.append("protocol_options_invalid")
    if _safe_number(manifest.get("threshold"), 0, 1) is None:
        errors.append("protocol_options_invalid")
    models = manifest.get("models")
    if not isinstance(models, list) or not models or any(model not in _SUITE_MODEL_NAMES for model in models) or len(models) != len(set(models)):
        errors.append("protocol_options_invalid")
    return list(dict.fromkeys(errors))


def prepare_protocol_artifacts(
    dossier_json,
    diagnosis_json,
    target_column=None,
    protocol_notes=None,
    *,
    secret=_PROTOCOL_SECRET,
    now=None,
    ttl_seconds=_PROTOCOL_DEFAULT_TTL_SECONDS,
):
    dossier = _object(dossier_json)
    diagnosis = _object(diagnosis_json)
    manifest, dataset, notes = _manifest_draft(diagnosis, target_column, protocol_notes)
    unresolved = []
    if diagnosis.get("valid") is not True:
        unresolved.append("dataset_not_valid")
    if _safe_sha(manifest.get("dataset_id")) is None:
        unresolved.append("dataset_id")
    if _safe_text(manifest.get("target_column"), 128) is None:
        unresolved.append("target_column")
    if not manifest.get("feature_columns"):
        unresolved.append("feature_columns")
    if manifest.get("sampling_strategy") == "balanced_undersample":
        unresolved.append("sampling_strategy")
    ttl = _safe_int(ttl_seconds, 1, 86_400) or _PROTOCOL_DEFAULT_TTL_SECONDS
    ttl = min(_PROTOCOL_MAX_TTL_SECONDS, max(_PROTOCOL_MIN_TTL_SECONDS, ttl))
    current = _safe_number(now)
    if current is None:
        current = time.time()
    expires_at = int(current + ttl)
    ready = not unresolved
    draft_id = _new_draft_id()
    unresolved_codes = list(dict.fromkeys(_protocol_unresolved_code(code) for code in unresolved))
    preview = {
        "protocol_version": _PROTOCOL_TOKEN_VERSION,
        "expires_in_seconds": ttl,
        "protocol_notes_digest": (
            "sha256:" + hashlib.sha256(notes.encode("utf-8")).hexdigest()
            if notes
            else None
        ),
        "dataset": dataset,
        "columns": _safe_columns(diagnosis.get("columns")),
        "target_candidates": _safe_list(diagnosis.get("target_candidates")),
        "risk_flags": [code for item in diagnosis.get("risk_flags", []) if (code := _safe_code(item)) is not None] if isinstance(diagnosis.get("risk_flags"), list) else [],
        "warnings": [_safe_text(item, 256) for item in diagnosis.get("warnings", []) if _safe_text(item, 256) is not None] if isinstance(diagnosis.get("warnings"), list) else [],
        "paper_summary": {
            "title": _safe_text(dossier.get("title"), 256) or "Unnamed paper",
            "metrics": _safe_metrics(dossier.get("metrics")),
        },
        "manifest_draft": manifest,
        "unresolved_protocol_fields": unresolved_codes,
    }
    payload = {
        "v": _PROTOCOL_TOKEN_VERSION,
        "exp": expires_at,
        "ready": ready,
        "draft_id": draft_id,
        "notes_digest": preview["protocol_notes_digest"],
        "manifest": manifest,
    }
    return {
        "protocol_preview_json": _json(preview),
        "protocol_token": _token_encode(payload, secret) if ready else "",
        "draft_id": draft_id if ready else "",
        "draft_expires_at": str(expires_at) if ready else "",
        "protocol_ready": ready,
        "protocol_errors": _json([_protocol_error(code) for code in unresolved_codes]),
    }


def normalize_protocol_confirmation(
    protocol_token,
    confirm_protocol,
    *,
    confirmed_options=None,
    secret=_PROTOCOL_SECRET,
    now=None,
):
    errors = []
    if confirm_protocol is not True:
        errors.append("protocol_not_confirmed")
    payload, token_error = _token_decode(protocol_token, secret)
    if token_error is not None:
        errors.append(token_error)
    current = _safe_number(now)
    if current is None:
        current = time.time()
    manifest = None
    draft_id = None
    draft_manifest_id = None
    if payload is not None:
        expires_at = _safe_int(payload.get("exp"), 1)
        draft_id = _safe_draft_id(payload.get("draft_id"))
        if (
            payload.get("v") != _PROTOCOL_TOKEN_VERSION
            or payload.get("ready") is not True
            or expires_at is None
            or draft_id is None
        ):
            errors.append("protocol_payload_invalid")
        elif current >= expires_at:
            errors.append("protocol_token_expired")
        manifest = payload.get("manifest")
        if isinstance(manifest, dict) and isinstance(confirmed_options, dict):
            draft_manifest_id = _safe_sha(manifest.get("manifest_id"))
            manifest = dict(manifest)
            confirmed_target = _safe_text(confirmed_options.get("target_column"), 128)
            if confirmed_target is not None and confirmed_target != manifest.get("target_column"):
                errors.append("protocol_target_mismatch")
            try:
                models = json.loads(confirmed_options.get("models_json")) if isinstance(confirmed_options.get("models_json"), str) else confirmed_options.get("models_json")
            except (TypeError, json.JSONDecodeError):
                models = None
            if isinstance(models, list) and models:
                manifest["models"] = models
            cv_folds = _safe_int(confirmed_options.get("cv_folds"), 3, 10)
            if cv_folds is not None:
                manifest["cv_folds"] = cv_folds
            metric = confirmed_options.get("optimization_metric")
            if metric in _SUITE_METRICS:
                manifest["optimization_metric"] = metric
            test_size = _safe_number(confirmed_options.get("test_size"), 0.1, 0.5)
            if test_size is not None:
                manifest["test_size"] = test_size
            random_state = _safe_int(
                confirmed_options.get("random_state"), 0, 2_147_483_647
            )
            if random_state is not None:
                manifest["random_state"] = random_state
            manifest_for_id = dict(manifest)
            manifest_for_id["manifest_id"] = ""
            manifest["manifest_id"] = "sha256:" + hashlib.sha256(_json(manifest_for_id).encode("utf-8")).hexdigest()
        errors.extend(_validate_manifest(manifest))
    if errors:
        safe_errors = [_protocol_error(code) for code in dict.fromkeys(errors)]
        return {"protocol_ok": False, "manifest_json": "{}", "draft_id": "", "draft_manifest_id": "", "protocol_errors": _json(safe_errors)}
    if draft_manifest_id is None and isinstance(manifest, dict):
        draft_manifest_id = _safe_sha(manifest.get("manifest_id"))
    return {"protocol_ok": True, "manifest_json": _json(manifest), "draft_id": draft_id, "draft_manifest_id": draft_manifest_id or "", "protocol_errors": "[]"}


def normalize_protocol_draft_write_response(body, status_code, expected_draft_id, expected_manifest_json=None):
    errors = []
    if status_code in {404, 410}:
        errors.append("protocol_draft_expired" if status_code == 410 else "protocol_draft_not_found")
    elif status_code in {401, 403, 409, 422}:
        errors.append("protocol_draft_token_mismatch")
    elif not isinstance(status_code, int) or status_code < 200 or status_code >= 300:
        errors.append("protocol_draft_write_failed")
    response = _object(body)
    expected = _object(expected_manifest_json)
    if not errors:
        if response.get("draft_id") != expected_draft_id or _safe_draft_id(expected_draft_id) is None:
            errors.append("protocol_draft_token_mismatch")
        response_manifest_id = _safe_sha(response.get("manifest_id"))
        response_dataset_id = _safe_sha(response.get("dataset_id"))
        expected_manifest_id = _safe_sha(expected.get("manifest_id"))
        expected_dataset_id = _safe_sha(expected.get("dataset_id"))
        if response_manifest_id is None:
            errors.append("protocol_draft_response_invalid")
        if response_dataset_id is None:
            errors.append("protocol_draft_response_invalid")
        if expected_manifest_id is None or expected_dataset_id is None:
            errors.append("protocol_draft_token_mismatch")
        elif (
            response_manifest_id != expected_manifest_id
            or response_dataset_id != expected_dataset_id
        ):
            errors.append("protocol_draft_token_mismatch")
        expires_at = _safe_int(response.get("expires_at"), 1)
        if expires_at is None:
            errors.append("protocol_draft_response_invalid")
    if errors:
        safe = [_draft_error(code, "save") for code in dict.fromkeys(errors)]
        return {
            "draft_saved_ok": False,
            "draft_id": "",
            "manifest_id": "",
            "dataset_id": "",
            "draft_expires_at": "",
            "draft_errors": _json(safe),
        }
    return {
        "draft_saved_ok": True,
        "draft_id": expected_draft_id,
        "manifest_id": response["manifest_id"],
        "dataset_id": response["dataset_id"],
        "draft_expires_at": str(expires_at),
        "draft_errors": "[]",
    }


def normalize_protocol_draft_read_response(
    body,
    status_code,
    expected_draft_id,
    manifest_json,
    expected_draft_manifest_id=None,
):
    errors = []
    if status_code == 404:
        errors.append("protocol_draft_not_found")
    elif status_code == 410:
        errors.append("protocol_draft_expired")
    elif status_code in {401, 403, 409, 422}:
        errors.append("protocol_draft_token_mismatch")
    elif not isinstance(status_code, int) or status_code < 200 or status_code >= 300:
        errors.append("protocol_draft_read_failed")
    response = _object(body)
    expected = _object(manifest_json)
    if not errors:
        if response.get("draft_id") != expected_draft_id or _safe_draft_id(expected_draft_id) is None:
            errors.append("protocol_draft_token_mismatch")
        draft_manifest_id = (
            _safe_sha(expected_draft_manifest_id)
            if expected_draft_manifest_id is not None
            else _safe_sha(expected.get("manifest_id"))
        )
        if draft_manifest_id is None or response.get("manifest_id") != draft_manifest_id:
            errors.append("protocol_draft_token_mismatch")
        if response.get("dataset_id") != expected.get("dataset_id"):
            errors.append("protocol_draft_token_mismatch")
        if not isinstance(response.get("dossier"), dict):
            errors.append("protocol_draft_response_invalid")
    if errors:
        safe = [_draft_error(code, "read") for code in dict.fromkeys(errors)]
        return {
            "dossier_ok": False,
            "dossier_json": "{}",
            "draft_id": "",
            "manifest_id": "",
            "dataset_id": "",
            "draft_errors": _json(safe),
        }
    return {
        "dossier_ok": True,
        "dossier_json": _json(response["dossier"]),
        "draft_id": expected_draft_id,
        "manifest_id": response["manifest_id"],
        "dataset_id": response["dataset_id"],
        "draft_errors": "[]",
    }


def _safe_job(value):
    if not isinstance(value, dict):
        return None
    job_id = value.get("job_id")
    status = value.get("status")
    if not isinstance(job_id, str) or _JOB_ID_RE.fullmatch(job_id) is None or status not in _KNOWN_JOB_STATUSES:
        return None
    result = {"job_id": job_id, "status": status}
    for key in ("stage", "result_id"):
        candidate = value.get(key)
        if not isinstance(candidate, str) or len(candidate) > 128 or any(ord(char) < 32 for char in candidate):
            continue
        if key == "result_id" and _EXPERIMENT_ID_RE.fullmatch(candidate) is None:
            continue
        result[key] = candidate
    return result


def _safe_result(value, job_id, status):
    if not isinstance(value, dict):
        return None
    result = {"job_id": job_id, "job_status": status, "status": status}
    experiment_id = value.get("experiment_id")
    if isinstance(experiment_id, str) and _EXPERIMENT_ID_RE.fullmatch(experiment_id):
        result["experiment_id"] = experiment_id
    elif not isinstance(value.get("results"), list):
        return None
    if isinstance(value.get("results"), list):
        safe_results = []
        for raw in value["results"][:64]:
            if not isinstance(raw, dict) or raw.get("model") not in _SUITE_MODEL_NAMES:
                continue
            item = {"model": raw["model"], "status": raw.get("status") if raw.get("status") in {"succeeded", "failed", "unavailable"} else "unavailable"}
            score = _safe_number(raw.get("cv_best_score"), 0, 1)
            if score is not None:
                item["cv_best_score"] = score
            metrics = raw.get("metrics")
            if isinstance(metrics, dict):
                item["metrics"] = {key: score for key in ("roc_auc", "accuracy", "balanced_accuracy", "precision", "recall", "f1") if (score := _safe_number(metrics.get(key), 0, 1)) is not None}
            for metric_key in ("cv_mean", "cv_std"):
                distribution = raw.get(metric_key)
                if isinstance(distribution, dict):
                    item[metric_key] = {
                        key: score
                        for key in ("roc_auc", "accuracy", "balanced_accuracy", "precision", "recall", "f1")
                        if (score := _safe_number(distribution.get(key), 0, 1)) is not None
                    }
            seed_means = raw.get("seed_means")
            if isinstance(seed_means, dict):
                item["seed_means"] = {
                    key: [
                        score
                        for value in values
                        if (score := _safe_number(value, 0, 1)) is not None
                    ]
                    for key, values in seed_means.items()
                    if key in ("roc_auc", "accuracy", "balanced_accuracy", "precision", "recall", "f1") and isinstance(values, list)
                }
            if isinstance(raw.get("error"), dict):
                item["error"] = {"code": _safe_code(raw["error"].get("code")) or "unavailable", "message": "Model result is unavailable."}
            safe_results.append(item)
        result["results"] = safe_results
    ranking = value.get("performance_ranking")
    if isinstance(ranking, list):
        result["performance_ranking"] = [model for model in ranking[:64] if model in _SUITE_MODEL_NAMES]
    return result


def _error_result(code, job_id=None, status="failed"):
    result = {"status": status, "errors": [{"code": code, "message": "The asynchronous experiment did not complete."}]}
    if isinstance(job_id, str) and _JOB_ID_RE.fullmatch(job_id):
        result["job_id"] = job_id
    return {"experiment_ok": False, "experiment_json": _json(result), "experiment_errors": _json(result["errors"])}


def _csv_bytes(value):
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, dict) and isinstance(value.get("bytes"), bytes):
        return value["bytes"]
    if isinstance(value, dict) and isinstance(value.get("path"), str):
        try:
            with open(value["path"], "rb") as handle:
                return handle.read(16 * 1024 * 1024 + 1)
        except (OSError, ValueError):
            return None
    return None


def _request(method, url, manifest_json, csv_bytes=None):
    data = None
    headers = {"Accept": "application/json"}
    if method == "POST":
        boundary = "----repro-protocol-boundary"
        data = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"manifest_json\"\r\n\r\n{manifest_json}\r\n".encode("utf-8")
        )
        if csv_bytes is not None:
            data += f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"training.csv\"\r\nContent-Type: text/csv\r\n\r\n".encode("utf-8") + csv_bytes + b"\r\n"
        data += f"--{boundary}--\r\n".encode("ascii")
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read(2 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:
        return 599, ""


def _poll_submitted_job_until_terminal(
    base_url,
    job,
    *,
    request_func=None,
    sleep_func=None,
    poll_interval_seconds=0.5,
    max_polls=60,
):
    if not isinstance(base_url, str) or re.fullmatch(r"https?://[A-Za-z0-9._:-]{1,200}", base_url.rstrip("/")) is None:
        return _error_result("job_transport_failed")
    safe_job = _safe_job(job)
    if safe_job is None:
        return _error_result("job_response_invalid")
    try:
        interval = min(10.0, max(0.1, float(poll_interval_seconds)))
    except (TypeError, ValueError):
        interval = 0.5
    polls = min(120, max(1, _safe_int(max_polls, 1) or 60))
    requester = request_func or (lambda method, url, payload=None: _request(method, url, "{}"))
    sleeper = sleep_func or time.sleep
    root = base_url.rstrip("/")
    job_id = safe_job["job_id"]
    for attempt in range(polls):
        try:
            status_code, body = requester("GET", root + f"/v1/jobs/{job_id}")
        except Exception:
            return _error_result("job_transport_failed", job_id)
        current = _safe_job(_object(body)) if status_code == 200 else None
        if current is None:
            return _error_result("job_status_invalid", job_id)
        status = current["status"]
        if status in _TERMINAL_JOB_STATUSES:
            if status == "failed":
                return _error_result("job_failed", job_id, status)
            if status == "cancelled":
                return _error_result("job_cancelled", job_id, status)
            if status == "needs_retry":
                return _error_result("job_needs_retry", job_id, status)
            try:
                result_code, result_body = requester("GET", root + f"/v1/jobs/{job_id}/result")
            except Exception:
                return _error_result("job_transport_failed", job_id, status)
            result = _safe_result(_object(result_body), job_id, status) if result_code == 200 else None
            if result is None:
                return _error_result("job_result_failed", job_id, status)
            result_id = current.get("result_id")
            if "experiment_id" not in result and isinstance(result_id, str):
                result["experiment_id"] = result_id
            return {"experiment_ok": True, "experiment_json": _json(result), "experiment_errors": "[]"}
        if attempt + 1 < polls:
            try:
                sleeper(interval)
            except Exception:
                return _error_result("job_transport_failed", job_id, status)
    return _error_result("poll_timeout", job_id, "running")


def poll_submitted_job_until_terminal(
    base_url,
    job_response_json,
    *,
    request_func=None,
    sleep_func=None,
    poll_interval_seconds=0.5,
    max_polls=60,
):
    job = _object(job_response_json)
    if not isinstance(job_response_json, (str, dict)) or _safe_job(job) is None:
        return _error_result("job_response_invalid")
    return _poll_submitted_job_until_terminal(
        base_url,
        job,
        request_func=request_func,
        sleep_func=sleep_func,
        poll_interval_seconds=poll_interval_seconds,
        max_polls=max_polls,
    )


def poll_job_until_terminal(
    base_url,
    manifest_json,
    training_csv=None,
    *,
    request_func=None,
    sleep_func=None,
    poll_interval_seconds=0.5,
    max_polls=60,
):
    if not isinstance(manifest_json, str) or not _object(manifest_json):
        return _error_result("protocol_payload_invalid")
    if not isinstance(base_url, str) or re.fullmatch(r"https?://[A-Za-z0-9._:-]{1,200}", base_url.rstrip("/")) is None:
        return _error_result("job_transport_failed")
    try:
        interval = min(10.0, max(0.1, float(poll_interval_seconds)))
    except (TypeError, ValueError):
        interval = 0.5
    polls = min(120, max(1, _safe_int(max_polls, 1) or 60))
    csv_bytes = _csv_bytes(training_csv)
    requester = request_func or (lambda method, url, payload=None: _request(method, url, payload or manifest_json, csv_bytes))
    sleeper = sleep_func or time.sleep
    root = base_url.rstrip("/")
    try:
        status_code, body = requester("POST", root + "/v1/jobs", manifest_json)
    except Exception:
        return _error_result("job_transport_failed")
    job = _safe_job(_object(body))
    if status_code not in {200, 202} or job is None:
        return _error_result("job_submit_rejected")
    return _poll_submitted_job_until_terminal(
        base_url,
        job,
        request_func=requester,
        sleep_func=sleeper,
        poll_interval_seconds=interval,
        max_polls=polls,
    )
