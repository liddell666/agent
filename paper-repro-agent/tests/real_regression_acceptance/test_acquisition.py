from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest
from openpyxl import Workbook

from real_regression_acceptance.acquisition import (
    AcquisitionError,
    acquire_case,
    fetch_verified,
)
from real_regression_acceptance.models import AcceptanceCase, SourceSpec


def _digest(content: bytes) -> str:
    return "sha256:" + sha256(content).hexdigest()


def _zip(member: str, content: bytes) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr(member, content)
    return stream.getvalue()


def _xlsx() -> bytes:
    stream = BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["X", "Y"])
    for index in range(20):
        sheet.append([index, index * 1.5])
    workbook.save(stream)
    return stream.getvalue()


def _case(
    dataset_zip: bytes,
    *,
    member: str = "dataset/data.csv",
    data_format: str = "csv",
    delimiter: str = ",",
    rename_columns: dict[str, str] | None = None,
) -> AcceptanceCase:
    paper = b"%PDF-1.7\nreal paper fixture"
    return AcceptanceCase.model_validate(
        {
            "id": "energy-efficiency",
            "paper": {
                "url": "https://sources.example/paper.pdf",
                "sha256": _digest(paper),
                "media_type": "application/pdf",
                "max_bytes": 1_000_000,
            },
            "dataset": {
                "url": "https://sources.example/data.zip",
                "sha256": _digest(dataset_zip),
                "media_type": "application/zip",
                "max_bytes": 1_000_000,
            },
            "transform": {
                "member": member,
                "format": data_format,
                "delimiter": delimiter,
                "sheet_name": "Sheet1" if data_format == "xlsx" else None,
                "rename_columns": rename_columns or {},
                "drop_columns": [],
            },
            "target_column": "target",
            "task_type": "regression",
        }
    )


def _transport(paper: bytes, dataset_zip: bytes) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("paper.pdf"):
            return httpx.Response(200, content=paper, request=request)
        return httpx.Response(200, content=dataset_zip, request=request)

    return httpx.MockTransport(handler)


def test_acquire_case_verifies_sources_and_writes_canonical_csv(tmp_path: Path) -> None:
    csv_bytes = b"x,target\n" + b"".join(f"{i},{i * 2.5}\n".encode() for i in range(20))
    dataset_zip = _zip("dataset/data.csv", csv_bytes)
    case = _case(dataset_zip)
    paper = b"%PDF-1.7\nreal paper fixture"

    acquired = acquire_case(case, tmp_path, _transport(paper, dataset_zip))

    assert acquired.paper_path.read_bytes().startswith(b"%PDF-")
    assert acquired.csv_path.read_text(encoding="utf-8").splitlines()[0] == "x,target"
    assert acquired.paper_digest == case.paper.sha256
    assert acquired.dataset_digest == case.dataset.sha256
    assert acquired.row_count == 20


def test_fetch_verified_reuses_only_a_valid_cache_entry(tmp_path: Path) -> None:
    content = b"%PDF-1.7\ncache"
    source = SourceSpec(
        url="https://sources.example/paper.pdf",
        sha256=_digest(content),
        media_type="application/pdf",
        max_bytes=100,
    )
    destination = tmp_path / "paper.pdf"
    destination.write_bytes(content)

    assert fetch_verified(source, destination) == source.sha256

    destination.write_bytes(b"changed")
    with pytest.raises(AcquisitionError, match="cached_digest_mismatch"):
        fetch_verified(source, destination)


@pytest.mark.parametrize(
    ("content", "source_digest", "max_bytes", "code"),
    [
        (b"<html>error</html>", None, 100, "paper_signature_invalid"),
        (b"%PDF-1.7\nlarge", None, 4, "source_too_large"),
        (b"%PDF-1.7\nwrong", "sha256:" + "b" * 64, 100, "source_digest_mismatch"),
    ],
)
def test_fetch_verified_rejects_bad_remote_content(
    tmp_path: Path,
    content: bytes,
    source_digest: str | None,
    max_bytes: int,
    code: str,
) -> None:
    source = SourceSpec(
        url="https://sources.example/paper.pdf",
        sha256=source_digest or _digest(content),
        media_type="application/pdf",
        max_bytes=max_bytes,
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=content, request=request)
    )

    with pytest.raises(AcquisitionError, match=code):
        fetch_verified(source, tmp_path / "paper.pdf", transport)


def test_fetch_verified_rejects_redirect_to_http(tmp_path: Path) -> None:
    content = b"%PDF-1.7\nredirect"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": "http://unsafe.example/paper.pdf"})
        return httpx.Response(200, content=content, request=request)

    source = SourceSpec(
        url="https://sources.example/paper.pdf",
        sha256=_digest(content),
        media_type="application/pdf",
        max_bytes=100,
    )

    with pytest.raises(AcquisitionError, match="insecure_redirect"):
        fetch_verified(source, tmp_path / "paper.pdf", httpx.MockTransport(handler))


def test_acquire_case_rejects_missing_archive_member(tmp_path: Path) -> None:
    dataset_zip = _zip("other.csv", b"x,target\n1,2\n")
    case = _case(dataset_zip)

    with pytest.raises(AcquisitionError, match="dataset_member_missing"):
        acquire_case(
            case,
            tmp_path,
            _transport(b"%PDF-1.7\nreal paper fixture", dataset_zip),
        )


def test_acquire_case_rejects_duplicate_columns_after_rename(tmp_path: Path) -> None:
    csv_bytes = b"x,Y\n" + b"".join(f"{i},{i}\n".encode() for i in range(20))
    dataset_zip = _zip("dataset/data.csv", csv_bytes)
    case = _case(dataset_zip, rename_columns={"Y": "x"})

    with pytest.raises(AcquisitionError, match="dataset_columns_duplicate"):
        acquire_case(
            case,
            tmp_path,
            _transport(b"%PDF-1.7\nreal paper fixture", dataset_zip),
        )


def test_acquire_case_rejects_non_finite_target(tmp_path: Path) -> None:
    rows = [f"{i},{i}" for i in range(19)] + ["19,NaN"]
    dataset_zip = _zip("dataset/data.csv", ("x,target\n" + "\n".join(rows)).encode())
    case = _case(dataset_zip)

    with pytest.raises(AcquisitionError, match="dataset_target_non_finite"):
        acquire_case(
            case,
            tmp_path,
            _transport(b"%PDF-1.7\nreal paper fixture", dataset_zip),
        )


def test_acquire_case_converts_xlsx_member(tmp_path: Path) -> None:
    dataset_zip = _zip("dataset/data.xlsx", _xlsx())
    case = _case(
        dataset_zip,
        member="dataset/data.xlsx",
        data_format="xlsx",
        rename_columns={"Y": "target"},
    )

    acquired = acquire_case(
        case,
        tmp_path,
        _transport(b"%PDF-1.7\nreal paper fixture", dataset_zip),
    )

    assert acquired.row_count == 20
    assert acquired.csv_path.read_text(encoding="utf-8").startswith("X,target\n")
