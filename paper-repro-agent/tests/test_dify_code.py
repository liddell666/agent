import json
from pathlib import Path

from dify.code.validate_evidence import main as validate_evidence
from dify.code.validate_parser import main as validate_parser
from dify.code.experiment_workflow import (
    normalize_experiment_http_failure,
    normalize_experiment_inputs,
    normalize_validation_http_failure,
)
from dify.code.comparison_workflow import (
    build_comparison_request,
    format_comparison_report,
    normalize_comparison_http_failure,
    normalize_dossier_http_failure,
    parse_comparison_response,
    parse_dossier_response,
    score_approximate_similarity,
    validate_thresholds,
)


def test_dify_code_nodes_do_not_use_future_imports() -> None:
    code_dir = Path(__file__).parents[1] / "dify" / "code"

    for path in (code_dir / "validate_parser.py", code_dir / "validate_evidence.py"):
        assert "from __future__ import" not in path.read_text(encoding="utf-8")


def _parsed_paper() -> dict:
    return {
        "page_count": 2,
        "markdown": "# Minimal Paper\n\nAUC: 0.91",
        "elements": [
            {"page": 1, "text": "We use the ExampleSet dataset."},
            {"page": 2, "text": "The test AUC is 0.91."},
        ],
        "warnings": ["table structure disabled"],
    }


def _dossier() -> dict:
    return {
        "title": "Minimal Paper",
        "research_problem": "A small test task.",
        "task_type": "classification",
        "datasets": [
            {
                "name": "ExampleSet",
                "description": "Test dataset.",
                "evidence": [
                    {
                        "page": 1,
                        "source_text": "We use the ExampleSet dataset.",
                        "source": "paper",
                        "confidence": 1.0,
                    }
                ],
            }
        ],
        "methods": [],
        "metrics": [
            {
                "name": "AUC",
                "reported_value": "0.91",
                "dataset": "ExampleSet",
                "split": "test",
                "evidence": [
                    {
                        "page": 2,
                        "source_text": "The test AUC is 0.91.",
                        "source": "paper",
                        "confidence": 1.0,
                    }
                ],
            }
        ],
        "gaps": [],
    }


def test_parser_validator_accepts_valid_response() -> None:
    result = validate_parser(json.dumps(_parsed_paper()), 200)

    assert result["can_continue"] is True
    assert json.loads(result["parsed_json"])["page_count"] == 2
    assert result["parser_warnings"] == ["table structure disabled"]


def test_parser_validator_maps_known_http_errors() -> None:
    assert validate_parser("{}", 401)["parser_warnings"] == [
        "解析器鉴权失败（HTTP 401），请检查工作流的 PARSER_API_TOKEN。"
    ]
    assert validate_parser("{}", 413)["parser_warnings"] == [
        "论文文件过大（HTTP 413），请上传不超过 50 MB 的 PDF。"
    ]
    assert validate_parser("{}", 422)["parser_warnings"] == [
        "PDF 无法解析（HTTP 422），请确认文件完整且未加密。"
    ]
    assert validate_parser("{}", 500)["parser_warnings"] == [
        "解析器内部错误（HTTP 500），请查看 paper-parser 日志。"
    ]


def test_parser_validator_rejects_oversized_or_incomplete_json() -> None:
    oversized = "x" * (2 * 1024 * 1024 + 1)
    assert validate_parser(oversized, 200)["can_continue"] is False

    for missing_key in ("page_count", "markdown", "elements"):
        payload = _parsed_paper()
        payload.pop(missing_key)
        result = validate_parser(json.dumps(payload), 200)
        assert result["can_continue"] is False
        assert missing_key in result["parser_warnings"][0]


def test_evidence_validator_accepts_page_backed_metric() -> None:
    result = validate_evidence(json.dumps(_dossier()), 2)

    assert result["can_continue"] is True
    assert result["invalid_paths"] == []
    assert "Minimal Paper" in result["markdown_summary"]
    assert "0.91" in result["markdown_summary"]


def test_evidence_validator_accepts_dify_structured_output_object() -> None:
    result = validate_evidence(_dossier(), 2)

    assert result["can_continue"] is True
    assert json.loads(result["validated_json"])["metrics"][0]["reported_value"] == "0.91"


def test_evidence_validator_reads_page_count_from_parser_json() -> None:
    result = validate_evidence(_dossier(), json.dumps(_parsed_paper()))

    assert result["can_continue"] is True


