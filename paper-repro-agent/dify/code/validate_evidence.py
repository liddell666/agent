"""Validate dossier citations and build a deterministic Markdown summary."""

import json
import unicodedata
from typing import Any


_METRIC_ALIASES = {
    "auc": "roc_auc",
    "roc_auc": "roc_auc",
    "总精度": "accuracy",
    "准确率": "accuracy",
    "平衡准确率": "balanced_accuracy",
    "精确率": "precision",
    "查准率": "precision",
    "召回率": "recall",
    "查全率": "recall",
    "f1值": "f1",
}
_SUPPORTED_METRICS = {
    "roc_auc",
    "accuracy",
    "balanced_accuracy",
    "precision",
    "recall",
    "f1",
}


def _normalized_metric_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    display_name = unicodedata.normalize("NFKC", value.strip())
    if "(" in display_name:
        display_name = display_name.split("(", 1)[0].strip()
    normalized = "_".join(
        display_name.casefold().replace("-", " ").replace("_", " ").split()
    )
    return _METRIC_ALIASES.get(normalized, normalized)


def _has_metric_qualifier(metric: dict[str, Any]) -> bool:
    if any(isinstance(metric.get(key), str) and metric[key].strip() for key in ("dataset", "split")):
        return True
    name = metric.get("name")
    if not isinstance(name, str):
        return False
    display_name = unicodedata.normalize("NFKC", name)
    return "(" in display_name and ")" in display_name


def _normalize_metrics(dossier: dict[str, Any]) -> None:
    metrics = dossier.get("metrics")
    if not isinstance(metrics, list):
        return
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        normalized_name = _normalized_metric_name(metric.get("name"))
        supported = normalized_name in _SUPPORTED_METRICS
        metric["normalized_name"] = normalized_name
        metric["supported"] = supported
        metric["ambiguous"] = not supported or not _has_metric_qualifier(metric)


def _evidence_pages(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    pages = sorted(
        {
            item.get("page")
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("page"), int)
            and not isinstance(item.get("page"), bool)
        }
    )
    return " ".join(f"[p.{page}]" for page in pages)


def _summary(dossier: dict[str, Any]) -> str:
    lines = [f"# {dossier.get('title') or '未命名论文'}", ""]
    lines.extend(
        [
            "## 研究任务",
            str(dossier.get("research_problem") or "未提取"),
            f"- 任务类型：{dossier.get('task_type') or 'uncertain'}",
            "",
            "## 数据集",
        ]
    )
    datasets = dossier.get("datasets") or []
    lines.extend(
        f"- {item.get('name', '未命名')}：{item.get('description', '')} {_evidence_pages(item.get('evidence'))}".rstrip()
        for item in datasets
        if isinstance(item, dict)
    )
    if not datasets:
        lines.append("- 未提取到有证据的数据集")

    lines.extend(["", "## 方法"])
    methods = dossier.get("methods") or []
    lines.extend(
        f"- {item.get('name', '未命名')}：{item.get('description', '')} {_evidence_pages(item.get('evidence'))}".rstrip()
        for item in methods
        if isinstance(item, dict)
    )
    if not methods:
        lines.append("- 未提取到有证据的方法")

    lines.extend(["", "## 报告指标"])
    metrics = dossier.get("metrics") or []
    for item in metrics:
        if not isinstance(item, dict):
            continue
        qualifiers = " / ".join(
            str(value) for value in (item.get("dataset"), item.get("split")) if value
        )
        qualifier_text = f"（{qualifiers}）" if qualifiers else ""
        lines.append(
            f"- {item.get('name', '未命名')}{qualifier_text}：{item.get('reported_value')} "
            f"{_evidence_pages(item.get('evidence'))}".rstrip()
        )
    if not metrics:
        lines.append("- 未提取到报告指标")

    lines.extend(["", "## 复现缺口"])
    gaps = dossier.get("gaps") or []
    lines.extend(f"- {gap}" for gap in gaps)
    if not gaps:
        lines.append("- 未发现明确缺口")
    return "\n".join(lines)


def main(dossier_json: Any, page_count: Any) -> dict[str, Any]:
    """Return exact invalid JSON paths instead of silently accepting bad evidence."""

    try:
        dossier = dossier_json if isinstance(dossier_json, dict) else json.loads(dossier_json)
    except (TypeError, json.JSONDecodeError):
        return {
            "validated_json": "",
            "markdown_summary": "",
            "invalid_paths": ["$"],
            "can_continue": False,
        }
    if not isinstance(dossier, dict):
        return {
            "validated_json": "",
            "markdown_summary": "",
            "invalid_paths": ["$"],
            "can_continue": False,
        }

    if isinstance(page_count, str) and page_count.lstrip().startswith("{"):
        try:
            page_count = json.loads(page_count).get("page_count", 0)
        except (AttributeError, json.JSONDecodeError):
            page_count = 0
    try:
        maximum_page = int(page_count)
    except (TypeError, ValueError):
        maximum_page = 0

    invalid_paths: list[str] = []
    for collection_name in ("datasets", "methods", "metrics"):
        collection = dossier.get(collection_name, [])
        if not isinstance(collection, list):
            invalid_paths.append(f"$.{collection_name}")
            continue
        for item_index, item in enumerate(collection):
            item_path = f"$.{collection_name}[{item_index}]"
            if not isinstance(item, dict):
                invalid_paths.append(item_path)
                continue
            evidence = item.get("evidence")
            requires_evidence = collection_name != "metrics" or item.get("reported_value") is not None
            if requires_evidence and (not isinstance(evidence, list) or not evidence):
                invalid_paths.append(f"{item_path}.evidence")
                continue
            if not isinstance(evidence, list):
                continue
            for evidence_index, citation in enumerate(evidence):
                citation_path = f"{item_path}.evidence[{evidence_index}]"
                if not isinstance(citation, dict):
                    invalid_paths.append(citation_path)
                    continue
                page = citation.get("page")
                if (
                    not isinstance(page, int)
                    or isinstance(page, bool)
                    or page < 1
                    or page > maximum_page
                ):
                    invalid_paths.append(f"{citation_path}.page")
                source_text = citation.get("source_text")
                if not isinstance(source_text, str) or not source_text.strip():
                    invalid_paths.append(f"{citation_path}.source_text")

    invalid_paths = list(dict.fromkeys(invalid_paths))
    if invalid_paths:
        return {
            "validated_json": "",
            "markdown_summary": "",
            "invalid_paths": invalid_paths,
            "can_continue": False,
        }

    _normalize_metrics(dossier)
    normalized = json.dumps(dossier, ensure_ascii=False, separators=(",", ":"))
    return {
        "validated_json": normalized,
        "markdown_summary": _summary(dossier),
        "invalid_paths": [],
        "can_continue": True,
    }
