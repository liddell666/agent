from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from scripts.workflow_release_integrity import (
    build_release_manifest,
    canonical_graph,
    compare_release_layers,
    graph_digest,
    source_digest,
)


def graph_fixture() -> dict[str, object]:
    return {
        "environment_variables": [{"name": "MODE", "value": "editor"}],
        "environment": {"mode": "editor", "locale": "en-US"},
        "viewport": {"x": 12, "y": 24, "zoom": 1.25},
        "nodes": [
            {
                "id": "answer",
                "selected": True,
                "position": {"x": 800, "y": 120},
                "data": {
                    "code": "return {'answer': result}",
                    "inputs": ["result"],
                    "outputs": ["answer"],
                },
            },
            {
                "id": "runner",
                "position": {"x": 120, "y": 120},
                "data": {
                    "code": "result = run(inputs)",
                    "inputs": ["query"],
                    "outputs": ["result"],
                    "conditions": [{"operator": "is", "value": "ready"}],
                },
            },
        ],
        "edges": [
            {
                "id": "edge-runner-answer",
                "source": "runner",
                "sourceHandle": "result",
                "target": "answer",
                "targetHandle": "result",
                "selected": True,
            }
        ],
    }


def test_graph_digest_ignores_layout_but_not_behavior():
    baseline = graph_fixture()
    layout_only = deepcopy(baseline)
    layout_only["nodes"].reverse()  # type: ignore[index,union-attr]
    layout_only["nodes"][0]["position"] = {"x": 999, "y": 999}  # type: ignore[index]
    layout_only["nodes"][0]["selected"] = False  # type: ignore[index]
    layout_only["viewport"] = {"x": 900, "y": 901, "zoom": 4}  # type: ignore[index]
    layout_only["environment_variables"] = [{"name": "MODE", "value": "runtime"}]  # type: ignore[index]
    layout_only["environment"] = {"mode": "runtime", "locale": "zh-CN"}  # type: ignore[index]
    layout_only["edges"].reverse()  # type: ignore[index,union-attr]

    assert graph_digest(layout_only) == graph_digest(baseline)

    behavior = deepcopy(baseline)
    behavior["nodes"][0]["data"]["code"] = "return {'changed': True}"  # type: ignore[index]
    assert graph_digest(behavior) != graph_digest(baseline)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda graph: graph["nodes"][0].update({"id": "different-answer"}),
        lambda graph: graph["nodes"][0]["data"].update({"inputs": ["other"]}),
        lambda graph: graph["nodes"][0]["data"].update({"outputs": ["other"]}),
        lambda graph: graph["nodes"][1]["data"].update({"conditions": []}),
        lambda graph: graph["edges"][0].update({"target": "runner"}),
    ],
)
def test_graph_digest_preserves_behavioral_identity(mutation):
    baseline = graph_fixture()
    changed = deepcopy(baseline)
    mutation(changed)

    assert graph_digest(changed) != graph_digest(baseline)


@pytest.mark.parametrize(
    "graph",
    [{}, {"nodes": []}, {"edges": []}, {"nodes": {}, "edges": []}],
)
def test_canonical_graph_requires_node_and_edge_arrays(graph):
    with pytest.raises(ValueError, match="workflow graph must contain node and edge arrays"):
        canonical_graph(graph)


def test_source_digest_uses_sorted_relative_posix_paths_and_file_bytes(tmp_path: Path):
    source_root = tmp_path / "source"
    nested = source_root / "nested"
    nested.mkdir(parents=True)
    first = source_root / "a.py"
    second = nested / "b.py"
    first.write_bytes(b"A = 1\n")
    second.write_bytes(b"B = 2\n")

    payload = b"a.py\0A = 1\n\0nested/b.py\0B = 2\n\0"
    expected = "sha256:" + sha256(payload).hexdigest()

    assert source_digest([second, first], source_root) == expected
    assert source_digest([first, second], source_root) == expected


