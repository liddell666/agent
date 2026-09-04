from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json

import pytest


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


def _ollama_document() -> dict:
    module = importlib.import_module("scripts.build_regression_dsl")
    return module.build_regression_dsl("ollama")


def _exec_code_node(title: str):
    return _exec_code_node_from(_document(), title)


def _exec_code_node_from(document: dict, title: str):
    node = next(
        node
        for node in document["workflow"]["graph"]["nodes"]
        if node["data"]["title"] == title
    )
    namespace: dict[str, object] = {}
    exec(compile(node["data"]["code"], f"<regression-dify:{title}>", "exec"), namespace)
    return namespace["main"]


def _combined_output(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _extractor_dossier() -> dict:
    return {
        "title": "Regression candidate",
        "research_problem": "",
        "task_type": "uncertain",
        "datasets": [],
        "methods": [],
        "metrics": [],
        "gaps": [],
    }


def _extractor_diagnostics(**overrides: object) -> dict:
    diagnostics = {
        "mode": "chunked",
        "page_count": 8,
        "candidate_page_count": 4,
        "initial_chunk_count": 2,
        "ollama_call_count": 3,
        "successful_chunk_count": 2,
        "split_retry_count": 1,
        "failed_chunk_count": 0,
        "elapsed_seconds": 1.25,
        "warnings": ["candidate_pages_capped"],
        "errors": [],
        "failed_page_ranges": [],
    }
    diagnostics.update(overrides)
    return diagnostics


def _exec_extractor_normalizer():
    module = importlib.import_module("scripts.build_regression_dsl")
    namespace: dict[str, object] = {}
    exec(compile(module._extractor_response_normalizer_code(), "<extractor-normalizer>", "exec"), namespace)
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


def test_regression_comparison_request_normalizes_chinese_regression_task_type() -> None:
    result = _exec_code_node("build_suite_comparison_request")(
        json.dumps(
            {
                "task_type": "回归",
                "metrics": [
                    {
                        "name": "MAE",
                        "normalized_name": "mae",
                        "supported": True,
                        "ambiguous": False,
                        "reported_value": "1.5",
                        "dataset": "synthetic regression data",
                        "split": "test",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {"experiment_id": "exp-chinese-task-type", "task_type": "regression"}
        ),
    )

    assert result["suite_comparison_request_ok"] is True
    assert json.loads(result["suite_comparison_request_json"])["reported_metrics"] == [
        {
            "name": "mae",
            "reported_value": 1.5,
            "dataset": "synthetic regression data",
            "split": "test",
        }
    ]


def test_regression_comparison_request_preserves_task_type_set_literal() -> None:
    request = next(
        node
        for node in _document()["workflow"]["graph"]["nodes"]
        if node["data"]["title"] == "build_suite_comparison_request"
    )

    assert 'in {"regression", "uncertain"}' in request["data"]["code"]


def test_regression_comparison_request_accepts_uncertain_dossier_when_execution_is_regression() -> None:
    dossier = {
        "task_type": "uncertain",
        "metrics": [{
            "name": "MAE",
            "supported": True,
            "ambiguous": False,
            "reported_value": 1.25,
            "dataset": "benchmark",
            "split": "test",
        }],
    }

    result = _exec_code_node("build_suite_comparison_request")(
        json.dumps(dossier, ensure_ascii=False),
        json.dumps({"experiment_id": "exp-uncertain-dossier", "task_type": "regression"}),
    )

    assert result["suite_comparison_request_ok"] is True
    assert json.loads(result["suite_comparison_request_json"]) == {
        "experiment_id": "exp-uncertain-dossier",
        "reported_metrics": [{
            "name": "mae",
            "reported_value": 1.25,
            "dataset": "benchmark",
            "split": "test",
        }],
    }
    assert dossier["task_type"] == "uncertain"


@pytest.mark.parametrize(
    ("dossier_task_type", "suite_task_type", "experiment_id"),
    [
        ("classification", "regression", "exp-explicit-conflict"),
        ("uncertain", "classification", "exp-wrong-suite"),
        ("uncertain", "regression", "not valid"),
    ],
)
def test_regression_comparison_request_rejects_conflicts_and_invalid_execution_authority(
    dossier_task_type: str,
    suite_task_type: str,
    experiment_id: str,
) -> None:
    result = _exec_code_node("build_suite_comparison_request")(
        json.dumps(
            {
                "task_type": dossier_task_type,
                "metrics": [{
                    "name": "RMSE",
                    "supported": True,
                    "ambiguous": False,
                    "reported_value": 2.0,
                }],
            },
            ensure_ascii=False,
        ),
        json.dumps({"experiment_id": experiment_id, "task_type": suite_task_type}),
    )

    assert result["suite_comparison_request_ok"] is False
    assert json.loads(result["suite_comparison_request_json"]) == {}
    assert json.loads(result["suite_comparison_request_errors"]) == [{
        "code": "invalid_regression_comparison_request",
        "message": "Regression experiment and supported metrics are required.",
    }]


def test_regression_validator_feeds_supported_natural_metrics_to_comparison_request() -> None:
    validator = _exec_code_node("validate_paper_dossier")
    dossier = {
        "title": "Synthetic regression evidence",
        "task_type": "regression",
        "datasets": [],
        "methods": [],
        "gaps": [],
        "metrics": [
            {
                "name": "Mean Absolute Error",
                "reported_value": 1.5,
                "dataset": "synthetic",
                "split": "test",
                "evidence": [{"page": 1, "source_text": "MAE=1.5"}],
            },
            {
                "name": "Root Mean Squared Error",
                "reported_value": 2.0,
                "dataset": "synthetic",
                "split": "test",
                "evidence": [{"page": 1, "source_text": "RMSE=2.0"}],
            },
            {
                "name": "R²",
                "reported_value": 0.8,
                "dataset": "synthetic",
                "split": "test",
                "evidence": [{"page": 1, "source_text": "R²=0.80"}],
            },
        ],
    }

    validated = validator(json.dumps(dossier, ensure_ascii=False), 1)
    assert validated["can_continue"] is True
    metrics = json.loads(validated["validated_json"])["metrics"]
    assert [metric["normalized_name"] for metric in metrics] == ["mae", "rmse", "r2"]
    assert all(metric["supported"] is True for metric in metrics)
    assert all(metric["ambiguous"] is False for metric in metrics)

    request = _exec_code_node("build_suite_comparison_request")(
        validated["validated_json"],
        json.dumps(
            {
                "experiment_id": "exp-validator-chain",
                "task_type": "regression",
            }
        ),
    )
    assert request["suite_comparison_request_ok"] is True
    assert json.loads(request["suite_comparison_request_json"])["reported_metrics"] == [
        {"name": "mae", "reported_value": 1.5, "dataset": "synthetic", "split": "test"},
        {"name": "rmse", "reported_value": 2.0, "dataset": "synthetic", "split": "test"},
        {"name": "r2", "reported_value": 0.8, "dataset": "synthetic", "split": "test"},
    ]


def test_successful_regression_suite_parser_allowlists_fields_and_redacts_backend_data() -> None:
    parser = _exec_code_node("parse_suite_response")
    raw = {
        "experiment_id": "exp-safe-suite",
        "job_id": "job-safe-suite",
        "job_status": "partial",
        "status": "partial",
        "task_type": "regression",
        "config": {
            "task_type": "regression",
            "models": REGRESSION_MODELS,
            "optimization_metric": "rmse",
            "cv_folds": 5,
            "n_iter": 8,
            "workflow_version": "SECRET_TOKEN_ABC123",
            "cookie": SENTINELS[2],
        },
        "dataset": {
            "dataset_id": "sha256:" + "b" * 64,
            "rows": 20,
            "target": SENTINELS[0],
        },
        "split_provenance": {
            "test_size": 0.2,
            "random_state": 42,
            "train_rows": 16,
            "test_rows": 4,
            "test_digest": "sha256:" + "c" * 64,
            "raw_test_rows": SENTINELS[0],
        },
        "performance_ranking": ["random_forest", "linear_regression", SENTINELS[1]],
        "results": [
            {
                "model": "random_forest",
                "status": "succeeded",
                "metrics": {"mae": 0.8, "rmse": 1.1, "r2": -0.2, "cookie": SENTINELS[2]},
                "raw_rows": SENTINELS[0],
            },
            {
                "model": "xgboost",
                "status": "failed",
                "metrics": {},
                "error": {"code": "cookie_secret", "message": SENTINELS[3]},
            },
            {
                "model": "gradient_boosting",
                "status": "unavailable",
                "metrics": {},
                "error": {"code": "missing_dependency", "message": SENTINELS[3]},
            },
        ],
        "raw_csv": SENTINELS[0],
        "pdf_body": SENTINELS[1],
        "cookie": SENTINELS[2],
    }

    result = parser(json.dumps(raw, ensure_ascii=False))

    assert result["experiment_ok"] is True
    safe = json.loads(result["experiment_json"])
    assert safe["task_type"] == "regression"
    assert safe["experiment_id"] == "exp-safe-suite"
    assert safe["job_id"] == "job-safe-suite"
    assert "workflow_version" not in safe["config"]
    assert safe["performance_ranking"] == ["random_forest", "linear_regression"]
    assert safe["split_provenance"] == {
        "test_size": 0.2,
        "random_state": 42,
        "train_rows": 16,
        "test_rows": 4,
        "test_digest": "sha256:" + "c" * 64,
    }
    assert safe["results"][0] == {
        "model": "random_forest",
        "status": "succeeded",
        "metrics": {"mae": 0.8, "rmse": 1.1, "r2": -0.2},
    }
    assert safe["results"][1] == {
        "model": "xgboost",
        "status": "failed",
        "metrics": {},
        "error": {"code": "unavailable", "message": "details redacted for privacy."},
    }
    assert safe["results"][2] == {
        "model": "gradient_boosting",
        "status": "unavailable",
        "metrics": {},
        "error": {"code": "missing_dependency", "message": "details redacted for privacy."},
    }
    assert all(sentinel not in _combined_output(result) for sentinel in SENTINELS)


@pytest.mark.parametrize(
    "title,arguments,expected_code",
    [
        (
            "protocol_confirmation_failure",
            (json.dumps([{"code": "protocol_options_invalid", "message": SENTINELS[2]}]),),
            "protocol_options_invalid",
        ),
        (
            "protocol_draft_read_failure",
            (json.dumps([{"code": "protocol_draft_read_failed", "message": SENTINELS[2]}]),),
            "protocol_draft_read_failed",
        ),
        ("normalize_job_submission_http_failure", (SENTINELS[1], SENTINELS[0]), "job_submit_failed"),
        ("normalize_validation_http_failure", (SENTINELS[1],), "validation_service_unavailable"),
        ("validation_semantic_failure", (SENTINELS[1], SENTINELS[0]), "dataset_validation_failed"),
        ("normalize_experiment_http_failure", (SENTINELS[1], SENTINELS[0]), "experiment_service_unavailable"),
        ("experiment_semantic_failure", (SENTINELS[1], SENTINELS[0], SENTINELS[2]), "experiment_failed"),
        ("request_failure", (SENTINELS[1], SENTINELS[0], SENTINELS[2]), "invalid_regression_comparison_request"),
        ("normalize_comparison_http_failure", (SENTINELS[1], SENTINELS[0], SENTINELS[2]), "comparison_service_unavailable"),
        (
            "comparison_semantic_failure",
            (SENTINELS[1], SENTINELS[0], SENTINELS[2], SENTINELS[3]),
            "invalid_regression_comparison_response",
        ),
    ],
)
def test_terminal_failure_finalizers_never_reemit_upstream_bodies(
    title: str, arguments: tuple[str, ...], expected_code: str
) -> None:
    result = _exec_code_node(title)(*arguments)

    assert set(result) == {
        "dossier_json",
        "validation_json",
        "experiment_json",
        "comparison_json",
        "assessment_json",
        "markdown_report",
    }
    assert result["dossier_json"] == "{}"
    assert json.loads(result["experiment_json"])["status"] == "failed"
    assert json.loads(result["experiment_json"])["errors"][0]["code"] == expected_code
    assert all(sentinel not in _combined_output(result) for sentinel in SENTINELS)


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


def test_similarity_assessment_preserves_allowlisted_model_identity() -> None:
    result = _exec_code_node("score_approximate_similarity")(
        json.dumps(
            {
                "items": [
                    {
                        "model": "random_forest",
                        "name": "rmse",
                        "paper_value": 2.0,
                        "independent_value": 1.8,
                        "absolute_difference": 0.2,
                        "relative_difference": -0.1,
                        "comparable": False,
                    }
                ]
            }
        ),
        0.05,
        0.1,
    )

    assessment = json.loads(result["assessment_json"])
    assert assessment["items"][0]["model"] == "random_forest"


def test_regression_assessment_emits_safe_strict_reason_codes() -> None:
    result = _exec_code_node("score_approximate_similarity")(
        json.dumps(
            {
                "items": [
                    {
                        "model": "random_forest",
                        "name": "rmse",
                        "paper_value": 2.0,
                        "independent_value": 1.8,
                        "absolute_difference": 0.2,
                        "relative_difference": -0.1,
                        "comparable": False,
                        "reason": SENTINELS[1],
                    }
                ]
            }
        ),
        0.05,
        0.1,
    )

    assessment = json.loads(result["assessment_json"])
    assert assessment["strict_status"] == "not_comparable"
    assert assessment["strict_reason_codes"] == ["paper_provenance_unverified"]
    assert all(sentinel not in _combined_output(result) for sentinel in SENTINELS)


def test_regression_report_preserves_only_allowlisted_strict_reason_codes() -> None:
    result = _exec_code_node("format_suite_comparison_report")(
        "{}",
        "{}",
        json.dumps(
            {
                "experiment_id": "exp-strict-reason",
                "status": "succeeded",
                "task_type": "regression",
                "results": [],
            }
        ),
        "{}",
        json.dumps(
            {
                "strict_status": "not_comparable",
                "approximate_status": "insufficient_metrics",
                "strict_reason_codes": [
                    "paper_provenance_unverified",
                    SENTINELS[2],
                ],
                "items": [],
            }
        ),
    )

    assessment = json.loads(result["assessment_json"])
    assert assessment["strict_reason_codes"] == ["paper_provenance_unverified"]
    assert all(sentinel not in _combined_output(result) for sentinel in SENTINELS)


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
                "split_provenance": {
                    "test_size": 0.2,
                    "random_state": 42,
                    "train_rows": 16,
                    "test_rows": 4,
                    "test_digest": "sha256:" + "c" * 64,
                },
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
                "items": [{"model": "linear_regression", "name": "rmse", "grade": "highly_similar"}],
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
        "strict comparison: strictly_comparable",
        "approximate comparison: highly_similar",
        "test digest: sha256:" + "c" * 64,
        "details redacted for privacy",
    ):
        assert required in report
    safe_experiment = json.loads(result["experiment_json"])
    assert safe_experiment["split_provenance"]["test_digest"] == "sha256:" + "c" * 64
    safe_assessment = json.loads(result["assessment_json"])
    assert safe_assessment["items"] == [
        {"model": "linear_regression", "name": "rmse", "grade": "highly_similar"}
    ]
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


def test_extractor_response_normalizer_returns_canonical_success() -> None:
    dossier = _extractor_dossier()
    diagnostics = _extractor_diagnostics()
    body = _canonical_json({"ok": True, "dossier": dossier, "diagnostics": diagnostics})

    result = _exec_extractor_normalizer()(body, 200)

    assert result == {
        "dossier_json": _canonical_json(dossier),
        "extraction_diagnostics_json": _canonical_json(diagnostics),
        "can_continue": True,
        "error_code": "",
    }


def test_extractor_response_normalizer_routes_chunk_failure_without_parser_code() -> None:
    diagnostics = _extractor_diagnostics(
        failed_chunk_count=1,
        errors=[{"code": "qwen_chunk_truncated", "page_range": [2, 5]}],
        failed_page_ranges=[[2, 5]],
    )
    body = _canonical_json(
        {
            "ok": False,
            "dossier": None,
            "diagnostics": diagnostics,
            "raw_paper_text": SENTINELS[1],
            "extractor_token": SENTINELS[2],
        }
    )

    result = _exec_extractor_normalizer()(body, 200)

    assert result == {
        "dossier_json": "{}",
        "extraction_diagnostics_json": _canonical_json(diagnostics),
        "can_continue": False,
        "error_code": "qwen_chunk_truncated",
    }
    assert "paper_parse_failed" not in json.dumps(result)
    assert all(sentinel not in json.dumps(result) for sentinel in SENTINELS)


@pytest.mark.parametrize(
    ("status_code", "body", "expected_code"),
    [
        (
            401,
            _canonical_json(
                {
                    "detail": {
                        "code": "invalid_token",
                        "request_id": SENTINELS[2],
                        "paper": SENTINELS[1],
                    }
                }
            ),
            "invalid_token",
        ),
        (
            429,
            _canonical_json(
                {
                    "detail": {
                        "code": "extraction_capacity_reached",
                        "request_id": SENTINELS[2],
                    }
                }
            ),
            "extraction_capacity_reached",
        ),
        (
            502,
            _canonical_json(
                {
                    "detail": {
                        "code": "ollama_unavailable",
                        "request_id": SENTINELS[2],
                    }
                }
            ),
            "ollama_unavailable",
        ),
    ],
)
def test_extractor_response_normalizer_maps_http_failures_without_echoing_body(
    status_code: int, body: str, expected_code: str
) -> None:
    result = _exec_extractor_normalizer()(body, status_code)

    assert result == {
        "dossier_json": "{}",
        "extraction_diagnostics_json": "{}",
        "can_continue": False,
        "error_code": expected_code,
    }
    assert all(sentinel not in json.dumps(result) for sentinel in SENTINELS)


def test_extractor_response_normalizer_rejects_malformed_json_without_echoing_body() -> None:
    body = "not-json:" + SENTINELS[1] + ":" + SENTINELS[2]

    result = _exec_extractor_normalizer()(body, 200)

    assert result == {
        "dossier_json": "{}",
        "extraction_diagnostics_json": "{}",
        "can_continue": False,
        "error_code": "extractor_response_invalid",
    }
    assert all(sentinel not in json.dumps(result) for sentinel in SENTINELS)


def test_extractor_response_normalizer_truncates_warning_and_error_lists() -> None:
    diagnostics = _extractor_diagnostics(
        warnings=[f"warning_{index:02d}" for index in range(60)],
        errors=[
            {"code": f"error_{index:02d}", "page_range": [1, 1]}
            for index in range(60)
        ],
        failed_chunk_count=60,
        failed_page_ranges=[[1, 1] for _ in range(60)],
    )
    body = _canonical_json(
        {"ok": False, "dossier": None, "diagnostics": diagnostics}
    )

    result = _exec_extractor_normalizer()(body, 200)

    normalized = json.loads(result["extraction_diagnostics_json"])
    assert result["can_continue"] is False
    assert result["error_code"] == "error_00"
    assert len(normalized["warnings"]) == 50
    assert normalized["warnings"] == diagnostics["warnings"][:50]
    assert len(normalized["errors"]) == 50
    assert len(normalized["failed_page_ranges"]) == 50
