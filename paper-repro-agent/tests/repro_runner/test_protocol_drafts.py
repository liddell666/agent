import base64
import hashlib
import hmac
import json

import pytest

from repro_runner.protocol_drafts import ProtocolDraftError, ProtocolDraftStore


def _token(
    draft_id: str,
    manifest_id: str,
    dataset_id: str,
    exp: int,
    *,
    secret: str = "test-secret",
    ready: bool = True,
    version: int = 1,
) -> str:
    payload = {
        "v": version,
        "exp": exp,
        "ready": ready,
        "draft_id": draft_id,
        "manifest": {"manifest_id": manifest_id, "dataset_id": dataset_id},
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"pt1.{encoded}.{signature}"


def test_save_and_load_store_only_dossier_and_metadata(tmp_path):
    token = _token(
        draft_id="draft-aaaaaaaa",
        manifest_id="sha256:" + "1" * 64,
        dataset_id="sha256:" + "2" * 64,
        exp=2_000,
    )
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_000)

    record = store.save(
        "draft-aaaaaaaa",
        token,
        {"title": "Paper", "metrics": [{"name": "AUC", "reported_value": 0.91}]},
    )

    assert record.draft_id == "draft-aaaaaaaa"
    assert record.manifest_id == "sha256:" + "1" * 64
    assert record.dataset_id == "sha256:" + "2" * 64
    assert store.load("draft-aaaaaaaa", token).dossier["title"] == "Paper"
    stored = (tmp_path / "draft-aaaaaaaa" / "draft.json").read_text(encoding="utf-8")
    assert "test-secret" not in stored
    assert "pt1." not in stored
    assert "AUC" in stored


def test_save_is_idempotent_only_for_identical_content(tmp_path):
    token = _token(
        draft_id="draft-aaaaaaaa",
        manifest_id="sha256:" + "1" * 64,
        dataset_id="sha256:" + "2" * 64,
        exp=2_000,
    )
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_000)
    dossier = {"title": "Paper", "metrics": []}
    first = store.save("draft-aaaaaaaa", token, dossier)
    second = store.save("draft-aaaaaaaa", token, dossier)
    assert second == first
    with pytest.raises(ProtocolDraftError) as error:
        store.save("draft-aaaaaaaa", token, {"title": "Changed"})
    assert error.value.code == "protocol_draft_token_mismatch"


def test_save_is_idempotent_across_later_retries_with_same_content(tmp_path):
    now = [1_000]
    token = _token(
        draft_id="draft-aaaaaaaa",
        manifest_id="sha256:" + "1" * 64,
        dataset_id="sha256:" + "2" * 64,
        exp=2_000,
    )
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: now[0])
    first = store.save("draft-aaaaaaaa", token, {"title": "Paper", "metrics": []})

    now[0] = 1_200
    second = store.save("draft-aaaaaaaa", token, {"title": "Paper", "metrics": []})

    assert second == first


def test_load_rejects_bad_signature_wrong_draft_and_expired_token(tmp_path):
    token = _token(
        draft_id="draft-aaaaaaaa",
        manifest_id="sha256:" + "1" * 64,
        dataset_id="sha256:" + "2" * 64,
        exp=1_001,
    )
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_000)
    store.save("draft-aaaaaaaa", token, {"title": "Paper"})
    with pytest.raises(ProtocolDraftError) as bad_signature:
        store.load("draft-aaaaaaaa", token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert bad_signature.value.code == "protocol_token_tampered"
    with pytest.raises(ProtocolDraftError) as malformed:
        store.load("draft-aaaaaaaa", "not-a-protocol-token")
    assert malformed.value.code == "protocol_token_malformed"
    with pytest.raises(ProtocolDraftError) as wrong_draft:
        store.load("draft-bbbbbbbb", token)
    assert wrong_draft.value.code == "protocol_draft_token_mismatch"
    expired = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_002)
    with pytest.raises(ProtocolDraftError) as expired_error:
        expired.load("draft-aaaaaaaa", token)
    assert expired_error.value.code == "protocol_draft_expired"


def test_cleanup_expired_removes_only_expired_drafts(tmp_path):
    now = [900]
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: now[0])
    store.save(
        "draft-aaaaaaaa",
        _token(
            "draft-aaaaaaaa",
            "sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
            999,
        ),
        {"title": "Old"},
    )
    store.save(
        "draft-bbbbbbbb",
        _token(
            "draft-bbbbbbbb",
            "sha256:" + "3" * 64,
            "sha256:" + "4" * 64,
            2_000,
        ),
        {"title": "New"},
    )
    now[0] = 1_000
    assert store.cleanup_expired() == 1
    assert not (tmp_path / "draft-aaaaaaaa").exists()
    assert (tmp_path / "draft-bbbbbbbb" / "draft.json").exists()


def test_rejects_unsafe_draft_ids(tmp_path):
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_000)
    token = _token(
        draft_id="draft-aaaaaaaa",
        manifest_id="sha256:" + "1" * 64,
        dataset_id="sha256:" + "2" * 64,
        exp=2_000,
    )
    for unsafe_id in ("../draft-aaaaaaaa", "draft-../../etc", "draft-short", "draft space"):
        with pytest.raises(ProtocolDraftError) as error:
            store.save(unsafe_id, token, {"title": "Paper"})
        assert error.value.code == "protocol_draft_token_mismatch"


def test_load_rejects_manifest_or_dataset_mismatch_for_existing_record(tmp_path):
    original_token = _token(
        draft_id="draft-aaaaaaaa",
        manifest_id="sha256:" + "1" * 64,
        dataset_id="sha256:" + "2" * 64,
        exp=2_000,
    )
    mismatched_manifest = _token(
        draft_id="draft-aaaaaaaa",
        manifest_id="sha256:" + "9" * 64,
        dataset_id="sha256:" + "2" * 64,
        exp=2_000,
    )
    mismatched_dataset = _token(
        draft_id="draft-aaaaaaaa",
        manifest_id="sha256:" + "1" * 64,
        dataset_id="sha256:" + "8" * 64,
        exp=2_000,
    )
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_000)
    store.save("draft-aaaaaaaa", original_token, {"title": "Paper"})

    with pytest.raises(ProtocolDraftError) as manifest_error:
        store.load("draft-aaaaaaaa", mismatched_manifest)
    assert manifest_error.value.code == "protocol_draft_token_mismatch"

    with pytest.raises(ProtocolDraftError) as dataset_error:
        store.load("draft-aaaaaaaa", mismatched_dataset)
    assert dataset_error.value.code == "protocol_draft_token_mismatch"


def test_rejects_invalid_payload_fields(tmp_path):
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_000)

    with pytest.raises(ProtocolDraftError) as wrong_version:
        store.save(
            "draft-aaaaaaaa",
            _token(
                "draft-aaaaaaaa",
                "sha256:" + "1" * 64,
                "sha256:" + "2" * 64,
                2_000,
                version=2,
            ),
            {"title": "Paper"},
        )
    assert wrong_version.value.code == "protocol_payload_invalid"

    with pytest.raises(ProtocolDraftError) as not_ready:
        store.save(
            "draft-aaaaaaaa",
            _token(
                "draft-aaaaaaaa",
                "sha256:" + "1" * 64,
                "sha256:" + "2" * 64,
                2_000,
                ready=False,
            ),
            {"title": "Paper"},
        )
    assert not_ready.value.code == "protocol_payload_invalid"
