from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
CANDIDATE_APP_ID = "17fe51d4-091f-4729-87ee-3c0a2e920918"


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: HttpUrl
    sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: Literal["application/pdf", "application/zip"]
    max_bytes: int = Field(ge=1, le=50_000_000)
    attribution: str = Field(min_length=1, max_length=500)
    rights: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_https(self) -> SourceSpec:
        if self.url.scheme != "https":
            raise ValueError("source URL must use HTTPS")
        return self


class DatasetTransform(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    member: str = Field(min_length=1, max_length=200)
    format: Literal["csv", "xls", "xlsx"]
    delimiter: Literal[",", ";"] = ","
    sheet_name: str | int | None = None
    rename_columns: dict[str, str] = Field(default_factory=dict)
    drop_columns: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reject_unsafe_member(self) -> DatasetTransform:
        member = PurePosixPath(self.member)
        if self.member.startswith(("/", "\\")) or "\\" in self.member or ".." in member.parts:
            raise ValueError("dataset archive member is unsafe")
        return self


class AcceptanceCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    paper: SourceSpec
    dataset: SourceSpec
    transform: DatasetTransform
    target_column: str = Field(min_length=1, max_length=128)
    task_type: Literal["regression"]
    paper_metric_overrides: tuple[dict[str, object], ...] = ()


class CorpusRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    candidate_app_id: Literal["17fe51d4-091f-4729-87ee-3c0a2e920918"]
    cases: tuple[AcceptanceCase, ...]


FailureCode = Literal[
    "acquisition_failed",
    "paper_parse_failed",
    "evidence_ambiguous",
    "dataset_invalid",
    "experiment_failed",
    "comparison_failed",
    "privacy_gate_failed",
    "service_unavailable",
]


class SafeCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    paper_digest: str = Field(pattern=SHA256_PATTERN)
    dataset_digest: str = Field(pattern=SHA256_PATTERN)
    paper_dataset_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    paper_test_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    workflow_run_ids: tuple[str, ...] = ()
    experiment_id: str | None = None
    task_type: Literal["regression"]
    status: Literal["succeeded", "failed"]
    failure_code: FailureCode | None = None
    metrics: dict[str, dict[str, float]] = Field(default_factory=dict)
    model_statuses: dict[str, str] = Field(default_factory=dict)
    performance_ranking: tuple[str, ...] = ()
    paper_closeness_ranking: tuple[str, ...] = ()
    strict_status: str = "not_comparable"
    approximate_status: str = "insufficient_metrics"
    strict_reason_codes: tuple[str, ...] = ()
    test_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    elapsed_seconds: float = Field(ge=0)


class CorpusEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    completed_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    false_strict_count: int = Field(ge=0)
    failure_counts: dict[str, int]
    gate_errors: tuple[str, ...]


def load_registry(path: Path, *, require_live_corpus: bool = True) -> CorpusRegistry:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    registry = CorpusRegistry.model_validate(loaded)
    ids = [case.id for case in registry.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case IDs must be unique")
    if require_live_corpus and len(ids) != 5:
        raise ValueError("live corpus must contain exactly five cases")
    return registry
