# Dify Workflow Metadata Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the verified Dify release boundary to promote a workflow graph with its complete non-secret workflow metadata and then use it to publish the E2E-tested candidate to production safely.

**Architecture:** Represent features and workflow variables as an immutable canonical JSON value with a SHA-256 identity. Thread that value through inspection, concurrency guards, backup verification, publish verification, and rollback verification while keeping graph-only callers compatible. The live promotion will use a disposable untracked container script and a canonical production-identity manifest.

**Tech Stack:** Python 3.12, dataclasses, hashlib/json, pytest 9, SQLAlchemy, Dify 1.16 `WorkflowService`, Docker Desktop.

## Global Constraints

- Promote the candidate graph together with normalized features, environment variables, and conversation variables.
- Never print or persist raw workflow variable values, credentials, tokens, database contents, or service exports.
- Preserve existing graph-only callers and existing result fields.
- Require graph and metadata optimistic-concurrency identities before the first production write.
- Verify graph and metadata for backup, active publication, and explicit rollback.
- Never select rollback versions by recency; use only the backup ID returned by this release.
- Do not mutate the candidate application.
- Do not mutate production until focused tests and the complete repository suite pass.
- Treat `rolled_back` or any exception as a failed production release.

---

## File Responsibility Map

- `scripts/update_v31_similarity_workflow.py`: metadata value/digest, release-state identity, graph-plus-metadata orchestration, and Dify adapter.
- `tests/test_v31_workflow_updater.py`: pure orchestration and in-memory Dify adapter regressions.
- `.live-artifacts/promote_candidate_to_production.py`: disposable untracked live orchestration; deleted after verification.
- `.worktree-preservation/multi-model-cv-2b69907-20260823/paper-repro-agent/.live-artifacts/release-baseline-candidate.json`: read-only fixed candidate provenance input.

### Task 1: Add Immutable Workflow Metadata Identity

**Files:**
- Modify: `scripts/update_v31_similarity_workflow.py`
- Test: `tests/test_v31_workflow_updater.py`

**Interfaces:**
- Produces: `WorkflowReleaseMetadata.from_values(*, features: object, environment_variables: object, conversation_variables: object) -> WorkflowReleaseMetadata`.
- Produces: `WorkflowReleaseMetadata.from_workflow(workflow: object) -> WorkflowReleaseMetadata`.
- Produces: read-only properties `features`, `environment_variables`, and `conversation_variables`, each returning a fresh decoded value.
- Produces: `workflow_metadata_digest(metadata: WorkflowReleaseMetadata) -> str`.
- Extends: `ExpectedReleaseIdentity(..., draft_metadata_digest: str | None = None)`.
- Extends: `ReleaseState` with `draft_metadata`, `published_metadata`, `draft_metadata_digest`, and `published_metadata_digest`.

- [ ] **Step 1: Write failing metadata value and inspection tests**

Update the workflow fixture so every fake workflow has the same three metadata attributes as Dify. Add tests equivalent to:

```python
def metadata_fixture(label: str) -> updater.WorkflowReleaseMetadata:
    return updater.WorkflowReleaseMetadata.from_values(
        features={"label": label, "file_upload": {"enabled": True}},
        environment_variables=[{"name": "endpoint", "value": label}],
        conversation_variables=[],
    )


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
    state = updater.inspect_release_state(fake, SimpleNamespace(id=APP_ID), object(), APP_ID)

    assert state.draft_metadata == metadata_fixture("draft")
    assert state.published_metadata == metadata_fixture("published")
    assert state.draft_metadata_digest == updater.workflow_metadata_digest(state.draft_metadata)
    assert state.published_metadata_digest == updater.workflow_metadata_digest(state.published_metadata)
    assert fake.write_calls == []
```

- [ ] **Step 2: Run the two tests and verify RED**

```powershell
python -m pytest tests/test_v31_workflow_updater.py::test_workflow_metadata_digest_is_deterministic_and_value_is_immutable tests/test_v31_workflow_updater.py::test_inspect_release_state_returns_metadata_identities_without_writes -q --basetemp=.tm1
```

Expected: FAIL because `WorkflowReleaseMetadata` is not defined.

- [ ] **Step 3: Implement the canonical metadata value and release-state fields**

Add a frozen value whose representation contains only its digest, not decoded values:

