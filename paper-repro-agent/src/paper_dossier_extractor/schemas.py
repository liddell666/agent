from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourcePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    kinds: tuple[str, ...] = ()


class CandidatePage(SourcePage):
    model_config = ConfigDict(extra="forbid", frozen=True)

    priority: int = Field(ge=0)
    reasons: tuple[str, ...] = ()


class CandidateSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pages: tuple[CandidatePage, ...]
    uncapped_count: int = Field(ge=0)
    warnings: tuple[str, ...] = ()


class PageChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(pattern=r"^chunk-[0-9]{3}(?:\.[12])*$")
    pages: tuple[SourcePage, ...] = Field(min_length=1, max_length=4)
    source_bytes: int = Field(ge=1, le=8_192)


class OllamaCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    finish_reason: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)


class PartialEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(ge=1)
    source_text: str = Field(min_length=1, max_length=320)


class PartialFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    evidence: tuple[PartialEvidence, ...] = Field(min_length=1, max_length=3)


class PartialMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=100)
    reported_value: float | None = None
    dataset: str | None = Field(default=None, max_length=200)
    split: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    evidence: tuple[PartialEvidence, ...] = Field(min_length=1, max_length=3)


class PartialDossier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None = Field(default=None, max_length=500)
    title_evidence: tuple[PartialEvidence, ...] = Field(default=(), max_length=1)
    research_problem: str | None = Field(default=None, max_length=1_000)
    task_type: Literal["regression", "uncertain"] = "uncertain"
    task_evidence: tuple[PartialEvidence, ...] = Field(default=(), max_length=2)
    datasets: tuple[PartialFact, ...] = Field(default=(), max_length=12)
    methods: tuple[PartialFact, ...] = Field(default=(), max_length=20)
    metrics: tuple[PartialMetric, ...] = Field(default=(), max_length=40)
    gaps: tuple[str, ...] = Field(default=(), max_length=20)


class FinalEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(ge=1)
    source_text: str = Field(min_length=1, max_length=320)
    source: Literal["paper"] = "paper"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class FinalFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    evidence: tuple[FinalEvidence, ...] = Field(min_length=1)


class FinalMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["mae", "rmse", "r2"]
    reported_value: float | None = None
    model: Literal[
        "linear_regression", "random_forest", "gradient_boosting", "xgboost"
    ] | None = None
    dataset: str | None = Field(default=None, max_length=200)
    split: str | None = Field(default=None, max_length=100)
    evidence: tuple[FinalEvidence, ...] = Field(min_length=1)


class FinalDossier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=500)
    research_problem: str = Field(default="", max_length=1_000)
    task_type: Literal["regression", "uncertain"]
    datasets: tuple[FinalFact, ...] = ()
    methods: tuple[FinalFact, ...] = ()
    metrics: tuple[FinalMetric, ...] = ()
    gaps: tuple[str, ...] = ()


class ExtractionError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    page_range: tuple[int, int] | None


class ExtractionDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["single", "chunked"]
    page_count: int = Field(ge=1)
    candidate_page_count: int = Field(ge=0)
    initial_chunk_count: int = Field(ge=0)
    ollama_call_count: int = Field(ge=0)
    successful_chunk_count: int = Field(ge=0)
    split_retry_count: int = Field(ge=0)
    failed_chunk_count: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0.0)
    warnings: tuple[str, ...] = ()
    errors: tuple[ExtractionError, ...] = ()
    failed_page_ranges: tuple[tuple[int, int], ...] = ()


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    dossier: FinalDossier | None
    diagnostics: ExtractionDiagnostics

    @model_validator(mode="after")
    def dossier_matches_status(self) -> "ExtractionResponse":
        if self.ok != (self.dossier is not None):
            raise ValueError("dossier must be present exactly when extraction succeeds")
        return self
