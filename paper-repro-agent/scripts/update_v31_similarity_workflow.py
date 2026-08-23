"""Safely add deterministic similarity scoring to the local Dify V3.1 app.

Run this script inside the Dify API container, where ``/app/api`` and the
configured database are available.  It uses Dify's own workflow service for
validation and publishing and creates a normal restorable workflow snapshot
before changing the draft.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

try:
    from scripts.workflow_release_integrity import (
        build_release_manifest,
        canonical_graph,
        compare_release_layers,
        graph_digest,
    )
except ModuleNotFoundError:  # pragma: no cover - direct ``python scripts/...`` use
    from workflow_release_integrity import (  # type: ignore[no-redef]
        build_release_manifest,
        canonical_graph,
        compare_release_layers,
        graph_digest,
    )


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


class ReleaseService(Protocol):
    """Side-effect boundary used by the release orchestration functions."""

    def read_draft(self, *, app_model: object, session: object) -> object: ...

    def read_published(self, *, app_model: object, session: object) -> object: ...

    def create_backup(
        self, *, app_model: object, session: object, mark: ReleaseMark
    ) -> object: ...

    def save_draft(
        self,
        *,
        app_model: object,
        session: object,
        graph: dict[str, object],
        metadata: WorkflowReleaseMetadata | None = None,
    ) -> object: ...

    def publish(
        self, *, app_model: object, session: object, mark: ReleaseMark
    ) -> object: ...

    def rollback(
        self,
        *,
        app_model: object,
        session: object,
        workflow_id: str,
        mark: ReleaseMark,
    ) -> object: ...


@dataclass(frozen=True)
class ReleaseMark:
    """Human-readable Dify version labels for a release and its backup."""

    name: str
    comment: str = ""
    backup_name: str = ""
    backup_comment: str = ""


@dataclass(frozen=True, repr=False)
class WorkflowReleaseMetadata:
    """Canonical workflow metadata without value-bearing repr output."""

    _canonical_json: str

    @classmethod
    def from_values(
        cls,
        *,
        features: object,
        environment_variables: object,
        conversation_variables: object,
    ) -> WorkflowReleaseMetadata:
        payload = {
            "features": copy.deepcopy(features),
            "environment_variables": copy.deepcopy(environment_variables),
            "conversation_variables": copy.deepcopy(conversation_variables),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return cls(encoded)

    @classmethod
    def from_workflow(cls, workflow: object) -> WorkflowReleaseMetadata:
        return cls.from_values(
            features=getattr(workflow, "normalized_features_dict"),
            environment_variables=getattr(workflow, "environment_variables"),
            conversation_variables=getattr(workflow, "conversation_variables"),
        )

    def _payload(self) -> dict[str, object]:
        value = json.loads(self._canonical_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor invariant
            raise RuntimeError("canonical workflow metadata is not an object")
        return value

    @property
    def features(self) -> object:
        return self._payload()["features"]

    @property
    def environment_variables(self) -> object:
        return self._payload()["environment_variables"]

    @property
    def conversation_variables(self) -> object:
        return self._payload()["conversation_variables"]

    def __repr__(self) -> str:
        return f"WorkflowReleaseMetadata(digest={workflow_metadata_digest(self)!r})"


def workflow_metadata_digest(metadata: WorkflowReleaseMetadata) -> str:
    """Return a stable identity without exposing metadata values."""

    if not isinstance(metadata, WorkflowReleaseMetadata):
        raise TypeError("metadata must be WorkflowReleaseMetadata")
    return "sha256:" + hashlib.sha256(metadata._canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExpectedReleaseIdentity:
    """Optimistic concurrency guard required before any release mutation."""

    app_id: str
    draft_digest: str
    draft_workflow_id: str | None = None
    draft_metadata_digest: str | None = None


@dataclass(frozen=True)
class ReleaseState:
    """Canonical read-only snapshot of one Dify application's release state."""

    app_id: str
    draft_workflow_id: str
    published_workflow_id: str
    draft_graph: dict[str, object]
    published_graph: dict[str, object]
    draft_digest: str
    published_digest: str
    draft_metadata: WorkflowReleaseMetadata
    published_metadata: WorkflowReleaseMetadata
    draft_metadata_digest: str
    published_metadata_digest: str


