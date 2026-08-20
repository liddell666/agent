import json

import pytest

from repro_runner.dossier import normalize_metric_name, parse_dossier, parse_reported_value
from repro_runner.schemas import DossierMetric


def _dossier(metrics: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {
            "title": "Minimal Paper",
            "research_problem": "Binary classification.",
            "task_type": "classification",
            "datasets": [],
            "methods": [],
            "metrics": metrics,
            "gaps": [],
        }
    ).encode()


def _evidence() -> list[dict[str, object]]:
    return [
        {
            "page": 8,
            "source_text": "The test metric was reported in the paper.",
            "source": "paper",
            "confidence": 1.0,
        }
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("AUC", "roc_auc"),
        ("ROC AUC", "roc_auc"),
        ("roc-auc", "roc_auc"),
        ("roc_auc", "roc_auc"),
        ("AUC（随机森林最优模型）", "roc_auc"),
        ("AUC (test set)", "roc_auc"),
        ("  Accuracy  ", "accuracy"),
        ("Balanced Accuracy", "balanced_accuracy"),
        ("balanced-accuracy", "balanced_accuracy"),
        ("PRECISION", "precision"),
        (" recall ", "recall"),
        ("F1", "f1"),
        ("总精度（阈值0.5，随机森林）", "accuracy"),
        ("精确率（随机森林）", "precision"),
        ("召回率", "recall"),
        ("F1值", "f1"),
    ],
)
def test_normalize_metric_name(name, expected):
    assert normalize_metric_name(name) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.91234567, 0.912346),
        ("0.91234567", 0.912346),
        (91, 91.0),
        ("91", 91.0),
        ("91%", 0.91),
        ("91锛卄", 0.91),
        (None, None),
        (True, None),
        (False, None),
        ("", None),
        ("   ", None),
        ("0.8-0.9", None),
        ("NaN", None),
        (float("nan"), None),
        ("inf", None),
        (float("inf"), None),
    ],
)
def test_parse_reported_value(value, expected):
    assert parse_reported_value(value) == expected


def test_parse_dossier_preserves_evidence_and_converts_percent():
    response = parse_dossier(
        "paper.json",
        _dossier(
            [
                {
                    "name": "AUC",
                    "reported_value": "91%",
                    "dataset": "test",
                    "split": "test",
                    "evidence": _evidence(),
                }
            ]
        ),
    )

    assert response.valid is True
    metric = response.metrics[0]
    assert metric.normalized_name == "roc_auc"
    assert metric.reported_value == 0.91
    assert metric.source == "paper_dossier"
    assert metric.evidence[0].model_dump() == _evidence()[0]


def test_dossier_metric_schema_requires_supported_flag():
    schema = DossierMetric.model_json_schema()

    assert schema["properties"]["supported"]["type"] == "boolean"
    assert "supported" in schema["required"]


def test_parse_dossier_marks_supported_metrics_and_preserves_unsupported_metrics():
    response = parse_dossier(
        "paper.json",
        _dossier(
            [
                {
                    "name": "AUC",
                    "reported_value": 0.91,
                    "evidence": _evidence(),
                },
                {
                    "name": "Matthews correlation coefficient",
                    "reported_value": 0.72,
                    "evidence": _evidence(),
                },
            ]
        ),
    )

    assert [metric.normalized_name for metric in response.metrics] == [
        "roc_auc",
        "matthews_correlation_coefficient",
    ]
    assert [metric.supported for metric in response.metrics] == [True, False]
    assert response.metrics[1].reported_value == 0.72
    assert response.metrics[1].evidence[0].source_text


def test_parse_dossier_supports_qualified_auc_display_names() -> None:
    response = parse_dossier(
        "paper.json",
        _dossier(
            [
                {
                    "name": "AUC（随机森林最优模型）",
                    "reported_value": 0.91,
                    "evidence": _evidence(),
                }
            ]
        ),
    )

    metric = response.metrics[0]
    assert metric.normalized_name == "roc_auc"
    assert metric.supported is True