```python
@dataclass(frozen=True, repr=False)
class WorkflowReleaseMetadata:
    _canonical_json: str

    @classmethod
    def from_values(cls, *, features: object, environment_variables: object, conversation_variables: object) -> "WorkflowReleaseMetadata":
        payload = {
            "features": copy.deepcopy(features),
            "environment_variables": copy.deepcopy(environment_variables),
            "conversation_variables": copy.deepcopy(conversation_variables),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return cls(encoded)

    @classmethod
    def from_workflow(cls, workflow: object) -> "WorkflowReleaseMetadata":
        return cls.from_values(
            features=getattr(workflow, "normalized_features_dict"),
            environment_variables=getattr(workflow, "environment_variables"),
            conversation_variables=getattr(workflow, "conversation_variables"),
        )

    def _payload(self) -> dict[str, object]:
        return json.loads(self._canonical_json)

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
    return "sha256:" + hashlib.sha256(metadata._canonical_json.encode("utf-8")).hexdigest()
```

Build draft/published metadata exactly once from the workflows already read by `inspect_release_state`, and add their digests to `ReleaseState`. Add the optional `draft_metadata_digest` field to `ExpectedReleaseIdentity` and its mapping normalizer.

- [ ] **Step 4: Run the focused tests and existing read-only inspection tests**

```powershell
python -m pytest tests/test_v31_workflow_updater.py -k "metadata_digest or inspect_release_state" -q --basetemp=.tm1
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit metadata identity**

```powershell
git add -- scripts/update_v31_similarity_workflow.py tests/test_v31_workflow_updater.py
git commit -m "feat: identify Dify workflow metadata"
```

### Task 2: Verify Metadata Across Publish and Rollback

**Files:**
- Modify: `scripts/update_v31_similarity_workflow.py`
- Test: `tests/test_v31_workflow_updater.py`

**Interfaces:**
- Extends: `ReleaseService.save_draft(..., graph: dict[str, object], metadata: WorkflowReleaseMetadata | None = None) -> object`.
- Extends: `publish_verified_graph(..., *, release_manifest: Mapping[str, object] | None = None, candidate_metadata: WorkflowReleaseMetadata | None = None) -> dict[str, object]`.
- Adds result fields only for metadata-aware calls: `candidate_metadata_digest`, `published_metadata_digest`, `failed_published_metadata_digest`, and `restored_metadata_digest`.

- [ ] **Step 1: Write the failing success and concurrency tests**

```python
def test_publish_verified_graph_publishes_candidate_metadata() -> None:
    current = graph_fixture()
    candidate = graph_fixture("return {'status': 'candidate'}")
    current_metadata = metadata_fixture("current")
    candidate_metadata = metadata_fixture("candidate")
    fake = FakeReleaseService(draft_metadata=current_metadata, published_metadata=current_metadata)
    identity = updater.ExpectedReleaseIdentity(
        APP_ID,
        graph_digest(current),
        DRAFT_ID,
        updater.workflow_metadata_digest(current_metadata),
    )

    result = updater.publish_verified_graph(
        fake,
        SimpleNamespace(id=APP_ID),
        object(),
        candidate,
        identity,
        release_mark(),
        candidate_metadata=candidate_metadata,
    )

    assert result["status"] == "published"
    assert result["candidate_metadata_digest"] == updater.workflow_metadata_digest(candidate_metadata)
    assert result["published_metadata_digest"] == result["candidate_metadata_digest"]
    assert fake.draft_metadata == candidate_metadata


def test_stale_metadata_identity_stops_before_backup() -> None:
    current = graph_fixture()
    live_metadata = metadata_fixture("live")
    fake = FakeReleaseService(draft_metadata=live_metadata)
    identity = updater.ExpectedReleaseIdentity(
        APP_ID,
        graph_digest(current),
        DRAFT_ID,
        updater.workflow_metadata_digest(metadata_fixture("stale")),
    )

    with pytest.raises(ValueError, match="Dify draft metadata digest mismatch"):
        updater.publish_verified_graph(
            fake,
            SimpleNamespace(id=APP_ID),
            object(),
            graph_fixture("return {'status': 'candidate'}"),
            identity,
            release_mark(),
            candidate_metadata=metadata_fixture("candidate"),
        )

    assert fake.write_calls == []
