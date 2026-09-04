from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys

from pypdf import PdfReader
import yaml

import pytest


PROJECT_ROOT = Path(__file__).parents[1]
BUILDER_PATH = PROJECT_ROOT / "scripts" / "build_regression_dsl.py"
SOURCE_DSL = PROJECT_ROOT / "dify" / "paper-comparison-workflow.yml"
DEEPSEEK_DSL = PROJECT_ROOT / "dify" / "paper-comparison-regression-workflow.yml"
OLLAMA_DSL = PROJECT_ROOT / "dify" / "paper-comparison-regression-workflow-ollama.yml"
DOSSIER_PROMPT = PROJECT_ROOT / "dify" / "paper-dossier-system-prompt.md"
REGRESSION_PDF = PROJECT_ROOT / "tests" / "fixtures" / "regression-paper.pdf"
PROTECTED_DSLS = tuple(
    PROJECT_ROOT / "dify" / name
    for name in (
        "paper-comparison-multimodel-workflow.yml",
        "paper-comparison-multimodel-workflow-ollama.yml",
        "paper-comparison-prepare-workflow.yml",
        "paper-comparison-prepare-workflow-ollama.yml",
        "paper-comparison-merged-workflow.yml",
        "paper-comparison-merged-workflow-ollama.yml",
    )
)


def _builder():
    return importlib.import_module("scripts.build_regression_dsl")


def _nodes(document: dict) -> list[dict]:
    return document["workflow"]["graph"]["nodes"]


def _node_map(document: dict) -> dict[str, dict]:
    return {node["data"]["title"]: node for node in _nodes(document)}


def _form_fields(node: dict) -> dict[str, dict]:
    return {item["key"]: item for item in node["data"]["body"]["data"]}


_RUN_BRANCH_TITLES = (
    "Start",
    "normalize_run_mode",
    "run_mode?",
    "normalize_suite_inputs",
    "normalize_protocol_confirmation",
    "protocol_ok?",
    "get_protocol_draft",
    "normalize_protocol_draft_read_response",
    "dossier_ok?",
    "validate_dataset",
    "parse_validation_response",
    "validation_ok?",
    "submit_confirmed_job",
    "parse_job_submission_response",
    "job_submission_ok?",
    "poll_confirmed_job",
    "parse_suite_response",
    "experiment_ok?",
    "build_suite_comparison_request",
    "comparison_request_ok?",
    "compare_model_suite_result",
    "parse_suite_comparison_response",
    "comparison_ok?",
    "score_approximate_similarity",
    "format_suite_comparison_report",
    "protocol_confirmation_failure",
    "protocol_draft_read_failure",
    "normalize_job_submission_http_failure",
    "normalize_validation_http_failure",
    "validation_semantic_failure",
    "normalize_experiment_http_failure",
    "experiment_semantic_failure",
    "request_failure",
    "normalize_comparison_http_failure",
    "comparison_semantic_failure",
    "aggregate_run_dossier_json",
    "aggregate_run_validation_json",
    "aggregate_run_experiment_json",
    "aggregate_run_comparison_json",
    "aggregate_run_assessment_json",
    "aggregate_run_markdown_report",
    "Output_run",
)


def _run_branch_snapshot(document: dict) -> tuple[dict, tuple[dict, ...]]:
    nodes = _node_map(document)
    node_snapshot = {title: nodes[title] for title in _RUN_BRANCH_TITLES}
    ids = {node["id"] for node in node_snapshot.values()}
    edge_snapshot = tuple(
        sorted(
            (
                edge
                for edge in document["workflow"]["graph"]["edges"]
                if edge["source"] in ids and edge["target"] in ids
            ),
            key=lambda edge: edge["id"],
        )
    )
    return node_snapshot, edge_snapshot


def test_regression_builder_module_exists() -> None:
    assert BUILDER_PATH.is_file()


def test_regression_builder_is_deterministic_and_does_not_mutate_source() -> None:
    source_before = SOURCE_DSL.read_bytes()
    protected_before = {path: path.read_bytes() for path in PROTECTED_DSLS}

    first = _builder().build_regression_dsl("deepseek")
    second = _builder().build_regression_dsl("deepseek")

    assert first == second
    assert SOURCE_DSL.read_bytes() == source_before
    assert {path: path.read_bytes() for path in PROTECTED_DSLS} == protected_before