def test_evidence_validator_reports_exact_invalid_paths() -> None:
    dossier = _dossier()
    dossier["metrics"][0]["evidence"] = []
    dossier["datasets"][0]["evidence"][0]["page"] = 3
    dossier["datasets"][0]["evidence"][0]["source_text"] = ""

    result = validate_evidence(json.dumps(dossier), 2)

    assert result["can_continue"] is False
    assert "$.metrics[0].evidence" in result["invalid_paths"]
    assert "$.datasets[0].evidence[0].page" in result["invalid_paths"]
    assert "$.datasets[0].evidence[0].source_text" in result["invalid_paths"]


def test_normalize_experiment_inputs_converts_boolean_to_form_text() -> None:
    assert normalize_experiment_inputs(False) == {"drop_duplicates_text": "false"}
    assert normalize_experiment_inputs(True) == {"drop_duplicates_text": "true"}


def test_normalize_validation_http_failure_returns_safe_payload() -> None:
    result = normalize_validation_http_failure()
    validation = json.loads(result["validation_json"])

    assert validation == {
        "valid": False,
        "errors": [
            {
                "code": "validation_service_unavailable",
                "message": "数据验证服务请求失败，请稍后重试。",
                "stage": "validate_dataset",
            }
        ],
    }
    assert result["experiment_json"] == "{}"
    assert "暂时不可用" in result["markdown_summary"]


def test_normalize_experiment_http_failure_preserves_validation() -> None:
    validation_json = json.dumps({"valid": True, "dataset": {"rows": 10}})
    result = normalize_experiment_http_failure(validation_json)
    experiment = json.loads(result["experiment_json"])

    assert json.loads(result["validation_json"])["dataset"]["rows"] == 10
    assert experiment == {
        "status": "failed",
        "errors": [
            {
                "code": "experiment_service_unavailable",
                "message": "实验服务请求失败，请稍后重试。",
                "stage": "run_experiment",
            }
        ],
    }
    assert "暂时不可用" in result["markdown_summary"]


def test_normalize_experiment_http_failure_does_not_echo_invalid_input() -> None:
    result = normalize_experiment_http_failure("not-json")

    assert json.loads(result["validation_json"]) == {"valid": True}
    assert "not-json" not in json.dumps(result, ensure_ascii=False)


def test_validate_thresholds_accepts_defaults_and_rejects_bad_order() -> None:
    assert validate_thresholds(0.05, 0.10)["thresholds_ok"] is True

    bad = validate_thresholds(0.10, 0.05)
    assert bad["thresholds_ok"] is False
    assert json.loads(bad["threshold_errors"])[0]["code"] == "invalid_similarity_thresholds"


def test_build_comparison_request_strips_display_fields_and_ambiguous_items() -> None:
    dossier = {
        "valid": True,
        "metrics": [
            {
                "name": "AUC",
                "normalized_name": "roc_auc",
                "supported": True,
                "reported_value": 0.91,
                "dataset": "test",
                "split": "test",
                "source": "paper_dossier",
                "evidence": [{"page": 8}],
                "display_name": "Area under curve",
                "ambiguous": False,
            },
            {
                "name": "F1",
                "normalized_name": "f1",
                "supported": True,
                "reported_value": 0.5,
                "source": "paper_dossier",
                "evidence": [],
                "ambiguous": True,
            },
            {
                "name": "MCC",
                "normalized_name": "mcc",
                "supported": False,
                "reported_value": 0.72,
                "source": "paper_dossier",
                "evidence": [{"page": 9}],
                "ambiguous": False,
            },
            "not a metric",
        ],
    }

    result = build_comparison_request(
        json.dumps(dossier), json.dumps({"experiment_id": "exp-123", "status": "succeeded"})
    )

    assert result["comparison_request_ok"] is True
    assert json.loads(result["comparison_request_json"]) == {
        "experiment_id": "exp-123",
        "reported_metrics": [
            {"name": "roc_auc", "reported_value": 0.91, "dataset": "test", "split": "test"}
        ],
    }


