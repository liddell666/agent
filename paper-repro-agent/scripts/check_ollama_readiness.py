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
EXTRACTOR_HEALTH_URL = "http://paper-dossier-extractor:8002/healthz"
EXTRACTOR_URL = "http://paper-dossier-extractor:8002/v1/extract-dossier"
EXTRACTOR_READINESS_URL = "http://paper-dossier-extractor:8002/v1/readiness/extractor"
EXTRACTOR_TOKEN_ENV = "PAPER_DOSSIER_EXTRACTOR_API_TOKEN"
EXTRACTOR_DIFY_TOKEN_ENV = "DIFY_EXTRACTOR_API_TOKEN"
EXTRACTOR_NUM_CTX = 16_384
EXTRACTOR_NUM_PREDICT = 1_536
EXTRACTOR_MAX_PROMPT_TOKENS = EXTRACTOR_NUM_CTX - EXTRACTOR_NUM_PREDICT
EXTRACTOR_MAX_CHUNK_SOURCE_BYTES = 8_192
EXTRACTOR_MAX_OLLAMA_CALLS = 12
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


SYNTHETIC_PAGE_TEXT = (
    "Synthetic readiness results for a regression dataset and method. "
    "RMSE is reported for the regression task. "
    + "x" * 20_000
)


def _serialize_synthetic_source(text: str) -> str:
    return json.dumps(
        {"page": 1, "kinds": ["text"], "text": text},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _clip_synthetic_source(text: str, limit: int) -> str:
    marker = "\n...[truncated for chunk budget]...\n"
    encoded = text.encode("utf-8")
    if len(_serialize_synthetic_source(text).encode("utf-8")) <= limit:
        return text
    marker_bytes = marker.encode("utf-8")
    low = len(marker_bytes)
    high = len(encoded)
    best = marker
    while low <= high:
        text_limit = (low + high) // 2
        remaining = text_limit - len(marker_bytes)
        head_limit = remaining // 2
        tail_limit = remaining - head_limit
        candidate = (
            encoded[:head_limit].decode("utf-8", errors="ignore")
            + marker
            + encoded[-tail_limit:].decode("utf-8", errors="ignore")
            if tail_limit
            else encoded[:head_limit].decode("utf-8", errors="ignore") + marker
        )
        if len(_serialize_synthetic_source(candidate).encode("utf-8")) <= limit:
            best = candidate
            low = text_limit + 1
        else:
            high = text_limit - 1
    return best


SYNTHETIC_SOURCE_TEXT = _clip_synthetic_source(
    SYNTHETIC_PAGE_TEXT,
    EXTRACTOR_MAX_CHUNK_SOURCE_BYTES,
)
SYNTHETIC_SOURCE = _serialize_synthetic_source(SYNTHETIC_SOURCE_TEXT)
SYNTHETIC_SOURCE_BYTES = len(SYNTHETIC_SOURCE.encode("utf-8"))
if SYNTHETIC_SOURCE_BYTES != EXTRACTOR_MAX_CHUNK_SOURCE_BYTES:
    raise RuntimeError("extractor synthetic source budget changed")
EXTRACTOR_SYNTHETIC_SOURCE_SHA256 = hashlib.sha256(
    SYNTHETIC_SOURCE.encode("utf-8")
).hexdigest()


def _synthetic_paper_json() -> str:
    return json.dumps(
        {
            "document_id": "readiness-synthetic-document",
            "file_name": "readiness-synthetic.pdf",
            "page_count": 1,
            "markdown": "",
            "elements": [
                {
                    "kind": "text",
                    "page": 1,
                    "text": SYNTHETIC_PAGE_TEXT,
                }
            ],
            "warnings": [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


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
    empty_completion_tokens: int,
    completion_tokens: int,
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
        "empty_completion_tokens": empty_completion_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "prompt_token_limit": OLLAMA_MAX_PROMPT_TOKENS,
        "num_ctx": OLLAMA_CONTEXT_NUM_CTX,
        "num_predict": OLLAMA_CONTEXT_NUM_PREDICT,
        "think": False,
    }


def _parse_local_response(
    raw_body: bytes,
    *,
    prompt_token_limit: int,
) -> tuple[str, int, int]:
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
    completion_tokens = document.get("eval_count")
    return (
        content,
        _validate_prompt_tokens(prompt_tokens, limit=prompt_token_limit),
        _validate_prompt_tokens(completion_tokens, limit=OLLAMA_CONTEXT_NUM_PREDICT),
    )


def _local_request(
    prompt: str,
    *,
    opener: Callable[..., Any],
    timeout: float,
    prompt_token_limit: int,
) -> tuple[str, int, int]:
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
        empty_response, empty_prompt_tokens, empty_completion_tokens = _local_request(
            "",
            opener=opener,
            timeout=timeout,
            prompt_token_limit=OLLAMA_CHAT_OVERHEAD_TOKENS,
        )
        _ = empty_response
        full_response, prompt_tokens, completion_tokens = _local_request(
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
                empty_completion_tokens=empty_completion_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
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
            globals()["SystemPromptMessage"] = SystemPromptMessage

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


EXTRACTOR_CHILD_SOURCE = f'''import contextlib
import hashlib
import json
import sys
import urllib.error
import urllib.request

HEALTH_URL = {EXTRACTOR_HEALTH_URL!r}
READINESS_URL = {EXTRACTOR_READINESS_URL!r}
MODEL = {OLLAMA_MODEL!r}
TIMEOUT = {DEFAULT_TIMEOUT_SECONDS}

def _failure_category(error):
    if isinstance(error, urllib.error.HTTPError):
        return "http_error"
    if isinstance(error, urllib.error.URLError):
        return "connection_error"
    if isinstance(error, TimeoutError):
        return "timeout"
    return "invalid_result"

def _positive_count(value, *, allow_zero=False):
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("invalid count")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError("invalid count")
    return value

def _safe_digest(value):
    if not isinstance(value, str):
        raise ValueError("invalid digest")
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("invalid digest")
    return value

def main():
    safe_output = None
    try:
        token = sys.stdin.read().strip()
        if not token:
            raise ValueError("missing input")
        sink = __import__("io").StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            health_request = urllib.request.Request(HEALTH_URL, method="GET")
            with urllib.request.urlopen(health_request, timeout=TIMEOUT) as response:
                health = json.loads(response.read())
            if health != {{
                "status": "ok",
                "service": "paper-dossier-extractor",
                "model": MODEL,
            }}:
                raise ValueError("invalid extractor health")

            readiness_request = urllib.request.Request(
                READINESS_URL,
                headers={{
                    "X-Extractor-Token": token,
                }},
                method="GET",
            )
            with urllib.request.urlopen(
                readiness_request, timeout=TIMEOUT
            ) as response:
                raw_body = response.read()
            document = json.loads(raw_body)

        if not isinstance(document, dict) or document.get("status") != "ready":
            raise ValueError("extractor did not return ready")
        safe_output = {{
            "ok": True,
            "response_nonempty": bool(raw_body),
            "response_length": len(raw_body),
            "response_sha256": hashlib.sha256(raw_body).hexdigest(),
            "source_bytes": _positive_count(document.get("source_bytes")),
            "source_sha256": _safe_digest(document.get("source_sha256")),
            "prompt_tokens": _positive_count(document.get("prompt_tokens")),
            "completion_tokens": _positive_count(document.get("completion_tokens")),
            "num_ctx": _positive_count(document.get("num_ctx")),
            "num_predict": _positive_count(document.get("num_predict")),
            "max_chunk_source_bytes": _positive_count(
                document.get("max_chunk_source_bytes")
            ),
            "max_ollama_calls": _positive_count(document.get("max_ollama_calls")),
            "page_count": _positive_count(document.get("page_count")),
            "candidate_page_count": _positive_count(
                document.get("candidate_page_count"), allow_zero=True
            ),
            "initial_chunk_count": _positive_count(
                document.get("initial_chunk_count"), allow_zero=True
            ),
            "ollama_call_count": _positive_count(
                document.get("ollama_call_count"), allow_zero=True
            ),
            "successful_chunk_count": _positive_count(
                document.get("successful_chunk_count"), allow_zero=True
            ),
            "split_retry_count": _positive_count(
                document.get("split_retry_count"), allow_zero=True
            ),
            "failed_chunk_count": _positive_count(
                document.get("failed_chunk_count"), allow_zero=True
            ),
        }}
        print(json.dumps(safe_output, sort_keys=True))
        return 0
    except BaseException as error:
        print(json.dumps({{"ok": False, "failure_category": _failure_category(error)}}, sort_keys=True))
        return 2

raise SystemExit(main())
'''


def _extractor_token(value: str | None) -> str | None:
    token = value
    if token is None:
        token = os.environ.get(EXTRACTOR_TOKEN_ENV)
        if token is None:
            token = os.environ.get(EXTRACTOR_DIFY_TOKEN_ENV)
    if not isinstance(token, str) or len(token) < 32 or "\n" in token:
        return None
    return token


def _safe_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        return None
    return value


def _extractor_probe_metadata(document: Mapping[str, object]) -> dict[str, object]:
    response_nonempty = document.get("response_nonempty")
    response_length = document.get("response_length")
    response_digest = _safe_digest(document.get("response_sha256"))
    source_bytes = document.get("source_bytes")
    source_digest = _safe_digest(document.get("source_sha256"))
    prompt_tokens = _validate_prompt_tokens(
        document.get("prompt_tokens"),
        limit=EXTRACTOR_MAX_PROMPT_TOKENS,
    )
    completion_tokens = _validate_prompt_tokens(
        document.get("completion_tokens"),
        limit=EXTRACTOR_NUM_PREDICT,
    )
    num_ctx = document.get("num_ctx")
    num_predict = document.get("num_predict")
    if (
        response_nonempty is not True
        or not isinstance(response_length, int)
        or isinstance(response_length, bool)
        or response_length <= 0
        or response_digest is None
        or source_bytes != EXTRACTOR_MAX_CHUNK_SOURCE_BYTES
        or source_digest != EXTRACTOR_SYNTHETIC_SOURCE_SHA256
        or num_ctx != EXTRACTOR_NUM_CTX
        or num_predict != EXTRACTOR_NUM_PREDICT
        or document.get("max_chunk_source_bytes") != EXTRACTOR_MAX_CHUNK_SOURCE_BYTES
        or document.get("max_ollama_calls") != EXTRACTOR_MAX_OLLAMA_CALLS
    ):
        raise _ProbeFailure("invalid_result")
    if prompt_tokens + EXTRACTOR_NUM_PREDICT > EXTRACTOR_NUM_CTX:
        raise _ProbeFailure("context_overflow")

    counts: dict[str, int] = {}
    for key in (
        "page_count",
        "candidate_page_count",
        "initial_chunk_count",
        "ollama_call_count",
        "successful_chunk_count",
        "split_retry_count",
        "failed_chunk_count",
    ):
        value = document.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise _ProbeFailure("invalid_result")
        counts[key] = value
    if (
        counts["page_count"] <= 0
        or counts["candidate_page_count"] <= 0
        or counts["initial_chunk_count"] <= 0
        or counts["ollama_call_count"] <= 0
        or counts["ollama_call_count"] > EXTRACTOR_MAX_OLLAMA_CALLS
        or counts["successful_chunk_count"] <= 0
        or counts["failed_chunk_count"] != 0
    ):
        raise _ProbeFailure("invalid_result")

    return {
        "response_nonempty": True,
        "response_length": response_length,
        "response_sha256": response_digest,
        "source_bytes": source_bytes,
        "source_sha256": source_digest,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        **counts,
        "num_ctx": EXTRACTOR_NUM_CTX,
        "num_predict": EXTRACTOR_NUM_PREDICT,
        "max_chunk_source_bytes": EXTRACTOR_MAX_CHUNK_SOURCE_BYTES,
        "max_ollama_calls": EXTRACTOR_MAX_OLLAMA_CALLS,
    }


def probe_extractor_boundary(
    *,
    runner: Callable[..., Any] = subprocess.run,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    token: str | None = None,
) -> dict[str, object]:
    start = monotonic()
    resolved_token = _extractor_token(token)
    if resolved_token is None:
        return failed_probe(
            provider="paper-dossier-extractor",
            mode="extractor_boundary",
            duration_seconds=_safe_duration(start, monotonic(), timeout),
            phase="prerequisite",
            category="prerequisite_failed",
        )
    phase = "transport"
    category = "subprocess_failed"
    try:
        completed = runner(
            [
                "docker",
                "exec",
                "-i",
                "docker-api-1",
                "/app/api/.venv/bin/python",
                "-c",
                EXTRACTOR_CHILD_SOURCE,
            ],
            input=resolved_token,
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            phase = "validation"
            try:
                child = json.loads(completed.stdout)
                child_category = child.get("failure_category") if isinstance(child, dict) else None
                if child_category in ALLOWED_FAILURE_CATEGORIES:
                    category = child_category
            except (TypeError, json.JSONDecodeError):
                pass
            raise RuntimeError("extractor child failed")
        phase, category = "validation", "invalid_result"
        document = json.loads(completed.stdout)
        if not isinstance(document, dict) or document.get("ok") is not True:
            raise ValueError("invalid extractor child result")
        metadata = _extractor_probe_metadata(document)
        duration = _safe_duration(start, monotonic(), timeout)
        return _successful_probe(
            provider="paper-dossier-extractor",
            mode="extractor_boundary",
            duration_seconds=duration,
            response_metadata=metadata,
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
        provider="paper-dossier-extractor",
        mode="extractor_boundary",
        duration_seconds=duration,
        phase=phase,
        category=category,
    )


def build_evidence(
    direct_ollama_probe: Mapping[str, object],
    extractor_probe: Mapping[str, object] | None,
    *,
    timestamp: str | None = None,
) -> dict[str, object]:
    def safe_duration(probe: Mapping[str, object]) -> float:
        duration = probe.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            return 0.0
        return round(min(max(float(duration), 0.0), DEFAULT_TIMEOUT_SECONDS), 6)

    def safe_failure(
        probe: Mapping[str, object],
        *,
        status: str,
        provider: str,
        mode: str,
        phase: str | None = None,
        category: str | None = None,
    ) -> dict[str, object]:
        safe: dict[str, object] = {
            "status": status,
            "provider": provider,
            "model": OLLAMA_MODEL,
            "mode": mode,
            "duration_seconds": safe_duration(probe),
            **_empty_response_metadata(),
        }
        safe["failure_phase"] = (
            phase if phase in ALLOWED_FAILURE_PHASES else "validation"
        )
        safe["failure_category"] = (
            category
            if category in ALLOWED_FAILURE_CATEGORIES
            else "unexpected_error"
        )
        return safe

    def sanitize_direct(probe: Mapping[str, object]) -> dict[str, object]:
        status = probe.get("status")
        if status not in {"ready", "failed", "not_run"}:
            status = "failed"
        length = probe.get("response_length")
        digest = _safe_digest(probe.get("response_sha256"))
        input_bytes = probe.get("input_bytes")
        input_digest = _safe_digest(probe.get("input_sha256"))
        empty_prompt_tokens = probe.get("empty_prompt_tokens")
        empty_completion_tokens = probe.get("empty_completion_tokens")
        prompt_tokens = probe.get("prompt_tokens")
        completion_tokens = probe.get("completion_tokens")
        response_valid = (
            probe.get("response_nonempty") is True
            and isinstance(length, int)
            and not isinstance(length, bool)
            and length > 0
            and digest is not None
            and input_bytes == OLLAMA_PRODUCTION_INPUT_BYTES
            and input_digest is not None
            and isinstance(empty_prompt_tokens, int)
            and not isinstance(empty_prompt_tokens, bool)
            and 0 < empty_prompt_tokens <= OLLAMA_CHAT_OVERHEAD_TOKENS
            and isinstance(empty_completion_tokens, int)
            and not isinstance(empty_completion_tokens, bool)
            and 0 < empty_completion_tokens <= OLLAMA_CONTEXT_NUM_PREDICT
            and isinstance(prompt_tokens, int)
            and not isinstance(prompt_tokens, bool)
            and 0 < prompt_tokens <= OLLAMA_MAX_PROMPT_TOKENS
            and isinstance(completion_tokens, int)
            and not isinstance(completion_tokens, bool)
            and 0 < completion_tokens <= OLLAMA_CONTEXT_NUM_PREDICT
            and probe.get("prompt_token_limit") == OLLAMA_MAX_PROMPT_TOKENS
            and probe.get("num_ctx") == OLLAMA_CONTEXT_NUM_CTX
            and probe.get("num_predict") == OLLAMA_CONTEXT_NUM_PREDICT
            and probe.get("think") is False
        )
        malformed_ready = status == "ready" and not response_valid
        if not response_valid:
            if malformed_ready:
                return safe_failure(
                    probe,
                    status="failed",
                    provider="ollama",
                    mode="direct_http",
                    phase="validation",
                    category="invalid_result",
                )
            return safe_failure(
                probe,
                status=status,
                provider="ollama",
                mode="direct_http",
                phase=probe.get("failure_phase"),
                category=probe.get("failure_category"),
            )
        return {
            "status": "ready",
            "provider": "ollama",
            "model": OLLAMA_MODEL,
            "mode": "direct_http",
            "duration_seconds": safe_duration(probe),
            "response_nonempty": True,
            "response_length": length,
            "response_sha256": digest,
            "input_bytes": input_bytes,
            "input_sha256": input_digest,
            "empty_prompt_tokens": empty_prompt_tokens,
            "empty_completion_tokens": empty_completion_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "prompt_token_limit": OLLAMA_MAX_PROMPT_TOKENS,
            "num_ctx": OLLAMA_CONTEXT_NUM_CTX,
            "num_predict": OLLAMA_CONTEXT_NUM_PREDICT,
            "think": False,
        }

    def sanitize_extractor(probe: Mapping[str, object]) -> dict[str, object]:
        status = probe.get("status")
        if status not in {"ready", "failed", "not_run"}:
            status = "failed"
        length = probe.get("response_length")
        response_digest = _safe_digest(probe.get("response_sha256"))
        source_bytes = probe.get("source_bytes")
        source_digest = _safe_digest(probe.get("source_sha256"))
        counts: dict[str, int] = {}
        counts_valid = True
        for key in (
            "page_count",
            "candidate_page_count",
            "initial_chunk_count",
            "ollama_call_count",
            "successful_chunk_count",
            "split_retry_count",
            "failed_chunk_count",
        ):
            value = probe.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                counts_valid = False
            else:
                counts[key] = value
        counts_valid = counts_valid and counts.get("page_count", 0) > 0
        counts_valid = counts_valid and counts.get("candidate_page_count", 0) > 0
        counts_valid = counts_valid and counts.get("initial_chunk_count", 0) > 0
        counts_valid = counts_valid and counts.get("ollama_call_count", 0) > 0
        counts_valid = counts_valid and counts.get("ollama_call_count", 0) <= EXTRACTOR_MAX_OLLAMA_CALLS
        counts_valid = counts_valid and counts.get("successful_chunk_count", 0) > 0
        counts_valid = counts_valid and counts.get("failed_chunk_count") == 0
        prompt_tokens = probe.get("prompt_tokens")
        completion_tokens = probe.get("completion_tokens")
        prompt_is_int = isinstance(prompt_tokens, int) and not isinstance(prompt_tokens, bool)
        completion_is_int = isinstance(completion_tokens, int) and not isinstance(
            completion_tokens, bool
        )
        token_overflow = (
            (prompt_is_int and prompt_tokens > EXTRACTOR_MAX_PROMPT_TOKENS)
            or (completion_is_int and completion_tokens > EXTRACTOR_NUM_PREDICT)
            or (
                prompt_is_int
                and completion_is_int
                and prompt_tokens > 0
                and completion_tokens > 0
                and prompt_tokens + EXTRACTOR_NUM_PREDICT > EXTRACTOR_NUM_CTX
            )
        )
        tokens_valid = (
            prompt_is_int
            and 0 < prompt_tokens <= EXTRACTOR_MAX_PROMPT_TOKENS
            and completion_is_int
            and 0 < completion_tokens <= EXTRACTOR_NUM_PREDICT
        )
        response_valid = (
            probe.get("response_nonempty") is True
            and isinstance(length, int)
            and not isinstance(length, bool)
            and length > 0
            and response_digest is not None
            and source_bytes == EXTRACTOR_MAX_CHUNK_SOURCE_BYTES
            and source_digest == EXTRACTOR_SYNTHETIC_SOURCE_SHA256
            and probe.get("num_ctx") == EXTRACTOR_NUM_CTX
            and probe.get("num_predict") == EXTRACTOR_NUM_PREDICT
            and probe.get("max_chunk_source_bytes") == EXTRACTOR_MAX_CHUNK_SOURCE_BYTES
            and probe.get("max_ollama_calls") == EXTRACTOR_MAX_OLLAMA_CALLS
            and counts_valid
            and tokens_valid
            and not token_overflow
        )
        if status == "ready" and not response_valid:
            return safe_failure(
                probe,
                status="failed",
                provider="paper-dossier-extractor",
                mode="extractor_boundary",
                phase="validation",
                category="context_overflow" if token_overflow else "invalid_result",
            )
        if status != "ready" or not response_valid:
            return safe_failure(
                probe,
                status=status,
                provider="paper-dossier-extractor",
                mode="extractor_boundary",
                phase=probe.get("failure_phase"),
                category=probe.get("failure_category"),
            )
        safe: dict[str, object] = {
            "status": "ready",
            "provider": "paper-dossier-extractor",
            "model": OLLAMA_MODEL,
            "mode": "extractor_boundary",
            "duration_seconds": safe_duration(probe),
            "response_nonempty": True,
            "response_length": length,
            "response_sha256": response_digest,
            "source_bytes": source_bytes,
            "source_sha256": source_digest,
            **counts,
            "num_ctx": EXTRACTOR_NUM_CTX,
            "num_predict": EXTRACTOR_NUM_PREDICT,
            "max_chunk_source_bytes": EXTRACTOR_MAX_CHUNK_SOURCE_BYTES,
            "max_ollama_calls": EXTRACTOR_MAX_OLLAMA_CALLS,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        return safe

    direct_safe = sanitize_direct(direct_ollama_probe)
    if extractor_probe is None:
        extractor_probe = failed_probe(
            provider="paper-dossier-extractor",
            mode="extractor_boundary",
            duration_seconds=0.0,
            phase="prerequisite",
            category="prerequisite_failed",
        )
        extractor_probe["status"] = "not_run"
    extractor_safe = sanitize_extractor(extractor_probe)
    probes: dict[str, object] = {
        "direct_ollama": direct_safe,
        "extractor_boundary": extractor_safe,
    }
    ready = all(
        probe.get("status") == "ready" for probe in (direct_safe, extractor_safe)
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
    extractor_probe_fn: Callable[[], dict[str, object]] = probe_extractor_boundary,
    dify_probe_fn: Callable[[], dict[str, object]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Run privacy-safe Ollama readiness probes.")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)

    local_probe = local_probe_fn()
    extractor_probe = None
    if local_probe.get("status") == "ready":
        extractor_probe = (dify_probe_fn or extractor_probe_fn)()
    evidence = build_evidence(local_probe, extractor_probe)
    write_evidence_atomic(arguments.output, evidence)
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
