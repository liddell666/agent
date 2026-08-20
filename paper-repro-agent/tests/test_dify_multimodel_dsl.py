import json
from pathlib import Path

import yaml

from scripts.build_multimodel_dsl import (
    build_multimodel_dsl,
    build_prepare_dsl,
    build_prepare_dsl_legacy,
    write_profile_dsls,
    write_multimodel_dsl,
)


PROJECT_ROOT = Path(__file__).parents[1]
SOURCE_DSL = PROJECT_ROOT / "dify" / "paper-comparison-workflow.yml"
GENERATED_DSL = PROJECT_ROOT / "dify" / "paper-comparison-multimodel-workflow.yml"
PREPARE_DSL = PROJECT_ROOT / "dify" / "paper-comparison-prepare-workflow.yml"
MERGED_DSL = PROJECT_ROOT / "dify" / "paper-comparison-merged-workflow.yml"
SECRET_SENTINELS = (
    "RAW_CSV_SECRET_07a1",
    "SECRET_TOKEN",
    "Traceback (most recent call last):",
    "sk-123456",
    "col_a,col_b\n1,2",
)


def _document(path: Path = GENERATED_DSL) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _nodes(document: dict) -> list[dict]:
    return document["workflow"]["graph"]["nodes"]


def _node_map(document: dict) -> dict[str, dict]:
    return {node["data"]["title"]: node for node in _nodes(document)}


def _assert_protocol_secret_binding(node: dict) -> None:
    protocol_secret = next(
        variable
        for variable in node["data"].get("variables", [])
        if variable["variable"] == "protocol_secret"
    )
    assert protocol_secret == {
        "value_selector": ["env", "DIFY_PROTOCOL_SECRET"],
        "value_type": "string",
        "variable": "protocol_secret",
    }
    assert "protocol_secret: str" in node["data"]["code"]
    assert "secret=protocol_secret" in node["data"]["code"]


def _exec_code_node(title: str):
    node = _node_map(_document())[title]
    namespace: dict[str, object] = {}
    exec(compile(node["data"]["code"], f"<dify:{title}>", "exec"), namespace)
    return namespace["main"]


def test_generator_output_is_deterministic_and_keeps_source_workflow_unchanged(tmp_path) -> None:
    original = SOURCE_DSL.read_bytes()
    first = tmp_path / "first.yml"
    second = tmp_path / "second.yml"

    write_multimodel_dsl(first)
    write_multimodel_dsl(second)

    assert first.read_bytes() == second.read_bytes()
    assert SOURCE_DSL.read_bytes() == original
    assert GENERATED_DSL.read_bytes() == first.read_bytes()


