import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import check_ollama_readiness as readiness


def test_production_prompt_shape_and_budget_are_explicit() -> None:
    assert readiness.OLLAMA_CONTEXT_NUM_CTX == 16_384
    assert readiness.OLLAMA_CONTEXT_NUM_PREDICT == 3_072
    assert readiness.OLLAMA_MAX_PROMPT_TOKENS == 13_312
    assert readiness.OLLAMA_PARSER_PAYLOAD_BYTES == 6_277
    assert readiness.OLLAMA_MAX_PROTOCOL_NOTES_CHARS == 512
    assert readiness.OLLAMA_MAX_PROTOCOL_NOTES_UTF8_BYTES == 2_048

    rendered = readiness.render_production_prompt(
        "x" * readiness.OLLAMA_PARSER_PAYLOAD_BYTES,
        "😀" * readiness.OLLAMA_MAX_PROTOCOL_NOTES_CHARS,
    )
    assert len(rendered.encode("utf-8")) == 12_800
    assert readiness.render_production_prompt("", "")


def test_local_probe_uses_chat_shape_and_validates_empty_and_full_token_budget() -> None:
    calls: list[dict[str, object]] = []

    class Response:
        def __init__(self, prompt_tokens: int) -> None:
            self.prompt_tokens = prompt_tokens

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "done": True,
                    "message": {"role": "assistant", "content": "ready"},
                    "prompt_eval_count": self.prompt_tokens,
                    "eval_count": 1,
                }
            ).encode("utf-8")

    def opener(request, timeout: float):
        body = json.loads(request.data)
        calls.append(body)
        return Response(7 if len(calls) == 1 else 9_000)

    result = readiness.probe_local_ollama(
        opener=opener,
        monotonic=lambda: 1.0 if len(calls) == 0 else 1.1,
    )

    assert result["status"] == "ready"
    assert result["input_bytes"] == 12_800
    assert result["empty_prompt_tokens"] == 7
    assert result["prompt_tokens"] == 9_000
    assert result["prompt_token_limit"] == 13_312
    assert len(calls) == 2
    assert calls[0]["messages"] == [{"role": "system", "content": ""}]
    assert calls[1]["messages"][0]["role"] == "system"
    assert len(calls[1]["messages"][0]["content"].encode("utf-8")) == 12_800
    assert calls[0]["options"] == {"num_ctx": 16_384, "num_predict": 3_072}
    assert calls[1]["options"] == {"num_ctx": 16_384, "num_predict": 3_072}
    assert calls[0]["think"] is False
    assert calls[1]["think"] is False


def test_local_probe_fails_closed_when_full_prompt_exceeds_reserved_context() -> None:
    calls = 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            nonlocal calls
            calls += 1
            return json.dumps(
                {
                    "done": True,
                    "message": {"role": "assistant", "content": "ready"},
                    "prompt_eval_count": 7 if calls == 1 else 13_313,
                }
            ).encode("utf-8")

    result = readiness.probe_local_ollama(
        opener=lambda *_args, **_kwargs: Response(),
        monotonic=lambda: 1.0,
    )

    assert result["status"] == "failed"
    assert result["failure_category"] == "context_overflow"
    assert result["response_nonempty"] is False


def test_dify_probe_uses_system_message_and_records_only_safe_boundary_metadata() -> None:
    captured: dict[str, object] = {}
    completion = "ready"

    def runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "response_nonempty": True,
                    "response_length": len(completion),
                    "response_sha256": hashlib.sha256(completion.encode()).hexdigest(),
                    "input_bytes": 12_800,
                    "input_sha256": hashlib.sha256(
                        readiness.SYNTHETIC_PROMPT.encode("utf-8")
                    ).hexdigest(),
                    "empty_prompt_tokens": 7,
                    "prompt_tokens": 9_000,
                    "prompt_token_limit": 13_312,
                    "num_ctx": 16_384,
                    "num_predict": 3_072,
                    "think": False,
                }
            ),
            stderr="private stderr must not persist",
        )

    result = readiness.probe_dify_model_boundary(
        runner=runner,
        monotonic=lambda: 1.0,
    )

    assert result["status"] == "ready"
    assert result["input_bytes"] == 12_800
    assert result["empty_prompt_tokens"] == 7
    assert result["prompt_tokens"] == 9_000
    assert result["prompt_token_limit"] == 13_312
    source = captured["argv"][-1]
    assert "SystemPromptMessage" in source
    assert "num_ctx" in source
    assert "num_predict" in source
    assert "render_production_prompt" in source
    assert captured["kwargs"]["timeout"] == 120
    assert "private stderr must not persist" not in json.dumps(result)


@pytest.mark.parametrize("prompt_tokens", [13_313, 0, True])
def test_dify_probe_rejects_invalid_or_overflow_prompt_token_evidence(prompt_tokens) -> None:
    result = readiness.probe_dify_model_boundary(
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "response_nonempty": True,
                    "response_length": 5,
                    "response_sha256": hashlib.sha256(b"ready").hexdigest(),
                    "input_bytes": 12_800,
                    "input_sha256": hashlib.sha256(
                        readiness.SYNTHETIC_PROMPT.encode("utf-8")
                    ).hexdigest(),
                    "empty_prompt_tokens": 7,
                    "prompt_tokens": prompt_tokens,
                    "prompt_token_limit": 13_312,
                    "num_ctx": 16_384,
                    "num_predict": 3_072,
                    "think": False,
                }
            ),
            stderr="",
        ),
        monotonic=lambda: 1.0,
    )

    assert result["status"] == "failed"
    assert result["failure_category"] == (
        "context_overflow" if prompt_tokens == 13_313 else "invalid_result"
    )
