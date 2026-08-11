import json
from pathlib import Path

import yaml

from scripts.build_multimodel_dsl import build_multimodel_dsl, write_multimodel_dsl


PROJECT_ROOT = Path(__file__).parents[1]
SOURCE_DSL = PROJECT_ROOT / "dify" / "paper-comparison-workflow.yml"
GENERATED_DSL = PROJECT_ROOT / "dify" / "paper-comparison-multimodel-workflow.yml"


def _document(path: Path = GENERATED_DSL) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _nodes(document: dict) -> list[dict]:
    return document["workflow"]["graph"]["nodes"]


def _node_map(document: dict) -> dict[str, dict]:
    return {node["data"]["title"]: node for node in _nodes(document)}


def test_generator_output_is_deterministic_and_keeps_source_workflow_unchanged(tmp_path) -> None:
    original = SOURCE_DSL.read_bytes()
    first = tmp_path / "first.yml"
    second = tmp_path / "second.yml"

    write_multimodel_dsl(first)
    write_multimodel_dsl(second)

    assert first.read_bytes() == second.read_bytes()
    assert SOURCE_DSL.read_bytes() == original
    assert GENERATED_DSL.read_bytes() == first.read_bytes()


def test_multimodel_dsl_has_suite_urls_inputs_and_stable_idempotency_key() -> None:
    document = _document()
    nodes = _node_map(document)
    start = next(node for node in _nodes(document) if node["data"]["type"] == "start")
    variables = {item["variable"]: item for item in start["data"]["variables"]}

    assert list(variables) == [
        "paper_dossier_json",
        "training_csv",
        "metric_overrides_json",
        "target_column",
        "test_size",
        "random_state",
        "models_json",
        "cv_folds",
        "optimization_metric",
        "n_iter",
        "use_gpu",
        "drop_duplicates",
        "close_threshold",
        "partial_threshold",
    ]
    assert variables["models_json"]["default"] == json.dumps(
        [
            "logistic_regression",
            "random_forest",
            "xgboost",
            "lightgbm",
            "svm",
            "knn",
            "mlp",
        ]
    )
    assert variables["cv_folds"]["default"] == 5
    assert variables["optimization_metric"]["default"] == "roc_auc"
    assert variables["n_iter"]["default"] == 8
    assert variables["use_gpu"]["default"] is False
    assert variables["drop_duplicates"]["default"] is False

    urls = {node["data"]["url"] for node in _nodes(document) if node["data"]["type"] == "http-request"}
    assert "http://repro-runner:8001/v1/run-model-suite" in urls
    assert "http://repro-runner:8001/v1/compare-model-suite-result" in urls

    suite_http = next(
        node for node in _nodes(document)
        if node["data"]["type"] == "http-request"
        and node["data"]["url"] == "http://repro-runner:8001/v1/run-model-suite"
    )
    fields = {item["key"]: item for item in suite_http["data"]["body"]["data"]}
    assert list(fields) == [
        "file",
        "target_column",
        "test_size",
        "random_state",
        "models_json",
        "cv_folds",
        "optimization_metric",
        "n_iter",
        "use_gpu",
        "drop_duplicates",
        "idempotency_key",
    ]
    assert fields["models_json"]["value"] == "{{#1900000000010.models_json_text#}}"
    assert fields["cv_folds"]["value"] == "{{#1900000000010.cv_folds_text#}}"
    assert fields["optimization_metric"]["value"] == "{{#1900000000010.optimization_metric_text#}}"
    assert fields["n_iter"]["value"] == "{{#1900000000010.n_iter_text#}}"
    assert fields["use_gpu"]["value"] == "{{#1900000000010.use_gpu_text#}}"
    assert fields["drop_duplicates"]["value"] == "{{#1900000000010.drop_duplicates_text#}}"
    assert fields["idempotency_key"]["value"] == "{{#sys.workflow_run_id#}}"

    request_node = nodes["build_suite_comparison_request"]["data"]
    compare_node = next(
        node for node in _nodes(document)
        if node["data"]["type"] == "http-request"
        and node["data"]["url"] == "http://repro-runner:8001/v1/compare-model-suite-result"
    )["data"]
    assert set(request_node["outputs"]) == {
        "suite_comparison_request_ok",
        "suite_comparison_request_json",
        "suite_comparison_request_errors",
    }
    assert compare_node["body"]["data"][0]["value"] == "{{#1900000000014.suite_comparison_request_json#}}"


def test_multimodel_dsl_has_one_end_six_string_outputs_and_no_secrets() -> None:
    document = _document()
    ends = [node for node in _nodes(document) if node["data"]["type"] == "end"]

    assert len(ends) == 1
    outputs = ends[0]["data"]["outputs"]
    assert {item["variable"] for item in outputs} == {
        "dossier_json",
        "validation_json",
        "experiment_json",
        "comparison_json",
        "assessment_json",
        "markdown_report",
    }
    assert all(item["value_type"] == "string" for item in outputs)

    source = GENERATED_DSL.read_text(encoding="utf-8")
    lowered = source.casefold()
    assert "value_type: secret" not in lowered
    assert "authorization: bearer" not in lowered
    assert "sk-" not in lowered
    assert _document()["dependencies"] == []
    assert _document()["workflow"].get("environment_variables") == []


def test_multimodel_embedded_python_compiles_and_failure_branches_avoid_http_outputs() -> None:
    document = _document()
    nodes = _nodes(document)
    titles_by_id = {node["id"]: node["data"]["title"] for node in nodes}
    types_by_id = {node["id"]: node["data"]["type"] for node in nodes}
    failure_targets = {
        edge["target"]
        for edge in document["workflow"]["graph"]["edges"]
        if edge["sourceHandle"] in {"fail-branch", "false"}
    }

    for node in nodes:
        data = node["data"]
        if data["type"] != "code":
            continue
        compile(data["code"], f"<dify:{data['title']}>", "exec")
        if node["id"] not in failure_targets:
            continue
        variable_sources = [item["value_selector"][0] for item in data.get("variables", [])]
        assert all(types_by_id[source_id] != "http-request" for source_id in variable_sources)
        assert all(titles_by_id[source_id] != "run_model_suite" for source_id in variable_sources)
        assert all(titles_by_id[source_id] != "compare_model_suite_result" for source_id in variable_sources)


def test_build_multimodel_dsl_returns_the_generated_document_contract() -> None:
    built = build_multimodel_dsl()
    stored = _document()

    assert built == stored
