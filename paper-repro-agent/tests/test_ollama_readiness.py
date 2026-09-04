from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from scripts import check_ollama_readiness as readiness


SENTINELS = (
    "PROMPT_SENTINEL_DO_NOT_PERSIST",
    "RESPONSE_SENTINEL_DO_NOT_PERSIST",
    "PROVIDER_BODY_SENTINEL",
    "EXCEPTION_SENTINEL",
    "STDERR_SENTINEL",
    "DISPATCH_SENTINEL",
    "Bearer secret-token",
    "session-cookie",
)


class FakeHttpResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def ticking_clock(*values: float):
    iterator = iter(values)
    return lambda: next(iterator)


def assert_allowlisted_probe(probe: dict[str, object]) -> None:
    allowed = {
        "status",
        "provider",
        "model",
        "mode",
        "duration_seconds",
        "response_nonempty",
        "response_length",
        "response_sha256",
        "input_bytes",
        "input_sha256",
        "empty_prompt_tokens",
        "empty_completion_tokens",
        "prompt_tokens",
        "completion_tokens",
        "prompt_token_limit",
        "num_ctx",
        "num_predict",
        "think",
        "source_bytes",
        "source_sha256",
        "page_count",
        "candidate_page_count",
        "initial_chunk_count",
        "ollama_call_count",
        "successful_chunk_count",
        "split_retry_count",
        "failed_chunk_count",
        "max_chunk_source_bytes",
        "max_ollama_calls",
        "failure_phase",
        "failure_category",
    }
    assert set(probe) <= allowed


def assert_private_text_absent(value: object) -> None:
    serialized = json.dumps(value, sort_keys=True)
    for sentinel in SENTINELS:
        assert sentinel not in serialized


def test_successful_probes_emit_only_allowlisted_metadata_and_hashes() -> None:
    recorded: dict[str, object] = {}
    payloads: list[dict[str, object]] = []

    def opener(request, timeout: float):
        recorded["url"] = request.full_url
        recorded["timeout"] = timeout
        payload = json.loads(request.data)
        payloads.append(payload)
        return FakeHttpResponse(
            {
                "done": True,
                "message": {"role": "assistant", "content": "ready"},
                "prompt_eval_count": 7 if len(payloads) == 1 else 9_000,
                "eval_count": 1,
                "ignored": "PROVIDER_BODY_SENTINEL",
            }
        )

    local = readiness.probe_local_ollama(
        opener=opener,
        monotonic=ticking_clock(10.0, 10.125),
    )

    def runner(argv, **kwargs):
        recorded["argv"] = argv
        recorded["runner_kwargs"] = kwargs
        completion = "DIFY_COMPLETION_DO_NOT_PERSIST"
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "response_nonempty": True,
                    "response_length": len(completion),
                    "response_sha256": hashlib.sha256(completion.encode()).hexdigest(),
                    "source_bytes": readiness.EXTRACTOR_MAX_CHUNK_SOURCE_BYTES,
                    "source_sha256": readiness.EXTRACTOR_SYNTHETIC_SOURCE_SHA256,
                    "prompt_tokens": 9_000,
                    "completion_tokens": 1,
                    "num_ctx": readiness.EXTRACTOR_NUM_CTX,
                    "num_predict": readiness.EXTRACTOR_NUM_PREDICT,
                    "max_chunk_source_bytes": readiness.EXTRACTOR_MAX_CHUNK_SOURCE_BYTES,
                    "max_ollama_calls": readiness.EXTRACTOR_MAX_OLLAMA_CALLS,
                    "page_count": 1,
                    "candidate_page_count": 1,
                    "initial_chunk_count": 1,
                    "ollama_call_count": 1,
                    "successful_chunk_count": 1,
                    "split_retry_count": 0,
                    "failed_chunk_count": 0,
                }
            ),
            stderr="STDERR_SENTINEL",
        )

    extractor = readiness.probe_extractor_boundary(
        runner=runner,
        token="x" * 32,
        monotonic=ticking_clock(20.0, 20.25),
    )
    evidence = readiness.build_evidence(
        local,
        extractor,
        timestamp="2026-08-29T00:00:00Z",
    )

    assert recorded["url"] == "http://127.0.0.1:11434/api/chat"
    assert recorded["timeout"] == 120
    assert payloads[0] == {
        "model": "qwen3:8b",
        "messages": [{"role": "system", "content": ""}],
        "stream": False,
        "think": False,
        "options": {
            "num_ctx": readiness.OLLAMA_CONTEXT_NUM_CTX,
            "num_predict": readiness.OLLAMA_CONTEXT_NUM_PREDICT,
        },
    }
    assert payloads[1]["model"] == "qwen3:8b"
    assert payloads[1]["messages"][0]["role"] == "system"
    assert (
        len(payloads[1]["messages"][0]["content"].encode("utf-8"))
        == readiness.OLLAMA_PRODUCTION_INPUT_BYTES
    )
    assert payloads[1]["options"] == payloads[0]["options"]
    assert payloads[1]["think"] is False
    assert recorded["argv"][:5] == [
        "docker",
        "exec",
        "-i",
        "docker-api-1",
        "/app/api/.venv/bin/python",
    ]
    assert recorded["runner_kwargs"]["timeout"] == 120
    assert recorded["runner_kwargs"]["capture_output"] is True
    assert recorded["runner_kwargs"]["text"] is True
    child_source = recorded["argv"][-1]
    assert readiness.EXTRACTOR_READINESS_URL in child_source
    assert readiness.EXTRACTOR_HEALTH_URL in child_source
    assert readiness.OLLAMA_MODEL in child_source
    assert "X-Extractor-Token" in child_source
    assert 'document.get("source_bytes")' in child_source
    assert 'document.get("prompt_tokens")' in child_source
    assert 'document.get("completion_tokens")' in child_source
    assert 'document.get("diagnostics")' not in child_source
    assert evidence["status"] == "ready"
    assert set(evidence) == {"schema", "timestamp", "status", "probes"}
    assert set(evidence["probes"]) == {"direct_ollama", "extractor_boundary"}
    for probe in evidence["probes"].values():
        assert_allowlisted_probe(probe)
        assert probe["status"] == "ready"
        assert probe["response_nonempty"] is True
        assert probe["response_length"] > 0
        assert len(probe["response_sha256"]) == 64
    assert evidence["probes"]["direct_ollama"]["input_bytes"] == readiness.OLLAMA_PRODUCTION_INPUT_BYTES
    assert evidence["probes"]["direct_ollama"]["prompt_tokens"] == 9_000
    assert evidence["probes"]["extractor_boundary"]["source_bytes"] == readiness.EXTRACTOR_MAX_CHUNK_SOURCE_BYTES
    assert evidence["probes"]["extractor_boundary"]["prompt_tokens"] == 9_000
    assert_private_text_absent(evidence)


