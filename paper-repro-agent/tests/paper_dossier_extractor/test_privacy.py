from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from paper_parser.schemas import PaperElement, ParsedPaper

from paper_dossier_extractor import api
from paper_dossier_extractor.config import Settings
from paper_dossier_extractor.schemas import OllamaCompletion
from paper_dossier_extractor.service import extract_dossier
from scripts import check_ollama_readiness as readiness
from scripts import build_regression_dsl


SOURCE_SENTINEL = "PRIVACY_SOURCE_SENTINEL_9137"
MODEL_SENTINEL = "PRIVACY_MODEL_OUTPUT_SENTINEL_9137"
TOKEN_SENTINEL = "PRIVACY_FAKE_TOKEN_SENTINEL_9137"
EXCEPTION_SENTINEL = "PRIVACY_EXCEPTION_SENTINEL_9137"


def _paper() -> ParsedPaper:
    return ParsedPaper(
        document_id="privacy-document",
        file_name="privacy.pdf",
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


def _serialized_keys(value: object) -> set[str]:
    keys: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, dict):
            keys.update(str(key) for key in item)
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return keys


def _assert_no_private_values(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    for sentinel in (
        SOURCE_SENTINEL,
        MODEL_SENTINEL,
        TOKEN_SENTINEL,
        EXCEPTION_SENTINEL,
    ):
        assert sentinel not in serialized
    assert not {
        "prompt",
        "completion",
        "source_text",
        "raw_prompt",
        "raw_completion",
        "raw_response",
        "token",
        "api_token",
        "exception",
        "stderr",
    } & _serialized_keys(value)


def test_api_failure_response_and_logs_do_not_echo_source_token_or_exception(
    caplog, monkeypatch
) -> None:
    token = "x" * 32
    settings = Settings(api_token=token)
    api.app.dependency_overrides[api.get_settings] = lambda: settings
    api.app.dependency_overrides[api.get_client] = lambda: object()
    monkeypatch.setattr(
        api,
        "extract_dossier",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(EXCEPTION_SENTINEL)),
    )

    with caplog.at_level(logging.INFO, logger=api.logger.name):
        with TestClient(api.app, raise_server_exceptions=False) as client:
            response = client.post(
                "/v1/extract-dossier",
                headers={"X-Extractor-Token": TOKEN_SENTINEL},
                json=_paper().model_dump(mode="json"),
            )
            assert response.status_code == 401
            invalid_token_text = response.text

            response = client.post(
                "/v1/extract-dossier",
                headers={"X-Extractor-Token": token},
                json=_paper().model_dump(mode="json"),
            )
            assert response.status_code == 500
            failure_text = response.text

    api.app.dependency_overrides.clear()
    assert SOURCE_SENTINEL not in invalid_token_text
    assert SOURCE_SENTINEL not in failure_text
    assert TOKEN_SENTINEL not in invalid_token_text
    assert EXCEPTION_SENTINEL not in failure_text
    assert SOURCE_SENTINEL not in caplog.text
    assert TOKEN_SENTINEL not in caplog.text
    assert EXCEPTION_SENTINEL not in caplog.text


def test_service_diagnostics_and_logs_do_not_echo_source_or_model_output(caplog) -> None:
    class InvalidClient:
        def complete(self, _chunk) -> OllamaCompletion:
            return OllamaCompletion(
                text=MODEL_SENTINEL,
                finish_reason="stop",
                prompt_tokens=1,
                completion_tokens=1,
            )

    response = extract_dossier(
        _paper(),
        Settings(api_token="x" * 32),
        InvalidClient(),
        clock=lambda: 10.0,
    )

    diagnostics = response.diagnostics.model_dump(mode="json")
    assert response.ok is False
    assert response.dossier is None
    _assert_no_private_values(diagnostics)
    assert SOURCE_SENTINEL not in caplog.text
    assert MODEL_SENTINEL not in caplog.text


