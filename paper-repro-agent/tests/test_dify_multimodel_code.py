import json

from dify.code.comparison_workflow import (
    build_suite_comparison_request,
    format_suite_comparison_report,
)
from dify.code.experiment_workflow import normalize_suite_inputs


RAW_SENTINEL = "RAW_CSV_SECRET_07a1"
SECRET_SENTINELS = (
    RAW_SENTINEL,
    "SECRET_TOKEN",
    "Traceback (most recent call last):",
    "sk-123456",
    "col_a,col_b\n1,2",
)


def test_normalize_suite_inputs_returns_exact_safe_form_defaults() -> None:
    result = normalize_suite_inputs(
        '["logistic_regression", "random_forest"]',
        5,
        "roc_auc",
        8,
        False,
        True,
    )

    assert result == {
        "models_json_text": '["logistic_regression", "random_forest"]',
        "cv_folds_text": "5",
        "optimization_metric_text": "roc_auc",
        "n_iter_text": "8",
        "use_gpu_text": "false",
        "drop_duplicates_text": "true",
    }


def test_normalize_suite_inputs_sanitizes_malformed_payloads_without_echoing() -> None:
    payload = f'{{"oops":"{RAW_SENTINEL}"}}'

    result = normalize_suite_inputs(
        payload,
        "9999",
        "precision",
        "-1",
        "yes",
        "no",
    )

    assert json.loads(result["models_json_text"]) == [
        "logistic_regression",
        "random_forest",
        "xgboost",
        "lightgbm",
        "svm",
        "knn",
        "mlp",
    ]
    assert result["cv_folds_text"] == "5"
    assert result["optimization_metric_text"] == "roc_auc"
    assert result["n_iter_text"] == "8"
    assert result["use_gpu_text"] == "false"
    assert result["drop_duplicates_text"] == "false"
    assert RAW_SENTINEL not in json.dumps(result, ensure_ascii=False)


def test_suite_request_only_forwards_experiment_id_and_supported_metric_provenance() -> None:
    dossier = json.dumps(
        {
            "metrics": [
                {
                    "name": "Accuracy",
                    "normalized_name": "accuracy",
                    "supported": True,
                    "ambiguous": False,
                    "reported_value": 0.951,
                    "dataset": "test",
                    "split": "test",
                    "dataset_id": "sha256:" + "1" * 64,
                    "test_size": 0.2,
                    "random_state": 42,
                    "train_rows": 80,
                    "test_rows": 20,
                    "test_digest": "sha256:" + "2" * 64,
                    "source": "paper_dossier",
                    "display_name": "Accuracy",
                    "evidence": [{"page": 9, "source_text": RAW_SENTINEL}],
                },
                {
                    "name": "MCC",
                    "normalized_name": "mcc",
                    "supported": False,
                    "ambiguous": False,
                    "reported_value": 0.72,
                },
                {
                    "name": "Recall",
                    "normalized_name": "recall",
                    "supported": True,
                    "ambiguous": True,
                    "reported_value": 0.91,
                },
            ]
        },
        ensure_ascii=False,
    )
    suite = json.dumps(
        {
            "experiment_id": "exp-20260811T000000Z-abcdef12",
            "results": [{"model": "random_forest", "status": "succeeded"}],
            "raw_rows": RAW_SENTINEL,
        },
        ensure_ascii=False,
    )

    result = build_suite_comparison_request(dossier, suite)

    assert result["suite_comparison_request_ok"] is True
    request = json.loads(result["suite_comparison_request_json"])
    assert request == {
        "experiment_id": "exp-20260811T000000Z-abcdef12",
        "reported_metrics": [
            {
                "name": "accuracy",
                "reported_value": 0.951,
                "dataset": "test",
                "split": "test",
                "dataset_id": "sha256:" + "1" * 64,
                "test_size": 0.2,
                "random_state": 42,
                "train_rows": 80,
                "test_rows": 20,
                "test_digest": "sha256:" + "2" * 64,
            }
        ],
    }
    assert RAW_SENTINEL not in result["suite_comparison_request_json"]


