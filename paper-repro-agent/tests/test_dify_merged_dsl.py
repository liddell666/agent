from pathlib import Path

import yaml

from scripts.build_multimodel_dsl import build_merged_dsl, write_merged_dsl


PROJECT_ROOT = Path(__file__).parents[1]
MERGED_DSL = PROJECT_ROOT / "dify" / "paper-comparison-merged-workflow.yml"


def _document() -> dict:
    return yaml.safe_load(MERGED_DSL.read_text(encoding="utf-8"))


def _nodes() -> list[dict]:
    return _document()["workflow"]["graph"]["nodes"]


def _node_map() -> dict[str, dict]:
    return {node["data"]["title"]: node for node in _nodes()}


def _has_edge(edges: list[dict], source: dict, target: dict, source_handle: str) -> bool:
    return any(
        edge["source"] == source["id"]
        and edge["sourceHandle"] == source_handle
        and edge["target"] == target["id"]
        for edge in edges
    )


def _exec_code_node(title: str):
    namespace: dict[str, object] = {}
    node = _node_map()[title]
    exec(compile(node["data"]["code"], title, "exec"), namespace)
    return namespace["main"]


def _walk_value_selectors(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "value_selector" and isinstance(item, list) and item:
                yield item
            else:
                yield from _walk_value_selectors(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_value_selectors(item)


def test_merged_builder_is_deterministic_and_matches_generated_file(tmp_path):
    first = tmp_path / "first.yml"
    second = tmp_path / "second.yml"
    write_merged_dsl(first)
    write_merged_dsl(second)

    assert first.read_bytes() == second.read_bytes()
    assert build_merged_dsl() == _document()


def test_merged_start_inputs_have_mode_specific_requirements():
    start = _node_map()["Start"]
    variables = {item["variable"]: item for item in start["data"]["variables"]}
    assert variables["run_mode"]["type"] == "select"
    assert variables["run_mode"]["options"] == ["prepare", "run"]
    assert variables["run_mode"]["default"] == "prepare"
    assert variables["training_csv"]["required"] is True
    assert variables["paper_pdf"]["required"] is False
    assert "paper_dossier_json" not in variables


def test_merged_outputs_are_separate_and_non_empty():
    ends = {node["data"]["title"]: node for node in _nodes() if node["data"]["type"] == "end"}
    assert {"Output_prepare", "Output_run"} <= set(ends)
    assert {item["variable"] for item in ends["Output_prepare"]["data"]["outputs"]} == {
        "protocol_preview_json",
        "protocol_token",
        "draft_expires_at",
    }
    assert {item["variable"] for item in ends["Output_run"]["data"]["outputs"]} == {
        "dossier_json",
        "validation_json",
        "experiment_json",
        "comparison_json",
        "assessment_json",
        "markdown_report",
    }


def test_merged_graph_wires_prepare_and_run_directly_to_their_outputs():
    nodes = _node_map()
    edges = _document()["workflow"]["graph"]["edges"]
    assert _has_edge(edges, nodes["run_mode?"], nodes["prepare_inputs_ok?"], "true")
    assert _has_edge(edges, nodes["draft_saved_ok?"], nodes["Output_prepare"], "true")
    assert _has_edge(edges, nodes["format_suite_comparison_report"], nodes["Output_run"], "source")
    assert all(
        edge["target"] != nodes["Output_run"]["id"]
        for edge in edges
        if edge["source"] == nodes["prepare_protocol_draft_response"]["id"]
    )


def test_prepare_draft_response_uses_expected_manifest_metadata_before_output():
    main = _exec_code_node("prepare_protocol_draft_response")
    manifest = {"manifest_id": "sha256:" + "1" * 64, "dataset_id": "sha256:" + "2" * 64}
    result = main(
        {
            "draft_id": "draft-aaaaaaaa",
            "manifest_id": manifest["manifest_id"],
            "dataset_id": manifest["dataset_id"],
            "expires_at": 1234,
        },
        201,
        "draft-aaaaaaaa",
        {"manifest_draft": manifest},
    )

    assert result["draft_saved_ok"] is True
    assert result["draft_expires_at"] == "1234"


def test_merged_run_path_reads_draft_with_header_token_and_no_prepare_inputs():
    nodes = _node_map()
    get_draft = nodes["get_protocol_draft"]
    assert get_draft["data"]["method"] == "get"
    assert get_draft["data"]["url"] == (
        f"http://repro-runner:8001/v1/protocol-drafts/{{{{#{nodes['normalize_protocol_confirmation']['id']}.draft_id#}}}}"
    )
    assert "X-Protocol-Token: {{#Start.protocol_token#}}" in get_draft["data"]["headers"]
    assert "protocol_token" not in get_draft["data"]["url"]

    run_titles = {
        "normalize_suite_inputs",
        "normalize_protocol_confirmation",
        "get_protocol_draft",
        "normalize_protocol_draft_read_response",
        "dossier_ok?",
        "validate_dataset",
        "submit_confirmed_job",
        "poll_confirmed_job",
        "parse_suite_response",
        "build_suite_comparison_request",
        "format_suite_comparison_report",
        "Output_run",
    }
    serialized_run_nodes = yaml.safe_dump(
        [node for node in _nodes() if node["data"]["title"] in run_titles],
        allow_unicode=True,
    )
    assert "paper_pdf" not in serialized_run_nodes
    assert "paper_dossier_json" not in serialized_run_nodes
    assert "extract_paper_dossier" not in serialized_run_nodes


def test_merged_failure_and_false_paths_all_have_direct_edges():
    edges = _document()["workflow"]["graph"]["edges"]
    outgoing = {(edge["source"], edge["sourceHandle"]) for edge in edges}
    for node in _nodes():
        if node["data"]["type"] == "http-request":
            assert (node["id"], "fail-branch") in outgoing, node["data"]["title"]
        if node["data"]["type"] == "if-else":
            assert (node["id"], "false") in outgoing, node["data"]["title"]


def test_merged_value_selectors_reference_existing_or_runtime_sources():
    node_ids = {node["id"] for node in _nodes()}
    allowed_runtime_sources = {"env", "sys"}
    missing = []
    for node in _nodes():
        for selector in _walk_value_selectors(node):
            source_id = selector[0]
            if source_id not in node_ids and source_id not in allowed_runtime_sources:
                missing.append((node["data"]["title"], selector))

    assert missing == []


def test_merged_failure_nodes_have_direct_output_edges_and_all_nodes_reach_end():
    nodes = _node_map()
    node_by_id = {node["id"]: node for node in _nodes()}
    edges = _document()["workflow"]["graph"]["edges"]
    adjacency: dict[str, set[str]] = {node["id"]: set() for node in _nodes()}
    for edge in edges:
        adjacency.setdefault(edge["source"], set()).add(edge["target"])

    direct_failure_pairs = {
        "protocol_confirmation_failure": "Output_protocol_confirmation_failure",
        "normalize_job_submission_http_failure": "Output_job_submission_failure",
        "normalize_validation_http_failure": "Output_normalize_validation_http_failure",
        "validation_semantic_failure": "Output_validation_semantic_failure",
        "normalize_experiment_http_failure": "Output_normalize_experiment_http_failure",
        "experiment_semantic_failure": "Output_experiment_semantic_failure",
        "request_failure": "Output_request_failure",
        "normalize_comparison_http_failure": "Output_normalize_comparison_http_failure",
        "comparison_semantic_failure": "Output_comparison_semantic_failure",
    }
    for source_title, output_title in direct_failure_pairs.items():
        assert _has_edge(edges, nodes[source_title], nodes[output_title], "source"), source_title

    end_ids = {node["id"] for node in _nodes() if node["data"]["type"] == "end"}
    cannot_reach_end = []
    for node_id, node in node_by_id.items():
        if node_id in end_ids:
            continue
        seen = set()
        stack = [node_id]
        found_end = False
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in end_ids:
                found_end = True
                break
            stack.extend(adjacency.get(current, set()) - seen)
        if not found_end:
            cannot_reach_end.append(node["data"]["title"])

    assert cannot_reach_end == []


def test_merged_embedded_python_compiles_and_contains_no_raw_inputs_or_secrets():
    document = _document()
    code_nodes = [node for node in _nodes() if node["data"]["type"] == "code"]
    for node in code_nodes:
        compile(node["data"]["code"], node["data"]["title"], "exec")
    serialized = yaml.safe_dump(document, allow_unicode=True)
    assert "raw_csv_secret_07a1" not in serialized
    assert "sk-" not in serialized.casefold()
    assert "local-only-fallback-not-for-production" not in serialized
    assert "SECRET_TOKEN" not in serialized
    assert "col_a,col_b\n1,2" not in serialized
    variables = document["workflow"].get("environment_variables", [])
    assert variables
    protocol_secret = next(
        item for item in variables
        if item["name"] == "DIFY_PROTOCOL_SECRET"
    )
    assert protocol_secret["value_type"] == "secret"
    assert protocol_secret["value"] == ""