def inspect_release_state(
    service: ReleaseService,
    app_model: object,
    session: object,
    expected_app_id: str,
) -> ReleaseState:
    """Read and canonicalize draft/published state without any write calls."""
    app_id = str(getattr(app_model, "id", ""))
    if app_id != expected_app_id:
        raise ValueError(
            f"Dify application identity mismatch: expected {expected_app_id}, got {app_id}"
        )

    draft = service.read_draft(app_model=app_model, session=session)
    if draft is None:
        raise ValueError("draft workflow was not found")
    published = service.read_published(app_model=app_model, session=session)
    if published is None:
        raise ValueError("published workflow was not found")

    draft_graph = canonical_graph(getattr(draft, "graph_dict"))
    published_graph = canonical_graph(getattr(published, "graph_dict"))
    draft_metadata = WorkflowReleaseMetadata.from_workflow(draft)
    published_metadata = WorkflowReleaseMetadata.from_workflow(published)
    return ReleaseState(
        app_id=app_id,
        draft_workflow_id=str(getattr(draft, "id")),
        published_workflow_id=str(getattr(published, "id")),
        draft_graph=draft_graph,
        published_graph=published_graph,
        draft_digest=graph_digest(draft_graph),
        published_digest=graph_digest(published_graph),
        draft_metadata=draft_metadata,
        published_metadata=published_metadata,
        draft_metadata_digest=workflow_metadata_digest(draft_metadata),
        published_metadata_digest=workflow_metadata_digest(published_metadata),
    )


