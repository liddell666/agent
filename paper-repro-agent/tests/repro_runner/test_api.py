import asyncio
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import hmac
import json
import logging
import shutil
from threading import Barrier, Event

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from dify.code.experiment_workflow import (
    normalize_protocol_confirmation,
    prepare_protocol_artifacts,
)
from repro_runner import api
from repro_runner.config import Settings, get_settings


def _csv(rows: int = 40) -> bytes:
    data = ["x1,x2,Y_cls"]
    for index in range(rows):
        data.append(f"{index % 2},{(index // 2) % 2},{index % 2}")
    return ("\n".join(data) + "\n").encode()


def _dossier() -> bytes:
    return json.dumps(
        {
            "title": "Minimal Paper",
            "research_problem": "Binary classification.",
            "task_type": "classification",
            "datasets": [],
            "methods": [],
            "metrics": [
                {
                    "name": "AUC",
                    "reported_value": "91%",
                    "evidence": [
                        {
                            "page": 1,
                            "source_text": "AUC is 91%.",
                            "source": "paper",
                        }
                    ],
                }
            ],
            "gaps": [],
        }
    ).encode()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        storage_dir=tmp_path / "experiments",
        max_upload_mb=1,
        protocol_secret="test-secret",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        if hasattr(test_client.app.state, "protocol_draft_store"):
            test_client.app.state.protocol_draft_store.clock = lambda: 1_000
        yield test_client
    api.app.dependency_overrides.clear()


@pytest.fixture
def client_with_raised_dossier_limits(tmp_path) -> TestClient:
    settings = Settings(
        storage_dir=tmp_path,
        max_upload_mb=1,
        max_dossier_mb=20,
        max_metric_overrides_kb=256,
    )
    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()


