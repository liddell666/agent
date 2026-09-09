import hashlib
import json

import pytest

from paper_parser.schemas import PaperElement, ParsedPaper

from paper_dossier_extractor.chunking import serialize_pages
from paper_dossier_extractor.config import Settings
from paper_dossier_extractor.ollama import OllamaError
from paper_dossier_extractor.schemas import OllamaCompletion, PageChunk
from paper_dossier_extractor.service import extract_dossier, extract_readiness_probe


SOURCE_SENTINEL = "SOURCE_SENTINEL"
MODEL_SENTINEL = "MODEL_SENTINEL"


class ScriptedClient:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[PageChunk] = []

    def complete(self, chunk: PageChunk) -> OllamaCompletion:
        self.calls.append(chunk)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, OllamaCompletion)
        return response


def _settings(**overrides: object) -> Settings:
    return Settings(api_token="x" * 32, **overrides)


def _completion(payload: dict[str, object]) -> OllamaCompletion:
    return OllamaCompletion(
        text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        finish_reason="stop",
        prompt_tokens=0,
        completion_tokens=0,
    )


def _paper(*page_texts: str) -> ParsedPaper:
    return ParsedPaper(
        document_id="document-id",
        file_name="paper.pdf",
        page_count=len(page_texts),
        markdown="",
        elements=[
            PaperElement(kind="text", page=page, text=text)
            for page, text in enumerate(page_texts, start=1)
        ],
        warnings=[],
    )


def _successful_partial(page: int = 1) -> dict[str, object]:
    return {
        "title": "Synthetic paper",
        "title_evidence": [{"page": page, "source_text": "Synthetic paper"}],
        "task_type": "regression",
        "task_evidence": [{"page": page, "source_text": "regression task"}],
        "metrics": [
            {
                "name": "RMSE",
                "reported_value": 2.0,
                "evidence": [{"page": page, "source_text": "RMSE was 2.0."}],
            }
        ],
    }


def test_short_paper_uses_single_mode_and_returns_a_dossier() -> None:
    paper = _paper(
        f"Synthetic paper; regression task. RMSE was 2.0. {SOURCE_SENTINEL}"
    )
    client = ScriptedClient(_completion(_successful_partial()))

    response = extract_dossier(paper, _settings(), client, clock=lambda: 10.0)

    assert response.ok is True
    assert response.dossier is not None
    assert response.dossier.title == "Synthetic paper"
    assert response.dossier.metrics[0].reported_value == 2.0
    assert response.diagnostics.mode == "single"
    assert response.diagnostics.initial_chunk_count == 1
    assert response.diagnostics.ollama_call_count == 1
    assert response.diagnostics.successful_chunk_count == 1
    assert response.diagnostics.failed_chunk_count == 0


def test_normal_service_diagnostics_keep_the_exact_public_contract() -> None:
    paper = _paper("Results: RMSE was 2.0.")
    client = ScriptedClient(_completion(_successful_partial()))

    response = extract_dossier(paper, _settings(), client, clock=lambda: 15.0)

    assert response.ok is True
    assert set(response.diagnostics.model_dump(mode="json")) == {
        "mode",
        "page_count",
        "candidate_page_count",
        "initial_chunk_count",
        "ollama_call_count",
        "successful_chunk_count",
        "split_retry_count",
        "failed_chunk_count",
        "elapsed_seconds",
        "warnings",
        "errors",
        "failed_page_ranges",
    }


def test_readiness_probe_returns_extractor_owned_metadata_and_effective_config() -> None:
    paper = _paper("Results: RMSE was 2.0.")
    settings = _settings(
        num_ctx=12_288,
        num_predict=1_024,
        max_chunk_source_bytes=4_096,
        max_ollama_calls=6,
    )
    client = ScriptedClient(
        OllamaCompletion(
            text=json.dumps(_successful_partial(), ensure_ascii=False, separators=(",", ":")),
            finish_reason="stop",
            prompt_tokens=321,
            completion_tokens=111,
        )
    )

    response = extract_readiness_probe(settings, client, clock=lambda: 15.0)
    chunk = client.calls[0]
    expected_source = serialize_pages(chunk.pages).encode("utf-8")

    assert response.status == "ready"
    assert response.service == "paper-dossier-extractor"
    assert response.model == settings.ollama_model
    assert response.source_bytes == chunk.source_bytes
    assert response.source_sha256 == hashlib.sha256(expected_source).hexdigest()
    assert response.prompt_tokens == 321
    assert response.completion_tokens == 111
    assert response.num_ctx == 12_288
    assert response.num_predict == 1_024
    assert response.max_chunk_source_bytes == 4_096
    assert response.max_ollama_calls == 6


