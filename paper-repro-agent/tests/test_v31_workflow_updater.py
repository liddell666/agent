from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from scripts import update_v31_similarity_workflow as updater
from scripts.workflow_release_integrity import (
    build_release_manifest,
    canonical_graph,
    graph_digest,
)


APP_ID = updater.APP_ID
DRAFT_ID = "0fd3ec44-599e-4936-841d-a6df3b8094eb"
PUBLISHED_ID = "94e00245-a1f8-48e3-aba6-41549ab75c6e"
BACKUP_ID = "53a5a8ec-6639-4080-8b15-fc2fc93116a5"
CANDIDATE_ID = "bc0e7389-3221-4b97-a0f0-edc36d31acc5"
ROLLBACK_ID = "d0da6f98-27ae-4691-80ae-d52dfcb33f8d"
UNEXPECTED_ACTIVE_ID = "0e41a28b-bfa2-41f3-853a-1bedf70ff53b"


def graph_fixture(code: str = "return {'status': 'current'}") -> dict[str, object]:
    return {
        "viewport": {"x": 10, "y": 20, "zoom": 1.5},
        "nodes": [
            {
                "id": "start",
                "position": {"x": 0, "y": 100},
                "data": {"type": "start", "code": code},
            },
            {
                "id": "end",
                "position": {"x": 400, "y": 100},
                "data": {"type": "end"},
            },
        ],
        "edges": [
            {
                "id": "start-end",
                "source": "start",
                "target": "end",
                "sourceHandle": "source",
                "targetHandle": "target",
            }
        ],
    }


def metadata_fixture(label: str) -> updater.WorkflowReleaseMetadata:
    return updater.WorkflowReleaseMetadata.from_values(
        features={"label": label, "file_upload": {"enabled": True}},
        environment_variables=[{"name": "endpoint", "value": label}],
        conversation_variables=[],
    )


def workflow(
    workflow_id: str,
    graph: dict[str, object],
    metadata: updater.WorkflowReleaseMetadata | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=workflow_id,
        graph_dict=deepcopy(graph),
        normalized_features_dict=deepcopy(
            metadata.features if metadata is not None else {"features": "current"}
        ),
        environment_variables=deepcopy(
            metadata.environment_variables if metadata is not None else ["environment"]
        ),
        conversation_variables=deepcopy(
            metadata.conversation_variables if metadata is not None else ["conversation"]
        ),
    )


class FakeReleaseService:
    def __init__(
        self,
        *,
        draft_graph: dict[str, object] | None = None,
        published_graph: dict[str, object] | None = None,
        post_publish_graph: dict[str, object] | None = None,
        post_publish_id: str = CANDIDATE_ID,
        fail_once_on: str | None = None,
        invalid_backup_graph: bool = False,
        backup_id: object = BACKUP_ID,
        draft_metadata: updater.WorkflowReleaseMetadata | None = None,
        published_metadata: updater.WorkflowReleaseMetadata | None = None,
    ) -> None:
        current = draft_graph or graph_fixture()
        published = published_graph or current
        self.draft = workflow(DRAFT_ID, current, draft_metadata)
        self.published = workflow(
            PUBLISHED_ID,
            published,
            published_metadata or draft_metadata,
        )
        self.post_publish_graph = deepcopy(post_publish_graph)
        self.post_publish_id = post_publish_id
        self.fail_once_on = fail_once_on
        self.invalid_backup_graph = invalid_backup_graph
        self.backup_id = backup_id
        self.backup_graph: dict[str, object] | None = None
        self.calls: list[str] = []
        self.write_calls: list[str] = []
        self.rollback_targets: list[str] = []

    def read_draft(self, *, app_model: object, session: object) -> SimpleNamespace:
        self.calls.append("read_draft")
        return self.draft

    def read_published(self, *, app_model: object, session: object) -> SimpleNamespace:
        self.calls.append("read_published")
        self._maybe_fail("read_published")
        return self.published

    def create_backup(
        self, *, app_model: object, session: object, mark: object
    ) -> SimpleNamespace:
        self.calls.append("create_backup")
        self.write_calls.append("create_backup")
        self.backup_graph = deepcopy(self.draft.graph_dict)
        result_graph = {"nodes": []} if self.invalid_backup_graph else self.backup_graph
        return SimpleNamespace(id=self.backup_id, graph_dict=deepcopy(result_graph))

    def save_draft(
        self,
        *,
        app_model: object,
        session: object,
        graph: dict[str, object],
    ) -> SimpleNamespace:
        self.calls.append("save_draft")
        self.write_calls.append("save_draft")
        self._maybe_fail("save_draft")
        self.draft = workflow(DRAFT_ID, graph)
        return self.draft

    def publish(
        self, *, app_model: object, session: object, mark: object
    ) -> SimpleNamespace:
        self.calls.append("publish")
        self.write_calls.append("publish")
        self._maybe_fail("publish")
        graph = self.post_publish_graph or self.draft.graph_dict
        result = workflow(CANDIDATE_ID, self.draft.graph_dict)
        self.published = workflow(self.post_publish_id, graph)
        return result

    def rollback(
        self,
        *,
        app_model: object,
        session: object,
        workflow_id: str,
        mark: object,
    ) -> SimpleNamespace:
        self.calls.append("rollback")
        self.write_calls.append("rollback")
        self.rollback_targets.append(workflow_id)
        assert workflow_id == BACKUP_ID
        assert self.backup_graph is not None
        self.published = workflow(ROLLBACK_ID, self.backup_graph)
        return self.published

    def _maybe_fail(self, operation: str) -> None:
        if operation == "read_published" and "create_backup" not in self.calls:
            return
        if self.fail_once_on == operation:
            self.fail_once_on = None
            raise RuntimeError(f"simulated {operation} failure")