def test_regression_workflow_has_explicit_task_models_metrics_and_defaults() -> None:
    document = _builder().build_regression_dsl("deepseek")
    serialized = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    nodes = _node_map(document)
    start = next(node for node in _nodes(document) if node["data"]["type"] == "start")
    inputs = {item["variable"]: item for item in start["data"]["variables"]}

    assert document["app"]["name"] == "Paper comparison Regression Candidate"
    assert document["workflow"]["name"] == "paper-comparison-regression-workflow"
    assert inputs["task_type"]["default"] == "regression"
    assert inputs["task_type"]["options"] == ["regression"]
    assert inputs["target_column"]["default"] == "target"
    assert json.loads(inputs["models_json"]["default"]) == [
        "linear_regression",
        "random_forest",
        "gradient_boosting",
        "xgboost",
    ]
    assert inputs["optimization_metric"]["default"] == "rmse"
    assert "task_type: regression" in serialized
    assert "linear_regression" in serialized
    assert "gradient_boosting" in serialized
    assert "logistic_regression" not in serialized
    assert "optimization_metric" in serialized
    assert "rmse" in serialized
    for title in ("diagnose_dataset", "validate_dataset"):
        assert _form_fields(nodes[title])["task_type"]["value"] == "regression"


def test_regression_builder_outputs_compile_and_keep_secret_environment_blank() -> None:
    document = _builder().build_regression_dsl("deepseek")
    for node in _nodes(document):
        data = node.get("data", {})
        if data.get("type") == "code":
            compile(data["code"], f"<regression-dify:{data['title']}>", "exec")

    secret = next(
        item
        for item in document["workflow"]["environment_variables"]
        if item["name"] == "DIFY_PROTOCOL_SECRET"
    )
    assert secret["value"] == ""
    assert secret["value_type"] == "secret"
    serialized = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    assert "local-only-fallback-not-for-production" not in serialized


@pytest.mark.parametrize("profile", ["deepseek", "ollama"])
def test_regression_comparison_request_uses_validated_dossier(profile: str) -> None:
    nodes = _node_map(_builder().build_regression_dsl(profile))
    draft_reader = nodes["normalize_protocol_draft_read_response"]
    request = nodes["build_suite_comparison_request"]
    dossier = next(
        item for item in request["data"]["variables"]
        if item["variable"] == "dossier_json"
    )

    assert dossier["value_selector"] == [draft_reader["id"], "dossier_json"]


def test_merged_regression_comparison_request_reads_saved_validated_dossier() -> None:
    nodes = _node_map(_builder().build_regression_dsl("deepseek"))
    request = nodes["build_suite_comparison_request"]
    dossier = next(
        item for item in request["data"]["variables"]
        if item["variable"] == "dossier_json"
    )

    assert dossier["value_selector"] == [
        nodes["normalize_protocol_draft_read_response"]["id"],
        "dossier_json",
    ]


@pytest.mark.parametrize(
    "mutation",
    ["classification_validator", "missing_request_node", "raw_dossier_wiring"],
)
def test_regression_graph_validation_rejects_broken_validator_comparison_chain(
    mutation: str,
) -> None:
    builder = _builder()
    document = builder.build_regression_dsl("deepseek")
    nodes = _node_map(document)
    if mutation == "classification_validator":
        nodes["validate_paper_dossier"]["data"]["code"] = (
            PROJECT_ROOT / "dify" / "code" / "validate_evidence.py"
        ).read_text(encoding="utf-8")
    elif mutation == "missing_request_node":
        document["workflow"]["graph"]["nodes"] = [
            node
            for node in _nodes(document)
            if node["data"]["title"] != "build_suite_comparison_request"
        ]
    elif mutation == "raw_dossier_wiring":
        request = nodes["build_suite_comparison_request"]
        dossier = next(
            item for item in request["data"]["variables"]
            if item["variable"] == "dossier_json"
        )
        dossier["value_selector"] = [nodes["extract_paper_dossier"]["id"], "dossier_json"]

    with pytest.raises(ValueError, match="regression validator/comparison chain"):
        builder._validate_regression_graph(document)


def test_regression_cli_writes_only_two_profile_artifacts_and_matches_builder(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(BUILDER_PATH),
            "--all-profiles",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "paper-comparison-regression-workflow-ollama.yml",
        "paper-comparison-regression-workflow.yml",
    ]
    for profile, path in (
        ("deepseek", tmp_path / DEEPSEEK_DSL.name),
        ("ollama", tmp_path / OLLAMA_DSL.name),
    ):
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == _builder().build_regression_dsl(profile)


