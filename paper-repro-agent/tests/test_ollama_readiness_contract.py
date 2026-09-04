import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import check_ollama_readiness as readiness


def _ready_direct_probe() -> dict[str, object]:
    response = "ready"
    return {
        "status": "ready",
        "provider": "ollama",
        "model": readiness.OLLAMA_MODEL,
        "mode": "direct_http",
        "duration_seconds": 0.1,
        "response_nonempty": True,
        "response_length": len(response),
        "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
        "input_bytes": readiness.OLLAMA_PRODUCTION_INPUT_BYTES,
        "input_sha256": hashlib.sha256(
            readiness.SYNTHETIC_PROMPT.encode("utf-8")
        ).hexdigest(),
        "empty_prompt_tokens": 7,
        "empty_completion_tokens": 1,
        "prompt_tokens": 9_000,
        "completion_tokens": 1,
        "prompt_token_limit": readiness.OLLAMA_MAX_PROMPT_TOKENS,
        "num_ctx": readiness.OLLAMA_CONTEXT_NUM_CTX,
        "num_predict": readiness.OLLAMA_CONTEXT_NUM_PREDICT,
        "think": False,
    }


def _ready_extractor_probe(*, prompt_tokens: int = 1_000) -> dict[str, object]:
    response = "{\"ok\":true}"
    return {
        "status": "ready",
        "provider": "paper-dossier-extractor",
        "model": readiness.OLLAMA_MODEL,
        "mode": "extractor_boundary",
        "duration_seconds": 0.2,
        "response_nonempty": True,
        "response_length": len(response),
        "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
        "source_bytes": readiness.EXTRACTOR_MAX_CHUNK_SOURCE_BYTES,
        "source_sha256": readiness.EXTRACTOR_SYNTHETIC_SOURCE_SHA256,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": 1,
        "num_ctx": readiness.EXTRACTOR_NUM_CTX,
        "num_predict": readiness.EXTRACTOR_NUM_PREDICT,
        "max_chunk_source_bytes": readiness.EXTRACTOR_MAX_CHUNK_SOURCE_BYTES,
        "max_ollama_calls": readiness.EXTRACTOR_MAX_OLLAMA_CALLS,
        "page_count": 4,
        "candidate_page_count": 4,
        "initial_chunk_count": 1,
        "ollama_call_count": 1,
        "successful_chunk_count": 1,
        "split_retry_count": 0,
        "failed_chunk_count": 0,
    }


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


def test_readiness_document_includes_extractor_boundary_and_exact_configuration() -> None:
    result = readiness.build_evidence(
        _ready_direct_probe(),
        _ready_extractor_probe(),
        timestamp="2026-09-04T00:00:00Z",
    )
    serialized = json.dumps(result, sort_keys=True)

    assert result["status"] == "ready"
    assert set(result["probes"]) == {"direct_ollama", "extractor_boundary"}
    assert result["probes"]["extractor_boundary"]["status"] == "ready"
    assert result["probes"]["extractor_boundary"]["num_ctx"] == 16_384
    assert result["probes"]["extractor_boundary"]["num_predict"] == 1_536
    assert result["probes"]["extractor_boundary"]["max_chunk_source_bytes"] == 8_192
    assert result["probes"]["extractor_boundary"]["max_ollama_calls"] == 12
    assert "source_text" not in serialized


def test_extractor_boundary_fails_when_prompt_plus_completion_exceeds_context() -> None:
    result = readiness.build_evidence(
        _ready_direct_probe(),
        _ready_extractor_probe(prompt_tokens=16_384 - 1_536 + 1),
    )

    extractor = result["probes"]["extractor_boundary"]
    assert result["status"] == "failed"
    assert extractor["status"] == "failed"
    assert extractor["failure_category"] == "context_overflow"
    assert extractor["response_nonempty"] is False


def test_extractor_boundary_rejects_a_ready_response_without_model_calls() -> None:
    child = {
        "ok": True,
        **_ready_extractor_probe(),
        "ollama_call_count": 0,
        "successful_chunk_count": 0,
    }

    result = readiness.probe_extractor_boundary(
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(child),
            stderr="",
        ),
        token="x" * 32,
        monotonic=lambda: 1.0,
    )

    assert result["status"] == "failed"
    assert result["failure_category"] == "invalid_result"


def test_extractor_boundary_rejects_mismatched_service_configuration() -> None:
    child = {
        "ok": True,
        **_ready_extractor_probe(),
        "num_ctx": 8_192,
        "num_predict": 512,
        "max_chunk_source_bytes": 4_096,
        "max_ollama_calls": 4,
    }

    result = readiness.probe_extractor_boundary(
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(child),
            stderr="",
        ),
        token="x" * 32,
        monotonic=lambda: 1.0,
    )

    assert result["status"] == "failed"
    assert result["failure_category"] == "invalid_result"


@pytest.mark.parametrize(
    "missing_keys",
    [
        ("prompt_tokens",),
        ("completion_tokens",),
        ("prompt_tokens", "completion_tokens"),
    ],
)
def test_extractor_boundary_rejects_missing_token_evidence(missing_keys) -> None:
    child = {
        "ok": True,
        **_ready_extractor_probe(),
    }
    for key in missing_keys:
        child.pop(key)

    result = readiness.probe_extractor_boundary(
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(child),
            stderr="",
        ),
        token="x" * 32,
        monotonic=lambda: 1.0,
    )

    assert result["status"] == "failed"
    assert result["failure_category"] == "invalid_result"


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
    assert result["empty_completion_tokens"] == 1
    assert result["prompt_tokens"] == 9_000
    assert result["completion_tokens"] == 1
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
                    "eval_count": 1,
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
