from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


DEFAULT_MAX_CARDINALITY = 5000
DEFAULT_MAX_TRANSFORMED_FEATURES = 2048
_DATETIME_TOKEN_RE = re.compile(
    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?)"
    r"|(\d{1,2}/\d{1,2}/\d{2,4})"
)


@dataclass(frozen=True, slots=True)
class FeatureGroups:
    numeric_columns: tuple[str, ...]
    categorical_columns: tuple[str, ...]
    transformed_feature_count: int


class ColumnPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_column: str
    feature_columns: list[str] = Field(default_factory=list)
    exclude_columns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_lists(self) -> "ColumnPlan":
        if len(self.feature_columns) != len(set(self.feature_columns)):
            raise ValueError("feature_columns must not contain duplicates")
        if len(self.exclude_columns) != len(set(self.exclude_columns)):
            raise ValueError("exclude_columns must not contain duplicates")
        if self.target_column in self.feature_columns:
            raise ValueError("target column cannot also be a feature column")
        return self


def resolve_feature_columns(
    columns: Sequence[str],
    *,
    target_column: str,
    feature_columns: Sequence[str] | None = None,
    exclude_columns: Sequence[str] | None = None,
) -> list[str]:
    available_columns = list(columns)
    if target_column not in available_columns:
        raise ValueError("target column must exist in the dataset")

    requested_features = list(feature_columns or [])
    requested_excludes = list(exclude_columns or [])
    if len(requested_features) != len(set(requested_features)):
        raise ValueError("feature_columns must not contain duplicates")
    if len(requested_excludes) != len(set(requested_excludes)):
        raise ValueError("exclude_columns must not contain duplicates")
    unknown_features = sorted(set(requested_features) - set(available_columns))
    if unknown_features:
        raise ValueError("feature_columns must reference known columns")
    unknown_excludes = sorted(set(requested_excludes) - set(available_columns))
    if unknown_excludes:
        raise ValueError("exclude_columns must reference known columns")
    if target_column in requested_features:
        raise ValueError("target column cannot also be a feature column")

    include_set = (
        set(requested_features)
        if requested_features
        else {column for column in available_columns if column != target_column}
    )
    exclude_set = {column for column in requested_excludes if column != target_column}

    return [
        column
        for column in available_columns
        if column != target_column and column in include_set and column not in exclude_set
    ]


def build_column_plan(
    columns: Sequence[str],
    *,
    target_column: str,
    feature_columns: Sequence[str] | None = None,
    exclude_columns: Sequence[str] | None = None,
) -> ColumnPlan:
    available_columns = list(columns)
    normalized_excludes = [
        column
        for column in available_columns
        if column != target_column and column in set(exclude_columns or [])
    ]
    return ColumnPlan(
        target_column=target_column,
        feature_columns=resolve_feature_columns(
            available_columns,
            target_column=target_column,
            feature_columns=feature_columns,
            exclude_columns=exclude_columns,
        ),
        exclude_columns=normalized_excludes,
    )


def build_preprocessor(
    frame: pd.DataFrame,
    feature_columns: Sequence[str],
    *,
    max_cardinality: int = DEFAULT_MAX_CARDINALITY,
    max_transformed_features: int = DEFAULT_MAX_TRANSFORMED_FEATURES,
) -> ColumnTransformer:
    """Build a bounded, fold-safe transformer for numeric and categorical data.

    The returned transformer is unfitted.  It may be cloned into each model
    pipeline so that imputers and category vocabularies are fitted only on the
    current training fold.
    """
    groups = classify_feature_columns(
        frame,
        feature_columns,
        max_cardinality=max_cardinality,
        max_transformed_features=max_transformed_features,
    )
    transformers: list[tuple[str, Pipeline, list[str]]] = []
    if groups.numeric_columns:
        transformers.append(
            (
                "numeric",
                Pipeline([("imputer", SimpleImputer(strategy="median"))]),
                list(groups.numeric_columns),
            )
        )
    if groups.categorical_columns:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        (
                            "onehot",
                            OneHotEncoder(
                                handle_unknown="ignore",
                                sparse_output=False,
                                dtype=float,
                            ),
                        ),
                    ]
                ),
                list(groups.categorical_columns),
            )
        )
    return ColumnTransformer(
        transformers,
        remainder="drop",
        sparse_threshold=0.0,
        verbose_feature_names_out=False,
    )


def classify_feature_columns(
    frame: pd.DataFrame,
    feature_columns: Sequence[str],
    *,
    max_cardinality: int = DEFAULT_MAX_CARDINALITY,
    max_transformed_features: int = DEFAULT_MAX_TRANSFORMED_FEATURES,
) -> FeatureGroups:
    """Return deterministic numeric/categorical groups without changing *frame*."""
    selected = list(feature_columns)
    if not selected:
        raise ValueError("missing_feature_columns")
    if len(selected) != len(set(selected)):
        raise ValueError("duplicate_feature_columns")
    unknown = [column for column in selected if column not in frame.columns]
    if unknown:
        raise ValueError("unknown_feature_columns")
    if max_cardinality < 1 or max_transformed_features < 1:
        raise ValueError("invalid_preprocessing_limits")

    numeric: list[str] = []
    categorical: list[str] = []
    transformed_count = 0
    for column in selected:
        series = frame[column]
        nonmissing = series[series.notna()]
        if nonmissing.empty:
            raise ValueError("all_missing_feature")
        if _is_numeric_column(nonmissing):
            numeric.append(column)
            transformed_count += 1
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            raise ValueError("unsupported_feature_type")
        if _looks_datetime_like(nonmissing):
            raise ValueError("unsupported_feature_type")
        unique_count = int(nonmissing.nunique(dropna=True))
        if unique_count > max_cardinality:
            raise ValueError("high_cardinality_feature")
        categorical.append(column)
        transformed_count += unique_count

    if transformed_count > max_transformed_features:
        raise ValueError("transformed_feature_limit_exceeded")
    return FeatureGroups(
        numeric_columns=tuple(numeric),
        categorical_columns=tuple(categorical),
        transformed_feature_count=transformed_count,
    )


def transformed_feature_names(preprocessor: ColumnTransformer) -> list[str]:
    """Return stable public names without transformer-internal prefixes."""
    try:
        names = preprocessor.get_feature_names_out()
    except (AttributeError, RuntimeError) as exc:
        raise ValueError("preprocessor must be fitted before naming features") from exc
    return [str(name) for name in names]


def _is_numeric_column(series: pd.Series) -> bool:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().any():
        return False
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError("non_finite_numeric_feature")
    return True


def _looks_datetime_like(series: pd.Series) -> bool:
    values = series.astype(str).str.strip()
    if not values.str.fullmatch(_DATETIME_TOKEN_RE).all():
        return False
    parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    return bool(len(parsed)) and parsed.notna().all()
