from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from paper_parser.schemas import PaperElement


@dataclass(frozen=True)
class AdapterResult:
    page_count: int
    markdown: str
    elements: list[PaperElement]
    warnings: list[str]


_KIND_BY_LABEL = {
    "table": "table",
    "picture": "picture",
    "image": "picture",
    "chart": "picture",
    "formula": "formula",
    "caption": "caption",
    "figure_title": "caption",
    "table_title": "caption",
}


def _label_value(item: Any) -> str:
    label = getattr(item, "label", "text")
    return str(getattr(label, "value", label)).lower()


def _item_text(item: Any, document: Any | None = None) -> str:
    text = getattr(item, "text", None)
    if text is not None:
        return str(text)
    exporter = getattr(item, "export_to_markdown", None)
    if exporter is not None and document is not None:
        try:
            return str(exporter(doc=document))
        except TypeError:
            return str(exporter())
    return ""


def normalize_item(item: Any, document: Any | None = None) -> PaperElement:
    provenance = list(getattr(item, "prov", []) or [])
    first = provenance[0] if provenance else None
    page = getattr(first, "page_no", None)
    raw_bbox = getattr(first, "bbox", None)
    bbox = None
    if raw_bbox is not None:
        bbox = tuple(float(getattr(raw_bbox, name)) for name in ("l", "t", "r", "b"))
    label = _label_value(item)
    kind = _KIND_BY_LABEL.get(label, "text")
    return PaperElement(
        kind=kind,
        page=page,
        text=_item_text(item, document),
        bbox=bbox,
        metadata={"docling_label": label},
    )


@lru_cache(maxsize=1)
def _converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False
    pipeline_options.do_table_structure = False
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )


def parse_pdf_path(path: Path, max_pages: int) -> AdapterResult:
    result = _converter().convert(
        path,
        max_num_pages=max_pages,
        max_file_size=path.stat().st_size,
    )
    document = result.document
    elements = [normalize_item(item, document) for item, _level in document.iterate_items()]
    warnings = []
    if any(element.page is None for element in elements):
        warnings.append("element_without_page_provenance")
    return AdapterResult(
        page_count=len(document.pages),
        markdown=document.export_to_markdown(),
        elements=elements,
        warnings=warnings,
    )