def publish_verified_graph(
    service: ReleaseService,
    app_model: object,
    session: object,
    candidate_graph: dict[str, object],
    expected_identity: ExpectedReleaseIdentity | ReleaseState | Mapping[str, object],
    mark: ReleaseMark | Mapping[str, object] | str,
    *,
    release_manifest: Mapping[str, object] | None = None,
    candidate_metadata: WorkflowReleaseMetadata | None = None,
) -> dict[str, object]:
    """Back up, publish, verify, and explicitly roll back a candidate graph."""
    identity = _normalize_expected_identity(expected_identity)
    release_mark = _normalize_release_mark(mark)
    candidate = copy.deepcopy(candidate_graph)
    candidate_digest = graph_digest(candidate)
    metadata_aware = candidate_metadata is not None
    validated_manifest = _validate_release_manifest(
        release_manifest,
        expected_identity=identity,
        candidate_digest=candidate_digest,
    )

    state = inspect_release_state(
        service,
        app_model,
        session,
        expected_app_id=identity.app_id,
    )
    if validated_manifest is not None:
        manifest_dify = validated_manifest["dify"]
        if not isinstance(manifest_dify, dict):  # validated above; keeps narrowing local
            raise ValueError("release_manifest must contain Task 5 Dify identity")
        if (
            manifest_dify["draft_workflow_id"] is not None
            and manifest_dify["draft_workflow_id"] != state.draft_workflow_id
        ):
            raise ValueError("release_manifest draft workflow identity mismatch")
        if (
            manifest_dify["published_workflow_id"] is not None
            and manifest_dify["published_workflow_id"] != state.published_workflow_id
        ):
            raise ValueError("release_manifest published workflow identity mismatch")
    if state.draft_digest != identity.draft_digest:
        raise ValueError(
            "Dify draft digest mismatch: "
            f"expected {identity.draft_digest}, got {state.draft_digest}"
        )
    if (
        identity.draft_workflow_id is not None
        and state.draft_workflow_id != identity.draft_workflow_id
    ):
        raise ValueError(
            "Dify draft workflow identity mismatch: "
            f"expected {identity.draft_workflow_id}, got {state.draft_workflow_id}"
        )
    if metadata_aware and identity.draft_metadata_digest is None:
        raise ValueError(
            "metadata-aware release identity requires draft_metadata_digest"
        )
    if (
        identity.draft_metadata_digest is not None
        and state.draft_metadata_digest != identity.draft_metadata_digest
    ):
        raise ValueError(
            "Dify draft metadata digest mismatch: "
            f"expected {identity.draft_metadata_digest}, "
            f"got {state.draft_metadata_digest}"
        )

    effective_candidate_metadata = candidate_metadata or state.draft_metadata
    candidate_metadata_digest = workflow_metadata_digest(
        effective_candidate_metadata
    )

    preexisting_drift = compare_release_layers(
        expected_source_digest=state.draft_digest,
        actual_source_digest=state.draft_digest,
        dsl_graph=state.draft_graph,
        draft_graph=state.draft_graph,
        published_graph=state.published_graph,
    )

    backup = service.create_backup(
        app_model=app_model,
        session=session,
        mark=ReleaseMark(
            name=release_mark.backup_name or f"Backup before {release_mark.name}",
            comment=release_mark.backup_comment,
        ),
    )
    try:
        backup_id = _workflow_id(backup, "backup")
    except RuntimeError as exc:
        raise RuntimeError(
            "unrecoverable Dify backup validation failure: "
            "no valid explicit rollback ID is available"
        ) from exc
    try:
        backup_graph = getattr(backup, "graph_dict")
        backup_digest = graph_digest(backup_graph)
        if backup_digest != state.draft_digest:
            raise RuntimeError(
                f"Dify backup digest mismatch: expected {state.draft_digest}, "
                f"got {backup_digest}"
            )
        backup_metadata_digest = workflow_metadata_digest(
            WorkflowReleaseMetadata.from_workflow(backup)
        )
        if backup_metadata_digest != state.draft_metadata_digest:
            raise RuntimeError(
                "Dify backup metadata digest mismatch: "
                f"expected {state.draft_metadata_digest}, "
                f"got {backup_metadata_digest}"
            )
    except Exception as exc:
        return _rollback_verified(
            service=service,
            app_model=app_model,
            session=session,
            state=state,
            release_mark=release_mark,
            backup_id=backup_id,
            candidate_digest=candidate_digest,
            candidate_metadata_digest=candidate_metadata_digest,
            requested_published_id=None,
            failed_published_id=None,
            failed_published_digest=None,
            failed_published_metadata_digest=None,
            metadata_aware=metadata_aware,
            preexisting_drift=preexisting_drift,
            failure_code="backup_validation_failed",
            failure_operation="validate_backup",
            failure_type=type(exc).__name__,
            release_manifest=validated_manifest,
        )

    published_id: str | None = None
    active_id: str | None = None
    active_digest: str | None = None
    active_metadata_digest: str | None = None
    operation = "save_draft"
    try:
        service.save_draft(
            app_model=app_model,
            session=session,
            graph=candidate,
            metadata=effective_candidate_metadata,
        )
        operation = "publish"
        published = service.publish(
            app_model=app_model,
            session=session,
            mark=release_mark,
        )
        published_id = _workflow_id(published, "published")
        operation = "read_published"
        active = service.read_published(app_model=app_model, session=session)
        operation = "verify_published"
        if active is not None:
            active_id = _workflow_id(active, "published")
            active_digest = graph_digest(getattr(active, "graph_dict"))
            active_metadata_digest = workflow_metadata_digest(
                WorkflowReleaseMetadata.from_workflow(active)
            )
    except Exception as exc:
        return _rollback_verified(
            service=service,
            app_model=app_model,
            session=session,
            state=state,
            release_mark=release_mark,
            backup_id=backup_id,
            candidate_digest=candidate_digest,
            candidate_metadata_digest=candidate_metadata_digest,
            requested_published_id=published_id,
            failed_published_id=active_id,
            failed_published_digest=active_digest,
            failed_published_metadata_digest=active_metadata_digest,
            metadata_aware=metadata_aware,
            preexisting_drift=preexisting_drift,
            failure_code="release_operation_failed",
            failure_operation=operation,
            failure_type=type(exc).__name__,
            release_manifest=validated_manifest,
        )

    if (
        active_digest == candidate_digest
        and active_id == published_id
        and active_metadata_digest == candidate_metadata_digest
    ):
        result: dict[str, object] = {
            "status": "published",
            "app_id": state.app_id,
            "draft_workflow_id": state.draft_workflow_id,
            "previous_published_workflow_id": state.published_workflow_id,
            "backup_workflow_id": backup_id,
            "published_workflow_id": published_id,
            "rollback_workflow_id": None,
            "candidate_digest": candidate_digest,
            "published_digest": active_digest,
            "preexisting_drift": preexisting_drift,
            "release_manifest": copy.deepcopy(validated_manifest),
        }
        if metadata_aware:
            result.update(
                {
                    "candidate_metadata_digest": candidate_metadata_digest,
                    "published_metadata_digest": active_metadata_digest,
                }
            )
        return result

    return _rollback_verified(
        service=service,
        app_model=app_model,
        session=session,
        state=state,
        release_mark=release_mark,
        backup_id=backup_id,
        candidate_digest=candidate_digest,
        candidate_metadata_digest=candidate_metadata_digest,
        requested_published_id=published_id,
        failed_published_id=active_id,
        failed_published_digest=active_digest,
        failed_published_metadata_digest=active_metadata_digest,
        metadata_aware=metadata_aware,
        preexisting_drift=preexisting_drift,
        failure_code="post_publish_verification_failed",
        failure_operation="verify_published",
        release_manifest=validated_manifest,
    )


