import json
from pathlib import Path

from dify.code.validate_evidence import main as validate_evidence
from dify.code.validate_parser import main as validate_parser
from dify.code.experiment_workflow import (
    normalize_experiment_http_failure,
    normalize_experiment_inputs,
    normalize_validation_http_failure,
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
