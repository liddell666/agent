"""Safely add deterministic similarity scoring to the local Dify V3.1 app.

Run this script inside the Dify API container, where ``/app/api`` and the
configured database are available.  It uses Dify's own workflow service for
validation and publishing and creates a normal restorable workflow snapshot
before changing the draft.
"""

from __future__ import annotations

import argparse
import copy
import json
from typing import Any


APP_ID = "b9a766a0-0ad0-415b-8d42-60459c92bec7"
SCORER_ID = "score_approximate_similarity"
FORMATTER_ID = "comparison_markdown"
GATE_ID = "comparison_gate"


SCORER_CODE = '''import json
import math

def main(comparison_json: str, close_threshold: float, partial_threshold: float) -> dict:
    try:
        comparison = json.loads(comparison_json)
    except (TypeError, json.JSONDecodeError):
        comparison = {"items": []}
    try:
        close, partial = float(close_threshold), float(partial_threshold)
    except (TypeError, ValueError):
        close = partial = -1.0
    if not (math.isfinite(close) and math.isfinite(partial) and 0 < close < partial <= 1):
        return {"assessment_json": json.dumps({
            "strict_status": "not_comparable",
            "approximate_status": "insufficient_metrics",
            "close_threshold": close,
            "partial_threshold": partial,
            "items": [],
            "errors": [{
                "code": "invalid_similarity_thresholds",
                "message": "Thresholds must satisfy 0 < close < partial <= 1."
            }]
        }, ensure_ascii=False, separators=(",", ":"))}
    graded = []
    for item in comparison.get("items", []) if isinstance(comparison, dict) else []:
        if not isinstance(item, dict) or item.get("paper_value") is None or item.get("independent_value") is None:
            continue
        try:
            paper = float(item["paper_value"])
            independent = float(item["independent_value"])
            difference = abs(float(item.get("absolute_difference") if paper == 0 else item.get("relative_difference")))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (paper, independent, difference)):
            continue
        grade = "highly_similar" if difference <= close else "partially_similar" if difference <= partial else "materially_different"
        graded.append({
            "name": item.get("name"),
            "paper_value": paper,
            "independent_value": independent,
            "difference_for_grade": round(difference, 6),
            "grade": grade,
            "comparable": item.get("comparable") is True,
            "reason": item.get("reason")
        })
    comparable = sum(item["comparable"] for item in graded)
    strict = "not_comparable" if not graded or comparable == 0 else "strictly_comparable" if comparable == len(graded) else "partially_comparable"
    grades = [item["grade"] for item in graded]
    approximate = "insufficient_metrics" if not grades else "materially_different" if "materially_different" in grades else "highly_similar" if all(item == "highly_similar" for item in grades) else "partially_similar"
    return {"assessment_json": json.dumps({
        "strict_status": strict,
        "approximate_status": approximate,
        "close_threshold": close,
        "partial_threshold": partial,
        "items": graded
    }, ensure_ascii=False, separators=(",", ":"))}
'''


FORMATTER_CODE = '''import json

def object_or_empty(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}

def display(value):
    if value is None or value == "":
        return "not_available"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("\\r", " ").replace("\\n", " ")

def assessment_for(items, name, index):
    if index < len(items) and isinstance(items[index], dict) and items[index].get("name") == name:
        return items[index]
    for item in items:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return {}

def main(comparison_json: str = "", assessment_json: str = "") -> dict:
    comparison = object_or_empty(comparison_json)
    assessment = object_or_empty(assessment_json)
    strict = assessment.get("strict_status", "not_comparable")
    approximate = assessment.get("approximate_status", "insufficient_metrics")
    lines = [
        "# Paper comparison",
        "",
        "status: completed",
        "strict: " + display(strict),
        "approximate: " + display(approximate),
        "close_threshold: " + display(assessment.get("close_threshold")),
        "partial_threshold: " + display(assessment.get("partial_threshold")),
        "",
        "## Metrics",
    ]
    items = comparison.get("items", [])
    graded = assessment.get("items", [])
    if not isinstance(items, list) or not items:
        lines.append("- No metric pair was returned.")
    else:
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            name = item.get("name", "unknown")
            grade = assessment_for(graded if isinstance(graded, list) else [], name, index).get("grade")
            lines.append(
                "- {name}: paper_value={paper}, independent_value={independent}, absolute_difference={absolute}, relative_difference={relative}, comparable={comparable}, reason={reason}, grade={grade}".format(
                    name=display(name),
                    paper=display(item.get("paper_value")),
                    independent=display(item.get("independent_value")),
                    absolute=display(item.get("absolute_difference")),
                    relative=display(item.get("relative_difference")),
                    comparable=display(item.get("comparable") is True),
                    reason=display(item.get("reason") or "strict provenance requirements satisfied"),
                    grade=display(grade),
                )
            )
    lines.extend([
        "",
        "Note: numeric similarity does not establish strict reproduction; dataset, split, and evaluation provenance are assessed separately.",
    ])
    return {"markdown_summary": "\\n".join(lines)}
'''