def test_readiness_probe_fails_closed_when_completion_tokens_exceed_reserved_budget() -> None:
    settings = _settings(num_predict=128)
    client = ScriptedClient(
        OllamaCompletion(
            text=json.dumps(_successful_partial(), ensure_ascii=False, separators=(",", ":")),
            finish_reason="stop",
            prompt_tokens=321,
            completion_tokens=129,
        )
    )

    with pytest.raises(
        RuntimeError,
        match="synthetic readiness completion exceeds reserved budget",
    ):
        extract_readiness_probe(settings, client, clock=lambda: 15.0)


def test_long_paper_uses_chunked_mode_and_preserves_chunk_order() -> None:
    paper = _paper(*(f"Results: RMSE was {page}.0." for page in range(1, 6)))
    client = ScriptedClient(*(_completion({}) for _ in range(5)))

    response = extract_dossier(
        paper,
        _settings(max_pages_per_chunk=2),
        client,
        clock=lambda: 20.0,
    )

    assert response.ok is True
    assert response.diagnostics.mode == "chunked"
    assert response.diagnostics.initial_chunk_count > 1
    assert response.diagnostics.successful_chunk_count == (
        response.diagnostics.initial_chunk_count
    )
    assert [
        tuple(page.page for page in chunk.pages) for chunk in client.calls
    ] == [(1, 2), (2, 3), (3, 4), (4, 5)]


def test_candidate_page_limit_reserves_one_model_call_per_initial_chunk() -> None:
    paper = _paper(
        *(f"Results: RMSE was {page}.0. " + "x" * 5_000 for page in range(1, 14))
    )
    client = ScriptedClient(*(_completion({}) for _ in range(12)))

    response = extract_dossier(
        paper,
        _settings(max_candidate_pages=32, max_ollama_calls=12),
        client,
        clock=lambda: 25.0,
    )

    assert response.ok is True
    assert response.diagnostics.candidate_page_count == 12
    assert response.diagnostics.initial_chunk_count == 12
    assert response.diagnostics.ollama_call_count == 12
    assert response.diagnostics.failed_chunk_count == 0
    assert "candidate_pages_capped" in response.diagnostics.warnings


def test_any_required_failed_chunk_fails_closed_without_a_dossier() -> None:
    paper = _paper("Results: RMSE was 2.0.")
    client = ScriptedClient(_completion({"not": "a valid partial"}))

    response = extract_dossier(paper, _settings(), client, clock=lambda: 30.0)

    assert response.ok is False
    assert response.dossier is None
    assert response.diagnostics.failed_chunk_count == 1
    assert response.diagnostics.errors[0].code == "qwen_chunk_invalid"
    assert response.diagnostics.failed_page_ranges == ((1, 1),)


def test_candidate_warnings_survive_in_content_free_diagnostics() -> None:
    paper = _paper(f"Unrelated prose. {SOURCE_SENTINEL}")
    client = ScriptedClient(_completion({}))

    response = extract_dossier(paper, _settings(), client, clock=lambda: 40.0)

    assert response.ok is True
    assert "no_explicit_regression_evidence_candidate" in response.diagnostics.warnings
    serialized = response.diagnostics.model_dump_json()
    assert SOURCE_SENTINEL not in serialized
    assert MODEL_SENTINEL not in serialized
    assert "source_text" not in serialized


def test_empty_model_metrics_use_a_verified_structural_table_fallback() -> None:
    paper = ParsedPaper(
        document_id="document-id",
        file_name="paper.pdf",
        page_count=1,
        markdown="",
        elements=[
            PaperElement(
                kind="text",
                page=1,
                text="Synthetic paper; regression task.",
            ),
            PaperElement(
                kind="table",
                page=1,
                text=(
                    "Table 9. Mean MAE/MAE(lm) on the training dataset. "
                    "Dataset n p Model 1 Model 2 abalone 4177 8 1.079 0.992"
                ),
            ),
        ],
        warnings=[],
    )
    client = ScriptedClient(
        _completion(
            {
                "title": "Synthetic paper",
                "title_evidence": [
                    {"page": 1, "source_text": "Synthetic paper"}
                ],
                "task_type": "regression",
                "task_evidence": [
                    {"page": 1, "source_text": "regression task"}
                ],
                "metrics": [
                    {
                        "name": "RMSE",
                        "reported_value": None,
                        "evidence": [
                            {
                                "page": 1,
                                "source_text": "R RMSE (U) MAE (U)",
                            }
                        ],
                    }
                ],
            }
        )
    )

    response = extract_dossier(paper, _settings(), client, clock=lambda: 45.0)

    assert response.ok is True
    assert response.dossier is not None
    assert len(response.dossier.metrics) == 1
    metric = response.dossier.metrics[0]
    assert metric.name == "mae"
    assert metric.reported_value == 1.079
    assert metric.dataset == "abalone"
    assert metric.split == "training"
    assert metric.model is None
    assert metric.evidence[0].source_text == "abalone 4177 8 1.079"
    assert "deterministic_metric_table_fallback" in response.diagnostics.warnings