def _protocol_token(secret, draft_id, manifest_id, dataset_id, exp):
    payload = {
        "v": 1,
        "exp": exp,
        "ready": True,
        "draft_id": draft_id,
        "manifest": {"manifest_id": manifest_id, "dataset_id": dataset_id},
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"pt1.{encoded}.{signature}"


def _job_count(client: TestClient) -> int:
    store = client.app.state.job_store
    with store._connect() as connection:
        row = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()
    return int(row[0])


def test_healthz_is_public(client: TestClient):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_protocol_draft_post_get_round_trip(client: TestClient, settings: Settings):
    token = _protocol_token(
        settings.protocol_secret,
        "draft-apiaaaaaa",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        2_000,
    )
    response = client.post(
        "/v1/protocol-drafts",
        data={
            "draft_id": "draft-apiaaaaaa",
            "protocol_token": token,
            "dossier_json": json.dumps({"title": "Paper", "metrics": []}),
        },
    )

    assert response.status_code == 200
    assert response.json()["draft_id"] == "draft-apiaaaaaa"
    fetched = client.get(
        "/v1/protocol-drafts/draft-apiaaaaaa",
        headers={"X-Protocol-Token": token},
    )
    assert fetched.status_code == 200
    assert fetched.json()["dossier"]["title"] == "Paper"
    assert fetched.json()["manifest_id"] == "sha256:" + "1" * 64


def test_generated_protocol_token_flows_through_drafts_and_rejects_unsafe_job_paths(
    client: TestClient, settings: Settings
):
    csv_content = _csv()
    dataset_id = "sha256:" + hashlib.sha256(csv_content).hexdigest()
    dossier_json = json.dumps(
        {
            "title": "Protocol Paper",
            "metrics": [
                {
                    "name": "AUC",
                    "normalized_name": "roc_auc",
                    "supported": True,
                    "ambiguous": False,
                    "reported_value": 0.91,
                }
            ],
        },
        ensure_ascii=False,
    )
    diagnosis_json = json.dumps(
        {
            "valid": True,
            "dataset": {
                "dataset_id": dataset_id,
                "target": "Y_cls",
                "rows": 40,
                "effective_rows": 40,
                "features": 2,
                "missing_values": 0,
                "duplicate_rows": 0,
                "column_names": ["x1", "x2", "Y_cls"],
                "class_counts": {"0": 20, "1": 20},
                "class_ratios": {"0": 0.5, "1": 0.5},
            },
            "recommended_options": {
                "target_column": "Y_cls",
                "feature_columns": ["x1", "x2"],
                "missing_policy": "reject",
                "sampling_strategy": "original",
                "comparison_mode": "paper_comparable",
                "test_size": 0.2,
                "random_state": 42,
                "cv_folds": 3,
                "optimization_metric": "roc_auc",
                "threshold": 0.5,
            },
            "columns": [],
            "target_candidates": ["Y_cls"],
            "risk_flags": [],
            "warnings": [],
        },
        ensure_ascii=False,
    )
    prepared = prepare_protocol_artifacts(
        dossier_json,
        diagnosis_json,
        target_column="Y_cls",
        secret=settings.protocol_secret,
        now=1_000,
        ttl_seconds=900,
    )
    assert prepared["protocol_ready"] is True

    created = client.post(
        "/v1/protocol-drafts",
        data={
            "draft_id": prepared["draft_id"],
            "protocol_token": prepared["protocol_token"],
            "dossier_json": dossier_json,
        },
    )
    assert created.status_code == 200

    confirmed = normalize_protocol_confirmation(
        prepared["protocol_token"],
        True,
        secret=settings.protocol_secret,
        now=1_000,
    )
    assert confirmed["protocol_ok"] is True

    fetched = client.get(
        f"/v1/protocol-drafts/{confirmed['draft_id']}",
        headers={"X-Protocol-Token": prepared["protocol_token"]},
    )
    assert fetched.status_code == 200
    assert fetched.json()["dossier"] == json.loads(dossier_json)

    changed_csv = csv_content.replace(b"0,0,0\n", b"9,0,0\n", 1)
    mismatch = client.post(
        "/v1/jobs",
        data={"manifest_json": confirmed["manifest_json"]},
        files={"file": ("changed.csv", changed_csv, "text/csv")},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "manifest_dataset_mismatch"
    assert _job_count(client) == 0

    unconfirmed = normalize_protocol_confirmation(
        prepared["protocol_token"],
        False,
        secret=settings.protocol_secret,
        now=1_000,
    )
    assert unconfirmed["protocol_ok"] is False
    assert json.loads(unconfirmed["protocol_errors"])[0]["code"] == "protocol_not_confirmed"
    assert _job_count(client) == 0

    client.app.state.protocol_draft_store.clock = lambda: int(
        prepared["draft_expires_at"]
    )
    expired = client.get(
        f"/v1/protocol-drafts/{confirmed['draft_id']}",
        headers={"X-Protocol-Token": prepared["protocol_token"]},
    )
    assert expired.status_code == 410
    assert expired.json()["detail"]["code"] == "protocol_draft_expired"
    assert _job_count(client) == 0

    shutil.rmtree(
        settings.storage_dir / "protocol-drafts" / confirmed["draft_id"],
    )
    missing = client.get(
        f"/v1/protocol-drafts/{confirmed['draft_id']}",
        headers={"X-Protocol-Token": prepared["protocol_token"]},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "protocol_draft_not_found"
    assert _job_count(client) == 0


@pytest.mark.parametrize("case", ["missing", "expired", "tampered", "wrong_draft"])
def test_protocol_draft_rejects_invalid_access(
    client: TestClient, settings: Settings, case: str
):
    valid = _protocol_token(
        settings.protocol_secret,
        "draft-apiaaaaaa",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        2_000,
    )
    client.post(
        "/v1/protocol-drafts",
        data={
            "draft_id": "draft-apiaaaaaa",
            "protocol_token": valid,
            "dossier_json": '{"title":"Paper"}',
        },
    )
    other = _protocol_token(
        settings.protocol_secret,
        "draft-otherxxx",
        "sha256:" + "3" * 64,
        "sha256:" + "4" * 64,
        2_000,
    )
    client.post(
        "/v1/protocol-drafts",
        data={
            "draft_id": "draft-otherxxx",
            "protocol_token": other,
            "dossier_json": '{"title":"Other"}',
        },
    )
    if case == "missing":
        response = client.get(
            "/v1/protocol-drafts/draft-missingx",
            headers={
                "X-Protocol-Token": _protocol_token(
                    settings.protocol_secret,
                    "draft-missingx",
                    "sha256:" + "1" * 64,
                    "sha256:" + "2" * 64,
                    2_000,
                )
            },
        )
        assert response.json()["detail"]["code"] == "protocol_draft_not_found"
    elif case == "expired":
        response = client.get(
            "/v1/protocol-drafts/draft-apiaaaaaa",
            headers={
                "X-Protocol-Token": _protocol_token(
                    settings.protocol_secret,
                    "draft-apiaaaaaa",
                    "sha256:" + "1" * 64,
                    "sha256:" + "2" * 64,
                    1,
                )
            },
        )
        assert response.json()["detail"]["code"] == "protocol_draft_expired"
    elif case == "tampered":
        response = client.get(
            "/v1/protocol-drafts/draft-apiaaaaaa",
            headers={
                "X-Protocol-Token": valid[:-1] + ("0" if valid[-1] != "0" else "1")
            },
        )
        assert response.json()["detail"]["code"] == "protocol_token_tampered"
    else:
        response = client.get(
            "/v1/protocol-drafts/draft-otherxxx",
            headers={"X-Protocol-Token": valid},
        )
        assert response.json()["detail"]["code"] == "protocol_draft_token_mismatch"


def test_protocol_draft_post_rejects_malformed_json_without_echoing_body_or_token(
    client: TestClient, settings: Settings
):
    token = _protocol_token(
        settings.protocol_secret,
        "draft-apiaaaaaa",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        2_000,
    )
    response = client.post(
        "/v1/protocol-drafts",
        data={
            "draft_id": "draft-apiaaaaaa",
            "protocol_token": token,
            "dossier_json": "{",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "protocol_payload_invalid"
    assert token not in response.text
    assert "traceback" not in response.text.casefold()


def test_protocol_draft_post_rejects_sensitive_content_without_echoing_body_or_token(
    client: TestClient, settings: Settings
):
    token = _protocol_token(
        settings.protocol_secret,
        "draft-apiaaaaaa",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        2_000,
    )
    dossier = '{"title":"Paper","raw_csv":"col_a,col_b\\n1,2"}'
    response = client.post(
        "/v1/protocol-drafts",
        data={
            "draft_id": "draft-apiaaaaaa",
            "protocol_token": token,
            "dossier_json": dossier,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "protocol_payload_invalid"
    assert token not in response.text
    assert "col_a,col_b" not in response.text


def test_protocol_draft_post_rejects_oversized_json_without_echoing_body_or_token(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(api, "MAX_DOSSIER_BYTES", 8)
    token = _protocol_token(
        settings.protocol_secret,
        "draft-apiaaaaaa",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        2_000,
    )
    oversized = "x" * 9
    response = client.post(
        "/v1/protocol-drafts",
        data={
            "draft_id": "draft-apiaaaaaa",
            "protocol_token": token,
            "dossier_json": oversized,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "protocol_payload_invalid"
    assert token not in response.text
    assert oversized[:64] not in response.text


def test_protocol_draft_get_requires_protocol_token_header(client: TestClient):
    response = client.get("/v1/protocol-drafts/draft-apiaaaaaa")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_protocol_draft_post_is_idempotent_for_same_dossier(
    client: TestClient, settings: Settings
):
    token = _protocol_token(
        settings.protocol_secret,
        "draft-apiaaaaaa",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        2_000,
    )
    payload = {
        "draft_id": "draft-apiaaaaaa",
        "protocol_token": token,
        "dossier_json": '{"title":"Paper","metrics":[]}',
    }

    first = client.post("/v1/protocol-drafts", data=payload)
    second = client.post("/v1/protocol-drafts", data=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()


def test_protocol_draft_cleanup_expired_runs_before_reads_and_writes(
    client: TestClient, settings: Settings
):
    store = client.app.state.protocol_draft_store
    token = _protocol_token(
        settings.protocol_secret,
        "draft-apiaaaaaa",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        2_000,
    )
    calls: list[str] = []
    original_cleanup = store.cleanup_expired

    def observed_cleanup(excluded_draft_id=None):
        calls.append("cleanup")
        return original_cleanup(excluded_draft_id=excluded_draft_id)

    store.cleanup_expired = observed_cleanup
    try:
        post = client.post(
            "/v1/protocol-drafts",
            data={
                "draft_id": "draft-apiaaaaaa",
                "protocol_token": token,
                "dossier_json": '{"title":"Paper"}',
            },
        )
        get = client.get(
            "/v1/protocol-drafts/draft-apiaaaaaa",
            headers={"X-Protocol-Token": token},
        )
    finally:
        store.cleanup_expired = original_cleanup

    assert post.status_code == 200
    assert get.status_code == 200
    assert calls == ["cleanup", "cleanup"]


def test_protocol_draft_get_returns_expired_for_requested_draft_while_cleaning_others(
    client: TestClient, settings: Settings
):
    store = client.app.state.protocol_draft_store
    requested_token = _protocol_token(
        settings.protocol_secret,
        "draft-apiaaaaaa",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        3_000,
    )
    other_token = _protocol_token(
        settings.protocol_secret,
        "draft-otherxxx",
        "sha256:" + "3" * 64,
        "sha256:" + "4" * 64,
        3_000,
    )
    store.clock = lambda: 1_000
    assert client.post(
        "/v1/protocol-drafts",
        data={
            "draft_id": "draft-apiaaaaaa",
            "protocol_token": requested_token,
            "dossier_json": '{"title":"Requested"}',
        },
    ).status_code == 200
    assert client.post(
        "/v1/protocol-drafts",
        data={
            "draft_id": "draft-otherxxx",
            "protocol_token": other_token,
            "dossier_json": '{"title":"Other"}',
        },
    ).status_code == 200

    store.clock = lambda: 1_950
    response = client.get(
        "/v1/protocol-drafts/draft-apiaaaaaa",
        headers={"X-Protocol-Token": requested_token},
    )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "protocol_draft_expired"
    assert (
        settings.storage_dir
        / "protocol-drafts"
        / "draft-apiaaaaaa"
        / "draft.json"
    ).exists()
    assert not (
        settings.storage_dir
        / "protocol-drafts"
        / "draft-otherxxx"
        / "draft.json"
    ).exists()


def test_protocol_draft_cleanup_failure_is_sanitized(
    client: TestClient, settings: Settings, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.ERROR, logger="repro_runner.api")
    store = client.app.state.protocol_draft_store
    token = _protocol_token(
        settings.protocol_secret,
        "draft-apiaaaaaa",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        2_000,
    )
    original_cleanup = store.cleanup_expired

    def failing_cleanup():
        raise OSError("private cleanup traceback with token pt1.secret")

    store.cleanup_expired = failing_cleanup
    try:
        response = client.post(
            "/v1/protocol-drafts",
            data={
                "draft_id": "draft-apiaaaaaa",
                "protocol_token": token,
                "dossier_json": '{"title":"Paper"}',
            },
        )
    finally:
        store.cleanup_expired = original_cleanup

    assert response.status_code == 200
    assert "pt1.secret" not in response.text
    assert "traceback" not in response.text.casefold()
    assert "protocol draft cleanup failed" in caplog.text
    assert "pt1.secret" not in caplog.text


def test_protocol_draft_write_failure_is_sanitized(
    client: TestClient, settings: Settings
):
    store = client.app.state.protocol_draft_store
    token = _protocol_token(
        settings.protocol_secret,
        "draft-apiaaaaaa",
        "sha256:" + "1" * 64,
        "sha256:" + "2" * 64,
        2_000,
    )
    original_save = store.save

    def failing_save(*_args, **_kwargs):
        raise OSError("private draft write traceback token")

    store.save = failing_save
    try:
        response = client.post(
            "/v1/protocol-drafts",
            data={
                "draft_id": "draft-apiaaaaaa",
                "protocol_token": token,
                "dossier_json": '{"title":"Paper"}',
            },
        )
    finally:
        store.save = original_save

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "protocol_draft_write_failed"
    assert token not in response.text
    assert "traceback" not in response.text.casefold()


def test_parse_dossier_returns_normalized_metrics(client: TestClient):
    response = client.post(
        "/v1/parse-dossier",
        files={"file": ("paper.json", _dossier(), "application/json")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["metrics"][0]["reported_value"] == 0.91


def test_parse_dossier_returns_validation_error_for_bad_dossier_json(client: TestClient):
    response = client.post(
        "/v1/parse-dossier",
        files={"file": ("paper.json", b"{", "application/json")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"][0]["code"] == "invalid_dossier_json"


def test_parse_dossier_rejects_large_file_without_calling_parser(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    called = False

    def unexpected_parser(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("parser must not be called")

    monkeypatch.setattr(api, "parse_dossier", unexpected_parser)
    response = client.post(
        "/v1/parse-dossier",
        files={
            "file": (
                "large.json",
                b"x" * (5 * 1024 * 1024 + 1),
                "application/json",
            )
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "dossier_file_too_large"
    assert called is False


def test_parse_dossier_hard_cap_cannot_be_raised_by_settings(
    client_with_raised_dossier_limits: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    called = False

    def unexpected_parser(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("parser must not be called")

    monkeypatch.setattr(api, "parse_dossier", unexpected_parser)
    response = client_with_raised_dossier_limits.post(
        "/v1/parse-dossier",
        files={
            "file": (
                "large.json",
                b"x" * (5 * 1024 * 1024 + 1),
                "application/json",
            )
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "dossier_file_too_large"
    assert called is False


def test_parse_dossier_sanitizes_unexpected_parser_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.ERROR, logger="repro_runner.api")

    def fail_parser(*_args, **_kwargs):
        raise RuntimeError("private dossier parser detail")

    monkeypatch.setattr(api, "parse_dossier", fail_parser)
    response = client.post(
        "/v1/parse-dossier",
        files={"file": ("paper.json", _dossier(), "application/json")},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "dossier_parse_failed"
    assert detail["request_id"]
    assert "private dossier parser detail" not in response.text
    assert "dossier parsing failed" in caplog.text
    assert detail["request_id"] in caplog.text
    assert "private dossier parser detail" not in caplog.text
    assert "Traceback" not in caplog.text


def test_parse_dossier_rejects_oversized_metric_overrides_semantically(
    client: TestClient,
):
    response = client.post(
        "/v1/parse-dossier",
        data={"metric_overrides_json": "x" * (64 * 1024 + 1)},
        files={"file": ("paper.json", _dossier(), "application/json")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"][0]["code"] == "invalid_metric_overrides"


def test_metric_override_hard_cap_cannot_be_raised_by_settings(
    client_with_raised_dossier_limits: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    called = False

    def unexpected_parser(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("parser must not be called")

    monkeypatch.setattr(api, "parse_dossier", unexpected_parser)
    response = client_with_raised_dossier_limits.post(
        "/v1/parse-dossier",
        data={"metric_overrides_json": "x" * (64 * 1024 + 1)},
        files={"file": ("paper.json", _dossier(), "application/json")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"][0]["code"] == "invalid_metric_overrides"
    assert called is False


def test_validate_dataset_returns_profile(client: TestClient):
    response = client.post(
        "/v1/validate-dataset",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["dataset"]["target"] == "Y_cls"


def test_diagnose_dataset_returns_structured_warnings_without_echoing_rows(
    client: TestClient,
):
    content = (
        "id,score,segment,constant,Y_cls\n"
        "1,0.1,alpha,always,0\n"
        "2,0.2,beta,always,0\n"
        "3,0.3,gamma,always,1\n"
        "4,0.4,delta,always,1\n"
    ).encode()

    response = client.post(
        "/v1/diagnose-dataset",
        data={"target_column": "Y_cls"},
        files={"file": ("data.csv", content, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["warnings"]
    assert "secret" not in response.text
    assert "1,0.1,alpha,always,0" not in response.text
    assert {column["name"] for column in body["columns"]} == {
        "id",
        "score",
        "segment",
        "constant",
        "Y_cls",
    }


def test_diagnose_dataset_requires_target_confirmation_when_default_target_is_missing(
    client: TestClient,
):
    content = b"x1,label\n1,0\n2,0\n3,1\n4,1\n"

    response = client.post(
        "/v1/diagnose-dataset",
        files={"file": ("data.csv", content, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["dataset"]["target"] is None
    assert body["target_candidates"] == ["label"]
    assert body["errors"][0]["code"] == "target_column_confirmation_required"


def test_diagnose_dataset_accepts_explicit_target_and_exclude_columns(
    client: TestClient,
):
    content = b"id,score,label\n1,0.1,0\n2,0.2,0\n3,0.3,1\n4,0.4,1\n"

    response = client.post(
        "/v1/diagnose-dataset",
        data={
            "target_column": "label",
            "exclude_columns": json.dumps(["id"]),
        },
        files={"file": ("data.csv", content, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["dataset"]["target"] == "label"
    assert body["recommended_options"]["exclude_columns"] == ["id"]


def test_diagnose_dataset_rejects_conflicting_exclude_column_inputs(
    client: TestClient,
):
    response = client.post(
        "/v1/diagnose-dataset",
        data={
            "target_column": "label",
            "exclude_columns": json.dumps(["id"]),
            "exclude_columns_json": json.dumps(["score"]),
        },
        files={
            "file": (
                "data.csv",
                b"id,score,label\n1,0.1,0\n2,0.2,0\n3,0.3,1\n4,0.4,1\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_diagnose_dataset_reports_unknown_exclude_column_without_500(
    client: TestClient,
):
    response = client.post(
        "/v1/diagnose-dataset",
        data={
            "target_column": "label",
            "exclude_columns": json.dumps(["missing"]),
        },
        files={
            "file": (
                "data.csv",
                b"id,score,label\n1,0.1,0\n2,0.2,0\n3,0.3,1\n4,0.4,1\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"][0]["code"] == "invalid_exclude_columns"
    assert "1,0.1,0" not in response.text


def test_diagnose_dataset_rejects_non_finite_numeric_feature_tokens(
    client: TestClient,
):
    response = client.post(
        "/v1/diagnose-dataset",
        data={"target_column": "label"},
        files={
            "file": (
                "data.csv",
                b"score,label\nInfinity,0\n1.0,0\n2.0,1\n3.0,1\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"][0]["code"] == "non_finite_numeric_feature"


def test_diagnose_dataset_returns_structured_error_when_rows_exceed_limit(tmp_path):
    settings = Settings(storage_dir=tmp_path, max_upload_mb=1, max_diagnostic_rows=3)
    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/diagnose-dataset",
            data={"target_column": "label"},
            files={
                "file": (
                    "data.csv",
                    b"x1,label\n1,0\n2,0\n3,1\n4,1\n",
                    "text/csv",
                )
            },
        )
    api.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"][0]["code"] == "too_many_rows"


def test_run_experiment_returns_id_and_metrics_and_persists_it(client: TestClient):
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 200
    result = response.json()
    fetched = client.get(f"/v1/experiments/{result['experiment_id']}")

    assert result["experiment_id"].startswith("exp-")
    assert "roc_auc" in result["metrics"]
    assert result["reproducibility_status"] == "baseline_only"
    assert fetched.status_code == 200
    assert fetched.json() == result


def test_run_experiment_same_idempotency_key_executes_and_saves_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    train_calls = 0
    save_calls = 0
    original_train = api.run_random_forest

    def counted_train(*args, **kwargs):
        nonlocal train_calls
        train_calls += 1
        return original_train(*args, **kwargs)

    def counted_save(result, _settings):
        nonlocal save_calls
        save_calls += 1
        return result.experiment_id

    monkeypatch.setattr(api, "run_random_forest", counted_train)
    monkeypatch.setattr(api, "save_result", counted_save)
    request = {
        "data": {"idempotency_key": "dify-run-123"},
        "files": {"file": ("data.csv", _csv(), "text/csv")},
    }

    first = client.post("/v1/run-experiment", **request)
    second = client.post("/v1/run-experiment", **request)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert train_calls == 1
    assert save_calls == 1


def test_concurrent_same_key_retries_join_before_admission_rejection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    calls = 0
    barrier = Barrier(4)
    training_started = Event()
    release_training = Event()
    replay_read_started = Event()
    release_replay_read = Event()
    original_train = api.run_random_forest
    original_read = api._read_upload
    read_calls = 0

    def blocking_train(*args, **kwargs):
        nonlocal calls
        calls += 1
        training_started.set()
        assert release_training.wait(timeout=5)
        return original_train(*args, **kwargs)

    async def blocking_replay_read(file, settings):
        nonlocal read_calls
        read_calls += 1
        if read_calls > 1:
            replay_read_started.set()
            assert await asyncio.to_thread(release_replay_read.wait, 5)
        return await original_read(file, settings)

    def post_experiment():
        barrier.wait(timeout=5)
        return client.post(
            "/v1/run-experiment",
            data={"idempotency_key": "concurrent-retry"},
            files={"file": ("data.csv", _csv(), "text/csv")},
        )

    monkeypatch.setattr(api, "run_random_forest", blocking_train)
    monkeypatch.setattr(api, "_read_upload", blocking_replay_read)
    monkeypatch.setattr(
        api, "save_result", lambda result, _settings: result.experiment_id
    )
    with ThreadPoolExecutor(max_workers=3) as executor:
        requests = [executor.submit(post_experiment) for _ in range(3)]
        barrier.wait(timeout=5)
        assert training_started.wait(timeout=5)
        wait(requests, timeout=1, return_when=FIRST_COMPLETED)
        release_training.set()
        assert replay_read_started.wait(timeout=5)
        wait(requests, timeout=1, return_when=FIRST_COMPLETED)
        release_replay_read.set()
        responses = [request.result(timeout=10) for request in requests]

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert responses[0].json() == responses[1].json() == responses[2].json()
    assert calls == 1


def test_run_experiment_rejects_idempotency_key_reuse_with_different_input(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    train_calls = 0
    original_train = api.run_random_forest

    def counted_train(*args, **kwargs):
        nonlocal train_calls
        train_calls += 1
        return original_train(*args, **kwargs)

    monkeypatch.setattr(api, "run_random_forest", counted_train)
    first = client.post(
        "/v1/run-experiment",
        data={"idempotency_key": "dify-run-conflict"},
        files={"file": ("data.csv", _csv(), "text/csv")},
    )
    conflict = client.post(
        "/v1/run-experiment",
        data={"idempotency_key": "dify-run-conflict", "random_state": "7"},
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["code"] == "idempotency_conflict"
    assert detail["request_id"]
    assert "dify-run-conflict" not in conflict.text
    assert train_calls == 1


def test_run_experiment_without_idempotency_key_remains_independent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    train_calls = 0
    original_train = api.run_random_forest

    def counted_train(*args, **kwargs):
        nonlocal train_calls
        train_calls += 1
        return original_train(*args, **kwargs)

    monkeypatch.setattr(api, "run_random_forest", counted_train)
    request = {"files": {"file": ("data.csv", _csv(), "text/csv")}}

    first = client.post("/v1/run-experiment", **request)
    second = client.post("/v1/run-experiment", **request)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["experiment_id"] != second.json()["experiment_id"]
    assert train_calls == 2


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"x1\n1\n", "missing_target_column"),
        (b"x1,Y_cls\n1,0\n2,0\n", "invalid_target_classes"),
        (b"x1,Y_cls\n1,0\n,1\n", "missing_values"),
        (b"x1,Y_cls\na,0\n2,1\n", "non_numeric_feature"),
        (b"x1,Y_cls\nNaN,0\n2,1\n", "non_finite_numeric_feature"),
        (b"x1,Y_cls\n1,0,extra\n2,1\n", "invalid_csv"),
    ],
)
def test_validate_dataset_returns_200_with_structured_errors(
    client: TestClient, content: bytes, code: str
):
    response = client.post(
        "/v1/validate-dataset",
        files={"file": ("data.csv", content, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["dataset"] is None
    assert body["errors"] == [
        {"code": code, "message": pytest.ANY if hasattr(pytest, "ANY") else body["errors"][0]["message"]}
    ]
    assert body["errors"][0]["message"]


def test_run_experiment_keeps_dataset_failures_as_422(client: TestClient):
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", b"x1\n1\n", "text/csv")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "missing_target_column"


def test_missing_experiment_has_not_found_code_and_request_id(client: TestClient):
    response = client.get("/v1/experiments/exp-20260805T010203Z-deadbeef")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_not_found"
    assert detail["request_id"]


@pytest.mark.parametrize("entrypoint", ["get", "compare"])
def test_corrupt_stored_result_returns_409_not_not_found(
    client: TestClient, tmp_path, entrypoint: str
):
    experiment_id = "exp-20260805T010203Z-deadbeef"
    directory = tmp_path / experiment_id
    directory.mkdir()
    (directory / "result.json").write_bytes(b"{not-json")

    if entrypoint == "get":
        response = client.get(f"/v1/experiments/{experiment_id}")
    else:
        response = client.post(
            "/v1/compare-result",
            json={"experiment_id": experiment_id, "reported_metrics": []},
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_result_incompatible"
    assert detail["request_id"]


def test_compare_result_uses_persisted_experiment(client: TestClient):
    result = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    ).json()
    response = client.post(
        "/v1/compare-result",
        json={
            "experiment_id": result["experiment_id"],
            "reported_metrics": [
                {
                    "name": "AUC",
                    "reported_value": 0.91,
                    "dataset": "test",
                    "split": "test",
                    "dataset_id": result["dataset"]["dataset_id"],
                    "test_size": result["split_provenance"]["test_size"],
                    "random_state": result["split_provenance"]["random_state"],
                    "train_rows": result["split_provenance"]["train_rows"],
                    "test_rows": result["split_provenance"]["test_rows"],
                    "test_digest": result["split_provenance"]["test_digest"],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["experiment_id"] == result["experiment_id"]
    assert response.json()["items"][0]["comparable"] is True


def test_run_experiment_returns_invalid_split_as_422(client: TestClient):
    response = client.post(
        "/v1/run-experiment",
        files={
            "file": (
                "data.csv",
                b"x,Y_cls\n1,0\n2,0\n3,1\n4,1\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_split"


def test_file_larger_than_configured_limit_is_413_with_request_id(client: TestClient):
    response = client.post(
        "/v1/validate-dataset",
        files={"file": ("large.csv", b"x" * (1024 * 1024 + 1), "text/csv")},
    )

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["code"] == "file_too_large"
    assert detail["request_id"]
    assert "traceback" not in response.text.casefold()


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/v1/run-experiment", None),
        ("/v1/run-experiment", {"model": "other"}),
        ("/v1/run-experiment", {"test_size": "0.9"}),
        ("/v1/run-experiment", {"idempotency_key": "x" * 129}),
    ],
)
def test_invalid_request_is_sanitized_with_request_id(
    client: TestClient, path: str, data: dict[str, str] | None
):
    kwargs = {} if data is None else {
        "data": data,
        "files": {"file": ("data.csv", _csv(), "text/csv")},
    }
    response = client.post(path, **kwargs)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_request"
    assert detail["request_id"]
    assert "traceback" not in response.text.casefold()


def test_training_failure_is_sanitized_with_request_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fail_training(*_args, **_kwargs):
        raise RuntimeError("private training stack")

    monkeypatch.setattr(api, "run_random_forest", fail_training)
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_failed"
    assert detail["request_id"]
    assert "private" not in response.text
    assert "traceback" not in response.text.casefold()


def test_requested_experiment_configuration_is_returned(client: TestClient):
    csv = (
        "x1,label\n" + "\n".join(f"{index},{index % 2}" for index in range(80)) + "\n"
    ).encode()
    response = client.post(
        "/v1/run-experiment",
        data={
            "target_column": "label",
            "random_state": "7",
            "drop_duplicates": "true",
        },
        files={"file": ("data.csv", csv, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dataset"]["target"] == "label"
    assert body["config"]["random_state"] == 7
    assert body["config"]["drop_duplicates"] is True
    assert body["dataset"]["effective_rows"] == 80


def test_run_experiment_admits_request_before_reading_or_parsing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    states: list[str] = []
    original_read = api._read_upload
    original_load = api.load_dataset

    async def observed_read(file, settings):
        assert api._get_experiment_limiter().is_saturated(
            settings.max_concurrent_experiments
        )
        states.append("read")
        return await original_read(file, settings)

    def observed_load(*args, **kwargs):
        settings = args[-1]
        assert api._get_experiment_limiter().is_saturated(
            settings.max_concurrent_experiments
        )
        states.append("parse")
        return original_load(*args, **kwargs)

    monkeypatch.setattr(api, "_read_upload", observed_read)
    monkeypatch.setattr(api, "load_dataset", observed_load)
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 200
    assert states == ["read", "parse"]


def test_keyed_experiment_admits_request_before_reading_or_fingerprinting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    states: list[str] = []
    original_read = api._read_upload

    async def observed_read(file, settings):
        assert api._get_experiment_limiter().is_saturated(
            settings.max_concurrent_experiments
        )
        states.append("read")
        return await original_read(file, settings)

    monkeypatch.setattr(api, "_read_upload", observed_read)
    monkeypatch.setattr(
        api, "save_result", lambda result, _settings: result.experiment_id
    )
    response = client.post(
        "/v1/run-experiment",
        data={"idempotency_key": "admission-boundary"},
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 200
    assert states == ["read"]


def test_async_admission_rejects_saturation_and_recovers_after_cancellation():
    async def scenario():
        settings = Settings(max_concurrent_experiments=1)
        api.app.state.experiment_limiter = api.ExperimentAdmissionLimiter()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hold_slot():
            async with api._admit_experiment(settings):
                entered.set()
                await release.wait()

        holder = asyncio.create_task(hold_slot())
        await entered.wait()
        with pytest.raises(HTTPException) as error:
            async with api._admit_experiment(settings):
                pass
        assert error.value.status_code == 429
        assert error.value.detail["code"] == "experiment_capacity_reached"

        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder

        async with api._admit_experiment(settings):
            pass

    asyncio.run(scenario())


def test_idempotency_registry_joins_concurrent_identical_requests():
    async def scenario():
        registry = api.ExperimentIdempotencyRegistry(max_entries=2)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "shared-result"

        first = asyncio.create_task(registry.execute("same", "fingerprint", operation))
        await started.wait()
        second = asyncio.create_task(registry.execute("same", "fingerprint", operation))
        await asyncio.sleep(0)
        release.set()

        assert await asyncio.gather(first, second) == [
            "shared-result",
            "shared-result",
        ]
        assert calls == 1

    asyncio.run(scenario())


def test_cancelling_registry_owner_cancels_work_before_admission_is_released():
    async def scenario():
        settings = Settings(max_concurrent_experiments=1)
        api.app.state.experiment_limiter = api.ExperimentAdmissionLimiter()
        registry = api.ExperimentIdempotencyRegistry(max_entries=2)
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def operation():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def request():
            async with api._admit_experiment(settings):
                return await registry.execute("cancelled", "fingerprint", operation)

        owner = asyncio.create_task(request())
        await started.wait()
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner

        assert stopped.is_set()
        async with api._admit_experiment(settings):
            pass

    asyncio.run(scenario())


def test_idempotency_registry_is_bounded_and_safe_across_event_loops():
    registry = api.ExperimentIdempotencyRegistry(max_entries=2)
    calls = 0

    async def run_sequence():
        nonlocal calls

        async def operation(value):
            nonlocal calls
            calls += 1
            return value

        await registry.execute("one", "fp-one", lambda: operation("one"))
        await registry.execute("two", "fp-two", lambda: operation("two"))
        await registry.execute("three", "fp-three", lambda: operation("three"))
        return await registry.execute("one", "fp-one", lambda: operation("one-again"))

    assert asyncio.run(run_sequence()) == "one-again"
    assert calls == 4

    async def new_loop():
        async def operation():
            nonlocal calls
            calls += 1
            return "unexpected-retraining"

        return await registry.execute("one", "fp-one", operation)

    assert asyncio.run(new_loop()) == "one-again"
    assert calls == 4
