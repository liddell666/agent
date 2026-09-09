import json
import math
import socket
from urllib.error import HTTPError

import pytest


def test_remaining_budget_bounds_transport_and_is_reset():
    from paper_dossier_extractor.ollama import call_budget
    timeouts = []

    def opener(request, timeout):
        timeouts.append(timeout)
        return FakeResponse(json.dumps(_response_payload()).encode())

    client = OllamaClient(_settings(), opener=opener)
    with call_budget(0.25):
        client.complete(_chunk())
    client.complete(_chunk())
    assert timeouts == [0.25, 360]
    with call_budget(0):
        with pytest.raises(OllamaError, match='ollama_timeout'):
            client.complete(_chunk())
    assert len(timeouts) == 2

from paper_dossier_extractor.config import Settings
from paper_dossier_extractor.ollama import (
    PARTIAL_SCHEMA,
    TABLE_PARTIAL_SCHEMA,
    OllamaClient,
    OllamaError,
    build_messages,
)
from paper_dossier_extractor.schemas import (
    OllamaCompletion,
    PageChunk,
    SourcePage,
)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _settings(**overrides: object) -> Settings:
    return Settings(api_token="x" * 32, **overrides)


def _chunk() -> PageChunk:
    pages = (
        SourcePage(page=2, text="Methods use a fixed split.", kinds=("text",)),
        SourcePage(page=7, text="RMSE = 2.0", kinds=("table", "text")),
    )
    return PageChunk(chunk_id="chunk-001", pages=pages, source_bytes=128)


def _narrative_chunk() -> PageChunk:
    pages = (
        SourcePage(page=2, text="Methods use a fixed split.", kinds=("text",)),
        SourcePage(page=7, text="RMSE = 2.0", kinds=("text",)),
    )
    return PageChunk(chunk_id="chunk-001", pages=pages, source_bytes=128)


def _response_payload() -> dict[str, object]:
    return {
        "message": {"role": "assistant", "content": '{"title":"A paper"}'},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 11,
        "eval_count": 5,
    }


def test_build_messages_uses_only_the_compact_chunk_page_envelope() -> None:
    messages = build_messages(_narrative_chunk())

    required_system_prompt = (
        "Return exactly one compact JSON object and no Markdown or analysis.\n"
        "Use exactly these top-level keys and value shapes:\n"
        '{"title":string|null,"title_evidence":[evidence],'
        '"research_problem":string|null,"task_type":"regression"|"uncertain",'
        '"task_evidence":[evidence],"datasets":[fact],"methods":[fact],'
        '"metrics":[metric],"gaps":[string]}.\n'
        'evidence={"page":integer,"source_text":string}; '
        'fact={"name":string,"description":string,"evidence":[evidence]}; '
        'metric={"name":"MAE"|"RMSE"|"R2","reported_value":number|null,'
        '"dataset":string|null,"split":string|null,"model":string|null,'
        '"evidence":[evidence]}.\n'
        "Use [] for unsupported arrays and null for unsupported nullable values. "
        "Do not add other keys.\n"
        "Use only the supplied page objects. Preserve their original page numbers.\n"
        "Extract only title, research_problem, regression task evidence, datasets,\n"
        "methods, MAE, RMSE, and R2. Copy a short exact source_text from its page.\n"
        "Put title support in title_evidence and task-type support in task_evidence.\n"
        "Do not infer a value, dataset, split, model, quotation, or page number.\n"
        "If evidence is absent, omit the fact and add a short gap.\n"
        "Inspect compact table text carefully. Treat labels such as MAE/MAE(lm) "
        "as MAE result columns only when numeric cells and row labels are present."
    )
    assert messages[0] == {"role": "system", "content": required_system_prompt}
    assert messages[1]["role"] == "user"

    expected_payload = {
        "chunk_id": "chunk-001",
        "pages": [
            {"page": 2, "text": "Methods use a fixed split.", "kinds": ["text"]},
            {"page": 7, "text": "RMSE = 2.0", "kinds": ["text"]},
        ],
    }
    user_content = messages[1]["content"]
    assert user_content == json.dumps(
        expected_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert json.loads(user_content) == expected_payload
    assert "source_bytes" not in user_content
    assert "protocol" not in user_content.lower()
    assert "csv" not in user_content.lower()
    assert "prior" not in user_content.lower()


def test_metric_table_chunks_use_bounded_table_prompt() -> None:
    messages = build_messages(_chunk())

    system_prompt = messages[0]["content"]
    assert "at most ONE unambiguous metric" in system_prompt
    assert "use model=null when the model label is" in system_prompt
    assert "supported metric table" in system_prompt
    assert "MAE/MAE(lm)" in system_prompt


def test_metric_table_message_focus_keeps_the_page_head() -> None:
    long_page = SourcePage(
        page=13,
        text="MAE table header and first row. " + "x" * 8_000,
        kinds=("table",),
    )
    chunk = PageChunk(chunk_id="chunk-013", pages=(long_page,), source_bytes=128)

    payload = json.loads(build_messages(chunk)[1]["content"])
    message_text = payload["pages"][0]["text"]

    assert message_text.startswith("MAE table header and first row.")
    assert len(message_text.encode("utf-8")) == 2_048


def test_metric_table_prompt_adds_a_bounded_structural_row_hint() -> None:
    page = SourcePage(
        page=13,
        text="MAE training dataset Dataset n p Model value alpha 10 2 0.42 beta 11 3 0.51",
        kinds=("table",),
        table_text="MAE training dataset Dataset n p Model value alpha 10 2 0.42 beta 11 3 0.51",
    )
    chunk = PageChunk(chunk_id="chunk-013", pages=(page,), source_bytes=128)

    messages = build_messages(chunk)
    payload = json.loads(messages[1]["content"])

    assert "first structural data-row label is alpha" in messages[0]["content"]
    assert "split=training" in messages[0]["content"]
    assert "name=MAE, dataset=alpha, split=training, model=null" in messages[0]["content"]
    assert payload["table_hint"] == "alpha"


def test_complete_posts_exact_bounded_request_and_returns_safe_metadata(caplog) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: float):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            json.dumps(_response_payload(), ensure_ascii=False).encode("utf-8")
        )

    settings = _settings()
    completion = OllamaClient(settings, opener=fake_urlopen).complete(_chunk())

    request = captured["request"]
    payload = json.loads(request.data)
    assert payload["model"] == "qwen3:8b"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["keep_alive"] == "1s"
    assert payload["format"] == TABLE_PARTIAL_SCHEMA
    assert payload["options"] == {
        "num_ctx": 16_384,
        "num_predict": 1_536,
        "temperature": 0,
    }
    assert payload["messages"] == build_messages(_chunk())
    assert request.full_url == "http://ollama:11434/api/chat"
    assert request.method == "POST"
    assert captured["timeout"] == settings.ollama_call_timeout_seconds
    assert completion == OllamaCompletion(
        text='{"title":"A paper"}',
        finish_reason="stop",
        prompt_tokens=11,
        completion_tokens=5,
    )
    assert "RMSE = 2.0" not in caplog.text


