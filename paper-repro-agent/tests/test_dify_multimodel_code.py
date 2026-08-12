import json

from dify.code.comparison_workflow import (
    build_suite_comparison_request,
    format_suite_comparison_report,
)
from dify.code.experiment_workflow import (
    normalize_protocol_confirmation,
    prepare_protocol_artifacts,
    normalize_suite_inputs,
    poll_job_until_terminal,
)


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


def test_suite_request_keeps_manual_override_qualifiers_and_rejects_noise() -> None:
    dossier = json.dumps(
        {
            "metrics": [
                {
                    "name": "AUC",
                    "normalized_name": "roc_auc",
                    "supported": True,
                    "ambiguous": False,
                    "reported_value": 0.850,
                    "dataset": "奉节县（全域模型）",
                    "split": "测试集",
                },
                {
                    "name": "Recall",
                    "normalized_name": "recall",
                    "supported": True,
                    "ambiguous": False,
                    "reported_value": 0.720,
                    "dataset": "col_a,col_b\n1,2",
                    "split": "Traceback (most recent call last):",
                },
                {
                    "name": "F1",
                    "normalized_name": "f1",
                    "supported": True,
                    "ambiguous": False,
                    "reported_value": 0.610,
                    "dataset": "untrusted label " + "x" * 80,
                    "split": "row_1,row_2,row_3,row_4,row_5,row_6,row_7,row_8,row_9,row_10,row_11",
                }
            ]
        },
        ensure_ascii=False,
    )
    suite = json.dumps(
        {"experiment_id": "exp-20260811T000000Z-override", "results": []},
        ensure_ascii=False,
    )

    result = build_suite_comparison_request(dossier, suite)

    assert result["suite_comparison_request_ok"] is True
    assert json.loads(result["suite_comparison_request_json"]) == {
        "experiment_id": "exp-20260811T000000Z-override",
        "reported_metrics": [
            {
                "name": "roc_auc",
                "reported_value": 0.85,
                "dataset": "奉节县（全域模型）",
                "split": "测试集",
            },
            {
                "name": "recall",
                "reported_value": 0.72,
            },
            {
                "name": "f1",
                "reported_value": 0.61,
            }
        ],
    }


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
            "job_id": "job-suite-report",
            "job_status": "partial",
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
        "job-suite-report",
        "job status: partial",
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


def test_normalize_protocol_confirmation_rejects_unconfirmed_manifest() -> None:
    result = normalize_protocol_confirmation(
        '{"manifest_id":"manifest-1","target_column":"Y_cls"}',
        False,
    )

    assert result["protocol_ok"] is False
    assert json.loads(result["protocol_errors"])[0]["code"] == "protocol_not_confirmed"


def test_prepare_protocol_artifacts_builds_safe_preview_and_short_lived_token() -> None:
    dossier_json = json.dumps(
        {
            "title": "Minimal paper",
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
                    "evidence": [{"page": 3, "source_text": RAW_SENTINEL}],
                }
            ],
        },
        ensure_ascii=False,
    )
    diagnosis_json = json.dumps(
        {
            "valid": True,
            "dataset": {
                "rows": 100,
                "effective_rows": 95,
                "features": 2,
                "target": "Y_cls",
                "missing_values": 1,
                "duplicate_rows": 4,
                "class_counts": {"0": 45, "1": 50},
                "class_ratios": {"0": 0.473684, "1": 0.526316},
                "column_names": ["x1", "x2", "Y_cls"],
                "column_types": {"x1": "numeric", "x2": "numeric", "Y_cls": "numeric"},
                "dataset_id": "sha256:" + "a" * 64,
            },
            "columns": [
                {"name": "x1", "inferred_type": "numeric", "missing_count": 0, "unique_count": 10, "is_target_candidate": False, "risk_flags": []},
                {"name": "x2", "inferred_type": "numeric", "missing_count": 1, "unique_count": 8, "is_target_candidate": False, "risk_flags": ["missing_values"]},
                {"name": "Y_cls", "inferred_type": "numeric", "missing_count": 0, "unique_count": 2, "is_target_candidate": True, "risk_flags": []},
            ],
            "target_candidates": ["Y_cls"],
            "risk_flags": ["missing_values"],
            "recommended_options": {
                "target_column": "Y_cls",
                "target_column_confirmed": True,
                "missing_policy": "reject",
                "sampling_strategy": "original",
                "comparison_mode": "paper_comparable",
                "feature_columns": ["x1", "x2"],
                "exclude_columns": [],
            },
        },
        ensure_ascii=False,
    )

    result = prepare_protocol_artifacts(
        dossier_json,
        diagnosis_json,
        target_column="Y_cls",
        protocol_notes="Focus on tabular binary classification only.",
        secret="unit-test-secret",
        now=1_786_377_600,
        ttl_seconds=300,
    )

    preview = json.loads(result["protocol_preview_json"])
    assert preview["dataset"]["dataset_id"] == "sha256:" + "a" * 64
    assert preview["manifest_draft"]["target_column"] == "Y_cls"
    assert preview["protocol_notes_digest"].startswith("sha256:")
    assert preview["paper_summary"]["metrics"][0]["evidence"] == [{"page": 3, "source": "paper_dossier"}]
    assert RAW_SENTINEL not in result["protocol_preview_json"]
    assert RAW_SENTINEL not in result["protocol_token"]

    confirmed = normalize_protocol_confirmation(
        result["protocol_token"],
        True,
        secret="unit-test-secret",
        now=1_786_377_700,
    )
    assert confirmed["protocol_ok"] is True
    manifest = json.loads(confirmed["manifest_json"])
    assert manifest["dataset_id"] == "sha256:" + "a" * 64
    assert manifest["target_column"] == "Y_cls"


