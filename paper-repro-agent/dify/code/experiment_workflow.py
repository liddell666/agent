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