def test_suite_request_sanitizes_invalid_provenance_fields_without_echoing_raw_payloads() -> None:
    dossier = json.dumps(
        {
            "metrics": [
                {
                    "name": "F1",
                    "normalized_name": "f1",
                    "supported": True,
                    "ambiguous": False,
                    "reported_value": 0.81,
                    "dataset": "full sample",
                    "split": "test",
                    "dataset_id": "sha256:" + "3" * 64,
                    "test_size": 0.25,
                    "random_state": 7,
                    "train_rows": 75,
                    "test_rows": 25,
                    "test_digest": "sha256:" + "4" * 64,
                },
                {
                    "name": "Recall",
                    "normalized_name": "recall",
                    "supported": True,
                    "ambiguous": False,
                    "reported_value": 0.73,
                    "dataset": "col_a,col_b\n1,2",
                    "split": "Traceback (most recent call last):",
                    "dataset_id": "sha256:" + "A" * 64,
                    "test_size": "nan",
                    "random_state": "sk-123456",
                    "train_rows": -1,
                    "test_rows": 0,
                    "test_digest": "SECRET_TOKEN",
                },
            ]
        },
        ensure_ascii=False,
    )
    suite = json.dumps(
        {
            "experiment_id": "exp-20260811t000000z-sanitized",
            "results": [{"model": "random_forest", "status": "succeeded"}],
        },
        ensure_ascii=False,
    )

    result = build_suite_comparison_request(dossier, suite)

    assert result["suite_comparison_request_ok"] is True
    assert json.loads(result["suite_comparison_request_json"]) == {
        "experiment_id": "exp-20260811t000000z-sanitized",
        "reported_metrics": [
            {
                "name": "f1",
                "reported_value": 0.81,
                "dataset": "full sample",
                "split": "test",
                "dataset_id": "sha256:" + "3" * 64,
                "test_size": 0.25,
                "random_state": 7,
                "train_rows": 75,
                "test_rows": 25,
                "test_digest": "sha256:" + "4" * 64,
            },
            {
                "name": "recall",
                "reported_value": 0.73,
            },
        ],
    }
    payload = json.dumps(result, ensure_ascii=False)
    for sentinel in SECRET_SENTINELS:
        assert sentinel not in payload


def test_suite_request_returns_stable_error_without_echoing_invalid_input() -> None:
    bad_suite = json.dumps(
        {"experiment_id": "exp-SECRET_TOKEN-sk-123456", "results": RAW_SENTINEL},
        ensure_ascii=False,
    )
    bad_dossier = json.dumps(
        {"metrics": [{"name": "accuracy", "supported": True, "ambiguous": False}]},
        ensure_ascii=False,
    )

    result = build_suite_comparison_request(bad_dossier, bad_suite)

    assert result["suite_comparison_request_ok"] is False
    assert json.loads(result["suite_comparison_request_json"]) == {}
    assert json.loads(result["suite_comparison_request_errors"]) == [
        {
            "code": "invalid_suite_comparison_request",
            "message": "Suite experiment ID and unambiguous metrics are required.",
        }
    ]
    payload = json.dumps(result, ensure_ascii=False)
    for sentinel in SECRET_SENTINELS:
        assert sentinel not in payload