def _edge(source: str, target: str, source_type: str, target_type: str, source_handle: str) -> dict[str, Any]:
    return {
        "id": f"{source}-{source_handle}-{target}-target",
        "type": "custom",
        "source": source,
        "target": target,
        "zIndex": 0,
        "sourceHandle": source_handle,
        "targetHandle": "target",
        "data": {
            "isInLoop": False,
            "sourceType": source_type,
            "targetType": target_type,
            "isInIteration": False,
        },
    }


def _scorer_node() -> dict[str, Any]:
    return {
        "id": SCORER_ID,
        "type": "custom",
        "width": 242,
        "height": 51,
        "zIndex": 0,
        "position": {"x": 9212, "y": 85.37023809523805},
        "positionAbsolute": {"x": 9212, "y": 85.37023809523805},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "data": {
            "type": "code",
            "title": "score_approximate_similarity",
            "code": SCORER_CODE,
            "code_language": "python3",
            "selected": False,
            "variables": [
                {
                    "variable": "comparison_json",
                    "value_type": "string",
                    "value_selector": ["comparison_parse", "comparison_json"],
                },
                {
                    "variable": "close_threshold",
                    "value_type": "number",
                    "value_selector": ["start", "close_threshold"],
                },
                {
                    "variable": "partial_threshold",
                    "value_type": "number",
                    "value_selector": ["start", "partial_threshold"],
                },
            ],
            "outputs": {"assessment_json": {"type": "string", "children": None}},
        },
    }


