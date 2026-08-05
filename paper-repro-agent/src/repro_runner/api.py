"""HTTP interface for the table-based reproduction baseline service."""

from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from repro_runner.compare import compare_metrics
from repro_runner.config import Settings, get_settings
from repro_runner.data import DatasetError, load_dataset
from repro_runner.engine import ExperimentError, run_random_forest
from repro_runner.schemas import (
    ComparisonResponse,
    DatasetOptions,
    ExperimentConfig,
    ExperimentResult,
    ReportedMetricInput,
    ValidationErrorItem,
    ValidationResponse,
)
from repro_runner.storage import (
    ResultFormatError,
    ResultNotFoundError,
    load_result,
    save_result,
)


logger = logging.getLogger(__name__)
app = FastAPI(title="Reproduction Runner", version="0.2.0")


class ComparisonRequest(BaseModel):
    """Metric values extracted from a paper for one stored experiment."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    reported_metrics: list[ReportedMetricInput] = Field(default_factory=list)


class ExperimentAdmissionLimiter:
    """App-owned event-loop limiters with immediate saturation rejection."""

    def __init__(self) -> None:
        self._limiters: dict[asyncio.AbstractEventLoop, dict[int, asyncio.Semaphore]] = {}

    def _semaphore(self, max_concurrent_experiments: int) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        per_loop = self._limiters.setdefault(loop, {})
        return per_loop.setdefault(
            max(1, max_concurrent_experiments),
            asyncio.Semaphore(max(1, max_concurrent_experiments)),
        )

    async def try_acquire(self, max_concurrent_experiments: int) -> bool:
        semaphore = self._semaphore(max_concurrent_experiments)
        if semaphore.locked():
            return False
        await semaphore.acquire()
        return True

    def release(self, max_concurrent_experiments: int) -> None:
        self._semaphore(max_concurrent_experiments).release()

    def is_saturated(self, max_concurrent_experiments: int) -> bool:
        capacity = max(1, max_concurrent_experiments)
        return any(
            semaphore.locked()
            for per_loop in self._limiters.values()
            if (semaphore := per_loop.get(capacity)) is not None
        )


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    """Keep malformed form and JSON errors stable without echoing user input."""
    return _error_response(422, "invalid_request", "request parameters are invalid")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/validate-dataset", response_model=ValidationResponse)
async def validate_dataset(
    file: Annotated[UploadFile, File()],
    target_column: Annotated[str | None, Form()] = None,
    settings: Settings = Depends(get_settings),
) -> ValidationResponse:
    content = await _read_upload(file, settings)
    options = DatasetOptions(
        target_column=target_column or settings.default_target_column,
    )
    try:
        bundle = await run_in_threadpool(load_dataset, content, options, settings)
    except DatasetError as exc:
        return ValidationResponse(
            valid=False,
            errors=[ValidationErrorItem(code=exc.code, message=exc.message)],
        )
    except Exception:
        request_id = _request_id()
        logger.exception("dataset validation failed request_id=%s", request_id)
        raise _internal_error("validation_failed", request_id) from None
    return ValidationResponse(valid=True, dataset=bundle.profile, warnings=bundle.warnings)


@app.post("/v1/run-experiment", response_model=ExperimentResult)
async def run_experiment(
    file: Annotated[UploadFile, File()],
    target_column: Annotated[str | None, Form()] = None,
    test_size: Annotated[float, Form(ge=0.1, le=0.5)] = 0.2,
    random_state: Annotated[int, Form(ge=0)] = 42,
    drop_duplicates: Annotated[bool, Form()] = False,
    model: Annotated[Literal["random_forest"], Form()] = "random_forest",
    settings: Settings = Depends(get_settings),
) -> ExperimentResult:
    options = DatasetOptions(
        target_column=target_column or settings.default_target_column,
        drop_duplicates=drop_duplicates,
    )
    config = ExperimentConfig(
        model=model,
        test_size=test_size,
        random_state=random_state,
        drop_duplicates=drop_duplicates,
    )
    async with _admit_experiment(settings):
        try:
            content = await _read_upload(file, settings)
            bundle = await run_in_threadpool(load_dataset, content, options, settings)
        except DatasetError as exc:
            raise _dataset_error(exc) from None
        except HTTPException:
            raise
        except Exception:
            request_id = _request_id()
            logger.exception("experiment input preparation failed request_id=%s", request_id)
            raise _internal_error("experiment_failed", request_id) from None

        try:
            result = await run_in_threadpool(_execute_experiment, bundle, config, settings)
        except ExperimentError as exc:
            raise _dataset_error(exc) from None
        except Exception:
            request_id = _request_id()
            logger.exception("experiment execution failed request_id=%s", request_id)
            raise _internal_error("experiment_failed", request_id) from None
    return result


@app.get("/v1/experiments/{experiment_id}", response_model=ExperimentResult)
async def get_experiment(
    experiment_id: str, settings: Settings = Depends(get_settings)
) -> ExperimentResult:
    try:
        return await run_in_threadpool(load_result, experiment_id, settings)
    except ResultNotFoundError:
        raise _not_found_error() from None
    except ResultFormatError:
        raise _result_format_error() from None
    except Exception:
        request_id = _request_id()
        logger.exception("experiment retrieval failed request_id=%s", request_id)
        raise _internal_error("experiment_lookup_failed", request_id) from None


@app.post("/v1/compare-result", response_model=ComparisonResponse)
async def compare_result(
    request: ComparisonRequest, settings: Settings = Depends(get_settings)
) -> ComparisonResponse:
    try:
        result = await run_in_threadpool(load_result, request.experiment_id, settings)
    except ResultNotFoundError:
        raise _not_found_error() from None
    except ResultFormatError:
        raise _result_format_error() from None
    except Exception:
        request_id = _request_id()
        logger.exception("experiment comparison lookup failed request_id=%s", request_id)
        raise _internal_error("comparison_failed", request_id) from None

    try:
        return compare_metrics(result, request.reported_metrics)
    except Exception:
        request_id = _request_id()
        logger.exception("experiment comparison failed request_id=%s", request_id)
        raise _internal_error("comparison_failed", request_id) from None


async def _read_upload(file: UploadFile, settings: Settings) -> bytes:
    """Read a bounded upload before CSV parsing to prevent excessive memory use."""
    max_bytes = settings.max_upload_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "file_too_large",
                "message": "CSV content exceeds the configured size limit",
                "request_id": _request_id(),
            },
        )
    return content


def _dataset_error(exc: DatasetError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": exc.code, "message": exc.message, "request_id": _request_id()},
    )


def _get_experiment_limiter() -> ExperimentAdmissionLimiter:
    limiter = getattr(app.state, "experiment_limiter", None)
    if limiter is None:
        limiter = ExperimentAdmissionLimiter()
        app.state.experiment_limiter = limiter
    return limiter


@asynccontextmanager
async def _admit_experiment(settings: Settings):
    """Serialize the entire read, parse, train, and save lifecycle per process."""
    limiter = _get_experiment_limiter()
    if not await limiter.try_acquire(settings.max_concurrent_experiments):
        raise HTTPException(
            status_code=429,
            detail={
                "code": "experiment_capacity_reached",
                "message": "the experiment service is at its configured capacity",
                "request_id": _request_id(),
            },
        )
    try:
        yield
    finally:
        limiter.release(settings.max_concurrent_experiments)


def _execute_experiment(bundle: object, config: object, settings: Settings) -> ExperimentResult:
    """Run and persist one already-admitted training job."""
    result = run_random_forest(bundle, config)  # type: ignore[arg-type]
    save_result(result, settings)
    return result


def _not_found_error() -> HTTPException:
    request_id = _request_id()
    return HTTPException(
        status_code=404,
        detail={
            "code": "experiment_not_found",
            "message": "experiment result was not found",
            "request_id": request_id,
        },
    )


def _result_format_error() -> HTTPException:
    request_id = _request_id()
    return HTTPException(
        status_code=409,
        detail={
            "code": "experiment_result_incompatible",
            "message": "experiment result uses an unsupported or corrupted format",
            "request_id": request_id,
        },
    )


def _internal_error(code: str, request_id: str) -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={
            "code": code,
            "message": "the experiment service could not complete the request",
            "request_id": request_id,
        },
    )


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": {"code": code, "message": message, "request_id": _request_id()}
        },
    )


def _request_id() -> str:
    return uuid4().hex
