from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import time
from typing import Callable


_DRAFT_ID = re.compile(r"draft-[A-Za-z0-9_-]{8,128}\Z")


@dataclass(frozen=True)
class ProtocolToken:
    version: int
    draft_id: str
    manifest_id: str
    dataset_id: str
    expires_at: int


@dataclass(frozen=True)
class ProtocolDraftRecord:
    draft_id: str
    protocol_version: int
    manifest_id: str
    dataset_id: str
    created_at: int
    expires_at: int
    dossier: dict[str, object]


class ProtocolDraftError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def verify_protocol_token(
    token: str, *, secret: str, draft_id: str, now: int
) -> ProtocolToken:
    """Verify pt1 token syntax, HMAC, ready flag, draft binding and expiry."""
    _validate_draft_id(draft_id)
    if not isinstance(token, str):
        raise ProtocolDraftError("protocol_token_malformed")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "pt1" or not parts[1] or not parts[2]:
        raise ProtocolDraftError("protocol_token_malformed")
    encoded_payload, provided_signature = parts[1], parts[2]
    if not re.fullmatch(r"[0-9a-f]{64}", provided_signature):
        raise ProtocolDraftError("protocol_token_malformed")
    expected_signature = hmac.new(
        secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise ProtocolDraftError("protocol_token_tampered")
    payload = _decode_payload(encoded_payload)
    if payload.get("v") != 1 or payload.get("ready") is not True:
        raise ProtocolDraftError("protocol_payload_invalid")
    payload_draft_id = payload.get("draft_id")
    manifest = payload.get("manifest")
    expires_at = payload.get("exp")
    if (
        not isinstance(payload_draft_id, str)
        or not isinstance(manifest, dict)
        or not isinstance(expires_at, int)
    ):
        raise ProtocolDraftError("protocol_payload_invalid")
    manifest_id = manifest.get("manifest_id")
    dataset_id = manifest.get("dataset_id")
    if not isinstance(manifest_id, str) or not isinstance(dataset_id, str):
        raise ProtocolDraftError("protocol_payload_invalid")
    if payload_draft_id != draft_id:
        raise ProtocolDraftError("protocol_draft_token_mismatch")
    if now >= expires_at:
        raise ProtocolDraftError("protocol_draft_expired")
    return ProtocolToken(
        version=1,
        draft_id=payload_draft_id,
        manifest_id=manifest_id,
        dataset_id=dataset_id,
        expires_at=expires_at,
    )


class ProtocolDraftStore:
    def __init__(
        self,
        root: Path,
        *,
        secret: str,
        ttl_seconds: int = 900,
        clock: Callable[[], float] = time.time,
    ):
        self.root = root
        self.secret = secret
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    def save(
        self, draft_id: str, token: str, dossier: dict[str, object]
    ) -> ProtocolDraftRecord:
        """Validate and atomically create an idempotent draft.json."""
        now = int(self.clock())
        parsed = verify_protocol_token(
            token, secret=self.secret, draft_id=draft_id, now=now
        )
        record = ProtocolDraftRecord(
            draft_id=parsed.draft_id,
            protocol_version=parsed.version,
            manifest_id=parsed.manifest_id,
            dataset_id=parsed.dataset_id,
            created_at=now,
            expires_at=min(parsed.expires_at, now + self.ttl_seconds),
            dossier=_normalize_dossier(dossier),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        directory = _draft_directory(self.root, draft_id)
        path = directory / "draft.json"
        if path.exists():
            existing = self._read_record(path)
            return _match_existing_or_raise(existing, record, now=now)

        try:
            directory.mkdir()
        except FileExistsError:
            existing = self._read_record(path)
            return _match_existing_or_raise(existing, record, now=now)
        try:
            _write_json_atomic(path, _record_payload(record))
        except Exception:
            if not path.exists():
                shutil.rmtree(directory, ignore_errors=True)
            raise
        return record

    def load(self, draft_id: str, token: str) -> ProtocolDraftRecord:
        """Validate token and return the unmodified stored dossier."""
        now = int(self.clock())
        parsed = verify_protocol_token(
            token, secret=self.secret, draft_id=draft_id, now=now
        )
        path = _draft_directory(self.root, draft_id) / "draft.json"
        record = self._read_record(path)
        if now >= record.expires_at:
            raise ProtocolDraftError("protocol_draft_expired")
        if (
            record.draft_id != parsed.draft_id
            or record.manifest_id != parsed.manifest_id
            or record.dataset_id != parsed.dataset_id
        ):
            raise ProtocolDraftError("protocol_draft_token_mismatch")
        return record

    def cleanup_expired(self) -> int:
        """Delete only draft directories whose stored expires_at is in the past."""
        now = int(self.clock())
        if not self.root.exists():
            return 0
        removed = 0
        for child in self.root.iterdir():
            if not child.is_dir() or not _DRAFT_ID.fullmatch(child.name):
                continue
            try:
                record = self._read_record(child / "draft.json")
            except (OSError, ValueError, json.JSONDecodeError, ProtocolDraftError):
                continue
            if now >= record.expires_at:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed

    def _read_record(self, path: Path) -> ProtocolDraftRecord:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ProtocolDraftError("protocol_draft_token_mismatch") from exc
        except UnicodeDecodeError as exc:
            raise ProtocolDraftError("protocol_draft_token_mismatch") from exc
        return _record_from_payload(payload)


def _decode_payload(encoded_payload: str) -> dict[str, object]:
    try:
        padding = "=" * (-len(encoded_payload) % 4)
        decoded = base64.urlsafe_b64decode((encoded_payload + padding).encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ProtocolDraftError("protocol_token_malformed") from exc
    if not isinstance(payload, dict):
        raise ProtocolDraftError("protocol_payload_invalid")
    return payload


def _validate_draft_id(draft_id: str) -> str:
    if not isinstance(draft_id, str) or not _DRAFT_ID.fullmatch(draft_id):
        raise ProtocolDraftError("protocol_draft_token_mismatch")
    return draft_id


def _draft_directory(root: Path, draft_id: str) -> Path:
    safe_draft_id = _validate_draft_id(draft_id)
    root_resolved = root.resolve()
    candidate = (root_resolved / safe_draft_id).resolve()
    if candidate.parent != root_resolved:
        raise ProtocolDraftError("protocol_draft_token_mismatch")
    return candidate


def _normalize_dossier(dossier: dict[str, object]) -> dict[str, object]:
    try:
        normalized = json.loads(
            json.dumps(dossier, ensure_ascii=False, sort_keys=True, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolDraftError("protocol_payload_invalid") from exc
    if not isinstance(normalized, dict):
        raise ProtocolDraftError("protocol_payload_invalid")
    return normalized


def _record_payload(record: ProtocolDraftRecord) -> dict[str, object]:
    return {
        "schema_version": 1,
        "draft_id": record.draft_id,
        "protocol_version": record.protocol_version,
        "manifest_id": record.manifest_id,
        "dataset_id": record.dataset_id,
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "dossier": record.dossier,
    }


def _record_from_payload(payload: object) -> ProtocolDraftRecord:
    if not isinstance(payload, dict):
        raise ProtocolDraftError("protocol_draft_token_mismatch")
    if payload.get("schema_version") != 1 or payload.get("protocol_version") != 1:
        raise ProtocolDraftError("protocol_draft_token_mismatch")
    draft_id = payload.get("draft_id")
    manifest_id = payload.get("manifest_id")
    dataset_id = payload.get("dataset_id")
    created_at = payload.get("created_at")
    expires_at = payload.get("expires_at")
    dossier = payload.get("dossier")
    if (
        not isinstance(draft_id, str)
        or not isinstance(manifest_id, str)
        or not isinstance(dataset_id, str)
        or not isinstance(created_at, int)
        or not isinstance(expires_at, int)
        or not isinstance(dossier, dict)
    ):
        raise ProtocolDraftError("protocol_draft_token_mismatch")
    _validate_draft_id(draft_id)
    return ProtocolDraftRecord(
        draft_id=draft_id,
        protocol_version=1,
        manifest_id=manifest_id,
        dataset_id=dataset_id,
        created_at=created_at,
        expires_at=expires_at,
        dossier=dossier,
    )


def _match_existing_or_raise(
    existing: ProtocolDraftRecord, expected: ProtocolDraftRecord, *, now: int
) -> ProtocolDraftRecord:
    if now >= existing.expires_at:
        raise ProtocolDraftError("protocol_draft_expired")
    if _canonical_record_identity(existing) != _canonical_record_identity(expected):
        raise ProtocolDraftError("protocol_draft_token_mismatch")
    return existing


def _canonical_record_identity(record: ProtocolDraftRecord) -> str:
    return json.dumps(
        {
            "draft_id": record.draft_id,
            "protocol_version": record.protocol_version,
            "manifest_id": record.manifest_id,
            "dataset_id": record.dataset_id,
            "dossier": record.dossier,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode(
        "utf-8"
    )
    handle = None
    try:
        handle = open(temporary, "xb")
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temporary, path)
    finally:
        if handle is not None:
            handle.close()
        if temporary.exists():
            temporary.unlink()
