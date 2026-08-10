import json
from pathlib import Path

import yaml

from dify.code.comparison_workflow import format_comparison_report


DSL_PATH = Path(__file__).parents[1] / "dify" / "paper-comparison-workflow.yml"
HTTP_CONTRACT = {
    "parse_dossier": "http://repro-runner:8001/v1/parse-dossier",
    "validate_dataset": "http://repro-runner:8001/v1/validate-dataset",
    "run_experiment": "http://repro-runner:8001/v1/run-experiment",
    "compare_result": "http://repro-runner:8001/v1/compare-result",
}
TERMINAL_FIELDS = {
    "dossier_json",
    "validation_json",
    "experiment_json",
    "comparison_json",
    "assessment_json",
    "markdown_report",
}


def _document() -> dict:
    return yaml.safe_load(DSL_PATH.read_text(encoding="utf-8"))


def _nodes(document: dict) -> list[dict]:
    return document["workflow"]["graph"]["nodes"]


def _by_title(document: dict) -> dict[str, dict]:
    return {node["data"]["title"]: node for node in _nodes(document)}


def _formatter_inputs() -> dict[str, str]:
    return {
        "dossier_json": json.dumps(
            {
                "title": "DSL formatter fixture",
                "metrics": [
                    {
                        "name": "AUC",
                        "normalized_name": "roc_auc",
                        "supported": True,
                        "ambiguous": False,
                        "reported_value": 0.91,
                        "dataset": "ExampleSet",
                        "split": "test",
                        "source": "paper_dossier",
                        "evidence": [{"page": 2, "source_text": "The test AUC is 0.91."}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        "validation_json": json.dumps(
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
        ),
        "experiment_json": json.dumps(
            {
                "experiment_id": "exp-dsl-contract",
                "status": "succeeded",
                "config": {"target_column": "Y_cls", "test_size": 0.2, "random_state": 42},
                "split_provenance": {"train_rows": 12144, "test_rows": 3036},
            },
            ensure_ascii=False,
        ),
        "comparison_json": json.dumps(
            {
                "experiment_id": "exp-dsl-contract",
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
        ),
        "assessment_json": json.dumps(
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
        ),
    }


def test_dsl_start_contract_matches_the_approved_design() -> None:
    document = _document()
    start = next(node for node in _nodes(document) if node["data"]["type"] == "start")
    variables = {item["variable"]: item for item in start["data"]["variables"]}

    assert list(variables) == [
        "paper_dossier_json",
        "training_csv",
        "metric_overrides_json",
        "target_column",
        "test_size",
        "random_state",
        "drop_duplicates",
        "close_threshold",
        "partial_threshold",
    ]
    dossier = variables["paper_dossier_json"]
    assert dossier["type"] == "file"
    assert dossier["required"] is True
    assert dossier["allowed_file_types"] == ["custom"]
    assert dossier["allowed_file_extensions"] == [".JSON"]
    assert dossier["allowed_file_upload_methods"] == ["local_file"]
    overrides = variables["metric_overrides_json"]
    assert overrides["type"] == "paragraph"
    assert overrides["default"] == "[]"
    assert overrides["required"] is False


def test_dsl_has_only_the_four_official_bounded_retry_http_nodes() -> None:
    document = _document()
    nodes = _by_title(document)
    http_nodes = [node for node in _nodes(document) if node["data"]["type"] == "http-request"]

    assert {node["data"]["title"] for node in http_nodes} == set(HTTP_CONTRACT)
    for title, expected_url in HTTP_CONTRACT.items():
        data = nodes[title]["data"]
        assert data["url"] == expected_url
        assert data["error_strategy"] == "fail-branch"
        retry = data["retry_config"]
        assert retry["retry_enabled"] is True
        assert 1 <= retry["max_retries"] <= 3
        assert 100 <= retry["retry_interval"] <= 5000
        timeout = data["timeout"]
        assert 0 < timeout["connect"] <= 30
        assert 0 < timeout["read"] <= 600
        assert 0 < timeout["write"] <= 600


def test_run_experiment_uses_the_stable_workflow_run_idempotency_key() -> None:
    node = _by_title(_document())["run_experiment"]["data"]
    fields = {item["key"]: item for item in node["body"]["data"]}

    assert fields["idempotency_key"]["type"] == "text"
    assert fields["idempotency_key"]["value"] == "{{#sys.workflow_run_id#}}"
    assert fields["idempotency_key"].get("file") in (None, [])


def test_backend_smoke_uses_one_stable_idempotency_key_per_run() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "smoke_comparison.ps1").read_text(
        encoding="utf-8"
    )

    assert script.count('idempotency_key = "smoke-comparison-$runId"') == 2


def test_dsl_has_no_llm_six_aggregators_and_one_output() -> None:
    document = _document()
    types = [node["data"]["type"] for node in _nodes(document)]

    assert "llm" not in types
    assert "knowledge-retrieval" not in types
    assert types.count("variable-aggregator") == 6
    assert types.count("end") == 1
    output = next(node for node in _nodes(document) if node["data"]["type"] == "end")
    assert {item["variable"] for item in output["data"]["outputs"]} == TERMINAL_FIELDS
    assert all(item["value_selector"][1] == "output" for item in output["data"]["outputs"])


def test_dsl_contains_no_dependencies_environment_values_or_secrets() -> None:
    document = _document()
    source = DSL_PATH.read_text(encoding="utf-8").casefold()

    assert document.get("dependencies") == []
    assert document["workflow"].get("environment_variables") == []
    assert "value_type: secret" not in source
    assert "authorization: bearer" not in source


def test_dsl_failure_bindings_only_reference_safe_completed_nodes() -> None:
    document = _document()
    nodes = _by_title(document)
    allowed_sources = {
        "normalize_dossier_http_failure": set(),
        "dossier_semantic_failure": {"parse_dossier_response"},
        "thresholds_failure": {"parse_dossier_response", "validate_thresholds"},
        "normalize_validation_http_failure": {"parse_dossier_response"},
        "validation_semantic_failure": {"parse_dossier_response", "parse_validation_response"},
        "normalize_experiment_http_failure": {"parse_dossier_response", "parse_validation_response"},
        "experiment_semantic_failure": {
            "parse_dossier_response",
            "parse_validation_response",
            "parse_experiment_response",
        },
        "request_failure": {
            "parse_dossier_response",
            "parse_validation_response",
            "parse_experiment_response",
        },
        "normalize_comparison_http_failure": {
            "parse_dossier_response",
            "parse_validation_response",
            "parse_experiment_response",
        },
        "comparison_semantic_failure": {
            "parse_dossier_response",
            "parse_validation_response",
            "parse_experiment_response",
            "parse_comparison_response",
        },
    }
    titles_by_id = {node["id"]: node["data"]["title"] for node in _nodes(document)}

    for title, allowed in allowed_sources.items():
        variables = nodes[title]["data"].get("variables", [])
        actual = {titles_by_id[item["value_selector"][0]] for item in variables}
        assert actual <= allowed
        assert all(source not in HTTP_CONTRACT for source in actual)


def test_every_embedded_python_node_compiles_and_failure_outputs_are_safe() -> None:
    document = _document()
    forbidden = "RAW_CSV_SECRET_07a1"
    failure_titles = {
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
    }

    for node in _nodes(document):
        data = node["data"]
        if data["type"] != "code":
            continue
        compiled = compile(data["code"], f"<dify:{data['title']}>", "exec")
        if data["title"] in failure_titles:
            assert set(data["outputs"]) == TERMINAL_FIELDS
            namespace: dict[str, object] = {}
            exec(compiled, namespace)
            arguments = {
                item["variable"]: "not-json " + forbidden for item in data.get("variables", [])
            }
            result = namespace["main"](**arguments)
            assert set(result) == TERMINAL_FIELDS
            assert all(isinstance(value, str) for value in result.values())
            assert forbidden not in json.dumps(result, ensure_ascii=False)


def test_embedded_formatter_executes_with_the_canonical_safe_report_contract() -> None:
    node = _by_title(_document())["format_comparison_report"]
    namespace: dict[str, object] = {}
    exec(compile(node["data"]["code"], "<dify:format_comparison_report>", "exec"), namespace)
    inputs = _formatter_inputs()

    embedded = namespace["main"](**inputs)
    canonical = format_comparison_report(**inputs)
    report = embedded["markdown_report"]

    assert embedded == canonical
    assert "\n## 数据与划分摘要\n" in report
    assert "\\n" not in report
    for required in (
        "DSL formatter fixture",
        "exp-dsl-contract",
        "15180",
        "12144",
        "3036",
        "roc_auc",
        "论文值: 0.91",
        "独立值: 0.871388",
        "绝对差: 0.038612",
        "相对差: -0.042431",
        "严格可比: false",
        "paper metric is missing dataset identity",
        "近似等级: highly_similar",
        "paper_dossier",
        "p.2",
        "not_comparable",
        "highly_similar",
        "近似指标一致不等于严格复现。",
    ):
        assert required in report
    assert "复现成功" not in report


def test_embedded_formatter_matches_selected_duplicate_metric_evidence() -> None:
    node = _by_title(_document())["format_comparison_report"]
    namespace: dict[str, object] = {}
    exec(compile(node["data"]["code"], "<dify:format_comparison_report>", "exec"), namespace)
    inputs = _formatter_inputs()
    dossier = json.loads(inputs["dossier_json"])
    selected = dossier["metrics"][0]
    dossier["metrics"] = [
        {
            **selected,
            "reported_value": 0.80,
            "dataset": "A",
            "source": "paper_dossier",
            "evidence": [{"page": 1}],
            "supported": True,
            "ambiguous": True,
        },
        {
            **selected,
            "dataset": "B",
            "source": "manual_override",
            "evidence": [{"page": 2}],
            "supported": True,
            "ambiguous": False,
        },
    ]
    inputs["dossier_json"] = json.dumps(dossier, ensure_ascii=False)

    embedded = namespace["main"](**inputs)
    detail = embedded["markdown_report"].split("## 论文来源与证据", 1)[0]

    assert "论文数据集/划分: B/test; 来源: manual_override; 证据页: p.2" in detail
    assert "论文数据集/划分: A/test; 来源: paper_dossier; 证据页: p.1" not in detail
