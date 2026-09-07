import logging
import secrets
from threading import Lock
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from paper_parser.config import Settings, get_settings
from paper_parser.schemas import ParsedPaper
from paper_parser.service import (
    InvalidPdfError,
    PageLimitExceededError,
    UploadTooLargeError,
    parse_pdf,
)


logger = logging.getLogger(__name__)
app = FastAPI(title="Paper Parser", version="0.1.0")
_parse_lock = Lock()


def _parse_with_capacity(content, file_name, settings):
    # The worker owns the reservation, including when its caller disconnects.
    if not _parse_lock.acquire(blocking=False):
        raise _error(429, "parser_capacity_reached")
    try:
        return parse_pdf(content, file_name, settings)
    finally:
        _parse_lock.release()


def _error(status_code: int, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/parse", response_model=ParsedPaper)
async def parse_endpoint(
    file: Annotated[UploadFile, File()],
    token: Annotated[str | None, Header(alias="X-Parser-Token")] = None,
    settings: Settings = Depends(get_settings),
) -> ParsedPaper:
    if token is None or not secrets.compare_digest(token, settings.parser_api_token):
        raise _error(401, "invalid_token")

    byte_limit = settings.max_upload_mb * 1024 * 1024
    content = await file.read(byte_limit + 1)
    if len(content) > byte_limit:
        raise _error(413, "upload_too_large")

    try:
        return await run_in_threadpool(
            _parse_with_capacity,
            content,
            file.filename or "paper.pdf",
            settings,
        )
    except HTTPException:
        raise
    except UploadTooLargeError:
        raise _error(413, "upload_too_large") from None
    except InvalidPdfError:
        raise _error(422, "invalid_pdf") from None
    except PageLimitExceededError:
        raise _error(422, "page_limit_exceeded") from None
    except Exception:
        request_id = uuid4().hex
        logger.exception("paper parse failed request_id=%s", request_id)
        raise HTTPException(
            status_code=500,
            detail={"code": "parse_failed", "request_id": request_id},
        ) from None
