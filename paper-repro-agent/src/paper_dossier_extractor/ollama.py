"""Privacy-safe, bounded transport for local Ollama chunk extraction."""

from __future__ import annotations

import json
import re
import socket
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable
from typing import Any
from urllib.request import Request, urlopen

from .config import Settings
from .schemas import OllamaCompletion, PageChunk, PartialDossier

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
    "If evidence is absent, omit the fact and add a short gap.\n"
    "Inspect compact table text carefully. Treat labels such as MAE/MAE(lm) "
    "as MAE result columns only when numeric cells and row labels are present."
)

_METRIC_TABLE_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9])(?:mae|mean absolute error|mean absolute deviation|"
    r"rmse|root mean squared error|root mean square error|r²|r2|r-squared|"
    r"coefficient of determination|平均绝对误差|均方根误差|决定系数)(?![a-z0-9])"
)

TABLE_SYSTEM_PROMPT = (
    "Return one compact JSON object only. Keys: title,title_evidence,"
    "research_problem,task_type,task_evidence,datasets,methods,metrics,gaps. "
    "Evidence is {page,source_text}; every evidence excerpt must be copied "
    "exactly from the supplied page. Metric is "
    "{name,reported_value,dataset,split,model,evidence}; name is MAE, RMSE, "
    "or R2. Use null or [] when unsupported. For a large result table, if any "
    "supported metric label and numeric cells are visible, emit at most ONE "
    "unambiguous metric from the first complete labeled row; use the table "
    "caption for dataset and split, and use model=null when the model label is "
    "not one of linear_regression, random_forest, gradient_boosting, or xgboost. "
    "A header written as MAE/MAE(lm) is a supported MAE result: use name=MAE "
    "and take a numeric cell from the first dataset row. "
    "If the user envelope includes table_hint, use that row label when selecting "
    "the one metric. "
    "Do not emit an empty metrics array when a supported metric table is visible. "
    "Do not list every table row."
)


def _bounded_partial_schema(*, metrics: int, datasets: int, methods: int) -> dict[str, object]:
    """Bound every chunk response below the fixed Ollama completion budget."""

    schema = PartialDossier.model_json_schema()
    properties = schema.get("properties")
    definitions = schema.get("$defs")
    if not isinstance(properties, dict) or not isinstance(definitions, dict):
        raise RuntimeError("partial dossier schema shape is invalid")

    for name, maximum in (
        ("metrics", metrics),
        ("datasets", datasets),
        ("methods", methods),
        ("gaps", 2),
        ("title_evidence", 1),
        ("task_evidence", 1),
    ):
        value = properties.get(name)
        if not isinstance(value, dict):
            raise RuntimeError("partial dossier schema property is invalid")
        value["maxItems"] = maximum

    for name in ("PartialMetric", "PartialFact"):
        definition = definitions.get(name)
        if not isinstance(definition, dict):
            raise RuntimeError("partial dossier schema definition is invalid")
        definition_properties = definition.get("properties")
        if not isinstance(definition_properties, dict):
            raise RuntimeError("partial dossier schema definition properties are invalid")
        evidence = definition_properties.get("evidence")
        if not isinstance(evidence, dict):
            raise RuntimeError("partial dossier schema evidence property is invalid")
        evidence["maxItems"] = 1

    partial_evidence = definitions.get("PartialEvidence")
    if not isinstance(partial_evidence, dict):
        raise RuntimeError("partial dossier evidence definition is invalid")
    evidence_properties = partial_evidence.get("properties")
    if not isinstance(evidence_properties, dict):
        raise RuntimeError("partial dossier evidence properties are invalid")
    source_text = evidence_properties.get("source_text")
    if not isinstance(source_text, dict):
        raise RuntimeError("partial dossier source text property is invalid")
    source_text["maxLength"] = 240
    return schema


PARTIAL_SCHEMA = _bounded_partial_schema(metrics=2, datasets=1, methods=2)
TABLE_PARTIAL_SCHEMA = _bounded_partial_schema(metrics=1, datasets=1, methods=1)

