from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paper_parser import api
from paper_parser.config import Settings, get_settings
from paper_parser.schemas import ParsedPaper
from paper_parser.service import InvalidPdfError


TOKEN = "test-token-that-is-at-least-32-chars"


def test_parser_capacity_queues_concurrent_parse_until_capacity_is_free(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        raise InvalidPdfError("synthetic")

    monkeypatch.setattr(api, "parse_pdf", blocked)

    def submit():
        return client.post('/v1/parse', headers={'X-Parser-Token': TOKEN},
                           files={'file': ('paper.pdf', b'%PDF-test', 'application/pdf')})

    with ThreadPoolExecutor() as pool:
        first = pool.submit(submit)
        try:
            assert entered.wait(5)
            second = pool.submit(submit)
            assert not second.done()
        finally:
            release.set()
        assert first.result(5).status_code == 422
        assert second.result(5).status_code == 422
    monkeypatch.setattr(api, 'parse_pdf', lambda *args: _parsed_paper())
    assert submit().status_code == 200


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        parser_api_token=TOKEN,
        max_upload_mb=1,
        work_dir=str(tmp_path),
    )
    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()


def _parsed_paper(file_name: str = "paper.pdf") -> ParsedPaper:
    return ParsedPaper(
        document_id="a" * 64,
        file_name=file_name,
        page_count=1,
        markdown="<!-- page=1 -->\nDataset: TinySet.",
        elements=[
            {
                "kind": "text",
                "page": 1,
                "text": "Dataset: TinySet.",
                "bbox": None,
                "metadata": {},
            }
        ],
        warnings=[],
    )


def test_health_is_public(client: TestClient):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"X-Parser-Token": "wrong-token"}])
def test_parse_requires_valid_token(client: TestClient, headers: dict[str, str]):
    response = client.post(
        "/v1/parse",
        headers=headers,
        files={"file": ("paper.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "invalid_token"}}


def test_valid_parse_returns_contract(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def fake_parse(content: bytes, file_name: str, settings: Settings) -> ParsedPaper:
        assert content == b"%PDF-fixture"
        assert settings.parser_api_token == TOKEN
        return _parsed_paper(file_name)

    monkeypatch.setattr(api, "parse_pdf", fake_parse)
    response = client.post(
        "/v1/parse",
        headers={"X-Parser-Token": TOKEN},
        files={"file": ("study.pdf", b"%PDF-fixture", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["file_name"] == "study.pdf"
    assert response.json()["elements"][0]["page"] == 1


def test_invalid_pdf_is_422(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def reject_pdf(*_args, **_kwargs):
        raise InvalidPdfError("sensitive parser detail")

    monkeypatch.setattr(api, "parse_pdf", reject_pdf)
    response = client.post(
        "/v1/parse",
        headers={"X-Parser-Token": TOKEN},
        files={"file": ("bad.pdf", b"not-a-pdf", "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_pdf"}}
    assert "sensitive" not in response.text


def test_oversized_pdf_is_413(client: TestClient):
    response = client.post(
        "/v1/parse",
        headers={"X-Parser-Token": TOKEN},
        files={"file": ("large.pdf", b"%PDF-" + b"x" * (1024 * 1024), "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": {"code": "upload_too_large"}}


def test_unknown_parser_failure_is_stable_and_sanitized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def crash(*_args, **_kwargs):
        raise RuntimeError("secret local path C:/private/paper.pdf")

    monkeypatch.setattr(api, "parse_pdf", crash)
    response = client.post(
        "/v1/parse",
        headers={"X-Parser-Token": TOKEN},
        files={"file": ("paper.pdf", b"%PDF-fixture", "application/pdf")},
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "parse_failed"
    assert "private" not in response.text
    assert "traceback" not in response.text.lower()