def _rollback_verified(
    *,
    service: ReleaseService,
    app_model: object,
    session: object,
    state: ReleaseState,
    release_mark: ReleaseMark,
    backup_id: str,
    candidate_digest: str,
    candidate_metadata_digest: str,
    requested_published_id: str | None,
    failed_published_id: str | None,
    failed_published_digest: str | None,
    failed_published_metadata_digest: str | None,
    metadata_aware: bool,
    preexisting_drift: list[dict[str, str]],
    failure_code: str,
    failure_operation: str,
    failure_type: str | None = None,
    release_manifest: dict[str, object] | None = None,
) -> dict[str, object]:
    rollback = service.rollback(
        app_model=app_model,
        session=session,
        workflow_id=backup_id,
        mark=ReleaseMark(
            name=f"Rollback after failed {release_mark.name}",
            comment=f"Restored explicit backup workflow {backup_id}.",
        ),
    )
    rollback_id = _workflow_id(rollback, "rollback")
    restored = service.read_published(app_model=app_model, session=session)
    if restored is None:
        raise RuntimeError(
            f"Dify rollback verification failed for explicit backup {backup_id}: "
            "published workflow was not found"
        )
    restored_id = _workflow_id(restored, "rollback")
    restored_digest = graph_digest(getattr(restored, "graph_dict"))
    if restored_id != rollback_id or restored_digest != state.draft_digest:
        raise RuntimeError(
            f"Dify rollback verification failed for explicit backup {backup_id}: "
            f"expected digest {state.draft_digest}, got {restored_digest}"
        )
    restored_metadata_digest = workflow_metadata_digest(
        WorkflowReleaseMetadata.from_workflow(restored)
    )
    if restored_metadata_digest != state.draft_metadata_digest:
        raise RuntimeError(
            "Dify rollback metadata verification failed for explicit backup "
            f"{backup_id}: expected digest {state.draft_metadata_digest}, "
            f"got {restored_metadata_digest}"
        )

    result: dict[str, object] = {
        "status": "rolled_back",
        "failure_code": failure_code,
        "failure_operation": failure_operation,
        "failure_type": failure_type,
        "app_id": state.app_id,
        "draft_workflow_id": state.draft_workflow_id,
        "previous_published_workflow_id": state.published_workflow_id,
        "backup_workflow_id": backup_id,
        "failed_published_workflow_id": failed_published_id,
        "requested_published_workflow_id": requested_published_id,
        "rollback_workflow_id": rollback_id,
        "candidate_digest": candidate_digest,
        "failed_published_digest": failed_published_digest,
        "restored_digest": restored_digest,
        "preexisting_drift": preexisting_drift,
        "release_manifest": copy.deepcopy(release_manifest),
    }
    if metadata_aware:
        result.update(
            {
                "candidate_metadata_digest": candidate_metadata_digest,
                "failed_published_metadata_digest": failed_published_metadata_digest,
                "restored_metadata_digest": restored_metadata_digest,
            }
        )
    return result