@pytest.mark.parametrize(
    "unsafe_path",
    [
        lambda root: root.parent / "outside.py",
        lambda root: Path("relative-to-cwd.py"),
    ],
)
def test_source_digest_rejects_paths_outside_the_source_root(tmp_path: Path, unsafe_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")

    with pytest.raises(ValueError):
        source_digest([unsafe_path(source_root)], source_root)


def manifest_kwargs() -> dict[str, object]:
    return {
        "git_commit": "abc123",
        "worktree_clean": True,
        "source_sha256": "sha256:" + "a" * 64,
        "dsl_sha256": "sha256:" + "b" * 64,
        "graph_sha256": "sha256:" + "c" * 64,
        "workflow_kind": "paper-comparison-multimodel",
        "workflow_version": "multimodel-0.8.0",
        "app_id": "b9a766a0-0ad0-415b-8d42-60459c92bec7",
        "draft_workflow_id": "0fd3ec44-599e-4936-841d-a6df3b8094eb",
        "published_workflow_id": "94e00245-a1f8-48e3-aba6-41549ab75c6e",
        "rollback_workflow_id": "53a5a8ec-6639-4080-8b15-fc2fc93116a5",
    }


def test_release_manifest_is_deterministic_and_contains_only_explicit_metadata():
    manifest = build_release_manifest(**manifest_kwargs())

    assert manifest == {
        "schema_version": 1,
        "git_commit": "abc123",
        "worktree_clean": True,
        "source_digest": "sha256:" + "a" * 64,
        "dsl_digest": "sha256:" + "b" * 64,
        "graph_digest": "sha256:" + "c" * 64,
        "workflow_kind": "paper-comparison-multimodel",
        "workflow_version": "multimodel-0.8.0",
        "dify": {
            "app_id": "b9a766a0-0ad0-415b-8d42-60459c92bec7",
            "draft_workflow_id": "0fd3ec44-599e-4936-841d-a6df3b8094eb",
            "published_workflow_id": "94e00245-a1f8-48e3-aba6-41549ab75c6e",
            "rollback_workflow_id": "53a5a8ec-6639-4080-8b15-fc2fc93116a5",
        },
    }
    assert "timestamp" not in manifest
    assert json.dumps(manifest, allow_nan=False)


def test_release_manifest_allows_missing_dify_ids_before_live_inspection():
    kwargs = manifest_kwargs()
    for name in (
        "app_id",
        "draft_workflow_id",
        "published_workflow_id",
        "rollback_workflow_id",
    ):
        kwargs[name] = None

    assert build_release_manifest(**kwargs)["dify"] == {
        "app_id": None,
        "draft_workflow_id": None,
        "published_workflow_id": None,
        "rollback_workflow_id": None,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_sha256", "sha256:" + "A" * 64),
        ("dsl_sha256", "sha256:not-a-digest"),
        ("graph_sha256", "sha256:" + "0" * 63),
        ("app_id", "not-a-uuid"),
        ("git_commit", "C:/Users/release-token"),
        ("workflow_kind", "paper comparison\nmultimodel"),
        ("worktree_clean", "true"),
    ],
)
def test_release_manifest_rejects_unsafe_metadata(field: str, value: object):
    kwargs = manifest_kwargs()
    kwargs[field] = value

    with pytest.raises(ValueError):
        build_release_manifest(**kwargs)


def changed_graph_fixture() -> dict[str, object]:
    changed = graph_fixture()
    changed["nodes"][0]["data"]["code"] = "return {'draft': result}"  # type: ignore[index]
    return changed


def other_changed_graph_fixture() -> dict[str, object]:
    changed = changed_graph_fixture()
    changed["nodes"][1]["data"]["conditions"] = []  # type: ignore[index]
    return changed


def test_compare_release_layers_reports_each_drift_layer_in_order():
    results = compare_release_layers(
        expected_source_digest="sha256:" + "a" * 64,
        actual_source_digest="sha256:" + "b" * 64,
        dsl_graph=graph_fixture(),
        draft_graph=changed_graph_fixture(),
        published_graph=other_changed_graph_fixture(),
    )

    assert [item["code"] for item in results] == [
        "source_dsl_drift",
        "dsl_draft_drift",
        "draft_published_drift",
    ]
    assert results[0] == {
        "code": "source_dsl_drift",
        "expected_digest": "sha256:" + "a" * 64,
        "actual_digest": "sha256:" + "b" * 64,
    }
    assert results[1] == {
        "code": "dsl_draft_drift",
        "expected_digest": graph_digest(graph_fixture()),
        "actual_digest": graph_digest(changed_graph_fixture()),
    }
    assert results[2] == {
        "code": "draft_published_drift",
        "expected_digest": graph_digest(changed_graph_fixture()),
        "actual_digest": graph_digest(other_changed_graph_fixture()),
    }
    assert json.dumps(results, allow_nan=False)


def test_compare_release_layers_reports_no_drift_for_matching_available_layers():
    baseline = graph_fixture()

    assert compare_release_layers(
        expected_source_digest="sha256:" + "a" * 64,
        actual_source_digest="sha256:" + "a" * 64,
        dsl_graph=baseline,
        draft_graph=deepcopy(baseline),
        published_graph=deepcopy(baseline),
    ) == []


def test_compare_release_layers_skips_unavailable_live_layers():
    assert compare_release_layers(
        expected_source_digest="sha256:" + "a" * 64,
        actual_source_digest="sha256:" + "a" * 64,
        dsl_graph=graph_fixture(),
    ) == []