def test_readiness_json_keeps_only_hashes_counts_and_configuration(tmp_path: Path) -> None:
    direct = {
        "status": "ready",
        "provider": "ollama",
        "model": readiness.OLLAMA_MODEL,
        "mode": "direct_http",
        "duration_seconds": 0.1,
        "response_nonempty": True,
        "response_length": 1,
        "response_sha256": hashlib.sha256(b"ready").hexdigest(),
        "input_bytes": readiness.OLLAMA_PRODUCTION_INPUT_BYTES,
        "input_sha256": hashlib.sha256(
            readiness.SYNTHETIC_PROMPT.encode("utf-8")
        ).hexdigest(),
        "empty_prompt_tokens": 7,
        "empty_completion_tokens": 1,
        "prompt_tokens": 100,
        "completion_tokens": 1,
        "prompt_token_limit": readiness.OLLAMA_MAX_PROMPT_TOKENS,
        "num_ctx": readiness.OLLAMA_CONTEXT_NUM_CTX,
        "num_predict": readiness.OLLAMA_CONTEXT_NUM_PREDICT,
        "think": False,
        "prompt": TOKEN_SENTINEL,
        "completion": MODEL_SENTINEL,
        "exception": EXCEPTION_SENTINEL,
    }
    extractor = {
        "status": "ready",
        "provider": "paper-dossier-extractor",
        "model": readiness.OLLAMA_MODEL,
        "mode": "extractor_boundary",
        "duration_seconds": 0.2,
        "response_nonempty": True,
        "response_length": 1,
        "response_sha256": hashlib.sha256(MODEL_SENTINEL.encode()).hexdigest(),
        "source_bytes": readiness.EXTRACTOR_MAX_CHUNK_SOURCE_BYTES,
        "source_sha256": readiness.EXTRACTOR_SYNTHETIC_SOURCE_SHA256,
        "prompt_tokens": 100,
        "completion_tokens": 1,
        "num_ctx": readiness.EXTRACTOR_NUM_CTX,
        "num_predict": readiness.EXTRACTOR_NUM_PREDICT,
        "max_chunk_source_bytes": readiness.EXTRACTOR_MAX_CHUNK_SOURCE_BYTES,
        "max_ollama_calls": readiness.EXTRACTOR_MAX_OLLAMA_CALLS,
        "page_count": 1,
        "candidate_page_count": 1,
        "initial_chunk_count": 1,
        "ollama_call_count": 1,
        "successful_chunk_count": 1,
        "split_retry_count": 0,
        "failed_chunk_count": 0,
        "source_text": SOURCE_SENTINEL,
        "token": TOKEN_SENTINEL,
        "stderr": EXCEPTION_SENTINEL,
    }

    evidence = readiness.build_evidence(direct, extractor)
    destination = tmp_path / "readiness.json"
    readiness.write_evidence_atomic(destination, evidence)
    persisted = json.loads(destination.read_text(encoding="utf-8"))

    _assert_no_private_values(evidence)
    _assert_no_private_values(persisted)
    assert persisted["probes"]["direct_ollama"]["response_sha256"] == direct[
        "response_sha256"
    ]
    assert persisted["probes"]["extractor_boundary"]["source_bytes"] == 8_192


def test_dify_extractor_failure_redacts_body_token_and_exception() -> None:
    namespace: dict[str, object] = {}
    source = build_regression_dsl._extractor_prepare_failure_code()
    exec(compile(source, "<extractor-failure>", "exec"), namespace)
    main = namespace["main"]

    result = main(
        TOKEN_SENTINEL,
        json.dumps(
            {
                "mode": "chunked",
                "page_count": 4,
                "warnings": [SOURCE_SENTINEL],
                "errors": [{"code": MODEL_SENTINEL, "page_range": [1, 1]}],
                "raw_prompt": SOURCE_SENTINEL,
                "exception": EXCEPTION_SENTINEL,
            },
            ensure_ascii=False,
        ),
    )

    _assert_no_private_values(result)
    assert json.loads(result["protocol_preview_json"])["errors"][0]["code"] == (
        "extractor_response_invalid"
    )