def test_committed_regression_dsls_match_deterministic_builder_output(tmp_path: Path) -> None:
    written = _builder().write_all_profile_dsls(output_root=tmp_path)

    assert {path.name for path in written} == {DEEPSEEK_DSL.name, OLLAMA_DSL.name}
    assert DEEPSEEK_DSL.read_bytes() == (tmp_path / DEEPSEEK_DSL.name).read_bytes()
    assert OLLAMA_DSL.read_bytes() == (tmp_path / OLLAMA_DSL.name).read_bytes()


def test_dossier_prompt_adds_regression_aliases_without_weakening_classification_rules() -> None:
    prompt = DOSSIER_PROMPT.read_text(encoding="utf-8")

    for alias in ("MAE", "RMSE", "R²", "R2"):
        assert alias in prompt
    assert 'task_type="regression"' in prompt
    assert 'task_type="uncertain"' in prompt
    assert "continuous-target regression" in prompt
    assert "Every metric with a non-null `reported_value` must contain at least one evidence object." in prompt
    assert "Copy each paper-reported value exactly." in prompt


def test_deepseek_regression_extractor_prompt_requires_usable_scalar_metrics() -> None:
    document = _builder().build_regression_dsl("deepseek")
    prompt = _node_map(document)["extract_paper_dossier"]["data"]["prompt_template"][0]["text"]

    assert "For this regression workflow" in prompt
    assert "MAE, RMSE, and R2/R^2" in prompt
    assert "paper predicts a continuous numeric target" in prompt
    assert "R²=>r2" in prompt
    assert "single finite numeric scalar" in prompt
    assert "Never put prose such as" in prompt
    assert "Search all page elements" in prompt
    assert "title, research_problem, task_type, datasets, methods, metrics, gaps" in prompt
    assert "Never omit any field" in prompt
    assert "one short verbatim excerpt" in prompt
    assert "Do not explain or reason" in prompt
    assert "Keep the output compact" in prompt
    assert "reported_value is a bare JSON number" in prompt
    assert "dataset and split must be non-empty" in prompt
    assert "Do not copy equations or metric definitions" in prompt


def test_regression_ollama_prompt_keeps_the_fixed_byte_budget() -> None:
    document = _builder().build_regression_dsl("ollama")
    nodes = _node_map(document)

    assert "extract_paper_dossier" not in nodes
    assert nodes["extract_paper_dossier_chunks"]["data"]["type"] == "http-request"


def test_synthetic_regression_pdf_is_small_and_contains_acceptance_literals() -> None:
    assert REGRESSION_PDF.stat().st_size < 100_000
    text = "\n".join(page.extract_text() or "" for page in PdfReader(REGRESSION_PDF).pages)

    for literal in ("synthetic acceptance data", "frozen train/test protocol", "MAE=1.5", "RMSE=2.0", "R²=0.80"):
        assert literal in text


