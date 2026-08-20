import pytest
import yaml
from pathlib import Path

from scripts.build_multimodel_dsl import (
    DEFAULT_LLM_PROFILE,
    LLM_PROFILES,
    build_merged_dsl,
    build_prepare_dsl,
    resolve_llm_profile,
    write_merged_dsl,
    write_profile_dsls,
)


def test_profiles_pin_provider_model_and_dependency() -> None:
    assert DEFAULT_LLM_PROFILE == "deepseek"
    assert set(LLM_PROFILES) == {"deepseek", "ollama"}

    deepseek = resolve_llm_profile()
    assert deepseek.provider == "langgenius/deepseek/deepseek"
    assert deepseek.model == "deepseek-v4-flash"
    assert deepseek.suffix == ""
    assert deepseek.dependency["value"]["marketplace_plugin_unique_identifier"].startswith(
        "langgenius/deepseek:0.0.19@"
    )

    ollama = resolve_llm_profile("ollama")
    assert ollama.provider == "langgenius/ollama/ollama"
    assert ollama.model == "qwen3:8b"
    assert ollama.suffix == "-ollama"
    assert ollama.dependency["value"]["marketplace_plugin_unique_identifier"] == (
        "langgenius/ollama:1.0.0@86dd6101fbd9de94e6681782700fa98c8a785c982918e6fe0e3f"
        "d507e15ba3f"
    )


def test_profile_resolution_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="deepseek.*ollama"):
        resolve_llm_profile("missing")


def _nodes(document: dict) -> list[dict]:
    return document["workflow"]["graph"]["nodes"]


def _node(document: dict, title: str) -> dict:
    return next(node for node in _nodes(document) if node["data"]["title"] == title)


def test_default_builder_remains_deepseek_compatible() -> None:
    default = _node(build_prepare_dsl(), "extract_paper_dossier")["data"]["model"]
    explicit = _node(build_prepare_dsl("deepseek"), "extract_paper_dossier")["data"]["model"]
    assert default == explicit
    assert default["provider"] == "langgenius/deepseek/deepseek"
    assert default["name"] == "deepseek-v4-flash"


def test_ollama_profile_preserves_prompt_and_pins_provider_metadata() -> None:
    document = build_prepare_dsl("ollama")
    model = _node(document, "extract_paper_dossier")["data"]["model"]
    assert model["provider"] == "langgenius/ollama/ollama"
    assert model["name"] == "qwen3:8b"
    assert _node(document, "extract_paper_dossier")["data"]["prompt_template"] == _node(
        build_prepare_dsl(), "extract_paper_dossier"
    )["data"]["prompt_template"]
    assert document["app"]["name"].endswith("-ollama")
    assert document["workflow"]["name"].endswith("-ollama")
    assert document["dependencies"][0]["value"]["marketplace_plugin_unique_identifier"].startswith(
        "langgenius/ollama:1.0.0@"
    )


def test_ollama_profile_disables_thinking_for_structured_output() -> None:
    ollama_model = _node(build_prepare_dsl("ollama"), "extract_paper_dossier")["data"]["model"]
    deepseek_model = _node(build_prepare_dsl("deepseek"), "extract_paper_dossier")["data"]["model"]

    assert ollama_model["completion_params"]["think"] is False
    assert "think" not in deepseek_model["completion_params"]


def test_ollama_profile_uses_grammar_compatible_shape_schema() -> None:
    ollama_data = _node(build_prepare_dsl("ollama"), "extract_paper_dossier")["data"]
    deepseek_data = _node(build_prepare_dsl("deepseek"), "extract_paper_dossier")["data"]
    ollama_schema = ollama_data["structured_output"]["schema"]

    def schema_keys(value: object) -> set[str]:
        if isinstance(value, dict):
            keys = set(value)
            properties = value.get("properties")
            nested = [item for key, item in value.items() if key != "properties"]
            if isinstance(properties, dict):
                nested.extend(properties.values())
            return keys | set().union(*(schema_keys(item) for item in nested))
        if isinstance(value, list):
            return set().union(*(schema_keys(item) for item in value))
        return set()

    keys = schema_keys(ollama_schema)
    for unsupported in ("$defs", "$ref", "additionalProperties", "default", "title"):
        assert unsupported not in keys
    assert set(ollama_schema["properties"]) == {
        "title",
        "research_problem",
        "task_type",
        "datasets",
        "methods",
        "metrics",
        "gaps",
    }
    assert ollama_schema["required"] == deepseek_data["structured_output"]["schema"]["required"]
    assert "$defs" in deepseek_data["structured_output"]["schema"]

    def named_property_schemas(value: object, name: str) -> list[dict]:
        if not isinstance(value, dict):
            return []
        properties = value.get("properties")
        found = [properties[name]] if isinstance(properties, dict) and isinstance(properties.get(name), dict) else []
        nested = [item for key, item in value.items() if key != "properties"]
        if isinstance(properties, dict):
            nested.extend(properties.values())
        return found + [schema for item in nested for schema in named_property_schemas(item, name)]

    evidence_schemas = named_property_schemas(ollama_schema, "evidence")
    assert evidence_schemas
    assert all(schema.get("minItems") == 1 for schema in evidence_schemas)


def test_ollama_bundle_has_no_secret_values() -> None:
    for document in (build_prepare_dsl("ollama"), build_merged_dsl("ollama")):
        serialized = yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=4096)
        assert "PARSER_API_TOKEN: replace" not in serialized
        assert "DIFY_PROTOCOL_SECRET: replace" not in serialized
        for item in document["workflow"]["environment_variables"]:
            if item["name"] in {"PARSER_API_TOKEN", "DIFY_PROTOCOL_SECRET"}:
                assert item["value"] == ""
                assert item["value_type"] == "secret"


def test_ollama_output_paths_are_suffixed_and_deterministic(tmp_path: Path) -> None:
    first = write_profile_dsls("ollama", output_root=tmp_path)
    first_bytes = tuple(path.read_bytes() for path in first)
    second = write_profile_dsls("ollama", output_root=tmp_path)
    assert tuple(path.name for path in first) == (
        "paper-comparison-merged-workflow-ollama.yml",
        "paper-comparison-multimodel-workflow-ollama.yml",
        "paper-comparison-prepare-workflow-ollama.yml",
    )
    assert tuple(path.read_bytes() for path in second) == first_bytes


def test_default_output_paths_have_no_profile_suffix(tmp_path: Path) -> None:
    paths = write_profile_dsls("deepseek", output_root=tmp_path)
    assert tuple(path.name for path in paths) == (
        "paper-comparison-merged-workflow.yml",
        "paper-comparison-multimodel-workflow.yml",
        "paper-comparison-prepare-workflow.yml",
    )


def test_single_file_writer_writes_only_requested_path(tmp_path: Path) -> None:
    target = tmp_path / "custom-merged.yml"
    write_merged_dsl(target)
    assert target.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["custom-merged.yml"]
