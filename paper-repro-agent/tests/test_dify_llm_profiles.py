import pytest

from scripts.build_multimodel_dsl import (
    DEFAULT_LLM_PROFILE,
    LLM_PROFILES,
    resolve_llm_profile,
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
