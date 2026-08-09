import pytest

from repro_runner.dossier import normalize_metric_name, parse_reported_value


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("AUC", "roc_auc"),
        ("ROC AUC", "roc_auc"),
        ("roc-auc", "roc_auc"),
        ("roc_auc", "roc_auc"),
        ("  Accuracy  ", "accuracy"),
        ("Balanced Accuracy", "balanced_accuracy"),
        ("balanced-accuracy", "balanced_accuracy"),
        ("PRECISION", "precision"),
        (" recall ", "recall"),
        ("F1", "f1"),
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
