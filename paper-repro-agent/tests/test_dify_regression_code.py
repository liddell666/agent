from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import time


REGRESSION_MODELS = [
    "linear_regression",
    "random_forest",
    "gradient_boosting",
    "xgboost",
]
SENTINELS = (
    "RAW_CSV_ROW_41,private-value",
    "PDF_BODY_SENTINEL_8021",
    "WORKFLOW_SECRET_9137",
    "Traceback (most recent call last): C:\\private\\rows.csv",
)


def _document() -> dict:
    module = importlib.import_module("scripts.build_regression_dsl")
    return module.build_regression_dsl("deepseek")


def _exec_code_node(title: str):
    node = next(
        node
        for node in _document()["workflow"]["graph"]["nodes"]
        if node["data"]["title"] == title
    )
    namespace: dict[str, object] = {}
    exec(compile(node["data"]["code"], f"<regression-dify:{title}>", "exec"), namespace)
    return namespace["main"]


def _resign_token(token: str, secret: str, mutation) -> str:
    _, encoded, _ = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    mutation(payload)
    changed = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), changed.encode("ascii"), hashlib.sha256).hexdigest()
    return f"pt1.{changed}.{signature}"


def _prepared_protocol(secret: str = SENTINELS[2]) -> dict:
    return _exec_code_node("prepare_protocol_artifacts")(
        json.dumps(
            {
                "title": "Synthetic regression paper",
                "task_type": "regression",
                "metrics": [
                    {
                        "name": "RMSE",
                        "normalized_name": "rmse",
                        "supported": True,
                        "reported_value": 2.0,
                        "evidence": [{"page": 1, "source_text": SENTINELS[1]}],
                    }
                ],
            }
        ),
        json.dumps(
            {
                "valid": True,
                "task_type": "regression",
                "dataset": {
                    "dataset_id": "sha256:" + "a" * 64,
                    "rows": 20,
                    "effective_rows": 20,
                    "features": 2,
                    "target": "target",
                    "column_names": ["x", "region", "target"],
                },
                "columns": [],
                "target_candidates": ["target"],
                "recommended_options": {
                    "target_column": "target",
                    "feature_columns": ["x", "region"],
                    "sampling_strategy": "original",
                    "optimization_metric": "rmse",
                    "cv_folds": 5,
                },
                "warnings": [SENTINELS[0]],
            }
        ),
        "target",
        SENTINELS[1],
        secret,
    )


def test_normalize_regression_inputs_returns_exact_safe_defaults_and_task() -> None:
    result = _exec_code_node("normalize_suite_inputs")(
        json.dumps(["linear_regression", "random_forest"]),
        5,
        "mae",
        8,
        False,
        True,
    )

    assert result == {
        "models_json_text": '["linear_regression", "random_forest"]',
        "cv_folds_text": "5",
        "optimization_metric_text": "mae",
        "n_iter_text": "8",
        "use_gpu_text": "false",
        "drop_duplicates_text": "true",
        "task_type_text": "regression",
    }


def test_malformed_regression_inputs_use_safe_defaults_without_echoing() -> None:
    raw = f'{{"model":"{SENTINELS[0]}"}}'
    result = _exec_code_node("normalize_suite_inputs")(
        raw,
        "999",
        SENTINELS[2],
        -1,
        "yes",
        "yes",
    )

    assert json.loads(result["models_json_text"]) == REGRESSION_MODELS
    assert result["cv_folds_text"] == "5"
    assert result["optimization_metric_text"] == "rmse"
    assert result["n_iter_text"] == "8"
    assert result["use_gpu_text"] == "false"
    assert result["drop_duplicates_text"] == "false"
    assert result["task_type_text"] == "regression"
    assert all(sentinel not in json.dumps(result) for sentinel in SENTINELS)


def test_prepare_and_confirmation_keep_task_exactly_regression_and_private() -> None:
    prepared = _prepared_protocol()
    confirmed = _exec_code_node("normalize_protocol_confirmation")(
        prepared["protocol_token"],
        True,
        "target",
        json.dumps(REGRESSION_MODELS),
        "5",
        "rmse",
        0.2,
        42,
        SENTINELS[2],
    )

    assert prepared["protocol_ready"] is True
    assert confirmed["protocol_ok"] is True
    manifest = json.loads(confirmed["manifest_json"])
    assert manifest["task_type"] == "regression"
    assert manifest["models"] == REGRESSION_MODELS
    assert manifest["optimization_metric"] == "rmse"
    assert manifest["threshold"] is None
    assert manifest["sampling_strategy"] == "original"
    assert manifest["workflow_version"] == "regression-1.0.0"
    combined = json.dumps({"prepared": prepared, "confirmed": confirmed}, ensure_ascii=False)
    assert all(sentinel not in combined for sentinel in SENTINELS)


def test_manifest_confirmation_rejects_any_altered_task_type() -> None:
    prepared = _prepared_protocol()
    changed = _resign_token(
        prepared["protocol_token"],
        SENTINELS[2],
        lambda payload: payload["manifest"].update({"task_type": "binary_classification"}),
    )

    confirmed = _exec_code_node("normalize_protocol_confirmation")(
        changed,
        True,
        "target",
        json.dumps(REGRESSION_MODELS),
        "5",
        "rmse",
        0.2,
        42,
        SENTINELS[2],
    )

    assert confirmed["protocol_ok"] is False
    assert confirmed["manifest_json"] == "{}"
    assert json.loads(confirmed["protocol_errors"]) == [
        {"code": "protocol_options_invalid", "message": "The confirmed protocol is not valid."}
    ]