@pytest.mark.parametrize(
    ("payload", "category"),
    [
        ({"done": False, "response": "RESPONSE_SENTINEL_DO_NOT_PERSIST"}, "incomplete_response"),
        ({"done": True, "response": ""}, "empty_response"),
    ],
)
def test_local_probe_requires_done_and_nonempty_response(payload: object, category: str) -> None:
    result = readiness.probe_local_ollama(
        opener=lambda *_args, **_kwargs: FakeHttpResponse(payload),
        monotonic=ticking_clock(1.0, 1.1),
    )

    assert result["status"] == "failed"
    assert result["failure_phase"] == "validation"
    assert result["failure_category"] == category
    assert result["response_nonempty"] is False
    assert result["response_length"] == 0
    assert result["response_sha256"] is None
    assert_private_text_absent(result)


@pytest.mark.parametrize(
    ("outcome", "category"),
    [
        (SimpleNamespace(returncode=0, stdout='{"ok":true,"response_nonempty":false,"response_length":0,"response_sha256":null}', stderr="STDERR_SENTINEL"), "empty_response"),
        (SimpleNamespace(returncode=7, stdout="RESPONSE_SENTINEL_DO_NOT_PERSIST", stderr="STDERR_SENTINEL"), "subprocess_failed"),
        (subprocess.TimeoutExpired(["docker"], 120, output="RESPONSE_SENTINEL_DO_NOT_PERSIST", stderr="STDERR_SENTINEL"), "timeout"),
    ],
)
def test_dify_failures_map_to_safe_categories_without_raw_leakage(outcome: object, category: str) -> None:
    def runner(*_args, **_kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    result = readiness.probe_dify_model_boundary(
        runner=runner,
        monotonic=ticking_clock(1.0, 1.2),
    )

    assert result["status"] == "failed"
    assert result["failure_category"] == category
    assert result["failure_phase"] in {"transport", "validation"}
    assert_private_text_absent(result)


def test_dify_probe_rejects_boolean_response_length() -> None:
    digest = hashlib.sha256(b"x").hexdigest()

    result = readiness.probe_dify_model_boundary(
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "response_nonempty": True,
                    "response_length": True,
                    "response_sha256": digest,
                }
            ),
            stderr="STDERR_SENTINEL",
        ),
        monotonic=ticking_clock(1.0, 1.1),
    )

    assert result["status"] == "failed"
    assert result["failure_phase"] == "validation"
    assert result["failure_category"] == "invalid_result"
    assert_private_text_absent(result)


