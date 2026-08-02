from types import SimpleNamespace

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