```

- [ ] **Step 2: Run the two tests and verify RED**

```powershell
python -m pytest tests/test_v31_workflow_updater.py::test_publish_verified_graph_publishes_candidate_metadata tests/test_v31_workflow_updater.py::test_stale_metadata_identity_stops_before_backup -q --basetemp=.tm2
```

Expected: FAIL because `candidate_metadata` is not accepted and the metadata guard is absent.

- [ ] **Step 3: Thread effective metadata through the success path**

After live inspection, compute:

```python
metadata_aware = candidate_metadata is not None
effective_candidate_metadata = candidate_metadata or state.draft_metadata
candidate_metadata_digest = workflow_metadata_digest(effective_candidate_metadata)
```

Check `identity.draft_metadata_digest` before backup, verify backup graph and metadata, call:

```python
service.save_draft(
    app_model=app_model,
    session=session,
    graph=candidate,
    metadata=effective_candidate_metadata,
)
```

Require both active digests to match before returning `published`. Add metadata result fields only when `metadata_aware` is true.

- [ ] **Step 4: Run success/concurrency tests and verify GREEN**

Run the Step 2 command again. Expected: `2 passed`.

- [ ] **Step 5: Write failing backup, post-publish, and rollback metadata regressions**

Add three tests using separate `backup_metadata`, `post_publish_metadata`, and `rollback_metadata` controls on `FakeReleaseService`:

```python
def test_post_publish_metadata_mismatch_rolls_back_explicit_backup() -> None:
    current = graph_fixture()
    current_metadata = metadata_fixture("current")
    candidate_metadata = metadata_fixture("candidate")
    fake = FakeReleaseService(
        draft_metadata=current_metadata,
        published_metadata=current_metadata,
        post_publish_metadata=metadata_fixture("unexpected"),
    )
    identity = updater.ExpectedReleaseIdentity(
        APP_ID,
        graph_digest(current),
        DRAFT_ID,
        updater.workflow_metadata_digest(current_metadata),
    )

    result = updater.publish_verified_graph(
        fake,
        SimpleNamespace(id=APP_ID),
        object(),
        graph_fixture("return {'status': 'candidate'}"),
        identity,
        release_mark(),
        candidate_metadata=candidate_metadata,
    )

    assert result["status"] == "rolled_back"
    assert result["failure_operation"] == "verify_published"
    assert result["backup_workflow_id"] == BACKUP_ID
    assert result["restored_metadata_digest"] == updater.workflow_metadata_digest(current_metadata)
    assert fake.rollback_targets == [BACKUP_ID]
    assert "unexpected" not in json.dumps(result)


def test_backup_metadata_mismatch_rolls_back_before_save() -> None:
    current = graph_fixture()
    current_metadata = metadata_fixture("current")
    fake = FakeReleaseService(
        draft_metadata=current_metadata,
        backup_metadata=metadata_fixture("invalid-backup"),
    )
    identity = updater.ExpectedReleaseIdentity(
        APP_ID,
        graph_digest(current),
        DRAFT_ID,
        updater.workflow_metadata_digest(current_metadata),
    )

    result = updater.publish_verified_graph(
        fake,
        SimpleNamespace(id=APP_ID),
        object(),
        graph_fixture("return {'status': 'candidate'}"),
        identity,
        release_mark(),
        candidate_metadata=metadata_fixture("candidate"),
    )

    assert result["status"] == "rolled_back"
    assert result["failure_code"] == "backup_validation_failed"
    assert "save_draft" not in fake.calls
    assert "invalid-backup" not in json.dumps(result)


def test_rollback_metadata_mismatch_is_rejected() -> None:
    current = graph_fixture()
    current_metadata = metadata_fixture("current")
    fake = FakeReleaseService(
        draft_metadata=current_metadata,
        post_publish_metadata=metadata_fixture("unexpected"),
        rollback_metadata=metadata_fixture("incomplete-restore"),
    )
    identity = updater.ExpectedReleaseIdentity(
        APP_ID,
        graph_digest(current),
        DRAFT_ID,
        updater.workflow_metadata_digest(current_metadata),
    )

    with pytest.raises(RuntimeError, match="Dify rollback metadata verification failed") as error:
        updater.publish_verified_graph(
            fake,
            SimpleNamespace(id=APP_ID),
            object(),
            graph_fixture("return {'status': 'candidate'}"),
            identity,
            release_mark(),
            candidate_metadata=metadata_fixture("candidate"),
        )

    assert "incomplete-restore" not in str(error.value)
