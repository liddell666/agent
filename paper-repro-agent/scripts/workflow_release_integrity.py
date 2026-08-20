"""Pure, deterministic contracts for workflow release integrity."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID


_VOLATILE_GRAPH_KEYS = frozenset(
    {
        "selected",
        "position",
        "positionAbsolute",
        "viewport",
        "width",
        "height",
        "zIndex",
    }
)
_VOLATILE_ROOT_KEYS = frozenset({"environment", "environment_variables"})
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_METADATA_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def canonical_graph(graph: dict[str, object]) -> dict[str, object]:
    """Return a behavior-only, deterministically ordered workflow graph."""
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("workflow graph must contain node and edge arrays")

    canonical = {
        key: _canonicalize(value)
        for key, value in graph.items()
        if key not in _VOLATILE_GRAPH_KEYS and key not in _VOLATILE_ROOT_KEYS
    }
    canonical["nodes"] = sorted(
        (_canonicalize_node(node) for node in nodes), key=_node_sort_key
    )
    canonical["edges"] = sorted(
        (_canonicalize_edge(edge) for edge in edges), key=_edge_sort_key
    )
    return canonical


def graph_digest(graph: dict[str, object]) -> str:
    payload = json.dumps(
        canonical_graph(graph),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def source_digest(paths: Sequence[Path], root: Path) -> str:
    """Hash source files by sorted, root-relative POSIX name and bytes."""
    if not isinstance(root, Path):
        raise ValueError("source root must be a Path")
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        raise ValueError("source root must be a directory")

    entries: list[tuple[str, Path]] = []
    for path in paths:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("source paths must be absolute Paths within the source root")
        resolved_path = path.resolve()
        try:
            relative = resolved_path.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("source path must be within the source root") from exc
        if not resolved_path.is_file():
            raise ValueError("source path must be a file")
        entries.append((relative.as_posix(), resolved_path))

    entries.sort(key=lambda entry: entry[0])
    if len({relative for relative, _ in entries}) != len(entries):
        raise ValueError("source paths must not contain duplicates")

    digest = hashlib.sha256()
    for relative, path in entries:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def build_release_manifest(
    *,
    git_commit: str,
    worktree_clean: bool,
    source_sha256: str,
    dsl_sha256: str,
    graph_sha256: str,
    workflow_kind: str,
    workflow_version: str,
    app_id: str | None = None,
    draft_workflow_id: str | None = None,
    published_workflow_id: str | None = None,
    rollback_workflow_id: str | None = None,
) -> dict[str, object]:
    """Build a deterministic, JSON-safe manifest without inspecting live state."""
    _require_metadata("git_commit", git_commit)
    _require_metadata("workflow_kind", workflow_kind)
    _require_metadata("workflow_version", workflow_version)
    if not isinstance(worktree_clean, bool):
        raise ValueError("worktree_clean must be a boolean")
    _require_digest("source_sha256", source_sha256)
    _require_digest("dsl_sha256", dsl_sha256)
    _require_digest("graph_sha256", graph_sha256)

    dify = {
        "app_id": _optional_uuid("app_id", app_id),
        "draft_workflow_id": _optional_uuid("draft_workflow_id", draft_workflow_id),
        "published_workflow_id": _optional_uuid(
            "published_workflow_id", published_workflow_id
        ),
        "rollback_workflow_id": _optional_uuid(
            "rollback_workflow_id", rollback_workflow_id
        ),
    }
    return {
        "schema_version": 1,
        "git_commit": git_commit,
        "worktree_clean": worktree_clean,
        "source_digest": source_sha256,
        "dsl_digest": dsl_sha256,
        "graph_digest": graph_sha256,
        "workflow_kind": workflow_kind,
        "workflow_version": workflow_version,
        "dify": dify,
    }


def compare_release_layers(
    *,
    expected_source_digest: str,
    actual_source_digest: str,
    dsl_graph: dict[str, object],
    draft_graph: dict[str, object] | None = None,
    published_graph: dict[str, object] | None = None,
) -> list[dict[str, str]]:
    """Compare source, DSL, draft, and published layers without side effects."""
    _require_digest("expected_source_digest", expected_source_digest)
    _require_digest("actual_source_digest", actual_source_digest)

    results: list[dict[str, str]] = []
    if expected_source_digest != actual_source_digest:
        results.append(
            {
                "code": "source_dsl_drift",
                "expected_digest": expected_source_digest,
                "actual_digest": actual_source_digest,
            }
        )

    dsl_digest = graph_digest(dsl_graph)
    if draft_graph is None:
        return results

    draft_digest = graph_digest(draft_graph)
    if dsl_digest != draft_digest:
        results.append(
            {
                "code": "dsl_draft_drift",
                "expected_digest": dsl_digest,
                "actual_digest": draft_digest,
            }
        )

    if published_graph is None:
        return results

    published_digest = graph_digest(published_graph)
    if draft_digest != published_digest:
        results.append(
            {
                "code": "draft_published_drift",
                "expected_digest": draft_digest,
                "actual_digest": published_digest,
            }
        )
    return results


def _canonicalize(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _canonicalize(item)
            for key, item in value.items()
            if key not in _VOLATILE_GRAPH_KEYS
        }
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value


def _canonicalize_node(node: object) -> object:
    return _canonicalize(node)


def _canonicalize_edge(edge: object) -> object:
    return _canonicalize(edge)


def _node_sort_key(node: object) -> str:
    if isinstance(node, Mapping):
        return str(node.get("id", ""))
    return ""


def _edge_sort_key(edge: object) -> tuple[str, str, str, str, str]:
    if not isinstance(edge, Mapping):
        return ("", "", "", "", "")
    return (
        str(edge.get("source", "")),
        str(edge.get("sourceHandle", "")),
        str(edge.get("target", "")),
        str(edge.get("targetHandle", "")),
        str(edge.get("id", "")),
    )


def _require_digest(name: str, value: object) -> None:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a sha256 digest")


def _require_metadata(name: str, value: object) -> None:
    if not isinstance(value, str) or _METADATA_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} contains unsafe metadata")


def _optional_uuid(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID or None")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a UUID or None") from exc
    if str(parsed) != value:
        raise ValueError(f"{name} must be a canonical UUID or None")
    return value
