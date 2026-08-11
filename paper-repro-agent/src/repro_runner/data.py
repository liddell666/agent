"""CSV validation, diagnostics, and privacy-preserving dataset profiling."""

from __future__ import annotations

import csv
import hashlib
import io
import math
from dataclasses import dataclass, field

import pandas as pd

from repro_runner.config import Settings
from repro_runner.preprocessing import ColumnPlan, build_column_plan
from repro_runner.schemas import (
    ColumnProfile,
    DatasetDiagnosticResponse,
    DatasetDiagnosticSummary,
    DatasetOptions,
    DatasetProfile,
    ValidationErrorItem,
)

_SNIFFER_DELIMITERS = ",\t;"
_TARGET_NAME_TOKENS = {"target", "label", "class", "outcome", "y_cls", "y"}
_ID_NAME_TOKENS = {"id", "identifier", "uuid", "guid", "index", "idx", "row"}


class DatasetError(ValueError):
    """A validation error safe to return to API clients."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ParsedCsv:
    frame: pd.DataFrame
    headers: list[str]
    delimiter: str
    dataset_id: str


@dataclass
class DatasetBundle:
    """Validated data and its pre-cleaning profile."""

    frame: pd.DataFrame
    target_column: str
    feature_columns: list[str]
    profile: DatasetProfile
    warnings: list[str] = field(default_factory=list)


def load_dataset(
    content: bytes, options: DatasetOptions, settings: Settings
) -> DatasetBundle:
    """Read a CSV, validate the numeric-only V2 contract, and return its profile."""
    parsed = _parse_csv(content, settings)
    frame = parsed.frame
    target_column = options.target_column or settings.default_target_column
    if target_column not in frame.columns:
        raise DatasetError(
            "missing_target_column", "the configured target column is missing"
        )

    plan = build_column_plan(
        list(frame.columns),
        target_column=target_column,
        feature_columns=options.feature_columns,
        exclude_columns=options.exclude_columns,
    )
    if not plan.feature_columns:
        raise DatasetError("missing_feature_columns", "at least one feature is required")

    profile_warnings: list[str] = []
    profile = profile_dataset(
        frame,
        target_column,
        profile_warnings,
        effective_rows=len(frame),
        dataset_id=parsed.dataset_id,
    )

    prepared_frame = frame.copy(deep=True)
    prepared_frame = _apply_missing_policy(prepared_frame, target_column, options)
    _validate_numeric_features(
        prepared_frame,
        plan.feature_columns,
        allow_missing=options.missing_policy == "impute",
    )
    _validate_target_classes(prepared_frame[target_column])

    if options.drop_duplicates:
        prepared_frame = prepared_frame.drop_duplicates().reset_index(drop=True)
    profile.effective_rows = len(prepared_frame)

    return DatasetBundle(
        frame=prepared_frame,
        target_column=target_column,
        feature_columns=plan.feature_columns,
        profile=profile,
        warnings=profile_warnings,
    )


def diagnose_dataset(
    content: bytes, options: DatasetOptions, settings: Settings
) -> DatasetDiagnosticResponse:
    try:
        parsed = _parse_csv(content, settings)
    except DatasetError as exc:
        return DatasetDiagnosticResponse(
            valid=False,
            errors=[ValidationErrorItem(code=exc.code, message=exc.message)],
        )

    frame = parsed.frame
    confirmed_target = _resolve_confirmed_target_column(frame, options)
    candidates = target_candidates(frame)
    columns = profile_columns(frame, confirmed_target)
    plan, plan_errors = _resolve_diagnostic_plan(frame, options, confirmed_target)
    flags = risk_flags(frame, confirmed_target)
    warnings = _diagnostic_warnings(frame, columns, flags)
    errors = _diagnostic_resource_errors(frame, columns, settings)
    errors.extend(
        _diagnostic_numeric_errors(
            frame=frame,
            target_column=confirmed_target,
        )
    )
    errors.extend(
        _diagnostic_errors(
        frame=frame,
        options=options,
        settings=settings,
        confirmed_target=confirmed_target,
        target_candidates_list=candidates,
        )
    )
    errors.extend(plan_errors)
    recommended_options = _recommended_options(
        options=options,
        confirmed_target=confirmed_target,
        target_candidates_list=candidates,
        flags=flags,
        plan=plan,
    )

    return DatasetDiagnosticResponse(
        valid=not errors,
        dataset=_diagnostic_summary(
            frame,
            columns,
            dataset_id=parsed.dataset_id,
            target_column=confirmed_target,
        ),
        columns=columns,
        target_candidates=candidates,
        risk_flags=flags,
        recommended_options=recommended_options,
        warnings=warnings,
        errors=errors,
    )


def profile_dataset(
    frame: pd.DataFrame,
    target_column: str,
    warnings: list[str],
    *,
    effective_rows: int | None = None,
    dataset_id: str = "",
) -> DatasetProfile:
    """Return aggregate metadata only; never include source rows in the profile."""
    feature_columns = [column for column in frame.columns if column != target_column]
    class_counts = _class_counts(frame[target_column])
    rows = len(frame)
    duplicate_rows = int(frame.duplicated().sum())
    if duplicate_rows and "dataset contains duplicate rows" not in warnings:
        warnings.append("dataset contains duplicate rows")

    numeric_ranges: dict[str, tuple[float, float]] = {}
    for column in feature_columns:
        if pd.api.types.is_numeric_dtype(frame[column]):
            numeric_ranges[str(column)] = (
                float(frame[column].min()),
                float(frame[column].max()),
            )

    return DatasetProfile(
        rows=rows,
        effective_rows=rows if effective_rows is None else effective_rows,
        features=len(feature_columns),
        target=target_column,
        missing_values=_dataset_missing_count(frame, target_column),
        duplicate_rows=duplicate_rows,
        class_counts=class_counts,
        class_ratios={label: count / rows for label, count in class_counts.items()},
        column_names=[str(column) for column in frame.columns],
        column_types={str(column): str(frame[column].dtype) for column in frame.columns},
        numeric_ranges=numeric_ranges,
        dataset_id=dataset_id,
    )


def profile_columns(
    frame: pd.DataFrame, target_column: str | None
) -> list[ColumnProfile]:
    candidates = set(target_candidates(frame))
    profiles: list[ColumnProfile] = []
    for column_name in frame.columns:
        series = frame[column_name]
        inferred_type = _infer_column_type(series)
        if column_name == target_column and column_name in candidates:
            inferred_type = "categorical"
        profiles.append(
            ColumnProfile(
                name=str(column_name),
                inferred_type=inferred_type,
                missing_count=_column_missing_count(
                    series, treat_target_tokens=column_name == target_column
                ),
                unique_count=_nonmissing_unique_count(
                    series, treat_target_tokens=column_name == target_column
                ),
                is_target_candidate=column_name in candidates,
                risk_flags=_column_risk_flags(
                    frame=frame,
                    column_name=str(column_name),
                    series=series,
                    inferred_type=inferred_type,
                    target_column=target_column,
                ),
            )
        )
    return profiles


def target_candidates(frame: pd.DataFrame) -> list[str]:
    candidates: list[str] = []
    for column_name in frame.columns:
        nonmissing = _nonmissing_series(frame[column_name], treat_target_tokens=True)
        unique_count = int(nonmissing.nunique(dropna=True))
        if unique_count == 2:
            candidates.append(str(column_name))
        elif _looks_target_like(str(column_name)) and 2 <= unique_count <= 10:
            candidates.append(str(column_name))
    return candidates


def risk_flags(frame: pd.DataFrame, target_column: str | None) -> list[str]:
    flags: list[str] = []
    for profile in profile_columns(frame, target_column):
        flags.extend(f"{flag}:{profile.name}" for flag in profile.risk_flags)

    if target_column is not None and target_column in frame.columns:
        target = _nonmissing_series(frame[target_column], treat_target_tokens=True)
        counts = target.value_counts(dropna=False)
        if len(counts) == 2 and counts.min() > 0:
            majority_ratio = float(counts.max()) / float(counts.sum())
            if majority_ratio >= 0.85:
                flags.append(f"severe_class_imbalance:{target_column}")
    return flags


def _parse_csv(content: bytes, settings: Settings) -> ParsedCsv:
    _validate_content_size(content, settings)
    text = _decode_text(content)
    delimiter = _detect_delimiter(text)
    headers = _validate_csv_records(text, delimiter)
    frame = _read_csv(text, delimiter)
    _validate_columns(headers, frame, settings)
    return ParsedCsv(
        frame=frame,
        headers=headers,
        delimiter=delimiter,
        dataset_id=_dataset_id(content),
    )


def _validate_content_size(content: bytes, settings: Settings) -> None:
    if not content:
        raise DatasetError("empty_file", "CSV content is empty")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise DatasetError(
            "file_too_large", "CSV content exceeds the configured size limit"
        )


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DatasetError("invalid_encoding", "CSV must be UTF-8 encoded") from exc


def _detect_delimiter(text: str) -> str:
    sample = text[:4096]
    header_line = text.splitlines()[0] if text.splitlines() else ""
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=_SNIFFER_DELIMITERS)
        detected = dialect.delimiter
    except csv.Error:
        detected = ","
    if detected in header_line:
        return detected

    counts = {candidate: header_line.count(candidate) for candidate in _SNIFFER_DELIMITERS}
    best_delimiter = max(counts, key=counts.get, default=",")
    return best_delimiter if counts.get(best_delimiter, 0) > 0 else detected


def _validate_csv_records(text: str, delimiter: str) -> list[str]:
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
        headers = next(reader)
        expected_width = len(headers)
        for record in reader:
            if not record:
                continue
            if len(record) != expected_width:
                raise DatasetError("invalid_csv", "CSV rows must match the header width")
        return headers
    except StopIteration as exc:
        raise DatasetError("invalid_csv", "CSV must include a valid header row") from exc
    except csv.Error as exc:
        raise DatasetError("invalid_csv", "CSV content cannot be parsed") from exc


def _read_csv(text: str, delimiter: str) -> pd.DataFrame:
    try:
        return pd.read_csv(
            io.StringIO(text),
            sep=delimiter,
            keep_default_na=False,
            na_filter=False,
        )
    except (pd.errors.EmptyDataError, pd.errors.ParserError, ValueError) as exc:
        raise DatasetError("invalid_csv", "CSV content cannot be parsed") from exc


def _validate_columns(
    headers: list[str], frame: pd.DataFrame, settings: Settings
) -> None:
    if not headers or any(not str(header).strip() for header in headers):
        raise DatasetError("missing_column_name", "all CSV columns must have names")
    if len(headers) != len(set(headers)):
        raise DatasetError("duplicate_column_name", "CSV column names must be unique")
    if len(frame.columns) > settings.max_columns:
        raise DatasetError("too_many_columns", "CSV exceeds the configured column limit")


def _apply_missing_policy(
    frame: pd.DataFrame, target_column: str, options: DatasetOptions
) -> pd.DataFrame:
    missing_values = _dataset_missing_count(frame, target_column)
    if options.missing_policy == "reject":
        if missing_values:
            raise DatasetError("missing_values", "dataset contains missing values")
        return frame

    normalized = frame.copy(deep=True)
    for column_name in normalized.columns:
        normalized[column_name] = normalized[column_name].map(
            lambda value: _normalize_missing_value(
                value,
                treat_target_tokens=column_name == target_column,
            )
        )
    if normalized[target_column].isna().any():
        raise DatasetError("missing_values", "dataset contains missing values")
    if options.missing_policy == "drop_rows":
        return normalized.dropna(axis=0).reset_index(drop=True)
    return normalized


def _validate_numeric_features(
    frame: pd.DataFrame, feature_columns: list[str], *, allow_missing: bool
) -> None:
    for column in feature_columns:
        series = frame[column]
        if any(_is_non_finite_token(value) for value in series):
            raise DatasetError(
                "non_finite_numeric_feature",
                "feature values must be finite numeric values",
            )
        try:
            numeric_values = pd.to_numeric(series, errors="raise")
        except (TypeError, ValueError) as exc:
            raise DatasetError(
                "non_numeric_feature", "all feature columns must be numeric"
            ) from exc
        if not allow_missing and numeric_values.isna().any():
            raise DatasetError("missing_values", "dataset contains missing values")
        if any(
            not math.isfinite(float(value))
            for value in numeric_values.dropna().tolist()
        ):
            raise DatasetError(
                "non_finite_numeric_feature",
                "feature values must be finite numeric values",
            )
        frame[column] = numeric_values


def _validate_target_classes(target: pd.Series) -> None:
    counts = _nonmissing_series(target, treat_target_tokens=True).value_counts(dropna=False)
    if len(counts) != 2 or (counts < 2).any():
        raise DatasetError(
            "invalid_target_classes",
            "target must contain exactly two classes with at least two rows each",
        )


def _diagnostic_summary(
    frame: pd.DataFrame,
    columns: list[ColumnProfile],
    *,
    dataset_id: str,
    target_column: str | None,
) -> DatasetDiagnosticSummary:
    rows = len(frame)
    duplicate_rows = int(frame.duplicated().sum())
    class_counts: dict[str, int] = {}
    class_ratios: dict[str, float] = {}
    if target_column is not None and target_column in frame.columns:
        class_counts = _class_counts(frame[target_column])
        if rows:
            class_ratios = {
                label: count / rows for label, count in class_counts.items()
            }

    features = len(frame.columns) - (1 if target_column in frame.columns else 0)
    numeric_ranges: dict[str, tuple[float, float]] = {}
    for column in frame.columns:
        if column == target_column:
            continue
        bounds = _finite_numeric_range(frame[column])
        if bounds is not None:
            numeric_ranges[str(column)] = bounds

    return DatasetDiagnosticSummary(
        rows=rows,
        effective_rows=rows,
        features=max(features, 0),
        target=target_column,
        missing_values=_dataset_missing_count(frame, target_column),
        duplicate_rows=duplicate_rows,
        class_counts=class_counts,
        class_ratios=class_ratios,
        column_names=[column.name for column in columns],
        column_types={column.name: column.inferred_type for column in columns},
        numeric_ranges=numeric_ranges,
        dataset_id=dataset_id,
    )


def _diagnostic_errors(
    *,
    frame: pd.DataFrame,
    options: DatasetOptions,
    settings: Settings,
    confirmed_target: str | None,
    target_candidates_list: list[str],
) -> list[ValidationErrorItem]:
    errors: list[ValidationErrorItem] = []
    if confirmed_target is None:
        if options.target_column_confirmed:
            errors.append(
                ValidationErrorItem(
                    code="missing_target_column",
                    message="the configured target column is missing",
                )
            )
        elif target_candidates_list:
            errors.append(
                ValidationErrorItem(
                    code="target_column_confirmation_required",
                    message="target column confirmation is required before running experiments",
                )
            )
        else:
            errors.append(
                ValidationErrorItem(
                    code="missing_target_column",
                    message="the configured target column is missing",
                )
            )
        return errors
    if (
        options.missing_policy == "reject"
        and _dataset_missing_count(frame, confirmed_target) > 0
    ):
        errors.append(
            ValidationErrorItem(
                code="missing_values",
                message="dataset contains missing values",
            )
        )
    try:
        _validate_target_classes(frame[confirmed_target])
    except DatasetError as exc:
        errors.append(ValidationErrorItem(code=exc.code, message=exc.message))
    return errors


def _recommended_options(
    *,
    options: DatasetOptions,
    confirmed_target: str | None,
    target_candidates_list: list[str],
    flags: list[str],
    plan: ColumnPlan | None,
) -> DatasetOptions:
    recommended_target = confirmed_target or (
        target_candidates_list[0] if target_candidates_list else options.target_column
    )
    feature_columns = [] if plan is None else list(plan.feature_columns)
    sampling_strategy = (
        "class_weight"
        if any(flag.startswith("severe_class_imbalance:") for flag in flags)
        else options.sampling_strategy
    )
    return DatasetOptions(
        target_column=recommended_target,
        target_column_confirmed=confirmed_target is not None,
        drop_duplicates=options.drop_duplicates,
        missing_policy=options.missing_policy,
        sampling_strategy=sampling_strategy,
        comparison_mode=options.comparison_mode,
        feature_columns=feature_columns,
        exclude_columns=list(options.exclude_columns),
    )


def _resolve_confirmed_target_column(
    frame: pd.DataFrame, options: DatasetOptions
) -> str | None:
    if not options.target_column_confirmed:
        return None
    if options.target_column not in frame.columns:
        return None
    return options.target_column


def _resolve_diagnostic_plan(
    frame: pd.DataFrame,
    options: DatasetOptions,
    confirmed_target: str | None,
) -> tuple[ColumnPlan | None, list[ValidationErrorItem]]:
    if confirmed_target is None:
        return None, []
    try:
        plan = build_column_plan(
            list(frame.columns),
            target_column=confirmed_target,
            feature_columns=options.feature_columns,
            exclude_columns=options.exclude_columns,
        )
    except ValueError as exc:
        message = str(exc)
        if "exclude_columns" in message:
            return None, [
                ValidationErrorItem(
                    code="invalid_exclude_columns",
                    message="exclude columns must reference known columns without duplicates",
                )
            ]
        if "feature_columns" in message:
            return None, [
                ValidationErrorItem(
                    code="invalid_feature_columns",
                    message="feature columns must reference known columns without duplicates",
                )
            ]
        return None, [
            ValidationErrorItem(
                code="missing_feature_columns",
                message="at least one feature is required",
            )
        ]
    if not plan.feature_columns:
        return None, [
            ValidationErrorItem(
                code="missing_feature_columns",
                message="at least one feature is required",
            )
        ]
    return plan, []


def _diagnostic_numeric_errors(
    *,
    frame: pd.DataFrame,
    target_column: str | None,
) -> list[ValidationErrorItem]:
    for column_name in frame.columns:
        if column_name == target_column:
            continue
        if _contains_non_finite_numeric_token(frame[column_name]):
            return [
                ValidationErrorItem(
                    code="non_finite_numeric_feature",
                    message="feature values must be finite numeric values",
                )
            ]
    return []


def _diagnostic_resource_errors(
    frame: pd.DataFrame,
    columns: list[ColumnProfile],
    settings: Settings,
) -> list[ValidationErrorItem]:
    errors: list[ValidationErrorItem] = []
    if len(frame) > settings.max_diagnostic_rows:
        errors.append(
            ValidationErrorItem(
                code="too_many_rows",
                message="dataset exceeds the configured diagnostic row limit",
            )
        )
    if any(
        column.inferred_type in {"categorical", "text"}
        and column.unique_count > settings.max_diagnostic_cardinality
        for column in columns
    ):
        errors.append(
            ValidationErrorItem(
                code="diagnostic_cardinality_limit_exceeded",
                message="one or more columns exceed the configured diagnostic cardinality limit",
            )
        )
    return errors


def _diagnostic_warnings(
    frame: pd.DataFrame, columns: list[ColumnProfile], flags: list[str]
) -> list[str]:
    warnings: list[str] = []
    if int(frame.duplicated().sum()) > 0:
        warnings.append("dataset contains duplicate rows")
    if any(flag.startswith("constant_column:") for flag in flags):
        warnings.append("one or more columns are constant")
    if any(flag.startswith("id_like_name:") for flag in flags):
        warnings.append("one or more columns look identifier-like")
    if any(flag.startswith("high_cardinality_text:") for flag in flags):
        warnings.append("one or more text columns are high-cardinality")
    if any(flag.startswith("severe_class_imbalance:") for flag in flags):
        warnings.append("target classes are severely imbalanced")
    if any(profile.is_target_candidate for profile in columns):
        warnings.append("target candidates are suggestions only until confirmed")
    return warnings


def _column_risk_flags(
    *,
    frame: pd.DataFrame,
    column_name: str,
    series: pd.Series,
    inferred_type: str,
    target_column: str | None,
) -> list[str]:
    flags: list[str] = []
    if inferred_type == "constant":
        flags.append("constant_column")
    if inferred_type == "text" and _is_high_cardinality_text(series):
        flags.append("high_cardinality_text")
    if _looks_id_like(column_name):
        flags.append("id_like_name")
    if _looks_target_derived(column_name, target_column):
        flags.append("target_derived_name")
    if _looks_row_number_series(series):
        flags.append("row_number_like")
    return flags


def _infer_column_type(series: pd.Series) -> str:
    if _nonmissing_unique_count(series, treat_target_tokens=False) <= 1:
        return "constant"
    if _coerce_finite_numeric_series(series) is not None:
        return "numeric"
    if isinstance(series.dtype, pd.CategoricalDtype):
        return "categorical"

    nonmissing = _nonmissing_series(series, treat_target_tokens=False)
    if not nonmissing.empty and _looks_datetime_like(nonmissing):
        return "datetime"

    unique_count = int(nonmissing.nunique(dropna=True))
    row_count = max(len(nonmissing), 1)
    if unique_count <= min(20, row_count // 2 + 1):
        return "categorical"
    return "text"


def _looks_datetime_like(series: pd.Series) -> bool:
    sample = series.astype(str)
    if not sample.str.fullmatch(
        r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?)|(\d{1,2}/\d{1,2}/\d{2,4})"
    ).all():
        return False
    parsed = pd.to_datetime(sample, errors="coerce")
    return bool(len(parsed)) and parsed.notna().all()


def _is_high_cardinality_text(series: pd.Series) -> bool:
    nonmissing = _nonmissing_series(series, treat_target_tokens=False)
    if len(nonmissing) < 4:
        return False
    unique_count = int(nonmissing.nunique(dropna=True))
    return unique_count >= 4 and (unique_count / len(nonmissing)) >= 0.8


def _coerce_finite_numeric_series(series: pd.Series) -> pd.Series | None:
    nonmissing = _nonmissing_series(series, treat_target_tokens=False)
    if nonmissing.empty:
        return None
    if pd.api.types.is_numeric_dtype(nonmissing):
        numeric = pd.to_numeric(nonmissing, errors="coerce")
        if numeric.isna().any():
            return None
        if not all(math.isfinite(float(value)) for value in numeric.tolist()):
            return None
        return numeric

    text_values = nonmissing.map(str)
    token_mask = text_values.map(_is_non_finite_token)
    if token_mask.any():
        remainder = text_values[~token_mask]
        if remainder.empty:
            return None
        coerced_remainder = pd.to_numeric(remainder, errors="coerce")
        if coerced_remainder.notna().all():
            return None
    numeric = pd.to_numeric(text_values, errors="coerce")
    if numeric.isna().any():
        return None
    if not all(math.isfinite(float(value)) for value in numeric.tolist()):
        return None
    return numeric


def _contains_non_finite_numeric_token(series: pd.Series) -> bool:
    nonmissing = _nonmissing_series(series, treat_target_tokens=False)
    if nonmissing.empty:
        return False
    if pd.api.types.is_numeric_dtype(nonmissing):
        numeric = pd.to_numeric(nonmissing, errors="coerce")
        return any(not math.isfinite(float(value)) for value in numeric.tolist())

    text_values = nonmissing.map(str)
    token_mask = text_values.map(_is_non_finite_token)
    if not token_mask.any():
        return False
    remainder = text_values[~token_mask]
    if remainder.empty:
        return True
    coerced_remainder = pd.to_numeric(remainder, errors="coerce")
    return coerced_remainder.notna().all()


def _finite_numeric_range(series: pd.Series) -> tuple[float, float] | None:
    numeric = _coerce_finite_numeric_series(series)
    if numeric is None or numeric.empty:
        return None
    minimum = float(numeric.min())
    maximum = float(numeric.max())
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        return None
    return (minimum, maximum)


def _looks_id_like(column_name: str) -> bool:
    normalized = column_name.strip().casefold()
    return any(token == normalized or normalized.endswith(f"_{token}") for token in _ID_NAME_TOKENS)


def _looks_target_like(column_name: str) -> bool:
    normalized = column_name.strip().casefold()
    return normalized in _TARGET_NAME_TOKENS or any(
        normalized.endswith(f"_{token}") for token in _TARGET_NAME_TOKENS
    )


def _looks_target_derived(column_name: str, target_column: str | None) -> bool:
    normalized = column_name.strip().casefold()
    if target_column and normalized == target_column.strip().casefold():
        return False
    return normalized.endswith("_score") or normalized.endswith("_pred") or normalized.endswith("_prob")


def _looks_row_number_series(series: pd.Series) -> bool:
    if not pd.api.types.is_numeric_dtype(series):
        return False
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().any():
        return False
    expected = pd.Series(range(1, len(numeric) + 1), index=numeric.index)
    return numeric.reset_index(drop=True).equals(expected.reset_index(drop=True))


def _column_missing_count(series: pd.Series, *, treat_target_tokens: bool) -> int:
    return sum(
        1
        for value in series
        if _is_missing_value(value, treat_target_tokens=treat_target_tokens)
    )


def _nonmissing_unique_count(series: pd.Series, *, treat_target_tokens: bool) -> int:
    return int(
        _nonmissing_series(series, treat_target_tokens=treat_target_tokens).nunique(
            dropna=True
        )
    )


def _dataset_missing_count(frame: pd.DataFrame, target_column: str | None) -> int:
    total = 0
    for column in frame.columns:
        total += _column_missing_count(
            frame[column], treat_target_tokens=column == target_column
        )
    return total


def _nonmissing_series(series: pd.Series, *, treat_target_tokens: bool) -> pd.Series:
    normalized = series.map(
        lambda value: _normalize_missing_value(
            value, treat_target_tokens=treat_target_tokens
        )
    )
    return normalized[normalized.notna()]


def _normalize_missing_value(value: object, *, treat_target_tokens: bool) -> object | None:
    if pd.isna(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if treat_target_tokens and stripped.casefold() in {"nan", "na", "n/a"}:
            return None
    return value


def _is_missing_value(value: object, *, treat_target_tokens: bool) -> bool:
    return _normalize_missing_value(value, treat_target_tokens=treat_target_tokens) is None


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


def _class_counts(target: pd.Series) -> dict[str, int]:
    counts = _nonmissing_series(target, treat_target_tokens=True).value_counts(sort=False)
    return {
        str(value): int(count)
        for value, count in sorted(counts.items(), key=lambda item: str(item[0]))
    }


def _dataset_id(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"
