"""Authenticated FastAPI boundary for the stateless dossier extractor."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .config import Settings, get_settings
from .ollama import OllamaClient, OllamaError
from .schemas import ExtractionResponse, OllamaCompletion, PageChunk
from .service import extract_dossier
from paper_parser.schemas import ParsedPaper


logger = logging.getLogger(__name__)
app = FastAPI(title="Paper Dossier Extractor", version="0.1.0")
_extraction_semaphore = asyncio.Semaphore(1)


def _request_id() -> str:
    return uuid4().hex


def _error(status_code: int, code: str, request_id: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "request_id": request_id},
    )


def _log_terminal(
    request_id: str,
    code: str,
    *,
    elapsed_seconds: float = 0.0,
    candidate_page_count: int = 0,
    initial_chunk_count: int = 0,
    ollama_call_count: int = 0,
    successful_chunk_count: int = 0,
    split_retry_count: int = 0,
    failed_chunk_count: int = 0,
) -> None:
    logger.info(
        "extractor terminal request_id=%s code=%s "
        "candidate_page_count=%d initial_chunk_count=%d "
        "ollama_call_count=%d successful_chunk_count=%d "
        "split_retry_count=%d failed_chunk_count=%d elapsed_seconds=%.3f",
        request_id,
        code,
        candidate_page_count,
        initial_chunk_count,
        ollama_call_count,
        successful_chunk_count,
        split_retry_count,
        failed_chunk_count,
        max(0.0, elapsed_seconds),
    )


def _log_response(
    request_id: str, response: ExtractionResponse, elapsed_seconds: float
) -> None:
    diagnostics = response.diagnostics
    code = "ok"
    if not response.ok:
        code = diagnostics.errors[0].code if diagnostics.errors else "extraction_failed"
    _log_terminal(
        request_id,
        code,
        elapsed_seconds=elapsed_seconds,
        candidate_page_count=diagnostics.candidate_page_count,
        initial_chunk_count=diagnostics.initial_chunk_count,
        ollama_call_count=diagnostics.ollama_call_count,
        successful_chunk_count=diagnostics.successful_chunk_count,
        split_retry_count=diagnostics.split_retry_count,
        failed_chunk_count=diagnostics.failed_chunk_count,
    )


def get_client(settings: Settings = Depends(get_settings)) -> OllamaClient:
    return OllamaClient(settings)


class _ObservedClient:
    """Capture only a safe Ollama error code across the chunk boundary."""

    def __init__(self, client: OllamaClient) -> None:
        self._client = client
        self.error: OllamaError | None = None

    def complete(self, chunk: PageChunk) -> OllamaCompletion:
        try:
            return self._client.complete(chunk)
        except OllamaError as error:
            self.error = error
            raise


@app.exception_handler(RequestValidationError)
async def request_validation_handler(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    request_id = _request_id()
    _log_terminal(request_id, "invalid_parser_json")
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "invalid_parser_json",
                "request_id": request_id,
            }
        },
    )


@app.get("/healthz")
def healthz(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {
        "status": "ok",
        "service": "paper-dossier-extractor",
        "model": settings.ollama_model,
    }


@app.post("/v1/extract-dossier", response_model=ExtractionResponse)
async def extract_dossier_endpoint(
    paper: ParsedPaper,
    token: Annotated[str | None, Header(alias="X-Extractor-Token")] = None,
    settings: Settings = Depends(get_settings),
    client: OllamaClient = Depends(get_client),
) -> ExtractionResponse:
    request_id = _request_id()
    if token is None or not secrets.compare_digest(token, settings.api_token):
        _log_terminal(request_id, "invalid_token")
        raise _error(401, "invalid_token", request_id)

    if _extraction_semaphore.locked():
        _log_terminal(request_id, "extraction_capacity_reached")
        raise _error(429, "extraction_capacity_reached", request_id)

    await _extraction_semaphore.acquire()
    started = time.monotonic()
    observed_client = _ObservedClient(client)
    try:
        try:
            response = await run_in_threadpool(
                extract_dossier,
                paper,
                settings,
                observed_client,
            )
        except OllamaError as error:
            elapsed_seconds = time.monotonic() - started
            _log_terminal(request_id, error.code, elapsed_seconds=elapsed_seconds)
            raise _error(502, error.code, request_id) from None
        except (TimeoutError, asyncio.TimeoutError):
            elapsed_seconds = time.monotonic() - started
            _log_terminal(request_id, "ollama_timeout", elapsed_seconds=elapsed_seconds)
            raise _error(502, "ollama_timeout", request_id) from None
        except Exception:
            elapsed_seconds = time.monotonic() - started
            _log_terminal(
                request_id,
                "extraction_failed",
                elapsed_seconds=elapsed_seconds,
            )
            raise _error(500, "extraction_failed", request_id) from None

        if not response.ok and observed_client.error is not None:
            error = observed_client.error
            elapsed_seconds = time.monotonic() - started
            _log_terminal(request_id, error.code, elapsed_seconds=elapsed_seconds)
            raise _error(502, error.code, request_id)

        elapsed_seconds = time.monotonic() - started
        _log_response(request_id, response, elapsed_seconds)
        return response
    finally:
        _extraction_semaphore.release()