```

Each test must assert no raw metadata value appears in the result or exception text.

- [ ] **Step 6: Run the three tests and verify RED**

```powershell
python -m pytest tests/test_v31_workflow_updater.py -k "post_publish_metadata_mismatch or backup_metadata_mismatch or rollback_metadata_mismatch" -q --basetemp=.tm2b
```

Expected: three failures caused by missing metadata verification.

- [ ] **Step 7: Extend rollback and failure records**

Pass candidate/active metadata digests into `_rollback_verified(...)`. After `read_published`, construct `WorkflowReleaseMetadata.from_workflow(restored)` and require its digest to equal `state.draft_metadata_digest`. Return only digests, never decoded metadata values.

- [ ] **Step 8: Run all updater tests**

```powershell
python -m pytest tests/test_v31_workflow_updater.py -q --basetemp=.tm2-all
```

Expected: all updater tests pass, including unchanged graph-only tests.

- [ ] **Step 9: Commit orchestration verification**

```powershell
git add -- scripts/update_v31_similarity_workflow.py tests/test_v31_workflow_updater.py
git commit -m "feat: verify complete Dify workflow releases"
```

### Task 3: Pass Candidate Metadata Through the Dify Adapter

**Files:**
- Modify: `scripts/update_v31_similarity_workflow.py`
- Test: `tests/test_v31_workflow_updater.py`

**Interfaces:**
- Consumes: `WorkflowReleaseMetadata | None` in `DifyReleaseService.save_draft(...)`.
- Produces: exact `features`, `environment_variables`, and `conversation_variables` arguments for `WorkflowService.sync_draft_workflow(...)`.

- [ ] **Step 1: Write the failing adapter propagation test**

```python
def test_dify_release_service_saves_explicit_candidate_metadata() -> None:
    workflow_service = FakeDifyWorkflowService()
    adapter = updater.DifyReleaseService(
        workflow_service,
        SimpleNamespace(id="account-id"),
        now_factory=lambda: "release-time",
    )
    app = SimpleNamespace(id=APP_ID, workflow_id=PUBLISHED_ID, updated_by=None, updated_at=None)
    session = FakeSession()
    adapter.read_draft(app_model=app, session=session)
    candidate_metadata = metadata_fixture("candidate")

    draft = adapter.save_draft(
        app_model=app,
        session=session,
        graph=graph_fixture("return {'status': 'candidate'}"),
        metadata=candidate_metadata,
    )

    assert updater.WorkflowReleaseMetadata.from_workflow(draft) == candidate_metadata
