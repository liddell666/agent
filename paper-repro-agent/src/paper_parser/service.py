from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from paper_parser import docling_adapter, paddle_adapter
from paper_parser.config import Settings
from paper_parser.schemas import PaperElement, ParsedPaper


class InvalidPdfError(ValueError):
    pass


class UploadTooLargeError(ValueError):
    pass


class PageLimitExceededError(ValueError):
    pass


def _text_length(elements: list[PaperElement], page: int) -> int:
    return sum(
        len("".join(element.text.split()))
        for element in elements
        if element.page == page
    )


def _merge_fallback(
    primary: list[PaperElement], fallback: list[PaperElement], pages: list[int]
) -> tuple[list[PaperElement], set[int]]:
    replaced = {
        page
        for page in pages
        if _text_length(fallback, page) > _text_length(primary, page)
    }
    merged = [element for element in primary if element.page not in replaced]
    merged.extend(element for element in fallback if element.page in replaced)
    merged.sort(key=lambda element: (element.page is None, element.page or 0))
    return merged, replaced


def _markdown_from_elements(elements: list[PaperElement]) -> str:
    pages = sorted({element.page for element in elements if element.page is not None})
    parts = []
    for page in pages:
        text = "\n\n".join(
            element.text for element in elements if element.page == page and element.text
        )
        parts.append(f"<!-- page={page} -->\n{text}")
    return "\n\n".join(parts)


def parse_pdf(content: bytes, file_name: str, settings: Settings) -> ParsedPaper:
    if not content.startswith(b"%PDF-"):
        raise InvalidPdfError("file does not have a PDF signature")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise UploadTooLargeError("PDF exceeds configured upload limit")
    try:
        page_count = len(PdfReader(BytesIO(content)).pages)
    except (PdfReadError, ValueError, OSError) as exc:
        raise InvalidPdfError("PDF structure is invalid") from exc
    if page_count > settings.max_pages:
        raise PageLimitExceededError("PDF exceeds configured page limit")

    work_dir = Path(settings.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    warnings = []
    with TemporaryDirectory(dir=work_dir) as request_dir:
        pdf_path = Path(request_dir) / "input.pdf"
        pdf_path.write_bytes(content)
        primary = docling_adapter.parse_pdf_path(pdf_path, settings.max_pages)
        fallback_pages = [
            page
            for page in range(1, page_count + 1)
            if _text_length(primary.elements, page) < 20
        ]
        limited_pages = fallback_pages[:20]
        try:
            fallback = paddle_adapter.parse_pages(pdf_path, limited_pages)
        except (ImportError, RuntimeError) as exc:
            fallback = []
            warnings.append(f"ocr_fallback_unavailable:{type(exc).__name__}")

    elements, replaced = _merge_fallback(primary.elements, fallback, limited_pages)
    warnings = list(primary.warnings) + warnings
    for page in limited_pages:
        if page not in replaced:
            warnings.append(f"low_confidence_page:{page}")
    for page in fallback_pages[20:]:
        warnings.append(f"fallback_limit_exceeded:{page}")
    markdown = _markdown_from_elements(elements) if replaced else primary.markdown
    return ParsedPaper(
        document_id=sha256(content).hexdigest(),
        file_name=file_name,
        page_count=page_count,
        markdown=markdown,
        elements=elements,
        warnings=warnings,
    )