def _validate_release_manifest(
    manifest: Mapping[str, object] | None,
    *,
    expected_identity: ExpectedReleaseIdentity,
    candidate_digest: str,
) -> dict[str, object] | None:
    if manifest is None:
        return None
    dify = manifest.get("dify")
    if not isinstance(dify, Mapping):
        raise ValueError("release_manifest must contain Task 5 Dify identity")
    try:
        validated = build_release_manifest(
            git_commit=manifest["git_commit"],  # type: ignore[arg-type]
            worktree_clean=manifest["worktree_clean"],  # type: ignore[arg-type]
            source_sha256=manifest["source_digest"],  # type: ignore[arg-type]
            dsl_sha256=manifest["dsl_digest"],  # type: ignore[arg-type]
            graph_sha256=manifest["graph_digest"],  # type: ignore[arg-type]
            workflow_kind=manifest["workflow_kind"],  # type: ignore[arg-type]
            workflow_version=manifest["workflow_version"],  # type: ignore[arg-type]
            app_id=dify.get("app_id"),  # type: ignore[arg-type]
            draft_workflow_id=dify.get("draft_workflow_id"),  # type: ignore[arg-type]
            published_workflow_id=dify.get("published_workflow_id"),  # type: ignore[arg-type]
            rollback_workflow_id=dify.get("rollback_workflow_id"),  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("release_manifest must match the Task 5 manifest contract") from exc
    if validated != dict(manifest):
        raise ValueError("release_manifest must be a canonical Task 5 manifest")
    if validated["graph_digest"] != candidate_digest:
        raise ValueError("release_manifest graph digest does not match candidate graph")
    validated_dify = validated["dify"]
    if not isinstance(validated_dify, dict):  # defensive; Task 5 always returns a dict
        raise ValueError("release_manifest must contain Task 5 Dify identity")
    if validated_dify["app_id"] != expected_identity.app_id:
        raise ValueError("release_manifest application identity mismatch")
    if (
        expected_identity.draft_workflow_id is not None
        and validated_dify["draft_workflow_id"] != expected_identity.draft_workflow_id
    ):
        raise ValueError("release_manifest draft workflow identity mismatch")
    return copy.deepcopy(validated)


def _normalize_expected_identity(
    value: ExpectedReleaseIdentity | ReleaseState | Mapping[str, object],
) -> ExpectedReleaseIdentity:
    if isinstance(value, Mapping):
        app_id = value.get("app_id")
        draft_digest = value.get("draft_digest")
        draft_workflow_id = value.get("draft_workflow_id")
        draft_metadata_digest = value.get("draft_metadata_digest")
    else:
        app_id = getattr(value, "app_id", None)
        draft_digest = getattr(value, "draft_digest", None)
        draft_workflow_id = getattr(value, "draft_workflow_id", None)
        draft_metadata_digest = getattr(value, "draft_metadata_digest", None)
    if not isinstance(app_id, str) or not app_id:
        raise ValueError("expected release identity requires app_id")
    if not isinstance(draft_digest, str) or not draft_digest:
        raise ValueError("expected release identity requires draft_digest")
    if draft_workflow_id is not None and not isinstance(draft_workflow_id, str):
        raise ValueError("expected draft_workflow_id must be a string")
    if draft_metadata_digest is not None and not isinstance(draft_metadata_digest, str):
        raise ValueError("expected draft_metadata_digest must be a string")
    return ExpectedReleaseIdentity(
        app_id,
        draft_digest,
        draft_workflow_id,
        draft_metadata_digest,
    )


def _normalize_release_mark(
    value: ReleaseMark | Mapping[str, object] | str,
) -> ReleaseMark:
    if isinstance(value, ReleaseMark):
        mark = value
    elif isinstance(value, str):
        mark = ReleaseMark(name=value)
    elif isinstance(value, Mapping):
        fields = {
            key: value.get(key, "")
            for key in ("name", "comment", "backup_name", "backup_comment")
        }
        if not all(isinstance(item, str) for item in fields.values()):
            raise ValueError("release mark values must be strings")
        mark = ReleaseMark(**fields)  # type: ignore[arg-type]
    else:
        raise ValueError("release mark must be a string, mapping, or ReleaseMark")
    if not mark.name.strip():
        raise ValueError("release mark name must not be empty")
    return mark


def _workflow_id(workflow: object, label: str) -> str:
    workflow_id = getattr(workflow, "id", None)
    if not isinstance(workflow_id, str) or not workflow_id:
        raise RuntimeError(f"Dify {label} workflow did not return an ID")
    try:
        canonical_id = str(UUID(workflow_id))
    except ValueError as exc:
        raise RuntimeError(f"Dify {label} workflow returned an invalid ID") from exc
    if canonical_id != workflow_id:
        raise RuntimeError(f"Dify {label} workflow returned a non-canonical ID")
    return workflow_id


class DifyReleaseService:
    """Adapt Dify's WorkflowService to the small release protocol above."""

    def __init__(self, workflow_service: object, account: object, *, now_factory: Any) -> None:
        self._service = workflow_service
        self._account = account
        self._now_factory = now_factory
        self._drafts: dict[str, object] = {}

    def read_draft(self, *, app_model: object, session: object) -> object:
        draft = self._service.get_draft_workflow(  # type: ignore[attr-defined]
            app_model=app_model,
            session=session,
        )
        if draft is not None:
            self._drafts[str(getattr(app_model, "id"))] = draft
        return draft

    def read_published(self, *, app_model: object, session: object) -> object:
        return self._service.get_published_workflow(  # type: ignore[attr-defined]
            app_model=app_model,
            session=session,
        )

    def create_backup(
        self, *, app_model: object, session: object, mark: ReleaseMark
    ) -> object:
        backup = self._publish_version(app_model=app_model, session=session, mark=mark)
        session.flush()  # type: ignore[attr-defined]
        return backup

    def save_draft(
        self,
        *,
        app_model: object,
        session: object,
        graph: dict[str, object],
        metadata: WorkflowReleaseMetadata | None = None,
    ) -> object:
        current = self._cached_draft(app_model)
        effective_metadata = metadata or WorkflowReleaseMetadata.from_workflow(
            current
        )
        draft = self._sync_draft(
            app_model=app_model,
            session=session,
            graph=graph,
            current=current,
            metadata=effective_metadata,
        )
        self._drafts[str(getattr(app_model, "id"))] = draft
        session.flush()  # type: ignore[attr-defined]
        return draft

    def publish(
        self, *, app_model: object, session: object, mark: ReleaseMark
    ) -> object:
        published = self._publish_version(
            app_model=app_model,
            session=session,
            mark=mark,
        )
        session.flush()  # type: ignore[attr-defined]
        self._activate(app_model, published)
        return published

    def rollback(
        self,
        *,
        app_model: object,
        session: object,
        workflow_id: str,
        mark: ReleaseMark,
    ) -> object:
        restore = getattr(self._service, "restore_published_workflow_to_draft", None)
        if callable(restore):
            self._activate_id(app_model, workflow_id)
            draft = restore(
                app_model=app_model,
                workflow_id=workflow_id,
                account=self._account,
                session=session,
            )
        else:
            source = self._service.get_published_workflow_by_id(  # type: ignore[attr-defined]
                app_model=app_model,
                workflow_id=workflow_id,
                session=session,
            )
            if source is None or _workflow_id(source, "backup") != workflow_id:
                raise RuntimeError(
                    f"explicit Dify backup workflow was not found: {workflow_id}"
                )

            current = self._cached_draft(app_model)
            draft = self._sync_draft(
                app_model=app_model,
                session=session,
                graph=copy.deepcopy(getattr(source, "graph_dict")),
                current=current,
                metadata=WorkflowReleaseMetadata.from_workflow(source),
            )
        self._drafts[str(getattr(app_model, "id"))] = draft
        session.flush()  # type: ignore[attr-defined]
        restored = self._publish_version(
            app_model=app_model,
            session=session,
            mark=mark,
        )
        session.flush()  # type: ignore[attr-defined]
        self._activate(app_model, restored)
        return restored

    def _cached_draft(self, app_model: object) -> object:
        app_id = str(getattr(app_model, "id"))
        draft = self._drafts.get(app_id)
        if draft is None:
            raise RuntimeError("Dify draft must be read before a release write")
        return draft

    def _sync_draft(
        self,
        *,
        app_model: object,
        session: object,
        graph: dict[str, object],
        current: object,
        metadata: WorkflowReleaseMetadata,
    ) -> object:
        return self._service.sync_draft_workflow(  # type: ignore[attr-defined]
            app_model=app_model,
            graph=copy.deepcopy(graph),
            features=metadata.features,
            unique_hash=getattr(current, "unique_hash", None),
            account=self._account,
            environment_variables=metadata.environment_variables,
            conversation_variables=metadata.conversation_variables,
            session=session,
            commit=False,
        )

    def _publish_version(
        self, *, app_model: object, session: object, mark: ReleaseMark
    ) -> object:
        result = self._service.publish_workflow(  # type: ignore[attr-defined]
            session=session,
            app_model=app_model,
            account=self._account,
            marked_name=mark.name,
            marked_comment=mark.comment,
        )
        if isinstance(result, tuple):
            return result[0]
        return result

    def _activate(self, app_model: object, workflow: object) -> None:
        self._activate_id(app_model, _workflow_id(workflow, "published"))

    def _activate_id(self, app_model: object, workflow_id: str) -> None:
        setattr(app_model, "workflow_id", workflow_id)
        setattr(app_model, "updated_by", getattr(self._account, "id"))
        setattr(app_model, "updated_at", self._now_factory())


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
        with session_maker() as session:
            app_model = session.get(App, APP_ID)
            if app_model is None:
                raise ValueError(f"app not found: {APP_ID}")
            account_id = app_model.updated_by or app_model.created_by
            account = session.get(Account, account_id)
            if account is None:
                raise ValueError("app owner account was not found")
            release_service = DifyReleaseService(
                service,
                account,
                now_factory=naive_utc_now,
            )
            draft = release_service.read_draft(app_model=app_model, session=session)
            if draft is None:
                raise ValueError("draft workflow was not found")

            transformed, changed = transform_graph(draft.graph_dict)
            if args.verify:
                state = inspect_release_state(
                    release_service,
                    app_model,
                    session,
                    expected_app_id=APP_ID,
                )
                validate_graph_contract(state.draft_graph)
                validate_graph_contract(state.published_graph)
                print(json.dumps({
                    "status": "verified",
                    "draft_workflow_id": state.draft_workflow_id,
                    "published_workflow_id": state.published_workflow_id,
                    "draft_digest": state.draft_digest,
                    "published_digest": state.published_digest,
                    "node_count": len(state.draft_graph["nodes"]),
                    "edge_count": len(state.draft_graph["edges"]),
                }, separators=(",", ":")))
                return

            if not changed:
                print(json.dumps({"status": "already_current", "draft_workflow_id": draft.id}, separators=(",", ":")))
                return

            result = publish_verified_graph(
                release_service,
                app_model,
                session,
                transformed,
                ExpectedReleaseIdentity(
                    app_id=APP_ID,
                    draft_digest=graph_digest(draft.graph_dict),
                    draft_workflow_id=str(draft.id),
                ),
                ReleaseMark(
                    name="V3.1 deterministic approximate similarity",
                    comment="Adds strict/approximate separation and deterministic metric grading.",
                    backup_name="Backup before V3.1 approximate similarity",
                    backup_comment="Restorable draft snapshot created by Codex before deterministic scorer wiring.",
                ),
            )
            output = {
                **result,
                "status": "updated" if result["status"] == "published" else result["status"],
                "node_count": len(transformed["nodes"]),
                "edge_count": len(transformed["edges"]),
            }
            session.commit()
            print(json.dumps(output, separators=(",", ":")))


if __name__ == "__main__":
    main()