def test_format_suite_comparison_report_returns_six_strings_with_rankings_and_safe_failures() -> None:
    dossier_json = json.dumps(
        {
            "title": "Multi-model paper",
            "metrics": [
                {
                    "name": "AUC",
                    "normalized_name": "roc_auc",
                    "supported": True,
                    "ambiguous": False,
                    "reported_value": 0.91,
                    "dataset": "test",
                    "split": "test",
                    "source": "paper_dossier",
                    "evidence": [{"page": 4, "source_text": RAW_SENTINEL}],
                }
            ],
        },
        ensure_ascii=False,
    )
    validation_json = json.dumps(
        {
            "valid": True,
            "dataset": {
                "rows": 100,
                "effective_rows": 95,
                "features": 12,
                "target": "Y_cls",
                "missing_values": 0,
                "duplicate_rows": 5,
            },
        },
        ensure_ascii=False,
    )
    suite_json = json.dumps(
        {
            "experiment_id": "exp-suite-report",
            "status": "partial",
            "config": {
                "models": ["logistic_regression", "random_forest", "xgboost"],
                "cv_folds": 5,
                "optimization_metric": "roc_auc",
                "n_iter": 8,
                "use_gpu": False,
            },
            "split_provenance": {
                "test_size": 0.2,
                "random_state": 42,
                "train_rows": 80,
                "test_rows": 20,
            },
            "performance_ranking": ["random_forest", "logistic_regression"],
            "results": [
                {
                    "model": "random_forest",
                    "status": "succeeded",
                    "cv_best_score": 0.88,
                    "metrics": {
                        "roc_auc": 0.89,
                        "accuracy": 0.84,
                        "f1": 0.83,
                        "recall": 0.82,
                    },
                },
                {
                    "model": "logistic_regression",
                    "status": "succeeded",
                    "cv_best_score": 0.86,
                    "metrics": {
                        "roc_auc": 0.87,
                        "accuracy": 0.81,
                        "f1": 0.8,
                        "recall": 0.79,
                    },
                },
                {
                    "model": "xgboost",
                    "status": "failed",
                    "error": {
                        "code": "model_failed",
                        "message": "bounded_failure"
                    },
                },
            ],
        },
        ensure_ascii=False,
    )
    comparison_json = json.dumps(
        {
            "experiment_id": "exp-suite-report",
            "paper_reference_metric": "roc_auc",
            "paper_distance_ranking": ["random_forest", "logistic_regression"],
            "items": [
                {
                    "model": "random_forest",
                    "name": "roc_auc",
                    "paper_value": 0.91,
                    "independent_value": 0.89,
                    "absolute_difference": 0.02,
                    "relative_difference": -0.021978,
                    "comparable": False,
                    "reason": "paper metric is missing dataset identity",
                },
                {
                    "model": "logistic_regression",
                    "name": "roc_auc",
                    "paper_value": 0.91,
                    "independent_value": 0.87,
                    "absolute_difference": 0.04,
                    "relative_difference": -0.043956,
                    "comparable": False,
                    "reason": "paper metric is missing dataset identity",
                },
            ],
        },
        ensure_ascii=False,
    )
    assessment_json = json.dumps(
        {
            "strict_status": "not_comparable",
            "approximate_status": "highly_similar",
            "paper_distance_ranking": ["random_forest", "logistic_regression"],
            "items": [
                {
                    "model": "random_forest",
                    "name": "roc_auc",
                    "grade": "highly_similar",
                },
                {
                    "model": "logistic_regression",
                    "name": "roc_auc",
                    "grade": "highly_similar",
                },
            ],
        },
        ensure_ascii=False,
    )

    result = format_suite_comparison_report(
        dossier_json,
        validation_json,
        suite_json,
        comparison_json,
        assessment_json,
    )

    assert set(result) == {
        "dossier_json",
        "validation_json",
        "experiment_json",
        "comparison_json",
        "assessment_json",
        "markdown_report",
    }
    assert all(isinstance(value, str) for value in result.values())
    report = result["markdown_report"]
    for required in (
        "exp-suite-report",
        "random_forest",
        "logistic_regression",
        "xgboost",
        "performance ranking",
        "paper-distance ranking",
        "cv_best_score",
        "safe_error=model_failed: bounded_failure",
        "paper metric is missing dataset identity",
        "not strict reproduction",
    ):
        assert required in report
    assert RAW_SENTINEL not in report


def test_format_suite_comparison_report_redacts_arbitrary_backend_error_text() -> None:
    result = format_suite_comparison_report(
        json.dumps({"title": "Sentinel paper"}, ensure_ascii=False),
        json.dumps({"valid": True}, ensure_ascii=False),
        json.dumps(
            {
                "experiment_id": "exp-suite-redaction",
                "status": "partial",
                "results": [
                    {
                        "model": "xgboost",
                        "status": "failed",
                        "error": {
                            "code": "model_failed",
                            "message": "Traceback (most recent call last): SECRET_TOKEN sk-123456 col_a,col_b\n1,2 C:\\secrets\\rows.csv",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        json.dumps({"experiment_id": "exp-suite-redaction", "items": []}, ensure_ascii=False),
        json.dumps({"strict_status": "not_comparable", "approximate_status": "insufficient_metrics", "items": []}, ensure_ascii=False),
    )

    report = result["markdown_report"]

    assert "safe_error=model_failed:" in report
    assert "details redacted for privacy" in report
    for sentinel in SECRET_SENTINELS[1:]:
        assert sentinel not in report
