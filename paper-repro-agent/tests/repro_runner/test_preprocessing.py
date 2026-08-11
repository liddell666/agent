from repro_runner.preprocessing import build_column_plan, resolve_feature_columns


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
