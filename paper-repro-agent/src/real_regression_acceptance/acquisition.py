from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path
import secrets
from zipfile import BadZipFile, ZipFile

import httpx
import pandas as pd

from .models import AcceptanceCase, SourceSpec


CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0
MAX_REDIRECTS = 5


class AcquisitionError(RuntimeError):
    """A stable source-acquisition failure without remote response content."""


@dataclass(frozen=True)
class AcquiredCase:
    paper_path: Path
    csv_path: Path
    paper_digest: str
    dataset_digest: str
    row_count: int


def _digest(content: bytes | bytearray) -> str:
    return "sha256:" + sha256(content).hexdigest()


def _validate_signature(source: SourceSpec, content: bytes | bytearray) -> None:
    if source.media_type == "application/pdf" and not content.startswith(b"%PDF-"):
        raise AcquisitionError("paper_signature_invalid")
    if source.media_type == "application/zip" and not content.startswith(b"PK"):
        raise AcquisitionError("dataset_signature_invalid")


def _atomic_write(destination: Path, content: bytes | bytearray) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{secrets.token_hex(8)}.tmp"
    )
    try:
        temporary.write_bytes(content)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def fetch_verified(
    source: SourceSpec,
    destination: Path,
    transport: httpx.BaseTransport | None = None,
) -> str:
    if destination.exists():
        content = destination.read_bytes()
        digest = _digest(content)
        if digest != source.sha256:
            raise AcquisitionError("cached_digest_mismatch")
        _validate_signature(source, content)
        return digest

    try:
        with httpx.Client(
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            transport=transport,
        ) as client:
            with client.stream("GET", str(source.url)) as response:
                response.raise_for_status()
                if response.url.scheme != "https":
                    raise AcquisitionError("insecure_redirect")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > source.max_bytes:
                        raise AcquisitionError("source_too_large")
    except AcquisitionError:
        raise
    except httpx.HTTPError as exc:
        raise AcquisitionError("source_download_failed") from exc

    digest = _digest(body)
    if digest != source.sha256:
        raise AcquisitionError("source_digest_mismatch")
    _validate_signature(source, body)
    _atomic_write(destination, body)
    return digest


def _read_member(archive_path: Path, member: str) -> bytes:
    try:
        with ZipFile(archive_path) as archive:
            info = archive.getinfo(member)
            if info.is_dir():
                raise AcquisitionError("dataset_member_missing")
            return archive.read(info)
    except KeyError as exc:
        raise AcquisitionError("dataset_member_missing") from exc
    except BadZipFile as exc:
        raise AcquisitionError("dataset_archive_invalid") from exc


def _read_frame(case: AcceptanceCase, content: bytes) -> pd.DataFrame:
    transform = case.transform
    stream = BytesIO(content)
    try:
        if transform.format == "csv":
            frame = pd.read_csv(stream, sep=transform.delimiter)
        elif transform.format == "xlsx":
            frame = pd.read_excel(
                stream,
                sheet_name=transform.sheet_name,
                engine="openpyxl",
            )
        else:
            frame = pd.read_excel(
                stream,
                sheet_name=transform.sheet_name,
                engine="xlrd",
            )
    except (ValueError, TypeError, OSError) as exc:
        raise AcquisitionError("dataset_parse_failed") from exc
    if not isinstance(frame, pd.DataFrame):
        raise AcquisitionError("dataset_parse_failed")
    return frame


def _normalize_frame(case: AcceptanceCase, frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    normalized.columns = [str(column).strip() for column in normalized.columns]
    normalized = normalized.rename(columns=case.transform.rename_columns)
    if normalized.columns.duplicated().any():
        raise AcquisitionError("dataset_columns_duplicate")
    missing_drop = [
        column for column in case.transform.drop_columns if column not in normalized.columns
    ]
    if missing_drop:
        raise AcquisitionError("dataset_drop_column_missing")
    normalized = normalized.drop(columns=list(case.transform.drop_columns))
    if case.target_column not in normalized.columns:
        raise AcquisitionError("dataset_target_missing")
    if len(normalized.index) < 20:
        raise AcquisitionError("dataset_too_small")
    target = pd.to_numeric(normalized[case.target_column], errors="coerce")
    if target.isna().any() or any(not math.isfinite(float(value)) for value in target):
        raise AcquisitionError("dataset_target_non_finite")
    normalized[case.target_column] = target
    return normalized


def _write_canonical_csv(destination: Path, frame: pd.DataFrame) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{secrets.token_hex(8)}.tmp"
    )
    try:
        frame.to_csv(temporary, index=False, encoding="utf-8", lineterminator="\n")
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def acquire_case(
    case: AcceptanceCase,
    cache_root: Path,
    transport: httpx.BaseTransport | None = None,
) -> AcquiredCase:
    case_root = cache_root / case.id
    paper_path = case_root / "paper.pdf"
    archive_path = case_root / "dataset.zip"
    csv_path = case_root / "dataset.csv"

    paper_digest = fetch_verified(case.paper, paper_path, transport)
    dataset_digest = fetch_verified(case.dataset, archive_path, transport)
    frame = _normalize_frame(
        case,
        _read_frame(case, _read_member(archive_path, case.transform.member)),
    )
    _write_canonical_csv(csv_path, frame)
    return AcquiredCase(
        paper_path=paper_path,
        csv_path=csv_path,
        paper_digest=paper_digest,
        dataset_digest=dataset_digest,
        row_count=len(frame.index),
    )