def test_build_comparison_request_requires_experiment_and_usable_metrics() -> None:
    missing_experiment = build_comparison_request(json.dumps({"metrics": [{"name": "f1"}]}), "{}")
    missing_metrics = build_comparison_request(json.dumps({"metrics": [{"ambiguous": True}]}), '{"experiment_id":"exp-123"}')
    missing_value = build_comparison_request(json.dumps({"metrics": [{"name": "recall"}]}), '{"experiment_id":"exp-123"}')

    for result in (missing_experiment, missing_metrics, missing_value):
        assert result["comparison_request_ok"] is False
        assert json.loads(result["comparison_request_json"]) == {}
        assert json.loads(result["comparison_request_errors"])[0]["code"] == "invalid_comparison_request"


def test_score_similarity_keeps_strict_and_approximate_status_separate() -> None:
    comparison = {
        "experiment_id": "exp-123",
        "items": [
            {
                "name": "roc_auc",
                "paper_value": 0.90,
                "independent_value": 0.87,
                "absolute_difference": 0.03,
                "relative_difference": -0.033333,
                "comparable": False,
                "reason": "paper metric is missing dataset identity",
            },
            {
                "name": "accuracy",
                "paper_value": 0.90,
                "independent_value": 0.81,
                "absolute_difference": 0.09,
                "relative_difference": -0.10,
                "comparable": False,
                "reason": "paper metric is missing dataset identity",
            },
        ],
    }

    assessment = json.loads(score_approximate_similarity(json.dumps(comparison), 0.05, 0.10)["assessment_json"])

    assert assessment["strict_status"] == "not_comparable"
    assert assessment["approximate_status"] == "partially_similar"
    assert [item["grade"] for item in assessment["items"]] == ["highly_similar", "partially_similar"]


def test_score_similarity_uses_absolute_difference_for_zero_paper_value() -> None:
    comparison = {
        "items": [
            {
                "name": "f1",
                "paper_value": 0.0,
                "independent_value": 0.2,
                "absolute_difference": 0.2,
                "relative_difference": None,
                "comparable": False,
                "reason": "paper metric is missing dataset identity",
            }
        ]
    }

    assessment = json.loads(score_approximate_similarity(json.dumps(comparison), 0.05, 0.10)["assessment_json"])

    assert assessment["items"][0]["difference_for_grade"] == 0.2
    assert assessment["items"][0]["grade"] == "materially_different"
    assert assessment["approximate_status"] == "materially_different"


def test_score_similarity_includes_exact_threshold_boundaries() -> None:
    comparison = {
        "items": [
            {"name": "one", "paper_value": 1.0, "independent_value": 1.05, "relative_difference": 0.05, "comparable": True},
            {"name": "two", "paper_value": 1.0, "independent_value": 1.10, "relative_difference": 0.10, "comparable": True},
        ]
    }

    assessment = json.loads(score_approximate_similarity(json.dumps(comparison), 0.05, 0.10)["assessment_json"])

    assert [item["grade"] for item in assessment["items"]] == ["highly_similar", "partially_similar"]
    assert assessment["strict_status"] == "strictly_comparable"


def test_response_parsers_and_dossier_failure_do_not_echo_malformed_payloads() -> None:
    dossier = parse_dossier_response("malformed dossier")
    comparison = parse_comparison_response("malformed comparison")
    failure = normalize_dossier_http_failure()

    assert dossier["dossier_ok"] is False
    assert comparison["comparison_ok"] is False
    assert json.loads(dossier["dossier_json"]) == {}
    assert json.loads(comparison["comparison_json"]) == {}
    assert json.loads(failure["dossier_json"])["errors"][0]["code"] == "dossier_service_unavailable"
    assert "malformed" not in json.dumps({"dossier": dossier, "comparison": comparison}, ensure_ascii=False)


def test_comparison_http_failure_preserves_completed_experiment_and_sanitizes_bad_inputs() -> None:
    result = normalize_comparison_http_failure(
        dossier_json=json.dumps({"valid": True}),
        validation_json=json.dumps({"valid": True}),
        experiment_json=json.dumps({"experiment_id": "exp-123", "status": "succeeded"}),
    )
    malformed = normalize_comparison_http_failure("bad dossier", "bad validation", "bad experiment")

    assert json.loads(result["experiment_json"])["experiment_id"] == "exp-123"
    assert json.loads(result["comparison_json"])["errors"][0]["code"] == "comparison_service_unavailable"
    assert json.loads(malformed["dossier_json"]) == {}
    assert "bad dossier" not in json.dumps(malformed, ensure_ascii=False)