def test_generated_dsls_are_byte_stable(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write_profile_dsls(profile="deepseek", output_root=first)
    write_profile_dsls(profile="deepseek", output_root=second)

    first_files = {path.name: path.read_bytes() for path in first.iterdir()}
    second_files = {path.name: path.read_bytes() for path in second.iterdir()}
    assert first_files == second_files


def test_multimodel_dsl_has_suite_urls_inputs_and_stable_idempotency_key() -> None:
    document = _document()
    nodes = _node_map(document)
    start = next(node for node in _nodes(document) if node["data"]["type"] == "start")
    variables = {item["variable"]: item for item in start["data"]["variables"]}

    assert list(variables) == [
        "paper_dossier_json",
        "training_csv",
        "protocol_token",
        "confirm_protocol",
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


def test_multimodel_dsl_has_terminal_outputs_and_no_secrets() -> None:
    document = _document()
    ends = [node for node in _nodes(document) if node["data"]["type"] == "end"]

    assert {node["data"]["title"] for node in ends} == {
        "Output",
        "Output_protocol_confirmation_failure",
        "Output_job_submission_failure",
        *(f"Output_{title}" for title in (
            "normalize_dossier_http_failure",
            "dossier_semantic_failure",
            "thresholds_failure",
            "normalize_validation_http_failure",
            "validation_semantic_failure",
            "normalize_experiment_http_failure",
            "experiment_semantic_failure",
            "request_failure",
            "normalize_comparison_http_failure",
            "comparison_semantic_failure",
        )),
    }
    outputs = next(node for node in ends if node["data"]["title"] == "Output")["data"]["outputs"]
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
    assert "authorization: bearer" not in lowered
    assert "raw_csv_secret_07a1" not in lowered
    assert "local-only-fallback-not-for-production" not in source
    assert _document()["dependencies"] == []


def test_generated_protocol_helper_workflows_declare_blank_secret_env() -> None:
    for path in (GENERATED_DSL, PREPARE_DSL, MERGED_DSL):
        source = path.read_text(encoding="utf-8")
        if "_PROTOCOL_SECRET" not in source and "DIFY_PROTOCOL_SECRET" not in source:
            continue
        document = _document(path)
        variables = {
            item["name"]: item
            for item in document["workflow"].get("environment_variables", [])
        }
        assert "DIFY_PROTOCOL_SECRET" in variables, path
        secret = variables["DIFY_PROTOCOL_SECRET"]
        assert secret["value_type"] == "secret", path
        assert secret["value"] == "", path
        assert secret["selector"] == ["env", "DIFY_PROTOCOL_SECRET"], path


def test_generated_protocol_code_nodes_bind_workflow_secret_explicitly() -> None:
    for path, title in (
        (GENERATED_DSL, "normalize_protocol_confirmation"),
        (PREPARE_DSL, "prepare_protocol_artifacts"),
    ):
        _assert_protocol_secret_binding(_node_map(_document(path))[title])


def test_prepare_builders_bind_workflow_secret_explicitly() -> None:
    for builder in (build_prepare_dsl_legacy, build_prepare_dsl):
        _assert_protocol_secret_binding(_node_map(builder())["prepare_protocol_artifacts"])


def test_protocol_branch_nodes_are_before_aggregators_and_output() -> None:
    titles = [node["data"]["title"] for node in _nodes(_document())]

    first_aggregator = titles.index("aggregate_dossier_json")
    assert titles.index("protocol_confirmation_failure") < first_aggregator
    assert titles.index("normalize_job_submission_http_failure") < first_aggregator
    assert titles[-1] == "Output"

    node_ids = {node["data"]["title"]: node["id"] for node in _nodes(_document())}
    edges = _document()["workflow"]["graph"]["edges"]
    first_aggregator_chain_edge = next(
        index
        for index, edge in enumerate(edges)
        if edge["source"] == node_ids["aggregate_dossier_json"]
    )
    aggregator_inputs = [
        index
        for index, edge in enumerate(edges)
        if edge["target"] == node_ids["aggregate_dossier_json"]
    ]
    assert max(aggregator_inputs) < first_aggregator_chain_edge


def test_early_protocol_branches_have_direct_end_outputs() -> None:
    document = _document()
    nodes = _nodes(document)
    node_ids = {node["data"]["title"]: node["id"] for node in nodes}
    ends = [node for node in nodes if node["data"]["type"] == "end"]

    assert {node["data"]["title"] for node in ends} >= {
        "Output",
        "Output_protocol_confirmation_failure",
        "Output_job_submission_failure",
    }
    edges = document["workflow"]["graph"]["edges"]
    for source_title, end_title in (
        ("protocol_confirmation_failure", "Output_protocol_confirmation_failure"),
        ("normalize_job_submission_http_failure", "Output_job_submission_failure"),
    ):
        assert any(
            edge["source"] == node_ids[source_title]
            and edge["target"] == node_ids[end_title]
            for edge in edges
        )


def test_success_path_wires_main_output_directly_from_report() -> None:
    document = _document()
    nodes = _nodes(document)
    node_ids = {node["data"]["title"]: node["id"] for node in nodes}
    output = next(node for node in nodes if node["data"]["title"] == "Output")

    assert {
        tuple(item["value_selector"])
        for item in output["data"]["outputs"]
    } == {
        (node_ids["format_suite_comparison_report"], variable)
        for variable in {
            "dossier_json",
            "validation_json",
            "experiment_json",
            "comparison_json",
            "assessment_json",
            "markdown_report",
        }
    }

    edges = document["workflow"]["graph"]["edges"]
    assert any(
        edge["source"] == node_ids["format_suite_comparison_report"]
        and edge["target"] == node_ids["Output"]
        for edge in edges
    )
    assert not any(
        edge["source"] == node_ids["aggregate_markdown_report"]
        and edge["target"] == node_ids["Output"]
        for edge in edges
    )


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


def test_generated_suite_code_carries_qualifiers_runtime_and_workflow_version() -> None:
    document = _document()
    nodes = _node_map(document)

    request_code = nodes["build_suite_comparison_request"]["data"]["code"]
    report_code = nodes["format_suite_comparison_report"]["data"]["code"]
    experiment_code = nodes["normalize_protocol_confirmation"]["data"]["code"]

    assert 'item["model"]' in request_code
    assert 'item["threshold"]' in request_code
    assert "runner commit" in report_code
    assert "workflow version" in report_code
    assert '"workflow_version": _SUITE_WORKFLOW_VERSION' in experiment_code


def test_build_multimodel_dsl_returns_the_generated_document_contract() -> None:
    built = build_multimodel_dsl()
    stored = _document()

    assert built == stored


def test_multimodel_embedded_suite_request_sanitizes_invalid_provenance() -> None:
    main = _exec_code_node("build_suite_comparison_request")

    result = main(
        json.dumps(
            {
                "metrics": [
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
                    }
                ]
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {"experiment_id": "exp-20260811t000000z-sanitized", "results": []},
            ensure_ascii=False,
        ),
    )

    assert result["suite_comparison_request_ok"] is True
    assert json.loads(result["suite_comparison_request_json"]) == {
        "experiment_id": "exp-20260811t000000z-sanitized",
        "reported_metrics": [{"name": "recall", "reported_value": 0.73}],
    }
    payload = json.dumps(result, ensure_ascii=False)
    for sentinel in SECRET_SENTINELS:
        assert sentinel not in payload


def test_generated_suite_request_keeps_manual_override_qualifiers_and_rejects_noise() -> None:
    main = _exec_code_node("build_suite_comparison_request")
    result = main(
        json.dumps(
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
                    },
                ]
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {"experiment_id": "exp-20260811T000000Z-override", "results": []},
            ensure_ascii=False,
        ),
    )

    request = json.loads(result["suite_comparison_request_json"])
    assert request["reported_metrics"] == [
        {
            "name": "roc_auc",
            "reported_value": 0.850,
            "dataset": "奉节县（全域模型）",
            "split": "测试集",
        },
        {
            "name": "recall",
            "reported_value": 0.720,
        },
        {
            "name": "f1",
            "reported_value": 0.610,
        },
    ]
    assert "for key, validator, raw in" in _node_map(_document())["build_suite_comparison_request"]["data"]["code"]


def test_multimodel_embedded_formatter_redacts_arbitrary_backend_error_text() -> None:
    main = _exec_code_node("format_suite_comparison_report")

    result = main(
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
        json.dumps(
            {
                "strict_status": "not_comparable",
                "approximate_status": "insufficient_metrics",
                "items": [],
            },
            ensure_ascii=False,
        ),
    )

    report = result["markdown_report"]

    assert "safe_error=model_failed:" in report
    assert "details redacted for privacy" in report
    for sentinel in SECRET_SENTINELS[1:]:
        assert sentinel not in report
