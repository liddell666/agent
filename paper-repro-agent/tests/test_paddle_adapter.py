from paper_parser import paddle_adapter


def test_elements_from_result_uses_one_based_page_and_reading_order():
    result = {
        "res": {
            "page_index": 1,
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_content": "Results",
                    "block_bbox": [1, 2, 3, 4],
                    "block_order": 1,
                },
                {
                    "block_label": "formula",
                    "block_content": "AUC=0.91",
                    "block_bbox": [5, 6, 7, 8],
                    "block_order": 2,
                },
            ],
        }
    }

    elements = paddle_adapter.elements_from_result(result)

    assert [element.page for element in elements] == [2, 2]
    assert [element.kind for element in elements] == ["text", "formula"]
    assert [element.text for element in elements] == ["Results", "AUC=0.91"]
