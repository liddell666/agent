from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
