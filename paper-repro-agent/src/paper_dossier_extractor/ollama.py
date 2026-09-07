"""Privacy-safe, bounded transport for local Ollama chunk extraction."""

from __future__ import annotations

import json
import socket
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable
from typing import Any
from urllib.request import Request, urlopen

from .config import Settings
from .schemas import OllamaCompletion, PageChunk

_call_budget: ContextVar[float | None] = ContextVar("ollama_call_budget", default=None)


@contextmanager
def call_budget(seconds: float):
    """Pass a request-local remaining budget through client decorators."""
    token = _call_budget.set(seconds)
    try:
        yield
    finally:
        _call_budget.reset(token)

SYSTEM_PROMPT = (
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
    "If evidence is absent, omit the fact and add a short gap."
)

_OLLAMA_ERROR_CODES = frozenset(
    {"ollama_unavailable", "ollama_timeout", "ollama_invalid_response"}
)


class OllamaError(RuntimeError):
    """An Ollama failure represented by a stable public code only."""

    def __init__(self, code: str) -> None:
        if code not in _OLLAMA_ERROR_CODES:
            raise ValueError("unsupported Ollama error code")
        self.code = code
        super().__init__(code)

    def __str__(self) -> str:
        return self.code


def build_messages(chunk: PageChunk) -> list[dict[str, str]]:
    """Build the fixed instruction and compact, page-addressable user payload."""

    payload = {
        "chunk_id": chunk.chunk_id,
        "pages": [
            {"page": page.page, "text": page.text, "kinds": list(page.kinds)}
            for page in chunk.pages
        ],
    }
    user_content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _is_timeout(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    return isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout))


def _strict_token_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


class OllamaClient:
    """Call Ollama's non-streaming chat endpoint without retaining raw bodies."""

    def __init__(
        self,
        settings: Settings,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._opener = opener

    def complete(self, chunk: PageChunk) -> OllamaCompletion:
        request_payload = {
            "model": self.settings.ollama_model,
            "messages": build_messages(chunk),
            "stream": False,
            "think": False,
            "options": {
                "num_ctx": self.settings.num_ctx,
                "num_predict": self.settings.num_predict,
                "temperature": 0,
            },
        }
        request_body = json.dumps(
            request_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        request = Request(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        timeout = self.settings.ollama_call_timeout_seconds
        remaining = _call_budget.get()
        if remaining is not None:
            timeout = min(timeout, remaining)
        if timeout <= 0:
            raise OllamaError("ollama_timeout")
        try:
            opener = self._opener or urlopen
            with opener(
                request,
                timeout=timeout,
            ) as response:
                raw_body = response.read()
        except Exception as error:
            if _is_timeout(error):
                raise OllamaError("ollama_timeout") from None
            raise OllamaError("ollama_unavailable") from None

        try:
            if isinstance(raw_body, (bytes, bytearray)):
                response_value = json.loads(bytes(raw_body).decode("utf-8"))
            elif isinstance(raw_body, str):
                response_value = json.loads(raw_body)
            else:
                raise TypeError
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            raise OllamaError("ollama_invalid_response") from None

        if not isinstance(response_value, dict):
            raise OllamaError("ollama_invalid_response")
        if "error" in response_value:
            raise OllamaError("ollama_unavailable")

        message = response_value.get("message")
        if not isinstance(message, dict):
            raise OllamaError("ollama_invalid_response")
        text = message.get("content")
        if not isinstance(text, str):
            raise OllamaError("ollama_invalid_response")

        finish_reason = response_value.get("done_reason")
        if finish_reason is None:
            finish_reason = response_value.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason:
            raise OllamaError("ollama_invalid_response")

        prompt_tokens = _strict_token_count(response_value.get("prompt_eval_count"))
        completion_tokens = _strict_token_count(response_value.get("eval_count"))
        if prompt_tokens is None or completion_tokens is None:
            raise OllamaError("ollama_invalid_response")

        try:
            return OllamaCompletion(
                text=text,
                finish_reason=finish_reason,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except (TypeError, ValueError):
            raise OllamaError("ollama_invalid_response") from None