def test_regression_comparison_request_accepts_natural_values_and_aliases_only() -> None:
    result = _exec_code_node("build_suite_comparison_request")(
        json.dumps(
            {
                "task_type": "regression",
                "metrics": [
                    {"name": "MAE", "supported": True, "reported_value": 1.5},
                    {"name": "Root Mean Squared Error", "supported": True, "reported_value": 2.0},
                    {"name": "R²", "supported": True, "reported_value": -0.25},
                    {"name": "AUC", "supported": True, "reported_value": 0.99},
                    {"name": SENTINELS[1], "supported": True, "reported_value": 3.0},
                ],
            },
            ensure_ascii=False,
        ),
        json.dumps({"experiment_id": "exp-regression-request", "task_type": "regression"}),
    )

    assert result["suite_comparison_request_ok"] is True
    request = json.loads(result["suite_comparison_request_json"])
    assert request == {
        "experiment_id": "exp-regression-request",
        "reported_metrics": [
            {"name": "mae", "reported_value": 1.5},
            {"name": "rmse", "reported_value": 2.0},
            {"name": "r2", "reported_value": -0.25},
        ],
    }
    assert all(sentinel not in json.dumps(result, ensure_ascii=False) for sentinel in SENTINELS)


def test_regression_suite_parser_rejects_missing_or_altered_task_without_echoing() -> None:
    parser = _exec_code_node("parse_suite_response")
    for task_type in (None, "binary_classification", SENTINELS[0]):
        result = parser(
            json.dumps(
                {
                    "experiment_id": "exp-wrong-task",
                    "status": "succeeded",
                    "task_type": task_type,
                    "config": {"task_type": task_type},
                    "results": [],
                    "raw_rows": SENTINELS[0],
                }
            )
        )
        assert result["experiment_ok"] is False
        assert json.loads(result["experiment_json"])["errors"] == [
            {"code": "invalid_regression_suite_response", "message": "Regression suite response is invalid."}
        ]
        assert all(sentinel not in json.dumps(result) for sentinel in SENTINELS)


def test_regression_report_uses_natural_metrics_both_rankings_and_redacts_outputs() -> None:
    result = _exec_code_node("format_suite_comparison_report")(
        json.dumps(
            {
                "title": "Synthetic regression paper",
                "metrics": [{"name": "RMSE", "evidence": [{"source_text": SENTINELS[1]}]}],
            }
        ),
        json.dumps(
            {
                "valid": True,
                "task_type": "regression",
                "dataset": {"rows": 20, "target": "target"},
                "raw_rows": SENTINELS[0],
            }
        ),
        json.dumps(
            {
                "experiment_id": "exp-regression-report",
                "job_id": "job-regression-report",
                "status": "partial",
                "task_type": "regression",
                "config": {"task_type": "regression", "optimization_metric": "rmse", "cv_folds": 5},
                "performance_ranking": ["random_forest", "linear_regression"],
                "results": [
                    {
                        "model": "random_forest",
                        "status": "succeeded",
                        "metrics": {"mae": 0.8, "rmse": 1.1, "r2": 0.1},
                    },
                    {
                        "model": "linear_regression",
                        "status": "succeeded",
                        "metrics": {"mae": 1.5, "rmse": 2.0, "r2": -0.25},
                    },
                    {
                        "model": "xgboost",
                        "status": "failed",
                        "error": {"code": "model_failed", "message": SENTINELS[3]},
                    },
                ],
                "raw_rows": SENTINELS[0],
            }
        ),
        json.dumps(
            {
                "experiment_id": "exp-regression-report",
                "paper_closeness_ranking": ["linear_regression", "random_forest"],
                "items": [
                    {
                        "model": "linear_regression",
                        "name": "rmse",
                        "paper_value": 2.0,
                        "independent_value": 2.0,
                        "absolute_difference": 0.0,
                        "comparable": True,
                        "reason": SENTINELS[1],
                    }
                ],
            }
        ),
        json.dumps(
            {
                "strict_status": "strictly_comparable",
                "approximate_status": "highly_similar",
                "items": [],
                "secret": SENTINELS[2],
            }
        ),
    )

    assert set(result) == {
        "dossier_json",
        "validation_json",
        "experiment_json",
        "comparison_json",
        "assessment_json",
        "markdown_report",
    }
    report = result["markdown_report"]
    for required in (
        "MAE=0.800",
        "RMSE=1.100",
        "R²=0.100",
        "R²=-0.250",
        "performance ranking: random_forest > linear_regression",
        "paper-closeness ranking: linear_regression > random_forest",
        "lower MAE/RMSE is better; higher R² is better",
        "details redacted for privacy",
    ):
        assert required in report
    assert "lower error is worse" not in report.casefold()
    combined = json.dumps(result, ensure_ascii=False)
    assert all(sentinel not in combined for sentinel in SENTINELS)


def test_regression_report_rejects_nonfinite_metrics_without_leaking_spellings() -> None:
    result = _exec_code_node("format_suite_comparison_report")(
        "{}",
        "{}",
        json.dumps(
            {
                "experiment_id": "exp-nonfinite",
                "task_type": "regression",
                "config": {"task_type": "regression"},
                "results": [
                    {
                        "model": "linear_regression",
                        "status": "succeeded",
                        "metrics": {"mae": float("nan"), "rmse": float("inf"), "r2": float("-inf")},
                    }
                ],
            }
        ),
        "{}",
        "{}",
    )

    combined = json.dumps(result).casefold()
    assert "nan" not in combined
    assert "infinity" not in combined
    assert "-inf" not in combined
