from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from pypdf import PdfReader, PdfWriter

from paper_parser.schemas import PaperElement


_KIND_BY_LABEL = {
    "table": "table",
    "formula": "formula",
    "image": "picture",
    "chart": "picture",
    "figure_title": "caption",
    "table_title": "caption",
}


def elements_from_result(
    result: dict[str, Any], page_override: int | None = None
) -> list[PaperElement]:
    payload = result.get("res", result)
    page_index = payload.get("page_index")
    page = page_override if page_override is not None else int(page_index or 0) + 1
    blocks = sorted(
        payload.get("parsing_res_list", []),
        key=lambda block: (
            block.get("block_order") is None,
            block.get("block_order") or 0,
        ),
    )
    elements = []
    for block in blocks:
        label = str(block.get("block_label", "text")).lower()
        raw_bbox = block.get("block_bbox")
        bbox = tuple(float(value) for value in raw_bbox) if raw_bbox is not None else None
        elements.append(
            PaperElement(
                kind=_KIND_BY_LABEL.get(label, "text"),
                page=page,
                text=str(block.get("block_content", "")),
                bbox=bbox,
                metadata={"paddle_label": label},
            )
        )
    return elements


@lru_cache(maxsize=1)
def _pipeline():
    from paddleocr import PPStructureV3

    return PPStructureV3(
        device="cpu",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        use_chart_recognition=False,
    )


def parse_pages(pdf_path: Path, pages: list[int]) -> list[PaperElement]:
    if not pages:
        return []
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for page in pages:
        writer.add_page(reader.pages[page - 1])

    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file:
            writer.write(temp_file)
            temp_path = Path(temp_file.name)
        elements = []
        for index, result in enumerate(_pipeline().predict(input=str(temp_path))):
            payload = result.json
            elements.extend(elements_from_result(payload, page_override=pages[index]))
        return elements
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
