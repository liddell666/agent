from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_parser.config import Settings
from paper_parser.schemas import PaperElement
from paper_parser import service


def fixture_bytes() -> bytes:
    return Path("tests/fixtures/minimal-paper.pdf").read_bytes()


def settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        parser_api_token="x" * 32,
        work_dir=str(tmp_path),
        **overrides,
    )


def test_parse_pdf_preserves_pages_and_text(monkeypatch, tmp_path):
    primary = SimpleNamespace(
        page_count=2,
        markdown="Dataset: TinySet\n\nAUC = 0.91",
        elements=[
            PaperElement(kind="text", page=1, text="Dataset: TinySet with 100 samples."),
            PaperElement(kind="text", page=2, text="Results: AUC = 0.91 on the test split."),
        ],
        warnings=[],
    )
    monkeypatch.setattr(service.docling_adapter, "parse_pdf_path", lambda *_args, **_kwargs: primary)
    monkeypatch.setattr(service.paddle_adapter, "parse_pages", lambda *_args, **_kwargs: [])

    result = service.parse_pdf(fixture_bytes(), "minimal-paper.pdf", settings(tmp_path))

    assert result.page_count == 2
    assert "AUC" in result.markdown
    assert {element.page for element in result.elements} == {1, 2}
    assert len(result.document_id) == 64


def test_non_pdf_signature_is_rejected(tmp_path):
    with pytest.raises(service.InvalidPdfError):
        service.parse_pdf(b"not a pdf", "paper.pdf", settings(tmp_path))


def test_file_over_limit_is_rejected_before_parsing(tmp_path):
    oversized = b"%PDF-" + (b"x" * (1024 * 1024))
    with pytest.raises(service.UploadTooLargeError):
        service.parse_pdf(oversized, "paper.pdf", settings(tmp_path, max_upload_mb=1))


def test_page_limit_is_rejected(monkeypatch, tmp_path):
    primary = SimpleNamespace(page_count=2, markdown="x", elements=[], warnings=[])
    monkeypatch.setattr(service.docling_adapter, "parse_pdf_path", lambda *_args, **_kwargs: primary)
    with pytest.raises(service.PageLimitExceededError):
        service.parse_pdf(fixture_bytes(), "paper.pdf", settings(tmp_path, max_pages=1))


def test_low_text_page_uses_better_fallback(monkeypatch, tmp_path):
    primary = SimpleNamespace(
        page_count=2,
        markdown="primary",
        elements=[
            PaperElement(kind="text", page=1, text="Enough primary text for page one."),
            PaperElement(kind="text", page=2, text="tiny"),
        ],
        warnings=[],
    )
    fallback = [
        PaperElement(kind="text", page=2, text="Recovered AUC = 0.91 from the scanned page.")
    ]
    requested_pages = []
    monkeypatch.setattr(service.docling_adapter, "parse_pdf_path", lambda *_args, **_kwargs: primary)

    def fake_fallback(_path, pages):
        requested_pages.extend(pages)
        return fallback

    monkeypatch.setattr(service.paddle_adapter, "parse_pages", fake_fallback)

    result = service.parse_pdf(fixture_bytes(), "paper.pdf", settings(tmp_path))

    assert requested_pages == [2]
    assert any("Recovered AUC" in element.text for element in result.elements)
    assert not any(element.text == "tiny" for element in result.elements)
