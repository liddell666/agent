from pathlib import Path

import pytest

from repro_runner.config import Settings
from repro_runner.data import DatasetError, load_dataset
from repro_runner.schemas import DatasetOptions, ExperimentConfig


CSV = Path(r"E:\论文复现\成果\2training_samples_15180.csv")


def test_defaults_match_v2_contract():
    settings = Settings()
    config = ExperimentConfig()

    assert settings.max_upload_mb == 100
    assert settings.max_columns == 256
    assert settings.max_concurrent_experiments == 1
    assert config.model == "random_forest"
    assert config.test_size == 0.2
    assert config.random_state == 42


def test_supplied_dataset_profile():
    if not CSV.exists():
        pytest.skip("supplied CSV is only available on the local Windows host")

    bundle = load_dataset(CSV.read_bytes(), DatasetOptions(), Settings())

    assert bundle.profile.rows == 15180
    assert bundle.profile.features == 16
    assert bundle.profile.missing_values == 0
    assert bundle.profile.duplicate_rows == 66
    assert bundle.profile.class_counts == {"0": 13800, "1": 1380}
    assert bundle.profile.effective_rows == 15180
    assert bundle.profile.dataset_id.startswith("sha256:")


def test_missing_target_is_rejected():
    content = b"x,y\n1,0\n2,1\n"

    with pytest.raises(DatasetError, match="target") as raised:
        load_dataset(content, DatasetOptions(), Settings())

    assert raised.value.code == "missing_target_column"


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"", "empty_file"),
        (b"x,Y_cls\n1,0\n2,0\n", "invalid_target_classes"),
        (b"x,Y_cls\n1,0\n,1\n", "missing_values"),
        (b"x,Y_cls\na,0\n2,1\n", "non_numeric_feature"),
    ],
)
def test_invalid_dataset_content_is_rejected(content, code):
    with pytest.raises(DatasetError) as raised:
        load_dataset(content, DatasetOptions(), Settings())

    assert raised.value.code == code


def test_duplicate_rows_add_warning_before_optional_cleanup():
    content = b"x,Y_cls\n1,0\n1,0\n2,0\n3,1\n3,1\n4,1\n"

    bundle = load_dataset(content, DatasetOptions(drop_duplicates=True), Settings())

    assert bundle.profile.rows == 6
    assert bundle.profile.duplicate_rows == 2
    assert bundle.profile.effective_rows == 4
    assert bundle.warnings == ["dataset contains duplicate rows"]
    assert len(bundle.frame) == 4


def test_misaligned_record_width_is_rejected():
    content = b"x,Y_cls\n1,0,unexpected\n2,1,unexpected\n"

    with pytest.raises(DatasetError) as raised:
        load_dataset(content, DatasetOptions(), Settings())

    assert raised.value.code == "invalid_csv"


def test_invalid_utf8_is_rejected():
    content = b"x,Y_cls\n\xff,0\n2,1\n"

    with pytest.raises(DatasetError) as raised:
        load_dataset(content, DatasetOptions(), Settings())

    assert raised.value.code == "invalid_encoding"


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"x,x,Y_cls\n1,2,0\n3,4,1\n", "duplicate_column_name"),
        (b"x,,Y_cls\n1,2,0\n3,4,1\n", "missing_column_name"),
    ],
)
def test_invalid_headers_are_rejected(content, code):
    with pytest.raises(DatasetError) as raised:
        load_dataset(content, DatasetOptions(), Settings())

    assert raised.value.code == code


def test_excess_columns_are_rejected():
    headers = [f"x{index}" for index in range(256)] + ["Y_cls"]
    row_zero = ["0"] * 256 + ["0"]
    row_one = ["1"] * 256 + ["1"]
    content = "\n".join(",".join(row) for row in (headers, row_zero, row_one)).encode()

    with pytest.raises(DatasetError) as raised:
        load_dataset(content, DatasetOptions(), Settings())

    assert raised.value.code == "too_many_columns"


@pytest.mark.parametrize("value", [b"Infinity", b"-Infinity", b"NaN"])
def test_non_finite_feature_values_are_rejected(value):
    content = b"x,Y_cls\n" + value + b",0\n1,0\n2,1\n3,1\n"

    with pytest.raises(DatasetError) as raised:
        load_dataset(content, DatasetOptions(), Settings())

    assert raised.value.code == "non_finite_numeric_feature"


@pytest.mark.parametrize("value", [b"NaN", b"NA", b"N/A"])
def test_conventional_missing_target_values_are_rejected(value):
    content = b"x,Y_cls\n1," + value + b"\n2,0\n3,0\n"

    with pytest.raises(DatasetError) as raised:
        load_dataset(content, DatasetOptions(), Settings())

    assert raised.value.code == "missing_values"


def test_dataset_identity_is_a_stable_content_digest():
    content = b"x,Y_cls\n1,0\n2,0\n3,1\n4,1\n"

    first = load_dataset(content, DatasetOptions(), Settings())
    second = load_dataset(content, DatasetOptions(), Settings())
    changed = load_dataset(
        b"x,Y_cls\n1,0\n2,0\n3,1\n5,1\n", DatasetOptions(), Settings()
    )

    assert first.profile.dataset_id == second.profile.dataset_id
    assert first.profile.dataset_id != changed.profile.dataset_id
    assert len(first.profile.dataset_id.removeprefix("sha256:")) == 64