def test_workflow_metadata_digest_is_deterministic_and_value_is_immutable() -> None:
    source = {
        "features": {"b": 2, "a": 1},
        "environment_variables": [{"name": "x", "value": "secret-value"}],
        "conversation_variables": [],
    }
    metadata = updater.WorkflowReleaseMetadata.from_values(**source)
    expected = updater.WorkflowReleaseMetadata.from_values(
        features={"a": 1, "b": 2},
        environment_variables=[{"value": "secret-value", "name": "x"}],
        conversation_variables=[],
    )
    source["features"]["a"] = 99
    returned = metadata.features
    returned["a"] = 88

    assert updater.workflow_metadata_digest(metadata) == updater.workflow_metadata_digest(expected)
    assert metadata.features == {"a": 1, "b": 2}
    assert "secret-value" not in repr(metadata)


def test_inspect_release_state_returns_metadata_identities_without_writes() -> None:
    fake = FakeReleaseService(
        draft_metadata=metadata_fixture("draft"),
        published_metadata=metadata_fixture("published"),
    )

    state = updater.inspect_release_state(
        fake,
        SimpleNamespace(id=APP_ID),
        object(),
        APP_ID,
    )

    assert state.draft_metadata == metadata_fixture("draft")
    assert state.published_metadata == metadata_fixture("published")
    assert state.draft_metadata_digest == updater.workflow_metadata_digest(
        state.draft_metadata
    )
    assert state.published_metadata_digest == updater.workflow_metadata_digest(
        state.published_metadata
    )
    assert fake.write_calls == []


def test_inspect_release_state_returns_canonical_read_only_snapshot() -> None:
    original = graph_fixture()
    fake = FakeReleaseService(draft_graph=original, published_graph=deepcopy(original))
    app = SimpleNamespace(id=APP_ID)

    state = updater.inspect_release_state(fake, app, object(), expected_app_id=APP_ID)

    assert state.app_id == APP_ID
    assert state.draft_workflow_id == DRAFT_ID
    assert state.published_workflow_id == PUBLISHED_ID
    assert state.draft_graph == canonical_graph(original)
    assert state.published_graph == canonical_graph(original)
    assert state.draft_digest == graph_digest(original)
    assert state.published_digest == graph_digest(original)
    assert fake.calls == ["read_draft", "read_published"]
    assert fake.write_calls == []
    assert original == graph_fixture()


def test_inspect_release_state_rejects_identity_before_service_calls() -> None:
    fake = FakeReleaseService()
    other_app = SimpleNamespace(id="53a5a8ec-6639-4080-8b15-fc2fc93116a5")

    with pytest.raises(ValueError, match="Dify application identity mismatch"):
        updater.inspect_release_state(fake, other_app, object(), expected_app_id=APP_ID)

    assert fake.calls == []
    assert fake.write_calls == []


def expected_identity(graph: dict[str, object]) -> dict[str, str]:
    return {"app_id": APP_ID, "draft_digest": graph_digest(graph)}