def test_complete_preserves_ollama_request_field_order_for_structured_output() -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: float):
        captured["body"] = request.data.decode("utf-8")
        return FakeResponse(
            json.dumps(_response_payload(), ensure_ascii=False).encode("utf-8")
        )

    OllamaClient(_settings(), opener=fake_urlopen).complete(_chunk())

    body = captured["body"]
    assert isinstance(body, str)
    assert body.startswith('{"model":')
    assert body.index('"messages"') < body.index('"format"')
    assert body.index('"format"') < body.index('"options"')


def test_complete_caps_structured_output_schema_for_table_chunks() -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: float):
        captured["payload"] = json.loads(request.data)
        return FakeResponse(
            json.dumps(_response_payload(), ensure_ascii=False).encode("utf-8")
        )

    OllamaClient(_settings(), opener=fake_urlopen).complete(_chunk())

    payload = captured["payload"]
    assert isinstance(payload, dict)
    schema = payload["format"]
    assert isinstance(schema, dict)
    assert schema["properties"]["metrics"]["maxItems"] == 1
    assert schema["properties"]["datasets"]["maxItems"] == 1
    assert schema["properties"]["methods"]["maxItems"] == 1
    assert schema["$defs"]["PartialMetric"]["properties"]["evidence"]["maxItems"] == 1


def test_complete_caps_structured_output_schema_for_narrative_chunks() -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout: float):
        captured["payload"] = json.loads(request.data)
        return FakeResponse(
            json.dumps(_response_payload(), ensure_ascii=False).encode("utf-8")
        )

    OllamaClient(_settings(), opener=fake_urlopen).complete(_narrative_chunk())

    payload = captured["payload"]
    assert isinstance(payload, dict)
    schema = payload["format"]
    assert schema == PARTIAL_SCHEMA
    assert schema["properties"]["metrics"]["maxItems"] == 2
    assert schema["properties"]["datasets"]["maxItems"] == 1
    assert schema["properties"]["methods"]["maxItems"] == 2
    assert schema["properties"]["gaps"]["maxItems"] == 2
    assert schema["$defs"]["PartialEvidence"]["properties"]["source_text"]["maxLength"] == 240


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b'{"message":', "ollama_invalid_response"),
        (json.dumps({"done": True}).encode("utf-8"), "ollama_invalid_response"),
        (
            json.dumps(
                {
                    **_response_payload(),
                    "prompt_eval_count": math.nan,
                }
            ).encode("utf-8"),
            "ollama_invalid_response",
        ),
    ],
)
def test_complete_sanitizes_http_shape_and_token_failures(body: bytes, code: str) -> None:
    def fake_urlopen(*_args, **_kwargs):
        return FakeResponse(body)

    with pytest.raises(OllamaError) as raised:
        OllamaClient(_settings(), opener=fake_urlopen).complete(_chunk())

    assert raised.value.code == code
    assert str(raised.value) == code
    assert "secret" not in str(raised.value)


def test_complete_maps_http_failure_to_unavailable_without_body_leakage() -> None:
    def fake_urlopen(*_args, **_kwargs):
        raise HTTPError(
            "http://ollama:11434/api/chat",
            404,
            "model secret unavailable",
            {},
            None,
        )

    with pytest.raises(OllamaError) as raised:
        OllamaClient(_settings(), opener=fake_urlopen).complete(_chunk())

    assert raised.value.code == "ollama_unavailable"
    assert str(raised.value) == "ollama_unavailable"
    assert "model secret unavailable" not in str(raised.value)


def test_complete_maps_timeout_without_exposing_transport_details() -> None:
    def fake_urlopen(*_args, **_kwargs):
        raise socket.timeout("TIMEOUT_SENTINEL")

    with pytest.raises(OllamaError) as raised:
        OllamaClient(_settings(), opener=fake_urlopen).complete(_chunk())

    assert raised.value.code == "ollama_timeout"
    assert str(raised.value) == "ollama_timeout"
    assert "TIMEOUT_SENTINEL" not in str(raised.value)
