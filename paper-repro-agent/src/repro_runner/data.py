"""CSV validation and privacy-preserving dataset profiling for V2 experiments."""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field

import pandas as pd

from repro_runner.config import Settings
from repro_runner.schemas import DatasetOptions, DatasetProfile


class DatasetError(ValueError):
    """A validation error safe to return to API clients."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class DatasetBundle:
    """Validated data and its pre-cleaning profile.

    ``frame`` may have duplicate rows removed when requested.  ``profile`` is
    intentionally calculated before that optional cleanup so callers can see
    the quality of the supplied data without retaining any source rows.
    """

    frame: pd.DataFrame
    target_column: str
    feature_columns: list[str]
    profile: DatasetProfile
    warnings: list[str] = field(default_factory=list)


def load_dataset(
    content: bytes, options: DatasetOptions, settings: Settings
) -> DatasetBundle:
    """Read an UTF-8 CSV, validate the V2 contract, and return its profile."""
    _validate_content_size(content, settings)
    headers = _validate_csv_records(content)
    frame = _read_csv(content)
    _validate_columns(headers, frame, settings)

    target_column = options.target_column or settings.default_target_column
    if target_column not in frame.columns:
        raise DatasetError(
            "missing_target_column", "the configured target column is missing"
        )

    if len(frame.columns) == 1:
        raise DatasetError("missing_feature_columns", "at least one feature is required")

    missing_values = _missing_value_count(frame)
    if missing_values:
        raise DatasetError("missing_values", "dataset contains missing values")

    feature_columns = [column for column in frame.columns if column != target_column]
    _validate_numeric_features(frame, feature_columns)
    _validate_target_classes(frame[target_column])

    warnings: list[str] = []
    profile = profile_dataset(frame, target_column, warnings)
    prepared_frame = frame.drop_duplicates().reset_index(drop=True) if options.drop_duplicates else frame

    return DatasetBundle(
        frame=prepared_frame,
        target_column=target_column,
        feature_columns=feature_columns,
        profile=profile,
        warnings=warnings,
    )


def profile_dataset(
    frame: pd.DataFrame, target_column: str, warnings: list[str]
) -> DatasetProfile:
    """Return aggregate metadata only; never include source rows in the profile."""
    feature_columns = [column for column in frame.columns if column != target_column]
    class_counts = _class_counts(frame[target_column])
    rows = len(frame)
    duplicate_rows = int(frame.duplicated().sum())
    if duplicate_rows and "dataset contains duplicate rows" not in warnings:
        warnings.append("dataset contains duplicate rows")

    return DatasetProfile(
        rows=rows,
        features=len(feature_columns),
        target=target_column,
        missing_values=_missing_value_count(frame),
        duplicate_rows=duplicate_rows,
        class_counts=class_counts,
        class_ratios={label: count / rows for label, count in class_counts.items()},
        column_names=[str(column) for column in frame.columns],
        column_types={str(column): str(frame[column].dtype) for column in frame.columns},
        numeric_ranges={
            str(column): (float(frame[column].min()), float(frame[column].max()))
            for column in feature_columns
        },
    )


def _validate_content_size(content: bytes, settings: Settings) -> None:
    if not content:
        raise DatasetError("empty_file", "CSV content is empty")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise DatasetError("file_too_large", "CSV content exceeds the configured size limit")


def _read_csv(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_csv(io.BytesIO(content), encoding="utf-8", keep_default_na=False)
    except UnicodeDecodeError as exc:
        raise DatasetError("invalid_encoding", "CSV must be UTF-8 encoded") from exc
    except (pd.errors.EmptyDataError, pd.errors.ParserError, ValueError) as exc:
        raise DatasetError("invalid_csv", "CSV content cannot be parsed") from exc


def _validate_columns(
    headers: list[str], frame: pd.DataFrame, settings: Settings
) -> None:
    if not headers or any(not header.strip() for header in headers):
        raise DatasetError("missing_column_name", "all CSV columns must have names")
    if len(headers) != len(set(headers)):
        raise DatasetError("duplicate_column_name", "CSV column names must be unique")
    if len(frame.columns) > settings.max_columns:
        raise DatasetError("too_many_columns", "CSV exceeds the configured column limit")


def _validate_csv_records(content: bytes) -> list[str]:
    try:
        records = csv.reader(io.StringIO(content.decode("utf-8")), strict=True)
        headers = next(records)
        expected_width = len(headers)
        for record in records:
            if len(record) != expected_width:
                raise DatasetError("invalid_csv", "CSV rows must match the header width")
        return headers
    except UnicodeDecodeError as exc:
        raise DatasetError("invalid_encoding", "CSV must be UTF-8 encoded") from exc
    except (StopIteration, csv.Error) as exc:
        raise DatasetError("invalid_csv", "CSV must include a valid header row") from exc


def _validate_numeric_features(frame: pd.DataFrame, feature_columns: list[str]) -> None:
    for column in feature_columns:
        try:
            numeric_values = pd.to_numeric(frame[column], errors="raise")
        except (TypeError, ValueError) as exc:
            if any(_is_non_finite_token(value) for value in frame[column]):
                raise DatasetError(
                    "non_finite_numeric_feature",
                    "feature values must be finite numeric values",
                ) from exc
            raise DatasetError(
                "non_numeric_feature", "all feature columns must be numeric"
            ) from exc
        if not all(math.isfinite(float(value)) for value in numeric_values):
            raise DatasetError(
                "non_finite_numeric_feature",
                "feature values must be finite numeric values",
            )
        frame[column] = numeric_values


def _validate_target_classes(target: pd.Series) -> None:
    class_counts = target.value_counts(dropna=False)
    if len(class_counts) != 2 or (class_counts < 2).any():
        raise DatasetError(
            "invalid_target_classes",
            "target must contain exactly two classes with at least two rows each",
        )


def _class_counts(target: pd.Series) -> dict[str, int]:
    counts = target.value_counts(sort=False)
    return {
        _class_label(value): int(count)
        for value, count in sorted(counts.items(), key=lambda item: _class_label(item[0]))
    }


def _class_label(value: object) -> str:
    return str(value)


def _missing_value_count(frame: pd.DataFrame) -> int:
    return sum(
        int(pd.isna(value))
        or int(isinstance(value, str) and not value.strip())
        for value in frame.to_numpy().flat
    )


def _is_non_finite_token(value: object) -> bool:
    return isinstance(value, str) and value.strip().casefold() in {
        "nan",
        "+nan",
        "-nan",
        "inf",
        "+inf",
        "-inf",
        "infinity",
        "+infinity",
        "-infinity",
    }