def transform_graph(source: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    graph = copy.deepcopy(source)
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("workflow graph must contain node and edge arrays")

    by_id = {node.get("id"): node for node in nodes if isinstance(node, dict)}
    for required in ("start", GATE_ID, FORMATTER_ID, "comparison_parse"):
        if required not in by_id:
            raise ValueError(f"required node is missing: {required}")

    start_variables = by_id["start"].get("data", {}).get("variables", [])
    start_names = {item.get("variable") for item in start_variables if isinstance(item, dict)}
    if not {"close_threshold", "partial_threshold"}.issubset(start_names):
        raise ValueError("start node is missing similarity thresholds")

    changed = SCORER_ID not in by_id
    if changed:
        for node in nodes:
            position = node.get("position") if isinstance(node, dict) else None
            absolute = node.get("positionAbsolute") if isinstance(node, dict) else None
            if isinstance(position, dict) and isinstance(position.get("x"), (int, float)) and position["x"] >= 9212:
                position["x"] += 342
            if isinstance(absolute, dict) and isinstance(absolute.get("x"), (int, float)) and absolute["x"] >= 9212:
                absolute["x"] += 342
        nodes.append(_scorer_node())
    else:
        scorer = by_id[SCORER_ID]
        if scorer.get("data", {}).get("code") != SCORER_CODE:
            scorer["data"] = _scorer_node()["data"]
            changed = True

    formatter = by_id[FORMATTER_ID]
    expected_variables = [
        {
            "variable": "comparison_json",
            "value_type": "string",
            "value_selector": ["comparison_parse", "comparison_json"],
        },
        {
            "variable": "assessment_json",
            "value_type": "string",
            "value_selector": [SCORER_ID, "assessment_json"],
        },
    ]
    if formatter.get("data", {}).get("code") != FORMATTER_CODE:
        formatter["data"]["code"] = FORMATTER_CODE
        changed = True
    if formatter.get("data", {}).get("variables") != expected_variables:
        formatter["data"]["variables"] = expected_variables
        changed = True

    direct_indexes = [
        index
        for index, edge in enumerate(edges)
        if isinstance(edge, dict) and edge.get("source") == GATE_ID and edge.get("target") == FORMATTER_ID
    ]
    if direct_indexes:
        for index in reversed(direct_indexes):
            del edges[index]
        changed = True

    required_edges = {
        (GATE_ID, SCORER_ID): _edge(GATE_ID, SCORER_ID, "if-else", "code", "true"),
        (SCORER_ID, FORMATTER_ID): _edge(SCORER_ID, FORMATTER_ID, "code", "code", "source"),
    }
    existing_pairs = {
        (edge.get("source"), edge.get("target"))
        for edge in edges
        if isinstance(edge, dict)
    }
    for pair, edge in required_edges.items():
        if pair not in existing_pairs:
            edges.append(edge)
            changed = True

    validate_graph_contract(graph)
    return graph, changed


def validate_graph_contract(graph: dict[str, Any]) -> None:
    nodes = graph["nodes"]
    edges = graph["edges"]
    by_id = {node.get("id"): node for node in nodes if isinstance(node, dict)}
    scorer = by_id.get(SCORER_ID)
    formatter = by_id.get(FORMATTER_ID)
    if scorer is None or formatter is None:
        raise ValueError("similarity scorer or formatter is missing")
    if scorer.get("data", {}).get("code") != SCORER_CODE:
        raise ValueError("scorer code does not match the verified implementation")
    if formatter.get("data", {}).get("code") != FORMATTER_CODE:
        raise ValueError("formatter code does not match the verified implementation")
    variables = formatter.get("data", {}).get("variables", [])
    if not any(item.get("value_selector") == [SCORER_ID, "assessment_json"] for item in variables):
        raise ValueError("formatter is not wired to assessment_json")
    pairs = {(edge.get("source"), edge.get("target")) for edge in edges if isinstance(edge, dict)}
    if (GATE_ID, SCORER_ID) not in pairs or (SCORER_ID, FORMATTER_ID) not in pairs:
        raise ValueError("comparison success path is not wired through the scorer")
    if (GATE_ID, FORMATTER_ID) in pairs:
        raise ValueError("comparison success path still bypasses the scorer")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.apply == args.verify:
        parser.error("choose exactly one of --apply or --verify")

    from sqlalchemy.orm import sessionmaker

    from app import app as flask_app
    from extensions.ext_database import db
    from libs.datetime_utils import naive_utc_now
    from models import Account
    from models.model import App
    from services.workflow_service import WorkflowService

    with flask_app.app_context():
        session_maker = sessionmaker(bind=db.engine, expire_on_commit=False)
        service = WorkflowService(session_maker=session_maker)
        with session_maker.begin() as session:
            app_model = session.get(App, APP_ID)
            if app_model is None:
                raise ValueError(f"app not found: {APP_ID}")
            account_id = app_model.updated_by or app_model.created_by
            account = session.get(Account, account_id)
            if account is None:
                raise ValueError("app owner account was not found")
            draft = service.get_draft_workflow(app_model=app_model, session=session)
            if draft is None:
                raise ValueError("draft workflow was not found")

            transformed, changed = transform_graph(draft.graph_dict)
            if args.verify:
                validate_graph_contract(draft.graph_dict)
                active = service.get_published_workflow(app_model=app_model, session=session)
                if active is None:
                    raise ValueError("published workflow was not found")
                validate_graph_contract(active.graph_dict)
                print(json.dumps({
                    "status": "verified",
                    "draft_workflow_id": draft.id,
                    "published_workflow_id": active.id,
                    "node_count": len(draft.graph_dict["nodes"]),
                    "edge_count": len(draft.graph_dict["edges"]),
                }, separators=(",", ":")))
                return

            if not changed:
                print(json.dumps({"status": "already_current", "draft_workflow_id": draft.id}, separators=(",", ":")))
                return

            backup = service.publish_workflow(
                session=session,
                app_model=app_model,
                account=account,
                marked_name="Backup before V3.1 approximate similarity",
                marked_comment="Restorable draft snapshot created by Codex before deterministic scorer wiring.",
            )
            session.flush()
            service.sync_draft_workflow(
                app_model=app_model,
                graph=transformed,
                features=draft.normalized_features_dict,
                unique_hash=draft.unique_hash,
                account=account,
                environment_variables=draft.environment_variables,
                conversation_variables=draft.conversation_variables,
                session=session,
                commit=False,
            )
            published = service.publish_workflow(
                session=session,
                app_model=app_model,
                account=account,
                marked_name="V3.1 deterministic approximate similarity",
                marked_comment="Adds strict/approximate separation and deterministic metric grading.",
            )
            session.flush()
            app_model.workflow_id = published.id
            app_model.updated_by = account.id
            app_model.updated_at = naive_utc_now()
            print(json.dumps({
                "status": "updated",
                "backup_workflow_id": backup.id,
                "published_workflow_id": published.id,
                "draft_workflow_id": draft.id,
                "node_count": len(transformed["nodes"]),
                "edge_count": len(transformed["edges"]),
            }, separators=(",", ":")))


if __name__ == "__main__":
    main()