def test_normalize_protocol_confirmation_rejects_target_override() -> None:
    prepared = prepare_protocol_artifacts(
        '{"title":"Paper"}',
        json.dumps(
            {
                "valid": True,
                "dataset": {
                    "dataset_id": "sha256:" + "b" * 64,
                    "target": "Y_cls",
                    "column_names": ["x1", "Y_cls"],
                },
                "recommended_options": {"feature_columns": ["x1"]},
            }
        ),
        target_column="Y_cls",
        secret="target-test-secret",
        now=1_786_377_600,
    )

    result = normalize_protocol_confirmation(
        prepared["protocol_token"],
        True,
        confirmed_options={"target_column": "other_target"},
        secret="target-test-secret",
        now=1_786_377_700,
    )

    assert result["protocol_ok"] is False
    assert json.loads(result["protocol_errors"])[0]["code"] == "protocol_target_mismatch"


def test_poll_job_until_terminal_returns_safe_terminal_result_and_bounds_intervals() -> None:
    calls: list[tuple[str, str]] = []
    sleeps: list[float] = []
    job_result = {
        "experiment_id": "exp-20260812T010203Z-deadbeef",
        "job_id": "job-123",
        "job_status": "partial",
        "status": "partial",
        "results": [],
    }

    def fake_request(method: str, url: str, payload: str | None = None) -> tuple[int, str]:
        calls.append((method, url))
        if method == "POST":
            return 202, json.dumps({"job_id": "job-123", "status": "queued"}, ensure_ascii=False)
        if url.endswith("/result"):
            return 200, json.dumps(job_result, ensure_ascii=False)
        if len([item for item in calls if item[0] == "GET" and not item[1].endswith("/result")]) == 1:
            return 200, json.dumps({"job_id": "job-123", "status": "running"}, ensure_ascii=False)
        return 200, json.dumps({"job_id": "job-123", "status": "partial", "result_id": "exp-20260812T010203Z-deadbeef"}, ensure_ascii=False)

    result = poll_job_until_terminal(
        "http://repro-runner:8001",
        '{"manifest_id":"manifest-1"}',
        request_func=fake_request,
        sleep_func=sleeps.append,
        poll_interval_seconds=0.25,
        max_polls=3,
    )

    assert result["experiment_ok"] is True
    experiment = json.loads(result["experiment_json"])
    assert experiment["job_id"] == "job-123"
    assert experiment["job_status"] == "partial"
    assert experiment["experiment_id"] == "exp-20260812T010203Z-deadbeef"
    assert sleeps == [0.25]
    assert calls == [
        ("POST", "http://repro-runner:8001/v1/jobs"),
        ("GET", "http://repro-runner:8001/v1/jobs/job-123"),
        ("GET", "http://repro-runner:8001/v1/jobs/job-123"),
        ("GET", "http://repro-runner:8001/v1/jobs/job-123/result"),
    ]


def test_poll_job_until_terminal_redacts_malformed_backend_payloads() -> None:
    def fake_request(method: str, url: str, payload: str | None = None) -> tuple[int, str]:
        if method == "POST":
            return 202, '{"job_id":"job-123","status":"queued"}'
        return 200, (
            '{"job_id":"job-123","status":"failed",'
            '"error_code":"Traceback (most recent call last): SECRET_TOKEN '
            'sk-123456 col_a,col_b\\n1,2"}'
        )

    result = poll_job_until_terminal(
        "http://repro-runner:8001",
        '{"manifest_id":"manifest-1","raw":"RAW_CSV_SECRET_07a1"}',
        request_func=fake_request,
        sleep_func=lambda _seconds: None,
        poll_interval_seconds=0.25,
        max_polls=1,
    )

    assert result["experiment_ok"] is False
    errors = json.loads(result["experiment_errors"])
    assert errors[0]["code"] == "job_failed"
    payload = json.dumps(result, ensure_ascii=False)
    for sentinel in SECRET_SENTINELS:
        assert sentinel not in payload


def test_poll_job_until_terminal_rejects_empty_terminal_result() -> None:
    def fake_request(method: str, url: str, payload: str | None = None) -> tuple[int, str]:
        if method == "POST":
            return 202, '{"job_id":"job-123","status":"queued"}'
        if url.endswith("/result"):
            return 200, "{}"
        return 200, '{"job_id":"job-123","status":"succeeded"}'

    result = poll_job_until_terminal(
        "http://repro-runner:8001",
        '{"manifest_id":"manifest-1"}',
        request_func=fake_request,
        sleep_func=lambda _seconds: None,
        max_polls=1,
    )

    assert result["experiment_ok"] is False
    assert json.loads(result["experiment_errors"])[0]["code"] == "job_result_failed"
