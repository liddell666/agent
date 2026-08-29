import json

from scripts.build_multimodel_dsl import (
    OLLAMA_CHAT_OVERHEAD_TOKENS,
    OLLAMA_COMPLETION_RESERVE_TOKENS,
    OLLAMA_CONTEXT_NUM_CTX,
    OLLAMA_CONTEXT_NUM_PREDICT,
    OLLAMA_FIXED_PROMPT_UTF8_BYTES,
    OLLAMA_MAX_PROTOCOL_NOTES_UTF8_BYTES,
    OLLAMA_PARSER_PAYLOAD_BYTES,
    build_prepare_dsl,
)
from scripts.build_regression_dsl import build_regression_dsl


def _node(document: dict, title: str) -> dict:
    return next(
        node for node in document["workflow"]["graph"]["nodes"]
        if node["data"]["title"] == title
    )


def _fixed_prompt_bytes(document: dict) -> int:
    prompt = _node(document, "extract_paper_dossier")["data"]["prompt_template"]
    total = 0
    for message in prompt:
        text = message["text"]
        text = text.replace("{{#2900000000003.parsed_json#}}", "")
        text = text.replace("{{#2900000000001.protocol_notes#}}", "")
        total += len(text.encode("utf-8"))
    return total


def test_ollama_budget_constants_prove_the_fixed_context_envelope() -> None:
    assert OLLAMA_CONTEXT_NUM_CTX == 16_384
    assert OLLAMA_CONTEXT_NUM_PREDICT == 2_048
    assert OLLAMA_FIXED_PROMPT_UTF8_BYTES == 4_475
    assert OLLAMA_MAX_PROTOCOL_NOTES_UTF8_BYTES == 2_048
    assert OLLAMA_CHAT_OVERHEAD_TOKENS == 512
    assert OLLAMA_COMPLETION_RESERVE_TOKENS == 2_048
    assert OLLAMA_PARSER_PAYLOAD_BYTES == 7_301
    assert (
        OLLAMA_FIXED_PROMPT_UTF8_BYTES
        + OLLAMA_MAX_PROTOCOL_NOTES_UTF8_BYTES
        + OLLAMA_PARSER_PAYLOAD_BYTES
        + OLLAMA_CHAT_OVERHEAD_TOKENS
        + OLLAMA_COMPLETION_RESERVE_TOKENS
        == OLLAMA_CONTEXT_NUM_CTX
    )


def test_fixed_prompt_bytes_are_recomputed_from_ollama_builder() -> None:
    assert _fixed_prompt_bytes(build_prepare_dsl("ollama")) == OLLAMA_FIXED_PROMPT_UTF8_BYTES


def test_only_regression_ollama_pins_context_and_activates_parser_budget() -> None:
    ollama = build_regression_dsl("ollama")
    llm = _node(ollama, "extract_paper_dossier")["data"]
    parser = _node(ollama, "validate_parser_response")["data"]["code"]

    assert llm["model"]["completion_params"]["think"] is False
    assert llm["model"]["completion_params"]["num_ctx"] == OLLAMA_CONTEXT_NUM_CTX
    assert llm["model"]["completion_params"]["num_predict"] == OLLAMA_CONTEXT_NUM_PREDICT
    assert "DEFAULT_CONTEXT_BUDGET_BYTES = 7301" in parser

    deepseek = build_regression_dsl("deepseek")
    deepseek_llm = _node(deepseek, "extract_paper_dossier")["data"]
    deepseek_parser = _node(deepseek, "validate_parser_response")["data"]["code"]
    assert deepseek_llm["model"]["completion_params"] == _node(
        build_prepare_dsl("deepseek"), "extract_paper_dossier"
    )["data"]["model"]["completion_params"]
    assert "DEFAULT_CONTEXT_BUDGET_BYTES" not in deepseek_parser


def test_regression_ollama_parser_code_compiles_and_uses_budgeted_main() -> None:
    parser_code = _node(
        build_regression_dsl("ollama"), "validate_parser_response"
    )["data"]["code"]
    namespace: dict[str, object] = {}
    exec(compile(parser_code, "<regression-ollama-parser>", "exec"), namespace)
    payload = {
        "page_count": 1,
        "markdown": "paper",
        "elements": [{"page": 1, "text": "evidence"}],
        "warnings": [],
    }
    result = namespace["main"](json.dumps(payload, ensure_ascii=False), 200)
    assert result["can_continue"] is True
    assert json.loads(result["parsed_json"])["page_count"] == 1
