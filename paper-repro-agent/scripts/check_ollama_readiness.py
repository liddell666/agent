from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_multimodel_dsl import (  # noqa: E402
    OLLAMA_CHAT_OVERHEAD_TOKENS,
    OLLAMA_COMPLETION_RESERVE_TOKENS,
    OLLAMA_CONTEXT_NUM_CTX,
    OLLAMA_CONTEXT_NUM_PREDICT,
    OLLAMA_FIXED_PROMPT_UTF8_BYTES,
    OLLAMA_MAX_PROTOCOL_NOTES_UTF8_BYTES,
    OLLAMA_PARSER_PAYLOAD_BYTES,
    build_prepare_dsl,
)


CANDIDATE_APP_ID = "17fe51d4-091f-4729-87ee-3c0a2e920918"
OLLAMA_PROVIDER = "langgenius/ollama/ollama"
OLLAMA_MODEL = "qwen3:8b"
LOCAL_OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_TIMEOUT_SECONDS = 120
SCHEMA = "ollama-readiness/v1"
OLLAMA_MAX_PROMPT_TOKENS = OLLAMA_CONTEXT_NUM_CTX - OLLAMA_CONTEXT_NUM_PREDICT
OLLAMA_MAX_PROTOCOL_NOTES_CHARS = 512
OLLAMA_PRODUCTION_INPUT_BYTES = (
    OLLAMA_FIXED_PROMPT_UTF8_BYTES
    + OLLAMA_PARSER_PAYLOAD_BYTES
    + OLLAMA_MAX_PROTOCOL_NOTES_UTF8_BYTES
)
PARSER_PLACEHOLDER = "{{#2900000000003.parsed_json#}}"
NOTES_PLACEHOLDER = "{{#2900000000001.protocol_notes#}}"
ALLOWED_FAILURE_PHASES = {"transport", "validation", "prerequisite"}
ALLOWED_FAILURE_CATEGORIES = {
    "connection_error",
    "context_overflow",
    "empty_response",
    "http_error",
    "incomplete_response",
    "invalid_json",
    "invalid_result",
    "prerequisite_failed",
    "provider_error",
    "subprocess_failed",
    "timeout",
    "unexpected_error",
}


def _prompt_template() -> str:
    document = build_prepare_dsl("ollama")
    nodes = document["workflow"]["graph"]["nodes"]
    node = next(
        item for item in nodes if item["data"].get("title") == "extract_paper_dossier"
    )
    messages = node["data"].get("prompt_template", [])
    if len(messages) != 1 or messages[0].get("role") != "system":
        raise RuntimeError("Ollama production prompt must contain one system message")
    text = messages[0].get("text")
    if not isinstance(text, str):
        raise RuntimeError("Ollama production system prompt is not text")
    if text.count(PARSER_PLACEHOLDER) != 1 or text.count(NOTES_PLACEHOLDER) != 1:
        raise RuntimeError("Ollama production prompt substitutions are not stable")
    return text


PRODUCTION_PROMPT_TEMPLATE = _prompt_template()
PRODUCTION_FIXED_PROMPT_BYTES = len(
    PRODUCTION_PROMPT_TEMPLATE.replace(PARSER_PLACEHOLDER, "").replace(
        NOTES_PLACEHOLDER, ""
    ).encode("utf-8")
)
if PRODUCTION_FIXED_PROMPT_BYTES != OLLAMA_FIXED_PROMPT_UTF8_BYTES:
    raise RuntimeError("Ollama fixed prompt byte budget changed; redesign required")


def render_production_prompt(parser_payload: str, protocol_notes: str) -> str:
    if not isinstance(parser_payload, str) or not isinstance(protocol_notes, str):
        raise TypeError("production prompt substitutions must be strings")
    return PRODUCTION_PROMPT_TEMPLATE.replace(PARSER_PLACEHOLDER, parser_payload).replace(
        NOTES_PLACEHOLDER, protocol_notes
    )


SYNTHETIC_PROMPT = render_production_prompt(
    "x" * OLLAMA_PARSER_PAYLOAD_BYTES,
    "😀" * OLLAMA_MAX_PROTOCOL_NOTES_CHARS,
)
SYNTHETIC_PROMPT_BYTES = len(SYNTHETIC_PROMPT.encode("utf-8"))
if SYNTHETIC_PROMPT_BYTES != OLLAMA_PRODUCTION_INPUT_BYTES:
    raise RuntimeError("Ollama synthetic production prompt byte budget changed")