_TABLE_FOCUS_BYTES = 2_048
_TABLE_ROW_HINT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])([A-Za-z][A-Za-z0-9_-]{1,63})\s+"
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s+"
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)
_TABLE_SPLIT_PATTERN = re.compile(
    r"(?i)\b(training|test|validation|development)\s+dataset\b"
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


def _is_metric_table_chunk(chunk: PageChunk) -> bool:
    return any(
        any(kind in {"table", "caption"} for kind in page.kinds)
        and _METRIC_TABLE_PATTERN.search(page.text)
        for page in chunk.pages
    )


def _head_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def _table_row_hint(chunk: PageChunk) -> str | None:
    for page in chunk.pages:
        source = page.table_text or page.text
        match = _TABLE_ROW_HINT_PATTERN.search(source)
        if match is not None:
            return match.group(1)
    return None


def _table_split_hint(chunk: PageChunk) -> str | None:
    for page in chunk.pages:
        source = page.table_text or page.text
        match = _TABLE_SPLIT_PATTERN.search(source)
        if match is not None:
            return match.group(1).casefold()
    return None


def _table_metric_hint(chunk: PageChunk) -> str | None:
    for page in chunk.pages:
        source = page.table_text or page.text
        match = _METRIC_TABLE_PATTERN.search(source)
        if match is None:
            continue
        value = match.group(0).casefold()
        if value in {"mae", "mean absolute error", "mean absolute deviation"}:
            return "MAE"
        if value in {"rmse", "root mean squared error", "root mean square error"}:
            return "RMSE"
        if value in {"r²", "r2", "r-squared", "coefficient of determination"}:
            return "R2"
    return None


def _table_metric_page(chunk: PageChunk) -> int | None:
    for page in chunk.pages:
        source = page.table_text or page.text
        if _METRIC_TABLE_PATTERN.search(source):
            return page.page
    return None


def _table_system_prompt(chunk: PageChunk) -> str:
    hint = _table_row_hint(chunk)
    if hint is None:
        return TABLE_SYSTEM_PROMPT
    split = _table_split_hint(chunk)
    metric = _table_metric_hint(chunk) or "the metric indicated by the table header"
    split_instruction = f"split={split}" if split is not None else "split=the caption's split"
    page = _table_metric_page(chunk)
    page_instruction = f"page {page}" if page is not None else "the supplied page"
    return (
        f"{TABLE_SYSTEM_PROMPT} The first structural data-row label is {hint}; "
        f"the required dataset row is {hint}. Extract exactly one {metric} metric "
        f"from that row: name={metric}, dataset={hint}, {split_instruction}, "
        "model=null, reported_value=the first decimal value after the row label, "
        f"and one exact evidence excerpt from {page_instruction}. Do not use an "
        "empty metrics array."
    )


def build_messages(chunk: PageChunk) -> list[dict[str, str]]:
    """Build the fixed instruction and compact, page-addressable user payload."""

    table_mode = _is_metric_table_chunk(chunk)
    payload = {
        "chunk_id": chunk.chunk_id,
        "pages": [
            {
                "page": page.page,
                "text": _head_utf8(page.table_text or page.text, _TABLE_FOCUS_BYTES)
                if table_mode
                else page.text,
                "kinds": list(page.kinds),
            }
            for page in chunk.pages
        ],
    }
    if table_mode:
        table_hint = _table_row_hint(chunk)
        if table_hint is not None:
            payload["table_hint"] = table_hint
    user_content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return [
        {
            "role": "system",
            "content": _table_system_prompt(chunk) if table_mode else SYSTEM_PROMPT,
        },
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
            "keep_alive": self.settings.ollama_keep_alive,
            "format": (
                TABLE_PARTIAL_SCHEMA
                if _is_metric_table_chunk(chunk)
                else PARTIAL_SCHEMA
            ),
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