def release_mark() -> dict[str, str]:
    return {
        "name": "Candidate release",
        "comment": "Verified candidate graph",
        "backup_name": "Backup before candidate release",
        "backup_comment": "Explicit rollback snapshot",
    }


def release_manifest(candidate: dict[str, object]) -> dict[str, object]:
    return build_release_manifest(
        git_commit="abc123",
        worktree_clean=True,
        source_sha256="sha256:" + "a" * 64,
        dsl_sha256="sha256:" + "b" * 64,
        graph_sha256=graph_digest(candidate),
        workflow_kind="paper-comparison-v31",
        workflow_version="3.1",
        app_id=APP_ID,
        draft_workflow_id=DRAFT_ID,
        published_workflow_id=PUBLISHED_ID,
    )


def test_publish_verified_graph_orders_backup_publish_and_post_verify() -> None:
    current = graph_fixture()
    candidate = graph_fixture("return {'status': 'candidate'}")
    candidate_before = deepcopy(candidate)
    fake = FakeReleaseService(draft_graph=current, published_graph=deepcopy(current))

    result = updater.publish_verified_graph(
        fake,
        SimpleNamespace(id=APP_ID),
        object(),
        candidate,
        expected_identity(current),
        release_mark(),
    )

    assert result["status"] == "published"
    assert result["backup_workflow_id"] == BACKUP_ID
    assert result["published_workflow_id"] == CANDIDATE_ID
    assert result["candidate_digest"] == graph_digest(candidate)
    assert result["published_digest"] == graph_digest(candidate)
    assert result["rollback_workflow_id"] is None
    assert fake.calls == [
        "read_draft",
        "read_published",
        "create_backup",
        "save_draft",
        "publish",
        "read_published",
    ]
    assert fake.draft.graph_dict == candidate_before
    assert candidate == candidate_before


def test_publish_verified_graph_validates_and_propagates_task5_manifest() -> None:
    current = graph_fixture()
    candidate = graph_fixture("return {'status': 'candidate'}")
    manifest = release_manifest(candidate)
    manifest_before = deepcopy(manifest)
    fake = FakeReleaseService(draft_graph=current)

    result = updater.publish_verified_graph(
        fake,
        SimpleNamespace(id=APP_ID),
        object(),
        candidate,
        expected_identity(current),
        release_mark(),
        release_manifest=manifest,
    )

    assert result["release_manifest"] == manifest_before
    assert result["release_manifest"] is not manifest
    assert manifest == manifest_before


def test_release_manifest_live_identity_mismatch_stops_before_writes() -> None:
    current = graph_fixture()
    candidate = graph_fixture("return {'status': 'candidate'}")
    manifest = release_manifest(candidate)
    manifest["dify"]["draft_workflow_id"] = ROLLBACK_ID  # type: ignore[index]
    fake = FakeReleaseService(draft_graph=current)

    with pytest.raises(ValueError, match="release_manifest draft workflow identity mismatch"):
        updater.publish_verified_graph(
            fake,
            SimpleNamespace(id=APP_ID),
            object(),
            candidate,
            expected_identity(current),
            release_mark(),
            release_manifest=manifest,
        )

    assert fake.calls == ["read_draft", "read_published"]
    assert fake.write_calls == []


def test_backup_validation_failure_uses_available_explicit_id_for_rollback() -> None:
    current = graph_fixture()
    fake = FakeReleaseService(draft_graph=current, invalid_backup_graph=True)

    result = updater.publish_verified_graph(
        fake,
        SimpleNamespace(id=APP_ID),
        object(),
        graph_fixture("return {'status': 'candidate'}"),
        expected_identity(current),
        release_mark(),
    )

    assert result["status"] == "rolled_back"
    assert result["failure_code"] == "backup_validation_failed"
    assert result["failure_operation"] == "validate_backup"
    assert result["backup_workflow_id"] == BACKUP_ID
    assert result["rollback_workflow_id"] == ROLLBACK_ID
    assert fake.rollback_targets == [BACKUP_ID]
    assert "save_draft" not in fake.calls


def test_backup_validation_without_valid_id_reports_unrecoverable() -> None:
    current = graph_fixture()
    fake = FakeReleaseService(draft_graph=current, backup_id="not-a-uuid")

    with pytest.raises(
        RuntimeError,
        match="unrecoverable Dify backup validation failure.*no valid explicit rollback ID",
    ):
        updater.publish_verified_graph(
            fake,
            SimpleNamespace(id=APP_ID),
            object(),
            graph_fixture("return {'status': 'candidate'}"),
            expected_identity(current),
            release_mark(),
        )

    assert fake.rollback_targets == []
    assert "save_draft" not in fake.calls


