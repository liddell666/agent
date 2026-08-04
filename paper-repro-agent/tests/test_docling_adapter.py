import sys
from types import ModuleType, SimpleNamespace

from paper_parser import docling_adapter


def test_normalize_item_preserves_page_kind_and_bbox():
    item = SimpleNamespace(
        label=SimpleNamespace(value="table"),
        text="AUC | 0.91",
        prov=[
            SimpleNamespace(
                page_no=2,
                bbox=SimpleNamespace(l=10.0, t=20.0, r=30.0, b=40.0),
            )
        ],
    )

    element = docling_adapter.normalize_item(item)

    assert element.kind == "table"
    assert element.page == 2
    assert element.bbox == (10.0, 20.0, 30.0, 40.0)
    assert element.text == "AUC | 0.91"


def test_normalize_item_keeps_missing_provenance_explicit():
    item = SimpleNamespace(label=SimpleNamespace(value="text"), text="orphan", prov=[])

    element = docling_adapter.normalize_item(item)

    assert element.page is None
    assert element.bbox is None


def test_converter_disables_docling_ocr_for_separate_page_fallback(monkeypatch):
    class FakePipelineOptions:
        def __init__(self):
            self.do_ocr = True
            self.do_table_structure = True

    class FakePdfFormatOption:
        def __init__(self, pipeline_options):
            self.pipeline_options = pipeline_options

    class FakeDocumentConverter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    converter_module = ModuleType("docling.document_converter")
    converter_module.DocumentConverter = FakeDocumentConverter
    converter_module.PdfFormatOption = FakePdfFormatOption
    base_models_module = ModuleType("docling.datamodel.base_models")
    base_models_module.InputFormat = type("InputFormat", (), {"PDF": "pdf"})
    options_module = ModuleType("docling.datamodel.pipeline_options")
    options_module.PdfPipelineOptions = FakePipelineOptions
    monkeypatch.setitem(sys.modules, "docling.document_converter", converter_module)
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", base_models_module)
    monkeypatch.setitem(sys.modules, "docling.datamodel.pipeline_options", options_module)
    docling_adapter._converter.cache_clear()

    converter = docling_adapter._converter()

    pdf_options = converter.kwargs["format_options"]["pdf"].pipeline_options
    assert pdf_options.do_ocr is False
    assert pdf_options.do_table_structure is False
    docling_adapter._converter.cache_clear()