```

- [ ] **Step 2: Run the test and verify RED**

```powershell
python -m pytest tests/test_v31_workflow_updater.py::test_dify_release_service_saves_explicit_candidate_metadata -q --basetemp=.tm3
```

Expected: FAIL because the adapter does not accept `metadata`.

- [ ] **Step 3: Implement explicit metadata selection**

In `save_draft`, use the supplied metadata when present; otherwise reconstruct metadata from the cached production draft. Change `_sync_draft` to accept `WorkflowReleaseMetadata` and call:

```python
return self._service.sync_draft_workflow(
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
```

For legacy rollback, construct metadata from the explicit backup workflow. Keep the complete Dify restore API path unchanged.

- [ ] **Step 4: Run adapter and full release tests**

```powershell
python -m pytest tests/test_v31_workflow_updater.py tests/test_workflow_release_integrity.py -q --basetemp=.tm3-all
```

Expected: all focused release tests pass without new warnings.

- [ ] **Step 5: Commit adapter support**

```powershell
git add -- scripts/update_v31_similarity_workflow.py tests/test_v31_workflow_updater.py
git commit -m "feat: sync complete Dify workflow candidates"
```

### Task 4: Verify, Integrate, and Promote the Candidate

**Files:**
- Create temporarily: `.live-artifacts/promote_candidate_to_production.py`
- Delete after verification: `.live-artifacts/promote_candidate_to_production.py`
- Read only: `.worktree-preservation/multi-model-cv-2b69907-20260823/paper-repro-agent/.live-artifacts/release-baseline-candidate.json`

**Interfaces:**
- Candidate app: `397fc669-c89b-4cfa-975d-2807495f7a5b`.
- Candidate published workflow: `d94aec0e-7beb-41e6-8886-ee5e3da7e49b`.
- Candidate graph digest: `sha256:25834f464834cfd45ad6e9b9215416075a1850b576ba42e66decca88c0a9ed80`.
- Production app: `b9a766a0-0ad0-415b-8d42-60459c92bec7`.
- Pre-release production draft: `0fd3ec44-599e-4936-841d-a6df3b8094eb`.
- Pre-release production published workflow: `94e00245-a1f8-48e3-aba6-41549ab75c6e`.

- [ ] **Step 1: Run the complete repository suite**

```powershell
python -m pytest -q --basetemp=.tm-full
```

Expected: all tests pass; only the existing Starlette/httpx deprecation warning is allowed.

- [ ] **Step 2: Merge the implementation branch locally and rerun the complete suite**

Fast-forward `master`, preserve the user's six existing DSL modifications, and rerun the Step 1 command with `--basetemp=.tm-merged`. Delete the short-lived implementation branch only after the merged suite passes.

- [ ] **Step 3: Perform a fresh read-only production preflight**

Inside `docker-api-1`, inspect both apps and require the exact IDs and graph digests listed above. Require candidate draft/published graph and metadata digests to match each other. Require production draft/published graph and metadata digests to match each other. Abort before backup if any identity changed.

- [ ] **Step 4: Build a canonical production-identity manifest**

Use `build_release_manifest(...)` with the preserved candidate's fixed fields:

```python
manifest = build_release_manifest(
    git_commit="58da8fcf1506d066dc8ddad85a6507d207e0edb8",
    worktree_clean=True,
    source_sha256="sha256:4bbaeb0ad6ac977e3b71fdf2afadc01180990f2757662ff927dc10f8bc813dc5",
    dsl_sha256="sha256:f2055cc7d9b06b277c05252dfb3b5489248a11370c0372d56c132f07f35516dc",
    graph_sha256="sha256:25834f464834cfd45ad6e9b9215416075a1850b576ba42e66decca88c0a9ed80",
    workflow_kind="app",
    workflow_version="0.7.0",
    app_id=PRODUCTION_APP_ID,
    draft_workflow_id=production_state.draft_workflow_id,
    published_workflow_id=production_state.published_workflow_id,
)
```

- [ ] **Step 5: Publish through the enhanced boundary**

Pass the raw candidate published `graph_dict`, `WorkflowReleaseMetadata.from_workflow(candidate_published)`, the exact production graph/metadata identity, the canonical manifest, and this mark:

```python
ReleaseMark(
    name="Release baseline 0.7.0 candidate promotion",
    comment="Promotes the independently verified candidate graph and workflow metadata.",
    backup_name="Backup before release baseline 0.7.0 promotion",
    backup_comment="Explicit production rollback snapshot created before candidate promotion.",
)
```

The disposable script must use this transaction and output structure (imports are from the copied updater/integrity modules and the Dify API container):

```python
import json
import sys
from sqlalchemy.orm import sessionmaker
from app import app
from extensions.ext_database import db
from libs.datetime_utils import naive_utc_now
from models import Account
from models.model import App
from services.workflow_service import WorkflowService
from update_v31_similarity_workflow import (
    DifyReleaseService,
    ExpectedReleaseIdentity,
    ReleaseMark,
    WorkflowReleaseMetadata,
    inspect_release_state,
    publish_verified_graph,
    workflow_metadata_digest,
)
from workflow_release_integrity import build_release_manifest, graph_digest

CANDIDATE_APP_ID = "397fc669-c89b-4cfa-975d-2807495f7a5b"
CANDIDATE_DRAFT_ID = "87902a21-48c6-4b69-87b7-88f74c7e2e85"
CANDIDATE_PUBLISHED_ID = "d94aec0e-7beb-41e6-8886-ee5e3da7e49b"
PRODUCTION_APP_ID = "b9a766a0-0ad0-415b-8d42-60459c92bec7"
PRODUCTION_DRAFT_ID = "0fd3ec44-599e-4936-841d-a6df3b8094eb"
PRODUCTION_PUBLISHED_ID = "94e00245-a1f8-48e3-aba6-41549ab75c6e"
CANDIDATE_GRAPH_DIGEST = "sha256:25834f464834cfd45ad6e9b9215416075a1850b576ba42e66decca88c0a9ed80"

def release_service(workflow_service, session, app_model):
    account = session.get(Account, app_model.updated_by or app_model.created_by)
    if account is None:
        raise ValueError(f"app owner not found: {app_model.id}")
    return DifyReleaseService(workflow_service, account, now_factory=naive_utc_now)

with app.app_context():
    maker = sessionmaker(bind=db.engine, expire_on_commit=False)
    workflow_service = WorkflowService(session_maker=maker)
    with maker() as session:
        candidate_app = session.get(App, CANDIDATE_APP_ID)
        production_app = session.get(App, PRODUCTION_APP_ID)
        if candidate_app is None or production_app is None:
            raise ValueError("candidate or production app was not found")
        candidate_service = release_service(workflow_service, session, candidate_app)
        production_service = release_service(workflow_service, session, production_app)
        candidate_state = inspect_release_state(candidate_service, candidate_app, session, CANDIDATE_APP_ID)
        production_state = inspect_release_state(production_service, production_app, session, PRODUCTION_APP_ID)
        if (
            candidate_state.draft_workflow_id != CANDIDATE_DRAFT_ID
            or candidate_state.published_workflow_id != CANDIDATE_PUBLISHED_ID
            or candidate_state.draft_digest != CANDIDATE_GRAPH_DIGEST
            or candidate_state.published_digest != CANDIDATE_GRAPH_DIGEST
            or candidate_state.draft_metadata_digest != candidate_state.published_metadata_digest
        ):
            raise ValueError("candidate release identity changed")
        if (
            production_state.draft_workflow_id != PRODUCTION_DRAFT_ID
            or production_state.published_workflow_id != PRODUCTION_PUBLISHED_ID
            or production_state.draft_digest != production_state.published_digest
            or production_state.draft_metadata_digest != production_state.published_metadata_digest
        ):
            raise ValueError("production release identity changed")
        candidate_published = candidate_service.read_published(app_model=candidate_app, session=session)
        if candidate_published is None or str(candidate_published.id) != CANDIDATE_PUBLISHED_ID:
            raise ValueError("candidate published workflow changed")
        candidate_metadata = WorkflowReleaseMetadata.from_workflow(candidate_published)
        if graph_digest(candidate_published.graph_dict) != CANDIDATE_GRAPH_DIGEST:
            raise ValueError("candidate graph changed")
        manifest = build_release_manifest(
            git_commit="58da8fcf1506d066dc8ddad85a6507d207e0edb8",
            worktree_clean=True,
            source_sha256="sha256:4bbaeb0ad6ac977e3b71fdf2afadc01180990f2757662ff927dc10f8bc813dc5",
            dsl_sha256="sha256:f2055cc7d9b06b277c05252dfb3b5489248a11370c0372d56c132f07f35516dc",
            graph_sha256=CANDIDATE_GRAPH_DIGEST,
            workflow_kind="app",
            workflow_version="0.7.0",
            app_id=PRODUCTION_APP_ID,
            draft_workflow_id=production_state.draft_workflow_id,
            published_workflow_id=production_state.published_workflow_id,
        )
        result = publish_verified_graph(
            production_service,
            production_app,
            session,
            candidate_published.graph_dict,
            ExpectedReleaseIdentity(
                app_id=PRODUCTION_APP_ID,
                draft_digest=production_state.draft_digest,
                draft_workflow_id=production_state.draft_workflow_id,
                draft_metadata_digest=production_state.draft_metadata_digest,
            ),
            ReleaseMark(
                name="Release baseline 0.7.0 candidate promotion",
                comment="Promotes the independently verified candidate graph and workflow metadata.",
                backup_name="Backup before release baseline 0.7.0 promotion",
                backup_comment="Explicit production rollback snapshot created before candidate promotion.",
            ),
            release_manifest=manifest,
            candidate_metadata=candidate_metadata,
        )
        session.commit()
        safe_result = {
            key: value for key, value in result.items()
            if key not in {"release_manifest"}
        }
        print(json.dumps(safe_result, sort_keys=True, separators=(",", ":")))
        if result["status"] != "published":
            raise SystemExit(2)
```

Commit the Dify SQLAlchemy session once after `publish_verified_graph(...)` returns. Exit nonzero if status is not `published`.

- [ ] **Step 6: Independently verify the active production release**

Open a new database session and require:

- production active graph digest equals `sha256:25834f464834cfd45ad6e9b9215416075a1850b576ba42e66decca88c0a9ed80`;
- production active metadata digest equals the candidate metadata digest captured in Step 3;
- production draft and published workflow IDs equal the IDs returned by the release result;
- the result contains a canonical explicit backup workflow ID and no rollback workflow ID;
- candidate app IDs and digests are unchanged.

Print only IDs, digests, status, and drift codes.

- [ ] **Step 7: Remove disposable scripts and report exact release evidence**

Delete the exact container `/tmp` directory and local `.live-artifacts/promote_candidate_to_production.py`. Preserve the canonical release result in the existing ignored preservation area only if it contains no raw graph or metadata values. Report the production app ID, prior workflow ID, backup ID, new published workflow ID, graph digest, metadata digest, and verification status.
