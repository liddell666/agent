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
    effective_rows: int = Field(ge=0)
    features: int = Field(ge=0)
    target: str
    missing_values: int = Field(ge=0)
    duplicate_rows: int = Field(ge=0)
    class_counts: dict[str, int] = Field(default_factory=dict)
    class_ratios: dict[str, float] = Field(default_factory=dict)
    column_names: list[str] = Field(default_factory=list)
    column_types: dict[str, str] = Field(default_factory=dict)
    numeric_ranges: dict[str, tuple[float, float]] = Field(default_factory=dict)
    dataset_id: str


class ValidationErrorItem(BaseModel):
    """A safe, structured reason that a CSV cannot be used for V2 training."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    dataset: DatasetProfile | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[ValidationErrorItem] = Field(default_factory=list)


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


class SplitProvenance(BaseModel):
    """Privacy-preserving identifiers for the held-out evaluation fold."""

    model_config = ConfigDict(extra="forbid")

    test_size: float = Field(ge=0.1, le=0.5)
    random_state: int = Field(ge=0)
    train_rows: int = Field(ge=1)
    test_rows: int = Field(ge=1)
    test_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ExperimentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    status: Literal["succeeded"]
    config: ExperimentConfig
    dataset: DatasetProfile
    metrics: ExperimentMetrics
    feature_importance: list[FeatureImportance]
    split_provenance: SplitProvenance
    reproducibility_status: Literal["baseline_only"] = "baseline_only"


class ReportedMetricInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    reported_value: float | str | None = None
    dataset: str | None = None
    split: str | None = None
    dataset_id: str | None = None
    test_size: float | None = Field(default=None, ge=0.1, le=0.5)
    random_state: int | None = Field(default=None, ge=0)
    train_rows: int | None = Field(default=None, ge=1)
    test_rows: int | None = Field(default=None, ge=1)
    test_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


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
