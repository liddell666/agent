import json


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
