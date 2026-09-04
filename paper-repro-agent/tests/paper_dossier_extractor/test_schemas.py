import pytest
from pydantic import ValidationError

from paper_dossier_extractor.schemas import (
    CandidatePage,
    CandidateSelection,
    ExtractionDiagnostics,
    ExtractionError,
    ExtractionResponse,
    FinalDossier,
    FinalEvidence,
    FinalFact,
    FinalMetric,
    OllamaCompletion,
    PageChunk,
    PartialDossier,
    PartialEvidence,
    PartialFact,
    PartialMetric,
    SourcePage,
)


def _partial_evidence(page: int = 1, text: str = "RMSE was 2.0.") -> PartialEvidence:
    return PartialEvidence(page=page, source_text=text)


def _final_evidence(page: int = 1, text: str = "RMSE was 2.0.") -> FinalEvidence:
    return FinalEvidence(page=page, source_text=text)


def _diagnostics() -> ExtractionDiagnostics:
    return ExtractionDiagnostics(
        mode="single",
        page_count=1,
        candidate_page_count=1,
        initial_chunk_count=1,
        ollama_call_count=1,
        successful_chunk_count=1,
        split_retry_count=0,
        failed_chunk_count=0,
        elapsed_seconds=0.5,
    )


def _final_dossier() -> FinalDossier:
    return FinalDossier(title="A paper", task_type="uncertain")


def test_source_page_requires_original_positive_page() -> None:
    with pytest.raises(ValidationError):
        SourcePage(page=0, text="Results: RMSE 2.0", kinds=["text"])


def test_diagnostics_forbid_source_content() -> None:
    fields = set(ExtractionDiagnostics.model_fields)
    assert fields == {
        "mode", "page_count", "candidate_page_count", "initial_chunk_count",
        "ollama_call_count", "successful_chunk_count", "split_retry_count",
        "failed_chunk_count", "elapsed_seconds", "warnings", "errors",
        "failed_page_ranges",
    }


@pytest.mark.parametrize(
    "model_type",
    [
        SourcePage,
        CandidatePage,
        CandidateSelection,
        PageChunk,
        OllamaCompletion,
        PartialEvidence,
        PartialFact,
        PartialMetric,
        PartialDossier,
        FinalEvidence,
        FinalFact,
        FinalMetric,
        FinalDossier,
        ExtractionError,
        ExtractionDiagnostics,
        ExtractionResponse,
    ],
)
def test_schema_models_are_immutable_and_forbid_extra_fields(model_type) -> None:
    assert model_type.model_config["extra"] == "forbid"
    assert model_type.model_config["frozen"] is True


def test_source_page_cannot_be_mutated_or_extended() -> None:
    page = SourcePage(page=1, text="Results")

    with pytest.raises(ValidationError):
        page.text = "Changed"
    with pytest.raises(ValidationError):
        SourcePage(page=1, text="Results", unexpected="value")


def test_page_chunk_enforces_id_page_and_byte_bounds() -> None:
    page = SourcePage(page=1, text="Results")
    valid = PageChunk(chunk_id="chunk-001.1", pages=(page,), source_bytes=8_192)
    assert valid.pages == (page,)

    with pytest.raises(ValidationError):
        PageChunk(chunk_id="chunk-1", pages=(page,), source_bytes=1)
    with pytest.raises(ValidationError):
        PageChunk(chunk_id="chunk-001", pages=(), source_bytes=1)
    with pytest.raises(ValidationError):
        PageChunk(chunk_id="chunk-001", pages=(page,), source_bytes=8_193)


def test_partial_and_final_dossier_boundaries_are_typed() -> None:
    partial_fact = PartialFact(name="Dataset", evidence=(_partial_evidence(),))
    partial_metric = PartialMetric(name="RMSE", evidence=(_partial_evidence(),))
    partial = PartialDossier(
        title="A paper",
        task_type="regression",
        datasets=(partial_fact,),
        methods=(partial_fact,),
        metrics=(partial_metric,),
    )
    assert partial.task_type == "regression"

    final_fact = FinalFact(name="Dataset", evidence=(_final_evidence(),))
    final_metric = FinalMetric(name="rmse", model="random_forest", evidence=(_final_evidence(),))
    final = FinalDossier(
        title="A paper",
        task_type="regression",
        datasets=(final_fact,),
        methods=(final_fact,),
        metrics=(final_metric,),
    )
    assert final.metrics[0].model == "random_forest"

    with pytest.raises(ValidationError):
        PartialDossier(task_type="classification")
    with pytest.raises(ValidationError):
        FinalMetric(name="accuracy", evidence=(_final_evidence(),))


def test_extraction_response_requires_dossier_exactly_for_success() -> None:
    diagnostics = _diagnostics()

    success = ExtractionResponse(
        ok=True,
        dossier=_final_dossier(),
        diagnostics=diagnostics,
    )
    failure = ExtractionResponse(ok=False, dossier=None, diagnostics=diagnostics)
    assert success.ok is True
    assert failure.dossier is None

    with pytest.raises(ValidationError):
        ExtractionResponse(ok=True, dossier=None, diagnostics=diagnostics)
    with pytest.raises(ValidationError):
        ExtractionResponse(ok=False, dossier=_final_dossier(), diagnostics=diagnostics)


def test_extraction_error_and_diagnostics_preserve_only_bounded_metadata() -> None:
    error = ExtractionError(code="qwen_chunk_truncated", page_range=(2, 5))
    diagnostics = ExtractionDiagnostics(
        mode="chunked",
        page_count=10,
        candidate_page_count=8,
        initial_chunk_count=3,
        ollama_call_count=4,
        successful_chunk_count=2,
        split_retry_count=1,
        failed_chunk_count=1,
        elapsed_seconds=12.25,
        warnings=("candidate_pages_capped",),
        errors=(error,),
        failed_page_ranges=((2, 5),),
    )

    assert diagnostics.errors == (error,)
    assert diagnostics.failed_page_ranges == ((2, 5),)
    assert "source_text" not in ExtractionDiagnostics.model_fields