def test_ollama_extractor_routes_through_authenticated_service() -> None:
    document = _builder().build_regression_dsl("ollama")
    nodes = _node_map(document)
    environment = {
        item["name"]: item for item in document["workflow"]["environment_variables"]
    }

    assert environment["DIFY_EXTRACTOR_API_TOKEN"] == {
        "description": "Dossier extractor token shared with paper-dossier-extractor; exported without a value.",
        "id": "39000000-0000-4000-8000-000000000002",
        "name": "DIFY_EXTRACTOR_API_TOKEN",
        "selector": ["env", "DIFY_EXTRACTOR_API_TOKEN"],
        "value": "",
        "value_type": "secret",
    }
    assert nodes["extract_paper_dossier_chunks"]["data"]["type"] == "http-request"
    assert "extract_paper_dossier" not in nodes

    extractor_node = nodes["extract_paper_dossier_chunks"]
    extractor = extractor_node["data"]
    assert "model" not in extractor
    assert "prompt_template" not in extractor
    assert extractor["url"] == "http://paper-dossier-extractor:8002/v1/extract-dossier"
    assert extractor["error_strategy"] == "fail-branch"
    assert "X-Extractor-Token:" in extractor["headers"]
    assert "{{#env.DIFY_EXTRACTOR_API_TOKEN#}}" in extractor["headers"]
    parser_validator = nodes["validate_parser_response"]
    assert extractor["body"]["type"] == "raw-text"
    assert extractor["body"]["data"] == [
        {
            "type": "text",
            "value": f"{{{{#{parser_validator['id']}.parsed_json#}}}}",
        }
    ]

    normalizer = nodes["normalize_extractor_response"]
    assert normalizer["data"]["type"] == "code"
    normalizer_variables = {
        item["variable"]: item for item in normalizer["data"]["variables"]
    }
    assert normalizer_variables["body"]["value_selector"] == [extractor_node["id"], "body"]
    assert normalizer_variables["status_code"]["value_selector"] == [extractor_node["id"], "status_code"]

    gate = nodes["extractor_ok?"]
    assert gate["data"]["cases"][0]["conditions"][0]["variable_selector"] == [
        normalizer["id"],
        "can_continue",
    ]
    validator = nodes["validate_paper_dossier"]
    dossier_input = next(
        item for item in validator["data"]["variables"] if item["variable"] == "dossier_json"
    )
    assert dossier_input["value_selector"] == [normalizer["id"], "dossier_json"]

    ids = {node["data"]["title"]: node["id"] for node in _nodes(document)}
    edges = document["workflow"]["graph"]["edges"]

    def has_edge(source: str, handle: str, target: str) -> bool:
        return any(
            edge["source"] == ids[source]
            and edge["sourceHandle"] == handle
            and edge["target"] == ids[target]
            for edge in edges
        )

    assert has_edge("normalize_extractor_response", "source", "extractor_ok?")
    assert has_edge("extractor_ok?", "true", "validate_paper_dossier")
    assert has_edge("extractor_ok?", "false", "prepare_extractor_failure")
    assert has_edge("extract_paper_dossier_chunks", "fail-branch", "prepare_extractor_failure")
    for variable in (
        "protocol_preview_json",
        "protocol_token",
        "draft_expires_at",
    ):
        aggregator_title = f"aggregate_prepare_{variable}"
        assert has_edge("prepare_extractor_failure", "source", aggregator_title)
        assert [
            nodes["prepare_extractor_failure"]["id"],
            variable,
        ] in nodes[aggregator_title]["data"]["variables"]


def test_ollama_extractor_failure_is_terminal_and_preserves_exact_stable_code() -> None:
    document = _builder().build_regression_dsl("ollama")
    nodes = _node_map(document)
    failure = nodes["prepare_extractor_failure"]
    namespace: dict[str, object] = {}
    exec(compile(failure["data"]["code"], "<prepare-extractor-failure>", "exec"), namespace)

    result = namespace["main"](
        "qwen_chunk_truncated",
        '{"mode":"chunked","page_count":8,"candidate_page_count":4,"initial_chunk_count":2,'
        '"ollama_call_count":3,"successful_chunk_count":2,"split_retry_count":1,'
        '"failed_chunk_count":1,"elapsed_seconds":1.25,"warnings":[],'
        '"errors":[{"code":"qwen_chunk_truncated","page_range":[2,5]}],'
        '"failed_page_ranges":[[2,5]]}',
    )

    assert json.loads(result["error_json"]) == [
        {
            "code": "qwen_chunk_truncated",
            "message": "Paper dossier extraction failed; details were redacted.",
        }
    ]
    assert json.loads(result["protocol_preview_json"])["errors"][0]["code"] == "qwen_chunk_truncated"
    assert json.loads(result["protocol_preview_json"])["extraction_diagnostics"]["errors"][0]["code"] == "qwen_chunk_truncated"
    assert nodes["prepare_extractor_failure"]["data"]["outputs"]["error_json"]


def test_ollama_extractor_change_does_not_change_deepseek_or_run_branch() -> None:
    builder = _builder()
    deepseek = builder.build_regression_dsl("deepseek")
    ollama = builder.build_regression_dsl("ollama")
    deepseek_nodes = _node_map(deepseek)

    assert deepseek_nodes["extract_paper_dossier"]["data"]["type"] == "llm"
    assert _run_branch_snapshot(deepseek) == _run_branch_snapshot(ollama)


def test_deepseek_extractor_artifact_remains_byte_identical_after_ollama_generation(tmp_path: Path) -> None:
    generated = _builder().write_profile_dsl("deepseek", output_root=tmp_path)

    assert generated == tmp_path / DEEPSEEK_DSL.name
    assert generated.read_bytes() == DEEPSEEK_DSL.read_bytes()
