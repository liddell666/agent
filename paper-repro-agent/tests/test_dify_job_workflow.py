import json
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).parents[1]
PREPARE_DSL = PROJECT_ROOT / "dify" / "paper-comparison-prepare-workflow.yml"
RUN_DSL = PROJECT_ROOT / "dify" / "paper-comparison-multimodel-workflow.yml"


def _document(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _nodes(document: dict) -> list[dict]:
    return document["workflow"]["graph"]["nodes"]


def _node_map(document: dict) -> dict[str, dict]:
    return {node["data"]["title"]: node for node in _nodes(document)}


def test_prepare_workflow_exists_with_preview_and_token_outputs() -> None:
    document = _document(PREPARE_DSL)
    start = next(node for node in _nodes(document) if node["data"]["type"] == "start")
    variables = {item["variable"]: item for item in start["data"]["variables"]}
    end = next(node for node in _nodes(document) if node["data"]["type"] == "end")

    assert list(variables)[:4] == [
        "paper_pdf",
        "training_csv",
        "protocol_notes",
        "target_column",
    ]
    assert {item["variable"] for item in end["data"]["outputs"]} == {
        "protocol_preview_json",
        "protocol_token",
    }


def test_file_inputs_declare_local_upload_types_and_extensions() -> None:
    for path in (PREPARE_DSL, RUN_DSL):
        document = _document(path)
        start = next(node for node in _nodes(document) if node["data"]["type"] == "start")
        variables = {item["variable"]: item for item in start["data"]["variables"]}

        assert variables["training_csv"]["allowed_file_types"] == ["document"]
        assert variables["training_csv"]["allowed_file_extensions"] == [".CSV"]
        assert variables["training_csv"]["allowed_file_upload_methods"] == ["local_file"]

        paper_key = "paper_pdf" if "paper_pdf" in variables else "paper_dossier_json"
        assert variables[paper_key]["allowed_file_types"] == (
            ["document"] if paper_key == "paper_pdf" else ["custom"]
        )
        assert variables[paper_key]["allowed_file_extensions"] == (
            [".PDF"] if paper_key == "paper_pdf" else [".JSON"]
        )
        assert variables[paper_key]["allowed_file_upload_methods"] == ["local_file"]


def test_prepare_workflow_parses_pdf_before_building_protocol() -> None:
    document = _document(PREPARE_DSL)
    nodes = _node_map(document)
    http_nodes = [node["data"] for node in _nodes(document) if node["data"]["type"] == "http-request"]
    urls = {node["url"] for node in http_nodes}

    environment_variables = document["workflow"]["environment_variables"]
    assert [item["name"] for item in environment_variables] == ["PARSER_API_TOKEN"]
    assert environment_variables[0]["value_type"] == "secret"
    assert environment_variables[0]["value"] == ""

    assert urls == {
        "http://paper-parser:8000/v1/parse",
        "http://repro-runner:8001/v1/diagnose-dataset",
    }
    parser = next(node for node in http_nodes if node["url"] == "http://paper-parser:8000/v1/parse")
    assert "PARSER_API_TOKEN" in parser["headers"]
    assert parser["body"]["type"] == "form-data"
    assert parser["body"]["data"][0]["file"] == ["2900000000001", "paper_pdf"]

    llm_nodes = [node["data"] for node in _nodes(document) if node["data"]["type"] == "llm"]
    assert len(llm_nodes) == 1
    assert llm_nodes[0]["model"]["name"] == "deepseek-v4-flash"
    assert "PaperDossier" in llm_nodes[0]["prompt_template"][0]["text"]
    assert {"validate_parser_response", "validate_paper_dossier"}.issubset(nodes)
    assert "parser_output_compacted_for_workflow_limit" in nodes["validate_parser_response"]["data"]["code"]


def test_run_workflow_requires_protocol_token_and_keeps_six_string_outputs() -> None:
    document = _document(RUN_DSL)
    start = next(node for node in _nodes(document) if node["data"]["type"] == "start")
    variables = {item["variable"]: item for item in start["data"]["variables"]}
    end = next(node for node in _nodes(document) if node["data"]["type"] == "end")

    assert "protocol_token" in variables
    assert "confirm_protocol" in variables
    assert {item["variable"] for item in end["data"]["outputs"]} == {
        "dossier_json",
        "validation_json",
        "experiment_json",
        "comparison_json",
        "assessment_json",
        "markdown_report",
    }
    assert all(item["value_type"] == "string" for item in end["data"]["outputs"])


def test_run_workflow_embeds_confirmation_and_polling_code() -> None:
    document = _document(RUN_DSL)
    nodes = _node_map(document)

    confirmation_code = nodes["normalize_protocol_confirmation"]["data"]["code"]
    polling_code = nodes["poll_confirmed_job"]["data"]["code"]

    compile(confirmation_code, "<dify:normalize_protocol_confirmation>", "exec")
    compile(polling_code, "<dify:poll_confirmed_job>", "exec")
    confirmation_namespace: dict[str, object] = {}
    exec(compile(confirmation_code, "<dify:normalize_protocol_confirmation>", "exec"), confirmation_namespace)
    confirmation_result = confirmation_namespace["main"]("", False, "Y_cls", "[]", "5", "roc_auc", 0.2, 42)
    assert confirmation_result["protocol_ok"] is False

    polling_namespace: dict[str, object] = {}
    exec(compile(polling_code, "<dify:poll_confirmed_job>", "exec"), polling_namespace)
    polling_result = polling_namespace["main"]("{}", None)
    assert polling_result["experiment_ok"] is False

    prepare_document = _document(PREPARE_DSL)
    prepare_nodes = _node_map(prepare_document)
    prepare_code = prepare_nodes["prepare_protocol_artifacts"]["data"]["code"]
    assert "from dify.code" not in confirmation_code
    assert "from dify.code" not in polling_code
    assert "from dify.code" not in prepare_code
    prepare_namespace: dict[str, object] = {}
    exec(compile(prepare_code, "<dify:prepare_protocol_artifacts>", "exec"), prepare_namespace)
    prepare_result = prepare_namespace["main"]("{}", "{}", "", "")
    assert set(prepare_result) == {"protocol_preview_json", "protocol_token"}

    assert "protocol_not_confirmed" in confirmation_code
    assert "/v1/jobs" in polling_code
    assert "/result" in polling_code
