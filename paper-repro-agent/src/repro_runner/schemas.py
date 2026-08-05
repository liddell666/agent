from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DatasetOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_column: str = "Y_cls"
    drop_duplicates: bool = False


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["random_forest"] = "random_forest"
    test_size: float = Field(default=0.2, ge=0.1, le=0.5)
    random_state: int = Field(default=42, ge=0)
    drop_duplicates: bool = False


class DatasetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: int = Field(ge=0)
    features: int = Field(ge=0)
    target: str
    missing_values: int = Field(ge=0)
    duplicate_rows: int = Field(ge=0)
    class_counts: dict[str, int] = Field(default_factory=dict)
    class_ratios: dict[str, float] = Field(default_factory=dict)
    column_names: list[str] = Field(default_factory=list)
    column_types: dict[str, str] = Field(default_factory=dict)
    numeric_ranges: dict[str, tuple[float, float]] = Field(default_factory=dict)


class ValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    dataset: DatasetProfile | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ExperimentMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roc_auc: float
    accuracy: float
    balanced_accuracy: float
    precision: float
    recall: float
    f1: float
    confusion_matrix: list[list[int]]


class FeatureImportance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: str
    importance: float


class ExperimentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    status: Literal["succeeded"]
    config: ExperimentConfig
    dataset: DatasetProfile
    metrics: ExperimentMetrics
    feature_importance: list[FeatureImportance]
    reproducibility_status: Literal["baseline_only"] = "baseline_only"


class ReportedMetricInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    reported_value: float | str | None = None
    dataset: str | None = None
    split: str | None = None


class ComparisonItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    paper_value: float | None = None
    independent_value: float | None = None
    absolute_difference: float | None = None
    relative_difference: float | None = None
    comparable: bool
    reason: str | None = None


class ComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    items: list[ComparisonItem] = Field(default_factory=list)
