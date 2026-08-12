import numpy as np
import pandas as pd
import pytest

from repro_runner.preprocessing import build_column_plan, resolve_feature_columns


def test_preprocessor_fits_numeric_and_categorical_columns_inside_transformer():
    frame = pd.DataFrame(
        {
            "slope": [1.0, 2.0, 3.0, 4.0],
            "landform": ["A", "B", "A", "C"],
        }
    )

    from repro_runner.preprocessing import build_preprocessor

    preprocessor = build_preprocessor(frame, ["slope", "landform"])
    transformed = preprocessor.fit_transform(frame)

    assert transformed.shape == (4, 4)
    assert not np.isnan(transformed).any()


def test_preprocessor_ignores_unseen_categories_with_stable_width():
    frame = pd.DataFrame({"slope": [1.0, 2.0], "landform": ["A", "B"]})
    holdout = pd.DataFrame({"slope": [3.0], "landform": ["unseen"]})

    from repro_runner.preprocessing import build_preprocessor

    preprocessor = build_preprocessor(frame, ["slope", "landform"])
    fitted = preprocessor.fit(frame)

    assert fitted.transform(holdout).shape == (1, 3)


def test_preprocessor_rejects_high_cardinality_feature_without_echoing_values():
    frame = pd.DataFrame(
        {
            "comment": ["secret-alpha", "secret-beta", "secret-gamma", "secret-delta"],
            "slope": [1.0, 2.0, 3.0, 4.0],
        }
    )

    from repro_runner.preprocessing import build_preprocessor

    with pytest.raises(ValueError, match="high_cardinality_feature") as raised:
        build_preprocessor(frame, ["comment", "slope"], max_cardinality=3)

    assert "secret-alpha" not in str(raised.value)


def test_preprocessor_rejects_datetime_like_features():
    frame = pd.DataFrame(
        {
            "event_date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "slope": [1.0, 2.0, 3.0],
        }
    )

    from repro_runner.preprocessing import build_preprocessor

    with pytest.raises(ValueError, match="unsupported_feature_type"):
        build_preprocessor(frame, ["event_date", "slope"])


def test_resolve_feature_columns_uses_dataset_order_deterministically():
    columns = ["id", "age", "segment", "constant", "Y_cls"]

    resolved = resolve_feature_columns(
        columns,
        target_column="Y_cls",
        feature_columns=["segment", "age"],
        exclude_columns=["id"],
    )

    assert resolved == ["age", "segment"]


def test_build_column_plan_is_stable_across_exclude_list_order():
    columns = ["id", "age", "segment", "constant", "Y_cls"]

    first = build_column_plan(
        columns,
        target_column="Y_cls",
        exclude_columns=["constant", "id"],
    )
    second = build_column_plan(
        columns,
        target_column="Y_cls",
        exclude_columns=["id", "constant"],
    )

    assert first == second
    assert first.feature_columns == ["age", "segment"]
