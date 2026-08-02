from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    source_text: str = Field(min_length=1, max_length=2000)
    source: Literal["paper", "supplement", "repository", "user", "inferred"]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def inferred_is_not_certain(self) -> "Evidence":
        if self.source == "inferred" and self.confidence >= 1.0:
            raise ValueError("inferred evidence must have confidence below 1.0")
        return self


class PaperElement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "table", "picture", "formula", "caption"]
    page: int | None = Field(default=None, ge=1)
    text: str
    bbox: tuple[float, float, float, float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceBackedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class ReportedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    reported_value: float | str | None = None
    dataset: str | None = None
    split: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def reported_value_has_evidence(self) -> "ReportedMetric":
        if self.reported_value is not None and not self.evidence:
            raise ValueError("reported_value requires evidence")
        return self


class ParsedPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    file_name: str
    page_count: int = Field(ge=1)
    markdown: str
    elements: list[PaperElement]
    warnings: list[str]


class PaperDossier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    research_problem: str
    task_type: str
    datasets: list[EvidenceBackedFact]
    methods: list[EvidenceBackedFact]
    metrics: list[ReportedMetric]
    gaps: list[str]