def test_publish_verified_graph_rejects_stale_draft_before_writes() -> None:
    current = graph_fixture()
    fake = FakeReleaseService(draft_graph=current)
    stale = expected_identity(graph_fixture("return {'status': 'stale'}"))

    with pytest.raises(ValueError, match="Dify draft digest mismatch"):
        updater.publish_verified_graph(
            fake,
            SimpleNamespace(id=APP_ID),
            object(),
            graph_fixture("return {'status': 'candidate'}"),
            stale,
            release_mark(),
        )

    assert fake.calls == ["read_draft", "read_published"]
    assert fake.write_calls == []


def test_publish_verified_graph_rolls_back_explicit_backup_on_digest_mismatch() -> None:
    current = graph_fixture()
    candidate = graph_fixture("return {'status': 'candidate'}")
    unexpected = graph_fixture("return {'status': 'unexpected'}")
    fake = FakeReleaseService(
        draft_graph=current,
        published_graph=deepcopy(current),
        post_publish_graph=unexpected,
    )

    result = updater.publish_verified_graph(
        fake,
        SimpleNamespace(id=APP_ID),
        object(),
        candidate,
        expected_identity(current),
        release_mark(),
    )

    assert result["status"] == "rolled_back"
    assert result["failed_published_workflow_id"] == CANDIDATE_ID
    assert result["backup_workflow_id"] == BACKUP_ID
    assert result["rollback_workflow_id"] == ROLLBACK_ID
    assert result["failed_published_digest"] == graph_digest(unexpected)
    assert result["restored_digest"] == graph_digest(current)
    assert fake.rollback_targets == [BACKUP_ID]
    assert fake.calls == [
        "read_draft",
        "read_published",
        "create_backup",
        "save_draft",
        "publish",
        "read_published",
        "rollback",
        "read_published",
    ]


def test_publish_verified_graph_records_actual_unexpected_active_workflow_id() -> None:
    current = graph_fixture()
    candidate = graph_fixture("return {'status': 'candidate'}")
    fake = FakeReleaseService(
        draft_graph=current,
        published_graph=deepcopy(current),
        post_publish_graph=deepcopy(candidate),
        post_publish_id=UNEXPECTED_ACTIVE_ID,
    )

    result = updater.publish_verified_graph(
        fake,
        SimpleNamespace(id=APP_ID),
        object(),
        candidate,
        expected_identity(current),
        release_mark(),
    )

    assert result["failed_published_workflow_id"] == UNEXPECTED_ACTIVE_ID
    assert result["requested_published_workflow_id"] == CANDIDATE_ID
    assert result["failed_published_digest"] == graph_digest(candidate)


@pytest.mark.parametrize("operation", ["save_draft", "publish", "read_published"])
def test_publish_verified_graph_rolls_back_exceptions_after_backup(
    operation: str,
) -> None:
    current = graph_fixture()
    candidate = graph_fixture("return {'status': 'candidate'}")
    fake = FakeReleaseService(draft_graph=current, fail_once_on=operation)

    result = updater.publish_verified_graph(
        fake,
        SimpleNamespace(id=APP_ID),
        object(),
        candidate,
        expected_identity(current),
        release_mark(),
    )

    assert result["status"] == "rolled_back"
    assert result["failure_code"] == "release_operation_failed"
    assert result["failure_operation"] == operation
    assert result["backup_workflow_id"] == BACKUP_ID
    assert result["rollback_workflow_id"] == ROLLBACK_ID
    assert result["restored_digest"] == graph_digest(current)
    assert fake.rollback_targets == [BACKUP_ID]


