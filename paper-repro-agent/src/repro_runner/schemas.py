import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from repro_runner.runtime import RuntimeProvenance, is_safe_metadata_value


MissingPolicy = Literal["reject", "drop_rows", "impute"]
SamplingStrategy = Literal["original", "class_weight", "balanced_undersample"]
ComparisonMode = Literal["paper_comparable", "real_world"]
InferredColumnType = Literal["numeric", "categorical", "text", "datetime", "constant"]
TaskType = Literal["binary_classification", "regression"]


class DatasetOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_column: str = "Y_cls"
    target_column_confirmed: bool = False
    task_type: TaskType = "binary_classification"
    drop_duplicates: bool = False
    missing_policy: MissingPolicy = "reject"
    sampling_strategy: SamplingStrategy = "original"
    comparison_mode: ComparisonMode = "paper_comparable"
    feature_columns: list[str] = Field(default_factory=list)
    exclude_columns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_column_lists(self) -> "DatasetOptions":
        if self.task_type == "regression" and self.sampling_strategy != "original":
            raise ValueError("sampling_strategy must be original for regression")
        if len(self.feature_columns) != len(set(self.feature_columns)):
            raise ValueError("feature_columns must not contain duplicates")
        if len(self.exclude_columns) != len(set(self.exclude_columns)):
            raise ValueError("exclude_columns must not contain duplicates")
        return self


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


class ColumnProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    inferred_type: InferredColumnType
    missing_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)
    is_target_candidate: bool
    risk_flags: list[str] = Field(default_factory=list)


class DatasetDiagnosticSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: int = Field(ge=0)
    effective_rows: int = Field(ge=0)
    features: int = Field(ge=0)
    target: str | None = None
    missing_values: int = Field(ge=0)
    duplicate_rows: int = Field(ge=0)
    class_counts: dict[str, int] = Field(default_factory=dict)
    class_ratios: dict[str, float] = Field(default_factory=dict)
    column_names: list[str] = Field(default_factory=list)
    column_types: dict[str, str] = Field(default_factory=dict)
    numeric_ranges: dict[str, tuple[float, float]] = Field(default_factory=dict)
    dataset_id: str


class ValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    dataset: DatasetProfile | None = None
    columns: list[ColumnProfile] = Field(default_factory=list)
    target_candidates: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    recommended_options: DatasetOptions | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[ValidationErrorItem] = Field(default_factory=list)


class DatasetDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    dataset: DatasetDiagnosticSummary | None = None
    columns: list[ColumnProfile] = Field(default_factory=list)
    target_candidates: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    recommended_options: DatasetOptions | None = None
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


class RegressionMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mae: float = Field(ge=0.0)
    rmse: float = Field(ge=0.0)
    r2: float

    @model_validator(mode="after")
    def require_finite_values(self) -> "RegressionMetrics":
        if not all(math.isfinite(value) for value in (self.mae, self.rmse, self.r2)):
            raise ValueError("regression metrics must be finite")
        return self


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
    split_provenance: SplitProvenance | None = None
    reproducibility_status: Literal["baseline_only", "legacy_incomparable"] = (
        "baseline_only"
    )

    @model_validator(mode="after")
    def mark_missing_provenance_as_legacy(self) -> "ExperimentResult":
        """Keep V2 artifacts created before split provenance safely readable."""
        if self.split_provenance is None:
            self.reproducibility_status = "legacy_incomparable"
        return self


ModelName = Literal[
    "logistic_regression",
    "linear_regression",
    "random_forest",
    "gradient_boosting",
    "xgboost",
    "lightgbm",
    "svm",
    "knn",
    "mlp",
]

DEFAULT_MODEL_NAMES: tuple[ModelName, ...] = (
    "logistic_regression",
    "random_forest",
    "xgboost",
    "lightgbm",
    "svm",
    "knn",
    "mlp",
)

RegressionModelName = Literal[
    "linear_regression",
    "random_forest",
    "gradient_boosting",
    "xgboost",
]

REGRESSION_MODEL_NAMES: tuple[RegressionModelName, ...] = (
    "linear_regression",
    "random_forest",
    "gradient_boosting",
    "xgboost",
)
REGRESSION_METRIC_NAMES = ("mae", "rmse", "r2")

OptimizationMetric = Literal[
    "roc_auc",
    "f1",
    "recall",
    "balanced_accuracy",
    "mae",
    "rmse",
    "r2",
]
_CLASSIFICATION_METRIC_NAMES = ("roc_auc", "f1", "recall", "balanced_accuracy")


class ModelSuiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: TaskType = "binary_classification"
    models: list[ModelName] = Field(default_factory=lambda: list(DEFAULT_MODEL_NAMES), min_length=1)
    test_size: float = Field(default=0.2, ge=0.1, le=0.5)
    random_state: int = Field(default=42, ge=0)
    drop_duplicates: bool = False
    cv_folds: int = Field(default=5, ge=3, le=10)
    n_seeds: int = Field(default=1, ge=1, le=10)
    optimization_metric: OptimizationMetric = "roc_auc"
    threshold: float | None = Field(default=0.5, ge=0.0, le=1.0)
    n_iter: int = Field(default=8, ge=1, le=32)
    use_gpu: bool = False
    n_jobs: int = Field(default=4, ge=1, le=16)
    workflow_version: str = Field(default="unknown", min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def apply_task_defaults(cls, value: Any) -> Any:
        data = dict(value or {})
        if data.get("task_type") == "regression":
            data.setdefault("models", list(REGRESSION_MODEL_NAMES))
            data.setdefault("optimization_metric", "rmse")
            data.setdefault("threshold", None)
        return data

    @field_validator("threshold")
    @classmethod
    def reject_non_finite_threshold(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("threshold must be finite")
        return value

    @field_validator("workflow_version")
    @classmethod
    def workflow_version_must_be_safe_metadata(cls, value: str) -> str:
        if not is_safe_metadata_value(value):
            raise ValueError("workflow_version must use safe metadata characters")
        return value

    @model_validator(mode="after")
    def reject_duplicate_models(self) -> "ModelSuiteConfig":
        if len(self.models) != len(set(self.models)):
            raise ValueError("models must not contain duplicates")
        if self.task_type == "regression":
            if any(model not in REGRESSION_MODEL_NAMES for model in self.models):
                raise ValueError("model is not supported for regression")
            if self.optimization_metric not in REGRESSION_METRIC_NAMES:
                raise ValueError("metric is not supported for regression")
            if self.threshold is not None:
                raise ValueError("threshold must be null for regression")
        else:
            if any(model not in DEFAULT_MODEL_NAMES for model in self.models):
                raise ValueError("model is not supported for binary classification")
            if self.optimization_metric not in _CLASSIFICATION_METRIC_NAMES:
                raise ValueError("metric is not supported for binary classification")
            if self.threshold is None:
                raise ValueError("threshold is required for binary classification")
        return self


class ExperimentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dataset_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    task_type: TaskType = "binary_classification"
    target_column: str
    feature_columns: list[str] = Field(min_length=1)
    missing_policy: MissingPolicy
    sampling_strategy: SamplingStrategy
    comparison_mode: ComparisonMode
    test_size: float = Field(ge=0.1, le=0.5)
    random_state: int = Field(ge=0)
    cv_folds: int = Field(ge=3, le=10)
    n_seeds: int = Field(default=1, ge=1, le=10)
    optimization_metric: OptimizationMetric
    threshold: float | None = Field(ge=0.0, le=1.0)
    models: list[ModelName] = Field(min_length=1)
    dossier_id: str | None = None
    workflow_version: str = Field(default="unknown", min_length=1, max_length=128)

    @field_validator("threshold")
    @classmethod
    def manifest_threshold_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("threshold must be finite")
        return value

    @field_validator("workflow_version")
    @classmethod
    def manifest_workflow_version_must_be_safe_metadata(cls, value: str) -> str:
        if not is_safe_metadata_value(value):
            raise ValueError("workflow_version must use safe metadata characters")
        return value

    @model_validator(mode="after")
    def validate_manifest_lists(self) -> "ExperimentManifest":
        if len(self.feature_columns) != len(set(self.feature_columns)):
            raise ValueError("feature_columns must not contain duplicates")
        if len(self.models) != len(set(self.models)):
            raise ValueError("models must not contain duplicates")
        if self.task_type == "regression":
            if any(model not in REGRESSION_MODEL_NAMES for model in self.models):
                raise ValueError("model is not supported for regression")
            if self.optimization_metric not in REGRESSION_METRIC_NAMES:
                raise ValueError("metric is not supported for regression")
            if self.threshold is not None:
                raise ValueError("threshold must be null for regression")
            if self.sampling_strategy != "original":
                raise ValueError("sampling_strategy must be original for regression")
        else:
            if any(model not in DEFAULT_MODEL_NAMES for model in self.models):
                raise ValueError("model is not supported for binary classification")
            if self.optimization_metric not in _CLASSIFICATION_METRIC_NAMES:
                raise ValueError("metric is not supported for binary classification")
            if self.threshold is None:
                raise ValueError("threshold is required for binary classification")
        return self


class PreprocessingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    numeric_columns: list[str] = Field(default_factory=list)
    categorical_columns: list[str] = Field(default_factory=list)
    transformed_feature_names: list[str] = Field(default_factory=list)
    sampling_strategy: SamplingStrategy = "original"


class ModelRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelName
    status: Literal["succeeded", "unavailable", "failed"]
    cv_best_score: float | None = None
    best_params: dict[str, Any] = Field(default_factory=dict)
    metrics: ExperimentMetrics | None = None
    feature_importance: list[FeatureImportance] = Field(default_factory=list)
    fit_seconds: float | None = None
    error: ValidationErrorItem | None = None
    split_provenance: SplitProvenance | None = None
    cv_mean: dict[str, float] = Field(default_factory=dict)
    cv_std: dict[str, float] = Field(default_factory=dict)
    cv_fold_scores: dict[str, list[float]] = Field(default_factory=dict)
    seed_means: dict[str, list[float]] = Field(default_factory=dict)


class ExperimentSuiteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    status: Literal["succeeded", "partial", "failed"]
    config: ModelSuiteConfig
    task_type: TaskType = "binary_classification"
    dataset: DatasetProfile
    split_provenance: SplitProvenance
    results: list[ModelRunResult] = Field(default_factory=list)
    performance_ranking: list[ModelName] = Field(default_factory=list)
    paper_closeness_ranking: list[ModelName] = Field(default_factory=list)
    reproducibility_status: Literal["cv_tuned", "cv_evaluated"] = "cv_evaluated"
    preprocessing: PreprocessingSummary | None = None
    runtime: RuntimeProvenance | None = None

    @model_validator(mode="before")
    @classmethod
    def derive_task_type_from_config(cls, value: Any) -> Any:
        data = dict(value or {})
        if "task_type" not in data:
            config = data.get("config")
            if isinstance(config, dict):
                data["task_type"] = config.get(
                    "task_type", "binary_classification"
                )
            else:
                data["task_type"] = getattr(
                    config, "task_type", "binary_classification"
                )
        return data

    @model_validator(mode="after")
    def require_matching_config_task_type(self) -> "ExperimentSuiteResult":
        if self.task_type != self.config.task_type:
            raise ValueError("task_type must match config.task_type")
        return self


JobStatus = Literal[
    "queued",
    "running",
    "cancel_requested",
    "cancelled",
    "needs_retry",
    "succeeded",
    "partial",
    "failed",
]


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    manifest_id: str
    dataset_id: str
    status: JobStatus
    stage: str
    progress: float = Field(ge=0.0, le=1.0)
    attempt: int = Field(ge=0)
    worker_pid: int | None = None
    result_id: str | None = None
    error_code: str | None = None
    created_at: str
    updated_at: str


class JobCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    manifest_id: str
    dataset_id: str
    status: JobStatus
    stage: str
    progress: float = Field(ge=0.0, le=1.0)
    attempt: int = Field(ge=0)
    result_id: str | None = None
    error_code: str | None = None


class JobStatusResponse(JobCreateResponse):
    worker_pid: int | None = None
    created_at: str
    updated_at: str


class ReportedMetricInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    reported_value: float | str | None = None
    model: ModelName | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
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


class SuiteComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    reported_metrics: list[ReportedMetricInput] = Field(default_factory=list)


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


class SuiteComparisonItem(ComparisonItem):
    model: ModelName


class SuiteComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    items: list[SuiteComparisonItem] = Field(default_factory=list)
    paper_reference_metric: str | None = None
    paper_closeness_ranking: list[ModelName] = Field(default_factory=list)


class DossierEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    source_text: str = Field(min_length=1, max_length=2000)
    source: Literal["paper", "supplement", "repository", "user", "inferred"]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DossierMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    normalized_name: str
    supported: bool
    reported_value: float | None = None
    model: ModelName | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
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
    source: Literal["paper_dossier", "manual_override"]
    evidence: list[DossierEvidence] = Field(default_factory=list)
    ambiguous: bool = False


class DossierParseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    title: str | None = None
    metrics: list[DossierMetric] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[ValidationErrorItem] = Field(default_factory=list)


class ProtocolDraftCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str
    protocol_version: int
    manifest_id: str
    dataset_id: str
    created_at: int
    expires_at: int


class ProtocolDraftReadResponse(ProtocolDraftCreateResponse):
    dossier: dict[str, Any]