@pytest.mark.parametrize(
    ("outcome", "category"),
    [
        (HTTPError("http://local", 500, "EXCEPTION_SENTINEL", {}, None), "http_error"),
        (ValueError("EXCEPTION_SENTINEL"), "invalid_json"),
        ({"done": False, "error": "PROVIDER_BODY_SENTINEL"}, "provider_error"),
    ],
)
def test_local_transport_json_and_provider_errors_are_safely_classified(
    outcome: object,
    category: str,
) -> None:
    def opener(*_args, **_kwargs):
        if isinstance(outcome, HTTPError):
            raise outcome
        if isinstance(outcome, ValueError):
            class InvalidJsonResponse(FakeHttpResponse):
                def read(self) -> bytes:
                    return b"{EXCEPTION_SENTINEL"

            return InvalidJsonResponse({})
        return FakeHttpResponse(outcome)

    result = readiness.probe_local_ollama(
        opener=opener,
        monotonic=ticking_clock(1.0, 1.2),
    )

    assert result["status"] == "failed"
    assert result["failure_category"] == category
    assert_private_text_absent(result)


def test_atomic_evidence_write_uses_sibling_then_replace(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "readiness.json"
    destination.write_text('{"old":true}', encoding="utf-8")
    calls: list[tuple[Path, Path, str]] = []
    real_replace = readiness.os.replace

    def replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        calls.append((source_path, target_path, source_path.read_text(encoding="utf-8")))
        real_replace(source, target)

    monkeypatch.setattr(readiness.os, "replace", replace)
    evidence = {
        "schema": "ollama-readiness/v1",
        "timestamp": "2026-08-29T00:00:00Z",
        "status": "failed",
        "probes": {},
    }

    readiness.write_evidence_atomic(destination, evidence)

    assert len(calls) == 1
    temporary, target, temporary_contents = calls[0]
    assert temporary.parent == destination.parent
    assert temporary != destination
    assert target == destination
    assert json.loads(temporary_contents) == evidence
    assert json.loads(destination.read_text(encoding="utf-8")) == evidence
    assert list(tmp_path.iterdir()) == [destination]


def test_cli_stdout_and_evidence_are_privacy_safe_on_failure(tmp_path: Path, capsys) -> None:
    destination = tmp_path / "readiness.json"
    calls: list[str] = []

    def failed_local() -> dict[str, object]:
        calls.append("local")
        return readiness.failed_probe(
            provider="ollama",
            mode="direct_http",
            duration_seconds=0.1,
            phase="transport",
            category="connection_error",
        )

    def dify_must_not_run() -> dict[str, object]:
        calls.append("dify")
        raise AssertionError("EXCEPTION_SENTINEL")

    exit_code = readiness.main(
        ["--output", str(destination)],
        local_probe_fn=failed_local,
        dify_probe_fn=dify_must_not_run,
    )

    stdout = capsys.readouterr().out
    persisted = destination.read_text(encoding="utf-8")
    assert exit_code == 1
    assert calls == ["local"]
    output_document = json.loads(stdout)
    assert output_document == json.loads(persisted)
    assert output_document["probes"]["extractor_boundary"]["status"] == "not_run"
    assert output_document["probes"]["extractor_boundary"]["failure_category"] == "prerequisite_failed"
    assert_private_text_absent(stdout)
    assert_private_text_absent(persisted)


def test_evidence_builder_drops_unrecognized_probe_fields() -> None:
    local = readiness.failed_probe(
        provider="ollama",
        mode="direct_http",
        duration_seconds=0.1,
        phase="transport",
        category="connection_error",
    )
    local["raw_response"] = "RESPONSE_SENTINEL_DO_NOT_PERSIST"
    local["exception"] = "EXCEPTION_SENTINEL"
    dify = readiness.failed_probe(
        provider=readiness.OLLAMA_PROVIDER,
        mode="dify_model_boundary",
        duration_seconds=0.2,
        phase="transport",
        category="subprocess_failed",
    )
    dify["stderr"] = "STDERR_SENTINEL"

    evidence = readiness.build_evidence(local, dify)

    for probe in evidence["probes"].values():
        assert_allowlisted_probe(probe)
    assert_private_text_absent(evidence)


def test_evidence_builder_downgrades_malformed_ready_probe() -> None:
    digest = hashlib.sha256(b"x").hexdigest()
    malformed_ready = {
        "status": "ready",
        "provider": readiness.OLLAMA_PROVIDER,
        "model": readiness.OLLAMA_MODEL,
        "mode": "extractor_boundary",
        "duration_seconds": 0.1,
        "response_nonempty": True,
        "response_length": True,
        "response_sha256": digest,
    }
    local_ready = {
        **malformed_ready,
        "provider": "ollama",
        "mode": "direct_http",
        "response_length": 1,
    }

    evidence = readiness.build_evidence(local_ready, malformed_ready)

    dify = evidence["probes"]["extractor_boundary"]
    assert evidence["status"] == "failed"
    assert dify["status"] == "failed"
    assert dify["failure_phase"] == "validation"
    assert dify["failure_category"] == "invalid_result"
    assert dify["response_nonempty"] is False
    assert dify["response_length"] == 0
    assert dify["response_sha256"] is None