def test_parse_dossier_extracts_model_and_threshold_without_cross_row_ambiguity() -> None:
    response = parse_dossier(
        "paper.json",
        _dossier(
            [
                {
                    "name": "AUC（随机森林最优模型）",
                    "reported_value": 0.91,
                    "evidence": _evidence(),
                },
                {
                    "name": "AUC（神经网络最优模型）",
                    "reported_value": 0.88,
                    "evidence": _evidence(),
                },
                {
                    "name": "总精度（阈值0.5，随机森林）",
                    "reported_value": 0.951,
                    "evidence": _evidence(),
                },
                {
                    "name": "总精度（阈值0.25，随机森林）",
                    "reported_value": 0.968,
                    "evidence": _evidence(),
                },
                {
                    "name": "AUC（全模型范围）",
                    "reported_value": "0.81～0.91",
                    "evidence": _evidence(),
                },
            ]
        ),
    )

    assert response.valid is True
    assert [metric.model for metric in response.metrics[:4]] == [
        "random_forest",
        "mlp",
        "random_forest",
        "random_forest",
    ]
    assert [metric.threshold for metric in response.metrics[2:4]] == [0.5, 0.25]
    assert [metric.ambiguous for metric in response.metrics[:4]] == [
        False,
        False,
        False,
        False,
    ]
    assert response.metrics[-1].reported_value is None


def test_override_replaces_unique_metric_and_preserves_unprovided_fields():
    response = parse_dossier(
        "paper.json",
        _dossier(
            [
                {
                    "name": "AUC",
                    "reported_value": 0.88,
                    "dataset": "benchmark-a",
                    "split": "validation",
                    "evidence": _evidence(),
                }
            ]
        ),
        json.dumps(
            [{"name": "roc_auc", "reported_value": 0.91, "split": "test"}]
        ),
    )

    metric = response.metrics[0]
    assert metric.reported_value == 0.91
    assert metric.split == "test"
    assert metric.dataset == "benchmark-a"
    assert metric.source == "manual_override"


def test_duplicate_metric_without_unique_override_is_ambiguous():
    response = parse_dossier(
        "paper.json",
        _dossier(
            [
                {
                    "name": "AUC",
                    "reported_value": 0.88,
                    "dataset": "A",
                    "evidence": _evidence(),
                },
                {
                    "name": "ROC AUC",
                    "reported_value": 0.91,
                    "dataset": "B",
                    "evidence": _evidence(),
                },
            ]
        ),
    )

    assert [metric.ambiguous for metric in response.metrics] == [True, True]
    assert response.warnings == ["ambiguous_metric"]


def test_override_with_dataset_narrowing_selects_only_one_duplicate():
    response = parse_dossier(
        "paper.json",
        _dossier(
            [
                {
                    "name": "AUC",
                    "reported_value": 0.88,
                    "dataset": "A",
                    "evidence": _evidence(),
                },
                {
                    "name": "ROC AUC",
                    "reported_value": 0.91,
                    "dataset": "B",
                    "evidence": _evidence(),
                },
            ]
        ),
        json.dumps([{"name": "AUC", "dataset": "A"}]),
    )

    assert [metric.ambiguous for metric in response.metrics] == [False, True]
    assert [metric.source for metric in response.metrics] == [
        "manual_override",
        "paper_dossier",
    ]
    assert response.warnings == ["ambiguous_metric"]


def test_duplicate_override_applies_all_selectors_before_selecting_metric():
    response = parse_dossier(
        "paper.json",
        _dossier(
            [
                {
                    "name": "AUC",
                    "reported_value": 0.88,
                    "dataset": "A",
                    "split": "test",
                    "evidence": _evidence(),
                },
                {
                    "name": "ROC AUC",
                    "reported_value": 0.91,
                    "dataset": "B",
                    "split": "train",
                    "evidence": _evidence(),
                },
            ]
        ),
        json.dumps([{"name": "AUC", "dataset": "A", "split": "train"}]),
    )

    assert [metric.ambiguous for metric in response.metrics] == [True, True]
    assert [metric.source for metric in response.metrics] == [
        "paper_dossier",
        "paper_dossier",
    ]
    assert response.metrics[0].split == "test"
    assert response.warnings == ["ambiguous_metric"]


@pytest.mark.parametrize(
    ("filename", "content", "overrides", "code"),
    [
        ("paper.txt", b"{}", "[]", "invalid_dossier_extension"),
        ("paper.json", b"\xff", "[]", "invalid_dossier_encoding"),
        ("paper.json", b"{", "[]", "invalid_dossier_json"),
        ("paper.json", b"{}", "[]", "invalid_dossier_schema"),
        ("paper.json", _dossier([]), "[]", "no_reported_metrics"),
        (
            "paper.json",
            _dossier([{"name": "AUC", "evidence": []}]),
            "{}",
            "invalid_metric_overrides",
        ),
    ],
)
def test_parse_dossier_returns_safe_validation_errors(
    filename, content, overrides, code
):
    response = parse_dossier(filename, content, overrides)

    assert response.valid is False
    assert response.errors[0].code == code
