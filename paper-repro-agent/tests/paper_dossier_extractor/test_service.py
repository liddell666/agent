import hashlib
import json

from paper_parser.schemas import PaperElement, ParsedPaper

from paper_dossier_extractor.chunking import serialize_pages
from paper_dossier_extractor.config import Settings
from paper_dossier_extractor.ollama import OllamaError
from paper_dossier_extractor.schemas import OllamaCompletion, PageChunk
from paper_dossier_extractor.service import extract_dossier


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


def test_service_diagnostics_include_safe_effective_config_and_actual_call_evidence() -> None:
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

    response = extract_dossier(paper, settings, client, clock=lambda: 15.0)
    chunk = client.calls[0]
    expected_source = serialize_pages(chunk.pages).encode("utf-8")

    assert response.ok is True
    assert response.diagnostics.num_ctx == 12_288
    assert response.diagnostics.num_predict == 1_024
    assert response.diagnostics.max_chunk_source_bytes == 4_096
    assert response.diagnostics.max_ollama_calls == 6
    assert response.diagnostics.source_bytes == chunk.source_bytes
    assert response.diagnostics.source_sha256 == hashlib.sha256(expected_source).hexdigest()
    assert response.diagnostics.prompt_tokens == 321
    assert response.diagnostics.completion_tokens == 111


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