def test_empty_model_metrics_use_a_flattened_performance_table_fallback() -> None:
    paper = ParsedPaper(
        document_id="document-id",
        file_name="paper.pdf",
        page_count=1,
        markdown="",
        elements=[
            PaperElement(
                kind="text",
                page=1,
                text="Synthetic paper; regression task.",
            ),
            PaperElement(
                kind="table",
                page=1,
                text=(
                    "Table 5. Test results by WEKA and iML on real estate dataset "
                    "via hold-out validation. Model WEKA SI (Ranking) iML SI "
                    "(Ranking) R RMSE (U) MAE (U) MAPE (%) R RMSE (U) MAE (U) "
                    "MAPE (%) I. Single CART ANN 0.740 10.762 5.882 13.210 "
                    "0.321 (6) 0.871 6.630 4.912 13.591 0.049 (3)"
                ),
            ),
        ],
        warnings=[],
    )
    client = ScriptedClient(
        _completion(
            {
                "title": "Synthetic paper",
                "title_evidence": [{"page": 1, "source_text": "Synthetic paper"}],
                "task_type": "regression",
                "task_evidence": [{"page": 1, "source_text": "regression task"}],
                "metrics": [
                    {
                        "name": "RMSE",
                        "reported_value": None,
                        "evidence": [
                            {
                                "page": 1,
                                "source_text": "R RMSE (U) MAE (U)",
                            }
                        ],
                    }
                ],
            }
        )
    )

    response = extract_dossier(paper, _settings(), client, clock=lambda: 45.0)

    assert response.ok is True
    assert response.dossier is not None
    assert len(response.dossier.metrics) == 1
    metric = response.dossier.metrics[0]
    assert metric.name == "rmse"
    assert metric.reported_value == 10.762
    assert metric.dataset == "real estate"
    assert metric.split == "hold-out validation"
    assert metric.model is None
    assert metric.evidence[0].source_text == "I. Single CART ANN 0.740 10.762"


def test_unqualified_model_metric_uses_a_model_performance_table_fallback() -> None:
    paper = ParsedPaper(
        document_id="document-id",
        file_name="paper.pdf",
        page_count=1,
        markdown="",
        elements=[
            PaperElement(
                kind="text",
                page=1,
                text=(
                    "Synthetic paper; regression task. The final models were evaluated "
                    "on the test set. The gradient boosting regressor achieved R 2 0.94."
                ),
            ),
            PaperElement(
                kind="table",
                page=1,
                text=(
                    "Table 6. Performance of regression models. Model MSE R 2 "
                    "gradient boosting regressor 15.79 0.94 RF regressor 21.61 0.91"
                ),
            ),
        ],
        warnings=[],
    )
    client = ScriptedClient(
        _completion(
            {
                "title": "Synthetic paper",
                "title_evidence": [{"page": 1, "source_text": "Synthetic paper"}],
                "task_type": "regression",
                "task_evidence": [{"page": 1, "source_text": "regression task"}],
                "metrics": [
                    {
                        "name": "R2",
                        "reported_value": 0.94,
                        "evidence": [
                            {
                                "page": 1,
                                "source_text": (
                                    "The gradient boosting regressor achieved R 2 0.94."
                                ),
                            }
                        ],
                    }
                ],
            }
        )
    )

    response = extract_dossier(paper, _settings(), client, clock=lambda: 45.0)

    assert response.ok is True
    assert response.dossier is not None
    qualified = [
        metric
        for metric in response.dossier.metrics
        if metric.model == "gradient_boosting" and metric.split == "test"
    ]
    assert len(qualified) == 1
    assert qualified[0].name == "r2"
    assert qualified[0].reported_value == 0.94
    assert (
        qualified[0].evidence[0].source_text
        == "gradient boosting regressor 15.79 0.94"
    )
    assert "deterministic_metric_table_fallback" in response.diagnostics.warnings


def test_service_logs_and_diagnostics_never_contain_source_or_model_output(
    caplog,
) -> None:
    paper = _paper(f"Results: RMSE was 2.0. {SOURCE_SENTINEL}")
    client = ScriptedClient(
        _completion(
            {
                **_successful_partial(),
                "gaps": [MODEL_SENTINEL],
            }
        )
    )

    response = extract_dossier(paper, _settings(), client, clock=lambda: 50.0)

    assert response.ok is True
    assert SOURCE_SENTINEL not in caplog.text
    assert MODEL_SENTINEL not in caplog.text
    assert SOURCE_SENTINEL not in response.diagnostics.model_dump_json()
    assert MODEL_SENTINEL not in response.diagnostics.model_dump_json()
