"""Read-only comparison of a workflow DSL and offline graph snapshots."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

sys.dont_write_bytecode = True

import yaml

from workflow_release_integrity import compare_release_layers, source_digest


class InputError(ValueError):
    """An input cannot be safely compared."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputError(message)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = _ArgumentParser(add_help=True)
    parser.add_argument("--dsl", required=True, type=Path)
    parser.add_argument("--source", required=True, nargs="+", type=Path)
    parser.add_argument("--draft-snapshot", type=Path)
    parser.add_argument("--published-snapshot", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def _load_yaml_document(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise InputError("DSL document must be an object")
    return loaded


def _load_json_snapshot(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise InputError("snapshot must be an object")
    return loaded


def _mapping(value: object, description: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InputError(f"{description} must be an object")
    return value


def _dsl_graph(document: Mapping[str, object]) -> dict[str, object]:
    workflow = _mapping(document.get("workflow"), "workflow")
    graph = workflow.get("graph")
    if not isinstance(graph, dict):
        raise InputError("workflow graph must be an object")
    return graph


def _snapshot_graph(snapshot: Mapping[str, object]) -> dict[str, object]:
    graph = snapshot.get("graph", snapshot)
    if not isinstance(graph, dict):
        raise InputError("snapshot graph must be an object")
    return graph


def _metadata(mapping: Mapping[str, object]) -> Mapping[str, object]:
    value = mapping.get("release", mapping.get("metadata", {}))
    return _mapping(value, "release metadata")


def _source_baseline(document: Mapping[str, object], actual_digest: str) -> str:
    metadata = _metadata(document)
    declared = metadata.get("source_digest", document.get("source_digest"))
    if declared is None:
        return actual_digest
    if not isinstance(declared, str):
        raise InputError("source digest must be a string")
    return declared


def _application_id(mapping: Mapping[str, object]) -> str | None:
    identity = mapping.get("identity", mapping)
    if not isinstance(identity, Mapping):
        raise InputError("snapshot identity must be an object")
    value = identity.get("app_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InputError("snapshot application identity must be a non-empty string")
    return value


def _require_matching_identity(
    document: Mapping[str, object], snapshots: list[Mapping[str, object]]
) -> None:
    expected = _application_id(_metadata(document))
    actual = [identity for snapshot in snapshots if (identity := _application_id(snapshot))]
    if expected is not None and any(identity != expected for identity in actual):
        raise InputError("application identity mismatch")
    if len(set(actual)) > 1:
        raise InputError("application identity mismatch")


def _source_root(paths: list[Path]) -> Path:
    resolved = [path.resolve() for path in paths]
    if not resolved:
        raise InputError("at least one source path is required")
    return Path(os.path.commonpath([str(path.parent) for path in resolved]))


def _compare(arguments: argparse.Namespace) -> list[dict[str, str]]:
    document = _load_yaml_document(arguments.dsl)
    dsl_graph = _dsl_graph(document)
    source_paths = [path.resolve() for path in arguments.source]
    actual_source_digest = source_digest(source_paths, _source_root(source_paths))

    draft_snapshot = (
        _load_json_snapshot(arguments.draft_snapshot)
        if arguments.draft_snapshot is not None
        else None
    )
    published_snapshot = (
        _load_json_snapshot(arguments.published_snapshot)
        if arguments.published_snapshot is not None
        else None
    )
    snapshots = [
        snapshot for snapshot in (draft_snapshot, published_snapshot) if snapshot is not None
    ]
    _require_matching_identity(document, snapshots)

    return compare_release_layers(
        expected_source_digest=_source_baseline(document, actual_source_digest),
        actual_source_digest=actual_source_digest,
        dsl_graph=dsl_graph,
        draft_graph=_snapshot_graph(draft_snapshot)
        if draft_snapshot is not None
        else None,
        published_graph=_snapshot_graph(published_snapshot)
        if published_snapshot is not None
        else None,
    )


def _write_result(*, drift: list[dict[str, str]], as_json: bool) -> None:
    if as_json:
        sys.stdout.write(
            json.dumps({"drift": drift}, sort_keys=True, separators=(",", ":")) + "\n"
        )
        return
    if drift:
        sys.stdout.write("\n".join(item["code"] for item in drift) + "\n")
        return
    sys.stdout.write("clean\n")


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _arguments(argv)
        drift = _compare(arguments)
    except (
        InputError,
        OSError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        yaml.YAMLError,
        ValueError,
    ):
        as_json = "--json" in (argv if argv is not None else sys.argv[1:])
        if as_json:
            sys.stdout.write('{"error":"invalid_input"}\n')
        else:
            sys.stdout.write("invalid_input\n")
        return 2

    _write_result(drift=drift, as_json=arguments.as_json)
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
