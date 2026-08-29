"""Validate the HTTP parser response before sending it to an LLM."""

import json
from typing import Any

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_CHARS = 360_000
MAX_PAGE_TEXT_CHARS = 1_400
MAX_MARKDOWN_CHARS = 64_000
COMPACTION_WARNING = "parser_output_compacted_for_workflow_limit"

HTTP_ERRORS = {
    401: "解析器鉴权失败（HTTP 401），请检查工作流的 PARSER_API_TOKEN。",
    413: "论文文件过大（HTTP 413），请上传不超过 50 MB 的 PDF。",
    422: "PDF 无法解析（HTTP 422），请确认文件完整且未加密。",
    500: "解析器内部错误（HTTP 500），请查看 paper-parser 日志。",
}


def _failure(message: str) -> dict[str, Any]:
    return {
        "parsed_json": "",
        "parser_warnings": [message],
        "can_continue": False,
    }


def _clip_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 0:
        return ""
    marker = "\n...[truncated for workflow size]...\n"
    if limit <= len(marker):
        return value[:limit]
    remaining = limit - len(marker)
    head = remaining // 2
    tail = remaining - head
    return value[:head] + marker + value[-tail:]


def _compact_elements(elements: list[Any], per_page_limit: int) -> list[dict[str, Any]]:
    page_text: dict[int, list[str]] = {}
    for element in elements:
        if not isinstance(element, dict):
            continue
        page = element.get("page")
        text = element.get("text")
        if not isinstance(page, int) or isinstance(page, bool):
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        kind = element.get("kind")
        prefix = f"[{kind}] " if isinstance(kind, str) and kind else ""
        page_text.setdefault(page, []).append(prefix + text.strip())

    return [
        {
            "kind": "text",
            "page": page,
            "text": _clip_text("\n".join(page_text[page]), per_page_limit),
        }
        for page in sorted(page_text)
    ]


def _serialized(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _compact_payload(payload: dict[str, Any]) -> str:
    serialized = _serialized(payload)
    if len(serialized) < MAX_OUTPUT_CHARS:
        return serialized

    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = [str(warnings)]
    warnings = [str(item) for item in warnings]
    page_limit = MAX_PAGE_TEXT_CHARS
    markdown_limit = MAX_MARKDOWN_CHARS
    for _ in range(12):
        compact = {
            "document_id": payload.get("document_id", ""),
            "file_name": payload.get("file_name", ""),
            "page_count": payload.get("page_count", 0),
            "markdown": _clip_text(str(payload.get("markdown", "")), markdown_limit),
            "elements": _compact_elements(payload.get("elements", []), page_limit),
            "warnings": warnings + [COMPACTION_WARNING],
        }
        serialized = _serialized(compact)
        if len(serialized) < MAX_OUTPUT_CHARS:
            return serialized
        page_limit = max(80, page_limit * 3 // 4)
        markdown_limit = max(0, markdown_limit * 3 // 4)

    compact["markdown"] = ""
    compact["elements"] = _compact_elements(payload.get("elements", []), 80)
    return _serialized(compact)


def main(body: str, status_code: int = 200) -> dict[str, Any]:
    """Return Dify-compatible validation outputs without raising user errors."""

    try:
        status = int(status_code)
    except (TypeError, ValueError):
        return _failure("解析器返回了无效的 HTTP 状态码。")

    if status in HTTP_ERRORS:
        return _failure(HTTP_ERRORS[status])
    if status < 200 or status >= 300:
        return _failure(f"解析器请求失败（HTTP {status}）。")
    if not isinstance(body, str):
        return _failure("解析器响应 body 不是文本。")
    if len(body.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return _failure("解析器 JSON 超过 2 MB，已停止发送给模型。")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return _failure("解析器响应不是有效 JSON。")
    if not isinstance(payload, dict):
        return _failure("解析器 JSON 顶层必须是对象。")

    page_count = payload.get("page_count")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        return _failure("解析器 JSON 的 page_count 缺失或无效。")

    markdown = payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        return _failure("解析器 JSON 的 markdown 缺失或为空。")

    elements = payload.get("elements")
    if not isinstance(elements, list) or not elements:
        return _failure("解析器 JSON 的 elements 缺失或为空。")

    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = ["解析器 warnings 字段格式无效，已忽略。"]
    else:
        warnings = [str(item) for item in warnings]

    return {
        "parsed_json": _compact_payload(payload),
        "parser_warnings": warnings,
        "can_continue": True,
    }