def _safe_duration(start: float, end: float, timeout: float) -> float:
    return round(min(max(end - start, 0.0), max(timeout, 0.0)), 6)


def _response_metadata(content: str) -> dict[str, object]:
    encoded = content.encode("utf-8")
    return {
        "response_nonempty": True,
        "response_length": len(content),
        "response_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _empty_response_metadata() -> dict[str, object]:
    return {
        "response_nonempty": False,
        "response_length": 0,
        "response_sha256": None,
    }


def failed_probe(
    *,
    provider: str,
    mode: str,
    duration_seconds: float,
    phase: str,
    category: str,
) -> dict[str, object]:
    safe_phase = phase if phase in ALLOWED_FAILURE_PHASES else "validation"
    safe_category = category if category in ALLOWED_FAILURE_CATEGORIES else "unexpected_error"
    return {
        "status": "failed",
        "provider": provider,
        "model": OLLAMA_MODEL,
        "mode": mode,
        "duration_seconds": duration_seconds,
        **_empty_response_metadata(),
        "failure_phase": safe_phase,
        "failure_category": safe_category,
    }


def _successful_probe(
    *,
    provider: str,
    mode: str,
    duration_seconds: float,
    response: str | None = None,
    response_metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    metadata = dict(response_metadata or _response_metadata(response or ""))
    return {
        "status": "ready",
        "provider": provider,
        "model": OLLAMA_MODEL,
        "mode": mode,
        "duration_seconds": duration_seconds,
        **metadata,
    }


class _ProbeFailure(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _validate_prompt_tokens(value: object, *, limit: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _ProbeFailure("invalid_result")
    if value > limit:
        raise _ProbeFailure("context_overflow")
    return value


def _boundary_metadata(
    *,
    empty_prompt_tokens: int,
    prompt_tokens: int,
    response: str,
) -> dict[str, object]:
    encoded_prompt = SYNTHETIC_PROMPT.encode("utf-8")
    return {
        "response_nonempty": True,
        "response_length": len(response),
        "response_sha256": hashlib.sha256(response.encode("utf-8")).hexdigest(),
        "input_bytes": len(encoded_prompt),
        "input_sha256": hashlib.sha256(encoded_prompt).hexdigest(),
        "empty_prompt_tokens": empty_prompt_tokens,
        "prompt_tokens": prompt_tokens,
        "prompt_token_limit": OLLAMA_MAX_PROMPT_TOKENS,
        "num_ctx": OLLAMA_CONTEXT_NUM_CTX,
        "num_predict": OLLAMA_CONTEXT_NUM_PREDICT,
        "think": False,
    }


def _parse_local_response(raw_body: bytes, *, prompt_token_limit: int) -> tuple[str, int]:
    try:
        document = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise _ProbeFailure("invalid_json") from exc
    if not isinstance(document, dict):
        raise _ProbeFailure("invalid_result")
    if document.get("error"):
        raise _ProbeFailure("provider_error")
    if document.get("done") is not True:
        raise _ProbeFailure("incomplete_response")
    message = document.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise _ProbeFailure("empty_response")
    prompt_tokens = document.get("prompt_eval_count")
    return content, _validate_prompt_tokens(
        prompt_tokens,
        limit=prompt_token_limit,
    )


def _local_request(
    prompt: str,
    *,
    opener: Callable[..., Any],
    timeout: float,
    prompt_token_limit: int,
) -> tuple[str, int]:
    payload = json.dumps(
        {
            "model": OLLAMA_MODEL,
            "messages": [{"role": "system", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {
                "num_ctx": OLLAMA_CONTEXT_NUM_CTX,
                "num_predict": OLLAMA_CONTEXT_NUM_PREDICT,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        LOCAL_OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener(request, timeout=timeout) as response:
        return _parse_local_response(
            response.read(),
            prompt_token_limit=prompt_token_limit,
        )


def probe_local_ollama(
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    start = monotonic()
    phase = "transport"
    category = "connection_error"
    try:
        phase = "validation"
        empty_response, empty_prompt_tokens = _local_request(
            "",
            opener=opener,
            timeout=timeout,
            prompt_token_limit=OLLAMA_CHAT_OVERHEAD_TOKENS,
        )
        _ = empty_response
        full_response, prompt_tokens = _local_request(
            SYNTHETIC_PROMPT,
            opener=opener,
            timeout=timeout,
            prompt_token_limit=OLLAMA_MAX_PROMPT_TOKENS,
        )
        duration = _safe_duration(start, monotonic(), timeout)
        return _successful_probe(
            provider="ollama",
            mode="direct_http",
            duration_seconds=duration,
            response_metadata=_boundary_metadata(
                empty_prompt_tokens=empty_prompt_tokens,
                prompt_tokens=prompt_tokens,
                response=full_response,
            ),
        )
    except _ProbeFailure as failure:
        category = failure.category
    except HTTPError:
        phase, category = "transport", "http_error"
    except (TimeoutError, subprocess.TimeoutExpired):
        phase, category = "transport", "timeout"
    except URLError:
        phase, category = "transport", "connection_error"
    except json.JSONDecodeError:
        phase, category = "validation", "invalid_json"
    except Exception:
        pass
    duration = _safe_duration(start, monotonic(), timeout)
    return failed_probe(
        provider="ollama",
        mode="direct_http",
        duration_seconds=duration,
        phase=phase,
        category=category,
    )


DIFY_CHILD_SOURCE = f'''import contextlib
import hashlib
import io
import json

APP_ID = {CANDIDATE_APP_ID!r}
PROVIDER = {OLLAMA_PROVIDER!r}
MODEL = {OLLAMA_MODEL!r}
EMPTY_PROMPT = ""
# render_production_prompt output is embedded below after content-free rendering.
PROMPT = {SYNTHETIC_PROMPT!r}
PROMPT_BYTES = {SYNTHETIC_PROMPT_BYTES}
PROMPT_TOKEN_LIMIT = {OLLAMA_MAX_PROMPT_TOKENS}
CHAT_OVERHEAD_LIMIT = {OLLAMA_CHAT_OVERHEAD_TOKENS}
NUM_CTX = {OLLAMA_CONTEXT_NUM_CTX}
NUM_PREDICT = {OLLAMA_CONTEXT_NUM_PREDICT}

def _prompt_tokens(result, limit):
    usage = getattr(result, "usage", None)
    value = getattr(usage, "prompt_tokens", None)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("invalid prompt token count")
    if value > limit:
        raise OverflowError("prompt exceeds reserved context")
    return value

def _invoke(model_instance, content, limit):
    result = model_instance.invoke_llm(
        prompt_messages=[SystemPromptMessage(content=content)],
        model_parameters={{"think": False, "num_ctx": NUM_CTX, "num_predict": NUM_PREDICT}},
        tools=[],
        stop=[],
        stream=False,
    )
    completion = result.message.get_text_content()
    if not isinstance(completion, str) or not completion.strip():
        raise RuntimeError("empty completion")
    return completion, _prompt_tokens(result, limit)

def main():
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            from app import app as flask_app
            from core.model_manager import ModelManager
            from extensions.ext_database import db
            from graphon.model_runtime.entities.message_entities import SystemPromptMessage
            from graphon.model_runtime.entities.model_entities import ModelType
            from models.model import App

            with flask_app.app_context():
                candidate = db.session.get(App, APP_ID)
                if candidate is None:
                    raise RuntimeError("candidate not found")
                model_instance = ModelManager.for_tenant(candidate.tenant_id).get_model_instance(
                    candidate.tenant_id,
                    PROVIDER,
                    ModelType.LLM,
                    MODEL,
                )
                _, empty_prompt_tokens = _invoke(
                    model_instance, EMPTY_PROMPT, CHAT_OVERHEAD_LIMIT
                )
                completion, prompt_tokens = _invoke(
                    model_instance, PROMPT, PROMPT_TOKEN_LIMIT
                )
        encoded_prompt = PROMPT.encode("utf-8")
        encoded = completion.encode("utf-8")
        print(json.dumps({{
            "ok": True,
            "response_nonempty": True,
            "response_length": len(completion),
            "response_sha256": hashlib.sha256(encoded).hexdigest(),
            "input_bytes": len(encoded_prompt),
            "input_sha256": hashlib.sha256(encoded_prompt).hexdigest(),
            "empty_prompt_tokens": empty_prompt_tokens,
            "prompt_tokens": prompt_tokens,
            "prompt_token_limit": PROMPT_TOKEN_LIMIT,
            "num_ctx": NUM_CTX,
            "num_predict": NUM_PREDICT,
            "think": False,
        }}, sort_keys=True))
        return 0
    except BaseException:
        print(json.dumps({{"ok": False}}, sort_keys=True))
        return 2

raise SystemExit(main())
'''


def probe_dify_model_boundary(
    *,
    runner: Callable[..., Any] = subprocess.run,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    start = monotonic()
    phase = "transport"
    category = "subprocess_failed"
    try:
        completed = runner(
            [
                "docker",
                "exec",
                "docker-api-1",
                "/app/api/.venv/bin/python",
                "-c",
                DIFY_CHILD_SOURCE,
            ],
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("child failed")
        phase, category = "validation", "invalid_result"
        document = json.loads(completed.stdout)
        if not isinstance(document, dict) or document.get("ok") is not True:
            raise ValueError("invalid child result")
        nonempty = document.get("response_nonempty")
        length = document.get("response_length")
        digest = document.get("response_sha256")
        if not isinstance(length, int) or isinstance(length, bool):
            raise ValueError("invalid response length")
        if nonempty is not True or length <= 0:
            category = "empty_response"
            raise ValueError("empty child result")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("invalid response digest")
        input_bytes = document.get("input_bytes")
        input_digest = document.get("input_sha256")
        empty_prompt_tokens = document.get("empty_prompt_tokens")
        prompt_tokens = document.get("prompt_tokens")
        prompt_token_limit = document.get("prompt_token_limit")
        num_ctx = document.get("num_ctx")
        num_predict = document.get("num_predict")
        think = document.get("think")
        if (
            input_bytes != OLLAMA_PRODUCTION_INPUT_BYTES
            or not isinstance(input_digest, str)
            or len(input_digest) != 64
            or any(character not in "0123456789abcdef" for character in input_digest)
            or prompt_token_limit != OLLAMA_MAX_PROMPT_TOKENS
            or num_ctx != OLLAMA_CONTEXT_NUM_CTX
            or num_predict != OLLAMA_CONTEXT_NUM_PREDICT
            or think is not False
        ):
            raise ValueError("invalid production boundary metadata")
        empty_prompt_tokens = _validate_prompt_tokens(
            empty_prompt_tokens,
            limit=OLLAMA_CHAT_OVERHEAD_TOKENS,
        )
        prompt_tokens = _validate_prompt_tokens(
            prompt_tokens,
            limit=OLLAMA_MAX_PROMPT_TOKENS,
        )
        duration = _safe_duration(start, monotonic(), timeout)
        return _successful_probe(
            provider=OLLAMA_PROVIDER,
            mode="dify_model_boundary",
            duration_seconds=duration,
            response_metadata={
                "response_nonempty": True,
                "response_length": length,
                "response_sha256": digest,
                "input_bytes": input_bytes,
                "input_sha256": input_digest,
                "empty_prompt_tokens": empty_prompt_tokens,
                "prompt_tokens": prompt_tokens,
                "prompt_token_limit": prompt_token_limit,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "think": think,
            },
        )
    except _ProbeFailure as failure:
        category = failure.category
    except subprocess.TimeoutExpired:
        phase, category = "transport", "timeout"
    except json.JSONDecodeError:
        phase, category = "validation", "invalid_result"
    except Exception:
        pass
    duration = _safe_duration(start, monotonic(), timeout)
    return failed_probe(
        provider=OLLAMA_PROVIDER,
        mode="dify_model_boundary",
        duration_seconds=duration,
        phase=phase,
        category=category,
    )


def build_evidence(
    local_probe: Mapping[str, object],
    dify_probe: Mapping[str, object] | None,
    *,
    timestamp: str | None = None,
) -> dict[str, object]:
    def sanitize(
        probe: Mapping[str, object],
        *,
        provider: str,
        mode: str,
    ) -> dict[str, object]:
        status = probe.get("status")
        if status not in {"ready", "failed", "not_run"}:
            status = "failed"
        duration = probe.get("duration_seconds")
        safe_duration = (
            round(min(max(float(duration), 0.0), DEFAULT_TIMEOUT_SECONDS), 6)
            if isinstance(duration, (int, float)) and not isinstance(duration, bool)
            else 0.0
        )
        length = probe.get("response_length")
        digest = probe.get("response_sha256")
        response_valid = (
            probe.get("response_nonempty") is True
            and isinstance(length, int)
            and not isinstance(length, bool)
            and length > 0
            and isinstance(digest, str)
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
        )
        input_bytes = probe.get("input_bytes")
        input_digest = probe.get("input_sha256")
        empty_prompt_tokens = probe.get("empty_prompt_tokens")
        prompt_tokens = probe.get("prompt_tokens")
        boundary_valid = (
            isinstance(input_bytes, int)
            and not isinstance(input_bytes, bool)
            and input_bytes == OLLAMA_PRODUCTION_INPUT_BYTES
            and isinstance(input_digest, str)
            and len(input_digest) == 64
            and all(character in "0123456789abcdef" for character in input_digest)
            and isinstance(empty_prompt_tokens, int)
            and not isinstance(empty_prompt_tokens, bool)
            and 0 < empty_prompt_tokens <= OLLAMA_CHAT_OVERHEAD_TOKENS
            and isinstance(prompt_tokens, int)
            and not isinstance(prompt_tokens, bool)
            and 0 < prompt_tokens <= OLLAMA_MAX_PROMPT_TOKENS
            and probe.get("prompt_token_limit") == OLLAMA_MAX_PROMPT_TOKENS
            and probe.get("num_ctx") == OLLAMA_CONTEXT_NUM_CTX
            and probe.get("num_predict") == OLLAMA_CONTEXT_NUM_PREDICT
            and probe.get("think") is False
        )
        response_valid = response_valid and boundary_valid
        malformed_ready = status == "ready" and not response_valid
        if malformed_ready:
            status = "failed"
        safe: dict[str, object] = {
            "status": status,
            "provider": provider,
            "model": OLLAMA_MODEL,
            "mode": mode,
            "duration_seconds": safe_duration,
            **(
                {
                    "response_nonempty": True,
                    "response_length": length,
                    "response_sha256": digest,
                    "input_bytes": input_bytes,
                    "input_sha256": input_digest,
                    "empty_prompt_tokens": empty_prompt_tokens,
                    "prompt_tokens": prompt_tokens,
                    "prompt_token_limit": probe["prompt_token_limit"],
                    "num_ctx": probe["num_ctx"],
                    "num_predict": probe["num_predict"],
                    "think": False,
                }
                if response_valid
                else _empty_response_metadata()
            ),
        }
        if status != "ready":
            phase = "validation" if malformed_ready else probe.get("failure_phase")
            category = "invalid_result" if malformed_ready else probe.get("failure_category")
            safe["failure_phase"] = (
                phase if phase in ALLOWED_FAILURE_PHASES else "validation"
            )
            safe["failure_category"] = (
                category if category in ALLOWED_FAILURE_CATEGORIES else "unexpected_error"
            )
        return safe

    local_safe = sanitize(local_probe, provider="ollama", mode="direct_http")
    if dify_probe is None:
        dify_probe = failed_probe(
            provider=OLLAMA_PROVIDER,
            mode="dify_model_boundary",
            duration_seconds=0.0,
            phase="prerequisite",
            category="prerequisite_failed",
        )
        dify_probe["status"] = "not_run"
    dify_safe = sanitize(
        dify_probe,
        provider=OLLAMA_PROVIDER,
        mode="dify_model_boundary",
    )
    probes: dict[str, object] = {
        "local_ollama": local_safe,
        "dify_model_boundary": dify_safe,
    }
    ready = dify_probe is not None and all(
        probe.get("status") == "ready" for probe in (local_safe, dify_safe)
    )
    return {
        "schema": SCHEMA,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "ready" if ready else "failed",
        "probes": probes,
    }


def write_evidence_atomic(path: Path, evidence: Mapping[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(evidence, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def main(
    argv: Sequence[str] | None = None,
    *,
    local_probe_fn: Callable[[], dict[str, object]] = probe_local_ollama,
    dify_probe_fn: Callable[[], dict[str, object]] = probe_dify_model_boundary,
) -> int:
    parser = argparse.ArgumentParser(description="Run privacy-safe Ollama readiness probes.")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)

    local_probe = local_probe_fn()
    dify_probe = None
    if local_probe.get("status") == "ready":
        dify_probe = dify_probe_fn()
    evidence = build_evidence(local_probe, dify_probe)
    write_evidence_atomic(arguments.output, evidence)
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