class FakeDifyWorkflowService:
    def __init__(self) -> None:
        graph = graph_fixture()
        self.draft = SimpleNamespace(
            id=DRAFT_ID,
            graph_dict=deepcopy(graph),
            normalized_features_dict={"features": "current"},
            unique_hash="draft-hash",
            environment_variables=["environment"],
            conversation_variables=["conversation"],
            rag_pipeline_variables=["rollback-rag-variable"],
            agent_bindings=["rollback-agent-binding"],
        )
        self.versions = {
            PUBLISHED_ID: workflow(PUBLISHED_ID, graph),
            BACKUP_ID: workflow(BACKUP_ID, graph),
        }
        self.publish_ids = iter((BACKUP_ID, CANDIDATE_ID, ROLLBACK_ID))
        self.calls: list[str] = []

    def get_draft_workflow(self, *, app_model: object, session: object) -> object:
        self.calls.append("get_draft_workflow")
        return self.draft

    def get_published_workflow(self, *, app_model: object, session: object) -> object:
        self.calls.append("get_published_workflow")
        return self.versions[getattr(app_model, "workflow_id")]

    def get_published_workflow_by_id(
        self, *, app_model: object, workflow_id: str, session: object
    ) -> object:
        self.calls.append(f"get_published_workflow_by_id:{workflow_id}")
        return self.versions.get(workflow_id)

    def sync_draft_workflow(self, **kwargs: object) -> object:
        self.calls.append("sync_draft_workflow")
        self.draft = SimpleNamespace(
            id=DRAFT_ID,
            graph_dict=deepcopy(kwargs["graph"]),
            normalized_features_dict=deepcopy(kwargs["features"]),
            unique_hash="next-hash",
            environment_variables=deepcopy(kwargs["environment_variables"]),
            conversation_variables=deepcopy(kwargs["conversation_variables"]),
        )
        return self.draft

    def publish_workflow(self, **kwargs: object) -> object:
        self.calls.append(f"publish_workflow:{kwargs['marked_name']}")
        workflow_id = next(self.publish_ids)
        published = SimpleNamespace(
            id=workflow_id,
            graph_dict=deepcopy(self.draft.graph_dict),
            normalized_features_dict=deepcopy(self.draft.normalized_features_dict),
            environment_variables=deepcopy(self.draft.environment_variables),
            conversation_variables=deepcopy(self.draft.conversation_variables),
            rag_pipeline_variables=deepcopy(
                getattr(self.draft, "rag_pipeline_variables", [])
            ),
            agent_bindings=deepcopy(getattr(self.draft, "agent_bindings", [])),
        )
        self.versions[workflow_id] = published
        return published


class FakeSession:
    def __init__(self) -> None:
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1


class CompleteRestoreDifyWorkflowService(FakeDifyWorkflowService):
    def __init__(self) -> None:
        super().__init__()
        self.restore_targets: list[str] = []
        self.active_id_during_restore: str | None = None

    def restore_published_workflow_to_draft(
        self,
        *,
        app_model: object,
        workflow_id: str,
        account: object,
        session: object,
    ) -> object:
        self.calls.append(f"restore_published_workflow_to_draft:{workflow_id}")
        self.restore_targets.append(workflow_id)
        self.active_id_during_restore = getattr(app_model, "workflow_id")
        source = self.versions[workflow_id]
        self.draft = SimpleNamespace(
            id=DRAFT_ID,
            graph_dict=deepcopy(source.graph_dict),
            normalized_features_dict=deepcopy(source.normalized_features_dict),
            unique_hash="restored-hash",
            environment_variables=deepcopy(source.environment_variables),
            conversation_variables=deepcopy(source.conversation_variables),
            rag_pipeline_variables=deepcopy(source.rag_pipeline_variables),
            agent_bindings=deepcopy(source.agent_bindings),
        )
        return self.draft


def test_dify_release_service_preserves_existing_service_contracts() -> None:
    workflow_service = FakeDifyWorkflowService()
    account = SimpleNamespace(id="account-id")
    app = SimpleNamespace(id=APP_ID, workflow_id=PUBLISHED_ID, updated_by=None, updated_at=None)
    session = FakeSession()
    adapter = updater.DifyReleaseService(
        workflow_service,
        account,
        now_factory=lambda: "release-time",
    )

    assert adapter.read_draft(app_model=app, session=session).id == DRAFT_ID
    assert adapter.read_published(app_model=app, session=session).id == PUBLISHED_ID
    assert adapter.create_backup(
        app_model=app,
        session=session,
        mark=updater.ReleaseMark("Backup", "backup comment"),
    ).id == BACKUP_ID
    adapter.save_draft(
        app_model=app,
        session=session,
        graph=graph_fixture("return {'status': 'candidate'}"),
    )
    assert adapter.publish(
        app_model=app,
        session=session,
        mark=updater.ReleaseMark("Candidate", "candidate comment"),
    ).id == CANDIDATE_ID
    assert app.workflow_id == CANDIDATE_ID
    assert adapter.rollback(
        app_model=app,
        session=session,
        workflow_id=BACKUP_ID,
        mark=updater.ReleaseMark("Rollback", "rollback comment"),
    ).id == ROLLBACK_ID

    assert app.workflow_id == ROLLBACK_ID
    assert app.updated_by == account.id
    assert app.updated_at == "release-time"
    assert workflow_service.draft.graph_dict == graph_fixture()
    assert session.flush_count == 5
    assert f"get_published_workflow_by_id:{BACKUP_ID}" in workflow_service.calls