def test_report_preserves_json_and_labels_evidence_without_claiming_reproduction_success() -> None:
    dossier_json = json.dumps(
        {
            "title": "Minimal Paper",
            "metrics": [{"name": "AUC", "source": "paper_dossier", "evidence": [{"page": 2}]}],
        },
        ensure_ascii=False,
    )
    validation_json = json.dumps({"valid": True, "dataset": {"rows": 40}}, ensure_ascii=False)
    experiment_json = json.dumps({"experiment_id": "exp-123", "status": "succeeded"}, ensure_ascii=False)
    comparison_json = json.dumps({"experiment_id": "exp-123", "items": []}, ensure_ascii=False)
    assessment_json = json.dumps(
        {"strict_status": "not_comparable", "approximate_status": "highly_similar", "items": []},
        ensure_ascii=False,
    )

    result = format_comparison_report(dossier_json, validation_json, experiment_json, comparison_json, assessment_json)

    for key, value in {
        "dossier_json": dossier_json,
        "validation_json": validation_json,
        "experiment_json": experiment_json,
        "comparison_json": comparison_json,
        "assessment_json": assessment_json,
    }.items():
        assert result[key] == value
    assert "paper_dossier" in result["markdown_report"]
    assert "p.2" in result["markdown_report"]
    assert "近似指标一致不等于严格复现。" in result["markdown_report"]
    assert "复现成功" not in result["markdown_report"]


def test_report_contains_complete_comparison_and_dataset_details_with_real_newlines() -> None:
    dossier_json = json.dumps(
        {
            "title": "Complete comparison fixture",
            "metrics": [
                {
                    "name": "AUC",
                    "normalized_name": "roc_auc",
                    "reported_value": 0.91,
                    "dataset": "ExampleSet",
                    "split": "test",
                    "source": "manual_override",
                    "evidence": [{"page": 2, "source_text": "The test AUC is 0.91."}],
                }
            ],
        },
        ensure_ascii=False,
    )
    validation_json = json.dumps(
        {
            "valid": True,
            "dataset": {
                "rows": 15180,
                "features": 16,
                "missing_values": 0,
                "duplicate_rows": 66,
            },
        },
        ensure_ascii=False,
    )
    experiment_json = json.dumps(
        {
            "experiment_id": "exp-report-contract",
            "status": "succeeded",
            "config": {"target_column": "Y_cls", "test_size": 0.2, "random_state": 42},
            "split_provenance": {"train_rows": 12144, "test_rows": 3036},
        },
        ensure_ascii=False,
    )
    comparison_json = json.dumps(
        {
            "experiment_id": "exp-report-contract",
            "items": [
                {
                    "name": "roc_auc",
                    "paper_value": 0.91,
                    "independent_value": 0.871388,
                    "absolute_difference": 0.038612,
                    "relative_difference": -0.042431,
                    "comparable": False,
                    "reason": "paper metric is missing dataset identity",
                }
            ],
        },
        ensure_ascii=False,
    )
    assessment_json = json.dumps(
        {
            "strict_status": "not_comparable",
            "approximate_status": "highly_similar",
            "close_threshold": 0.05,
            "partial_threshold": 0.10,
            "items": [
                {
                    "name": "roc_auc",
                    "paper_value": 0.91,
                    "independent_value": 0.871388,
                    "difference_for_grade": 0.042431,
                    "grade": "highly_similar",
                    "comparable": False,
                    "reason": "paper metric is missing dataset identity",
                }
            ],
        },
        ensure_ascii=False,
    )

    report = format_comparison_report(
        dossier_json,
        validation_json,
        experiment_json,
        comparison_json,
        assessment_json,
    )["markdown_report"]

    assert "\n## 数据与划分摘要\n" in report
    assert "\\n" not in report
    for required in (
        "Complete comparison fixture",
        "exp-report-contract",
        "15180",
        "16",
        "12144",
        "3036",
        "Y_cls",
        "0.2",
        "42",
        "roc_auc",
        "论文值: 0.91",
        "独立值: 0.871388",
        "绝对差: 0.038612",
        "相对差: -0.042431",
        "严格可比: false",
        "paper metric is missing dataset identity",
        "近似等级: highly_similar",
        "manual_override",
        "p.2",
        "not_comparable",
        "highly_similar",
        "近似指标一致不等于严格复现。",
    ):
        assert required in report
    assert "复现成功" not in report
