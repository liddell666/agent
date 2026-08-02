import pytest
from pydantic import ValidationError

from paper_parser.schemas import Evidence, PaperDossier


def test_evidence_requires_page_and_quote():
    evidence = Evidence(page=3, source_text="AUC was 0.91.", source="paper")
    assert evidence.page == 3


def test_reported_fact_without_evidence_is_rejected():
    with pytest.raises(ValidationError):
        PaperDossier(
            title="Example",
            research_problem="Classification",
            task_type="machine_learning",
            datasets=[],
            methods=[],
            metrics=[{"name": "AUC", "reported_value": 0.91, "evidence": []}],
            gaps=[],
        )


def test_inferred_evidence_cannot_claim_full_confidence():
    with pytest.raises(ValidationError):
        Evidence(
            page=2,
            source_text="The framework is inferred from the diagram.",
            source="inferred",
            confidence=1.0,
        )


def test_unknown_dossier_fields_are_rejected():
    with pytest.raises(ValidationError):
        PaperDossier(
            title="Example",
            research_problem="Classification",
            task_type="machine_learning",
            datasets=[],
            methods=[],
            metrics=[],
            gaps=[],
            invented=True,
        )
