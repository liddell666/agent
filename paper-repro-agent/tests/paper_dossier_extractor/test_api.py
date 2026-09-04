import re
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from paper_parser.schemas import PaperElement, ParsedPaper

from paper_dossier_extractor import api
from paper_dossier_extractor.config import Settings
from paper_dossier_extractor.ollama import OllamaError
from paper_dossier_extractor.schemas import (
    ExtractionDiagnostics,
    ExtractionResponse,
    FinalDossier,
    OllamaCompletion,
    PageChunk,
)


TOKEN = "extractor-token-that-is-at-least-32-chars"
SOURCE_SENTINEL = "SOURCE_SENTINEL"


def _paper() -> ParsedPaper:
    return ParsedPaper(
        document_id="document-id",
        file_name="paper.pdf",
        page_count=1,
        markdown="",
        elements=[
            PaperElement(
                kind="text",
                page=1,
                text=f"Results: RMSE was 2.0. {SOURCE_SENTINEL}",
            )
        ],
        warnings=[],
    )


def _diagnostics(*, failed: int = 0) -> ExtractionDiagnostics:
    return ExtractionDiagnostics(
        mode="single",
        page_count=1,
        candidate_page_count=1,
        initial_chunk_count=1,
        ollama_call_count=1,
        successful_chunk_count=0 if failed else 1,
        split_retry_count=0,
        failed_chunk_count=failed,
        elapsed_seconds=0.1,
        num_ctx=16_384,
        num_predict=1_536,
        max_chunk_source_bytes=8_192,
        max_ollama_calls=12,
    )


def _success_response() -> ExtractionResponse:
    return ExtractionResponse(
        ok=True,
        dossier=FinalDossier(title="Synthetic paper", task_type="uncertain"),
        diagnostics=_diagnostics(),
    )


def _semantic_failure_response() -> ExtractionResponse:
    return ExtractionResponse(
        ok=False,
        dossier=None,
        diagnostics=_diagnostics(failed=1),
    )


@pytest.fixture
def client():
    settings = Settings(api_token=TOKEN)
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    api.app.dependency_overrides[api.get_client] = lambda: object()
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()


def _post(client: TestClient, *, headers: dict[str, str] | None = None):
    return client.post(
        "/v1/extract-dossier",
        headers=headers,
        json=_paper().model_dump(mode="json"),
    )


def test_health_is_public_and_reports_fixed_service_identity(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "paper-dossier-extractor",
        "model": "qwen3:8b",
    }


@pytest.mark.parametrize("headers", [{}, {"X-Extractor-Token": "wrong-token"}])
def test_extraction_requires_a_valid_token(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = _post(client, headers=headers)

    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_token"
    assert re.fullmatch(r"[0-9a-f]{32}", detail["request_id"])
    assert SOURCE_SENTINEL not in response.text
    assert "traceback" not in response.text.lower()


def test_valid_token_uses_constant_time_comparison(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_compare_digest = api.secrets.compare_digest
    calls: list[tuple[str, str]] = []

    def compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return original_compare_digest(left, right)

    monkeypatch.setattr(api.secrets, "compare_digest", compare_digest)
    monkeypatch.setattr(api, "extract_dossier", lambda *_args: _success_response())

    response = _post(client, headers={"X-Extractor-Token": TOKEN})

    assert response.status_code == 200
    assert calls == [(TOKEN, TOKEN)]


def test_invalid_parser_json_is_sanitized_with_a_request_id(
    client: TestClient,
) -> None:
    body = _paper().model_dump(mode="json")
    body["unexpected"] = SOURCE_SENTINEL

    response = client.post(
        "/v1/extract-dossier",
        headers={"X-Extractor-Token": TOKEN},
        json=body,
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_parser_json"
    assert re.fullmatch(r"[0-9a-f]{32}", detail["request_id"])
    assert SOURCE_SENTINEL not in response.text
    assert "traceback" not in response.text.lower()


def test_second_concurrent_extraction_is_rejected_and_slot_recovers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = Event()
    release = Event()

    def blocking_extract(*_args):
        entered.set()
        assert release.wait(timeout=5)
        return _success_response()

    monkeypatch.setattr(api, "extract_dossier", blocking_extract)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(
            _post, client, headers={"X-Extractor-Token": TOKEN}
        )
        assert entered.wait(timeout=5)

        second = _post(client, headers={"X-Extractor-Token": TOKEN})
        assert second.status_code == 429
        detail = second.json()["detail"]
        assert detail["code"] == "extraction_capacity_reached"
        assert detail["request_id"]

        release.set()
        first = first_future.result(timeout=5)

    assert first.status_code == 200


def test_ollama_failure_is_a_sanitized_bad_gateway(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args):
        raise OllamaError("ollama_unavailable")

    monkeypatch.setattr(api, "extract_dossier", fail)

    response = _post(client, headers={"X-Extractor-Token": TOKEN})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "ollama_unavailable"
    assert re.fullmatch(r"[0-9a-f]{32}", detail["request_id"])
    assert SOURCE_SENTINEL not in response.text
    assert "traceback" not in response.text.lower()


def test_client_ollama_failure_is_a_bad_gateway(
    client: TestClient,
) -> None:
    class FailingClient:
        def complete(self, _chunk: PageChunk) -> OllamaCompletion:
            raise OllamaError("ollama_unavailable")

    api.app.dependency_overrides[api.get_client] = lambda: FailingClient()

    response = _post(client, headers={"X-Extractor-Token": TOKEN})

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ollama_unavailable"


def test_semantic_extraction_failure_returns_a_200_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        api, "extract_dossier", lambda *_args: _semantic_failure_response()
    )

    response = _post(client, headers={"X-Extractor-Token": TOKEN})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["dossier"] is None
