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
from paper_dossier_extractor.ollama import OllamaClient, OllamaError, build_messages
from paper_dossier_extractor.schemas import (
    OllamaCompletion,
    PageChunk,
    PartialDossier,
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


def _response_payload() -> dict[str, object]:
    return {
        "message": {"role": "assistant", "content": '{"title":"A paper"}'},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 11,
        "eval_count": 5,
    }


def test_build_messages_uses_only_the_compact_chunk_page_envelope() -> None:
    messages = build_messages(_chunk())

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
            {"page": 7, "text": "RMSE = 2.0", "kinds": ["table", "text"]},
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
    assert payload["format"] == PartialDossier.model_json_schema()
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