def test_dify_release_service_uses_complete_restore_api_when_available() -> None:
    workflow_service = CompleteRestoreDifyWorkflowService()
    account = SimpleNamespace(id="account-id")
    app = SimpleNamespace(id=APP_ID, workflow_id=PUBLISHED_ID, updated_by=None, updated_at=None)
    session = FakeSession()
    adapter = updater.DifyReleaseService(
        workflow_service,
        account,
        now_factory=lambda: "release-time",
    )

    adapter.read_draft(app_model=app, session=session)
    backup = adapter.create_backup(
        app_model=app,
        session=session,
        mark=updater.ReleaseMark("Backup"),
    )
    adapter.save_draft(
        app_model=app,
        session=session,
        graph=graph_fixture("return {'status': 'candidate'}"),
    )
    workflow_service.draft.rag_pipeline_variables = ["candidate-rag-variable"]
    workflow_service.draft.agent_bindings = ["candidate-agent-binding"]
    adapter.publish(
        app_model=app,
        session=session,
        mark=updater.ReleaseMark("Candidate"),
    )

    adapter.rollback(
        app_model=app,
        session=session,
        workflow_id=backup.id,
        mark=updater.ReleaseMark("Rollback"),
    )

    assert workflow_service.restore_targets == [BACKUP_ID]
    assert workflow_service.active_id_during_restore == BACKUP_ID
    assert workflow_service.draft.rag_pipeline_variables == ["rollback-rag-variable"]
    assert workflow_service.draft.agent_bindings == ["rollback-agent-binding"]


def v31_graph_fixture() -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": "start",
                "position": {"x": 0, "y": 0},
                "positionAbsolute": {"x": 0, "y": 0},
                "data": {
                    "variables": [
                        {"variable": "close_threshold"},
                        {"variable": "partial_threshold"},
                    ]
                },
            },
            {
                "id": updater.GATE_ID,
                "position": {"x": 8800, "y": 0},
                "positionAbsolute": {"x": 8800, "y": 0},
                "data": {"type": "if-else"},
            },
            {
                "id": "comparison_parse",
                "position": {"x": 8500, "y": 0},
                "positionAbsolute": {"x": 8500, "y": 0},
                "data": {"type": "code"},
            },
            {
                "id": updater.FORMATTER_ID,
                "position": {"x": 9212, "y": 0},
                "positionAbsolute": {"x": 9212, "y": 0},
                "data": {
                    "type": "code",
                    "code": "legacy formatter",
                    "variables": [],
                },
            },
        ],
        "edges": [
            {
                "id": "legacy-direct",
                "source": updater.GATE_ID,
                "target": updater.FORMATTER_ID,
            }
        ],
    }


def test_transform_graph_is_idempotent_and_does_not_mutate_input() -> None:
    original = v31_graph_fixture()
    original_before = deepcopy(original)

    once, first_changed = updater.transform_graph(original)
    twice, second_changed = updater.transform_graph(once)

    assert first_changed is True
    assert second_changed is False
    assert twice == once
    assert original == original_before
    updater.validate_graph_contract(twice)


@pytest.mark.parametrize(
    "close_threshold,partial_threshold",
    [(0, 0.15), (0.2, 0.2), (0.3, 0.2), (0.1, 1.1), ("bad", 0.2)],
)
def test_invalid_similarity_thresholds_return_structured_error(
    close_threshold: object, partial_threshold: object
) -> None:
    namespace: dict[str, object] = {}
    exec(updater.SCORER_CODE, namespace)

    result = namespace["main"]("{}", close_threshold, partial_threshold)  # type: ignore[operator]
    assessment = json.loads(result["assessment_json"])

    assert assessment["strict_status"] == "not_comparable"
    assert assessment["approximate_status"] == "insufficient_metrics"
    assert assessment["items"] == []
    assert assessment["errors"] == [
        {
            "code": "invalid_similarity_thresholds",
            "message": "Thresholds must satisfy 0 < close < partial <= 1.",
        }
    ]
