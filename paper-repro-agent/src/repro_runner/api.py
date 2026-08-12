"""HTTP interface for the table-based reproduction baseline service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from repro_runner.compare import compare_metrics, compare_suite_metrics
from repro_runner.config import (
    MAX_DOSSIER_BYTES,
    MAX_METRIC_OVERRIDES_BYTES,
    Settings,
    get_settings,
)
from repro_runner.data import DatasetError, diagnose_dataset, load_dataset
from repro_runner.dossier import parse_dossier
from repro_runner.engine import ExperimentError, run_random_forest
from repro_runner.job_runner import JobRunner, stage_job_inputs
from repro_runner.job_store import JobStore
from repro_runner.idempotency import (
    IdempotencyCapacityError,
    IdempotencyConflictError,
    IdempotencyRegistry,
)
from repro_runner.schemas import (
    ComparisonResponse,
    DEFAULT_MODEL_NAMES,
    DatasetDiagnosticResponse,
    DatasetOptions,
    DossierParseResponse,
    ExperimentConfig,
    ExperimentManifest,
    ExperimentResult,
    ExperimentSuiteResult,
    JobCreateResponse,
    JobStatusResponse,
    ModelSuiteConfig,
    ReportedMetricInput,
    SuiteComparisonRequest,
    SuiteComparisonResponse,
    ValidationErrorItem,
    ValidationResponse,
)
from repro_runner.suite_engine import run_model_suite
from repro_runner.storage import (
    ResultFormatError,
    ResultNotFoundError,
    create_experiment_id,
    load_result,
    load_suite_result,
    save_result,
    save_suite_result,
)


logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(application: FastAPI):
    settings = _settings_for_app(application)
    store = JobStore(settings.job_store_path)
    application.state.job_store = store
    runner = JobRunner(
        store=store,
        settings=settings,
        execute_job_resolver=lambda: _default_execute_job,
    )
    application.state.job_runner = runner
    runner.start()
    try:
        yield
    finally:
        runner.stop()


app = FastAPI(title="Reproduction Runner", version="0.2.0", lifespan=_lifespan)


class ExperimentIdempotencyRegistry(IdempotencyRegistry[ExperimentResult]):
    """Idempotency registry specialized for experiment results."""


class ModelSuiteIdempotencyRegistry(IdempotencyRegistry[ExperimentSuiteResult]):
    """Idempotency registry specialized for model suite results."""


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


@app.post("/v1/parse-dossier", response_model=DossierParseResponse)
async def parse_dossier_upload(
    file: Annotated[UploadFile, File()],
    metric_overrides_json: Annotated[str, Form()] = "[]",
    settings: Settings = Depends(get_settings),
) -> DossierParseResponse:
    configured_limit = settings.max_metric_overrides_kb * 1024
    if len(metric_overrides_json.encode("utf-8")) > min(
        configured_limit, MAX_METRIC_OVERRIDES_BYTES
    ):
        return DossierParseResponse(
            valid=False,
            errors=[
                ValidationErrorItem(
                    code="invalid_metric_overrides",
                    message="Metric overrides exceed the configured size limit.",
                )
            ],
        )

    content = await _read_dossier_upload(file, settings)
    try:
        return await run_in_threadpool(
            parse_dossier, file.filename or "", content, metric_overrides_json
        )
    except Exception:
        request_id = _request_id()
        logger.error("dossier parsing failed request_id=%s", request_id)
        raise _internal_error("dossier_parse_failed", request_id) from None


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


@app.post("/v1/diagnose-dataset", response_model=DatasetDiagnosticResponse)
async def diagnose_dataset_route(
    file: Annotated[UploadFile, File()],
    target_column: Annotated[str | None, Form()] = None,
    exclude_columns: Annotated[str | None, Form()] = None,
    exclude_columns_json: Annotated[str | None, Form()] = None,
    settings: Settings = Depends(get_settings),
) -> DatasetDiagnosticResponse:
    content = await _read_upload(file, settings)
    parsed_exclude_columns = _parse_exclude_columns_inputs(
        exclude_columns=exclude_columns,
        exclude_columns_json=exclude_columns_json,
    )
    options = DatasetOptions(
        target_column=target_column or settings.default_target_column,
        target_column_confirmed=target_column is not None,
        exclude_columns=parsed_exclude_columns,
    )
    try:
        return await run_in_threadpool(diagnose_dataset, content, options, settings)
    except HTTPException:
        raise
    except Exception:
        request_id = _request_id()
        logger.exception("dataset diagnosis failed request_id=%s", request_id)
        raise _internal_error("validation_failed", request_id) from None


@app.post("/v1/run-experiment", response_model=ExperimentResult)
async def run_experiment(
    file: Annotated[UploadFile, File()],
    target_column: Annotated[str | None, Form()] = None,
    test_size: Annotated[float, Form(ge=0.1, le=0.5)] = 0.2,
    random_state: Annotated[int, Form(ge=0)] = 42,
    drop_duplicates: Annotated[bool, Form()] = False,
    model: Annotated[Literal["random_forest"], Form()] = "random_forest",
    idempotency_key: Annotated[str | None, Form(min_length=1, max_length=128)] = None,
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
    if idempotency_key is None:
        async with _admit_experiment(settings):
            content = await _read_experiment_upload(file, settings)
            return await _run_experiment_content(content, options, config, settings)

    registry = _get_experiment_idempotency_registry()
    try:
        while True:
            reservation = registry.reserve(idempotency_key)
            try:
                if reservation.owner:
                    try:
                        async with _admit_experiment(settings):
                            content = await _read_experiment_upload(file, settings)
                            fingerprint = _experiment_request_fingerprint(
                                content, options, config
                            )
                            result = await _run_experiment_content(
                                content, options, config, settings
                            )
                            registry.complete(reservation, fingerprint, result)
                            return result
                    except BaseException:
                        registry.abort(reservation)
                        raise

                if not await registry.wait(reservation):
                    continue
                await registry.acquire_verification(reservation)
                try:
                    async with _admit_experiment(settings):
                        content = await _read_experiment_upload(file, settings)
                        fingerprint = _experiment_request_fingerprint(
                            content, options, config
                        )
                        return registry.replay(reservation, fingerprint)
                finally:
                    registry.release_verification(reservation)
            finally:
                registry.release(reservation)
    except IdempotencyConflictError:
        raise _idempotency_conflict_error() from None
    except IdempotencyCapacityError:
        raise _idempotency_capacity_error() from None


@app.post("/v1/run-model-suite", response_model=ExperimentSuiteResult)
async def run_model_suite_route(
    file: Annotated[UploadFile, File()],
    target_column: Annotated[str | None, Form()] = None,
    models_json: Annotated[str, Form()] = json.dumps(list(DEFAULT_MODEL_NAMES)),
    test_size: Annotated[float, Form(ge=0.1, le=0.5)] = 0.2,
    random_state: Annotated[int, Form(ge=0)] = 42,
    drop_duplicates: Annotated[bool, Form()] = False,
    cv_folds: Annotated[int, Form(ge=3, le=10)] = 5,
    optimization_metric: Annotated[
        Literal["roc_auc", "f1", "recall", "balanced_accuracy"], Form()
    ] = "roc_auc",
    threshold: Annotated[float, Form(ge=0.0, le=1.0)] = 0.5,
    n_iter: Annotated[int, Form(ge=1, le=32)] = 8,
    use_gpu: Annotated[bool, Form()] = False,
    n_jobs: Annotated[int, Form(ge=1, le=16)] = 4,
    idempotency_key: Annotated[str | None, Form(min_length=1, max_length=128)] = None,
    settings: Settings = Depends(get_settings),
) -> ExperimentSuiteResult:
    options = DatasetOptions(
        target_column=target_column or settings.default_target_column,
        drop_duplicates=drop_duplicates,
    )
    config = _parse_model_suite_config(
        models_json=models_json,
        test_size=test_size,
        random_state=random_state,
        drop_duplicates=drop_duplicates,
        cv_folds=cv_folds,
        optimization_metric=optimization_metric,
        threshold=threshold,
        n_iter=n_iter,
        use_gpu=use_gpu,
        n_jobs=n_jobs,
    )
    if idempotency_key is None:
        async with _admit_experiment(settings):
            content = await _read_experiment_upload(file, settings)
            return await _run_model_suite_request_content(content, options, config, settings)

    registry = _get_model_suite_idempotency_registry()
    try:
        while True:
            reservation = registry.reserve(idempotency_key)
            try:
                if reservation.owner:
                    try:
                        async with _admit_experiment(settings):
                            content = await _read_experiment_upload(file, settings)
                            fingerprint = _model_suite_request_fingerprint(
                                content, options, config
                            )
                            result = await _run_model_suite_request_content(
                                content, options, config, settings
                            )
                            registry.complete(reservation, fingerprint, result)
                            return result
                    except BaseException:
                        registry.abort(reservation)
                        raise

                if not await registry.wait(reservation):
                    continue
                await registry.acquire_verification(reservation)
                try:
                    async with _admit_experiment(settings):
                        content = await _read_experiment_upload(file, settings)
                        fingerprint = _model_suite_request_fingerprint(
                            content, options, config
                        )
                        return registry.replay(reservation, fingerprint)
                finally:
                    registry.release_verification(reservation)
            finally:
                registry.release(reservation)
    except IdempotencyConflictError:
        raise _idempotency_conflict_error() from None
    except IdempotencyCapacityError:
        raise _idempotency_capacity_error() from None


@app.post("/v1/jobs", response_model=JobCreateResponse, status_code=202)
async def create_job(
    file: Annotated[UploadFile, File()],
    manifest_json: Annotated[str, Form()],
    settings: Settings = Depends(get_settings),
) -> JobCreateResponse:
    content = await _read_experiment_upload(file, settings)
    manifest = _parse_job_manifest(manifest_json)
    try:
        bundle = await run_in_threadpool(
            load_dataset,
            content,
            DatasetOptions(
                target_column=manifest.target_column,
                target_column_confirmed=True,
                missing_policy=manifest.missing_policy,
                sampling_strategy=manifest.sampling_strategy,
                comparison_mode=manifest.comparison_mode,
                feature_columns=list(manifest.feature_columns),
            ),
            settings,
        )
    except DatasetError as exc:
        raise _dataset_error(exc) from None
    except Exception:
        request_id = _request_id()
        logger.exception("job validation failed request_id=%s", request_id)
        raise _internal_error("job_create_failed", request_id) from None

    if bundle.profile.dataset_id != manifest.dataset_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "manifest_dataset_mismatch",
                "message": "request parameters are invalid",
                "request_id": _request_id(),
            },
        )

    store = _get_job_store()
    existing = store.lookup_by_fingerprint(manifest.manifest_id, manifest.dataset_id)
    if existing is not None:
        return _job_create_response(existing)
    if store.has_active_job():
        raise _job_capacity_error()

    job_id = store.create(manifest.manifest_id, manifest.dataset_id)
    try:
        stage_job_inputs(job_id, manifest, content, settings)
    except Exception:
        request_id = _request_id()
        logger.exception("job staging failed request_id=%s", request_id)
        store.mark_failed(job_id, error_code="input_persistence_failed")
        raise _internal_error("job_create_failed", request_id) from None
    _get_job_runner().notify()
    return _job_create_response(store.get(job_id))


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


@app.get("/v1/model-suites/{experiment_id}", response_model=ExperimentSuiteResult)
async def get_model_suite(
    experiment_id: str, settings: Settings = Depends(get_settings)
) -> ExperimentSuiteResult:
    try:
        return await run_in_threadpool(load_suite_result, experiment_id, settings)
    except ResultNotFoundError:
        raise _not_found_error() from None
    except ResultFormatError:
        raise _result_format_error() from None
    except Exception:
        request_id = _request_id()
        logger.exception("model suite retrieval failed request_id=%s", request_id)
        raise _internal_error("experiment_lookup_failed", request_id) from None


@app.get("/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str) -> JobStatusResponse:
    try:
        return _job_status_response(_get_job_store().get(job_id))
    except LookupError:
        raise _job_not_found_error() from None


@app.post("/v1/jobs/{job_id}/cancel", response_model=JobStatusResponse)
async def cancel_job(job_id: str) -> JobStatusResponse:
    store = _get_job_store()
    try:
        store.request_cancel(job_id)
        return _job_status_response(store.get(job_id))
    except LookupError:
        raise _job_not_found_error() from None


@app.get("/v1/jobs/{job_id}/result", response_model=ExperimentSuiteResult)
async def get_job_result(
    job_id: str, settings: Settings = Depends(get_settings)
) -> ExperimentSuiteResult:
    store = _get_job_store()
    try:
        job = store.get(job_id)
    except LookupError:
        raise _job_not_found_error() from None
    if job.result_id is None:
        raise _job_result_unavailable_error()
    try:
        return await run_in_threadpool(load_suite_result, job.result_id, settings)
    except ResultNotFoundError:
        raise _job_result_unavailable_error() from None
    except ResultFormatError:
        raise _result_format_error() from None
    except Exception:
        request_id = _request_id()
        logger.exception("job result retrieval failed request_id=%s", request_id)
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


@app.post("/v1/compare-model-suite-result", response_model=SuiteComparisonResponse)
async def compare_model_suite_result(
    request: SuiteComparisonRequest, settings: Settings = Depends(get_settings)
) -> SuiteComparisonResponse:
    try:
        result = await run_in_threadpool(load_suite_result, request.experiment_id, settings)
    except ResultNotFoundError:
        raise _not_found_error() from None
    except ResultFormatError:
        raise _result_format_error() from None
    except Exception:
        request_id = _request_id()
        logger.exception("model suite comparison lookup failed request_id=%s", request_id)
        raise _internal_error("comparison_failed", request_id) from None

    try:
        return await run_in_threadpool(
            compare_suite_metrics, result, request.reported_metrics
        )
    except Exception:
        request_id = _request_id()
        logger.exception("model suite comparison failed request_id=%s", request_id)
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


async def _read_dossier_upload(file: UploadFile, settings: Settings) -> bytes:
    """Read a bounded dossier upload before parsing its JSON payload."""
    max_bytes = min(settings.max_dossier_mb * 1024 * 1024, MAX_DOSSIER_BYTES)
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "dossier_file_too_large",
                "message": "Dossier content exceeds the configured size limit",
                "request_id": _request_id(),
            },
        )
    return content


async def _read_experiment_upload(file: UploadFile, settings: Settings) -> bytes:
    try:
        return await _read_upload(file, settings)
    except HTTPException:
        raise
    except Exception:
        request_id = _request_id()
        logger.exception("experiment input preparation failed request_id=%s", request_id)
        raise _internal_error("experiment_failed", request_id) from None


async def _run_experiment_content(
    content: bytes,
    options: DatasetOptions,
    config: ExperimentConfig,
    settings: Settings,
) -> ExperimentResult:
    try:
        bundle = await run_in_threadpool(load_dataset, content, options, settings)
    except DatasetError as exc:
        raise _dataset_error(exc) from None
    except Exception:
        request_id = _request_id()
        logger.exception("experiment input preparation failed request_id=%s", request_id)
        raise _internal_error("experiment_failed", request_id) from None

    try:
        return await run_in_threadpool(_execute_experiment, bundle, config, settings)
    except ExperimentError as exc:
        raise _dataset_error(exc) from None
    except Exception:
        request_id = _request_id()
        logger.exception("experiment execution failed request_id=%s", request_id)
        raise _internal_error("experiment_failed", request_id) from None


async def _run_model_suite_request_content(
    content: bytes,
    options: DatasetOptions,
    config: ModelSuiteConfig,
    settings: Settings,
) -> ExperimentSuiteResult:
    try:
        return await run_in_threadpool(
            _run_model_suite_content, content, options, config, settings
        )
    except HTTPException:
        raise
    except Exception:
        request_id = _request_id()
        logger.exception("model suite execution failed request_id=%s", request_id)
        raise _internal_error("experiment_failed", request_id) from None


def _experiment_request_fingerprint(
    content: bytes, options: DatasetOptions, config: ExperimentConfig
) -> str:
    metadata = json.dumps(
        {
            "options": options.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(content)
    digest.update(b"\x00")
    digest.update(metadata)
    return digest.hexdigest()


def _model_suite_request_fingerprint(
    content: bytes, options: DatasetOptions, config: ModelSuiteConfig
) -> str:
    metadata = json.dumps(
        {
            "options": options.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(content)
    digest.update(b"\x00")
    digest.update(metadata)
    return digest.hexdigest()


def _dataset_error(exc: DatasetError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": exc.code, "message": exc.message, "request_id": _request_id()},
    )


def _settings_for_app(application: FastAPI) -> Settings:
    override = application.dependency_overrides.get(get_settings)
    if override is not None:
        return override()
    return get_settings()


def _get_job_store() -> JobStore:
    store = getattr(app.state, "job_store", None)
    if store is None:
        store = JobStore(_settings_for_app(app).job_store_path)
        app.state.job_store = store
    return store


def _get_job_runner() -> JobRunner:
    runner = getattr(app.state, "job_runner", None)
    if runner is None:
        settings = _settings_for_app(app)
        runner = JobRunner(
            store=_get_job_store(),
            settings=settings,
            execute_job_resolver=lambda: _default_execute_job,
        )
        runner.start()
        app.state.job_runner = runner
    return runner


def _job_create_response(job) -> JobCreateResponse:
    return _job_response(job, JobCreateResponse)


def _job_status_response(job) -> JobStatusResponse:
    return _job_response(job, JobStatusResponse)


def _job_response(job, response_model):
    payload = job.model_dump(include=set(response_model.model_fields))
    return response_model.model_validate(payload)


def _parse_job_manifest(manifest_json: str) -> ExperimentManifest:
    try:
        return ExperimentManifest.model_validate_json(manifest_json)
    except Exception:
        raise _invalid_request_exception() from None


def _get_experiment_limiter() -> ExperimentAdmissionLimiter:
    limiter = getattr(app.state, "experiment_limiter", None)
    if limiter is None:
        limiter = ExperimentAdmissionLimiter()
        app.state.experiment_limiter = limiter
    return limiter


def _get_experiment_idempotency_registry() -> ExperimentIdempotencyRegistry:
    registry = getattr(app.state, "experiment_idempotency_registry", None)
    if registry is None:
        registry = ExperimentIdempotencyRegistry()
        app.state.experiment_idempotency_registry = registry
    return registry


def _get_model_suite_idempotency_registry() -> ModelSuiteIdempotencyRegistry:
    registry = getattr(app.state, "model_suite_idempotency_registry", None)
    if registry is None:
        registry = ModelSuiteIdempotencyRegistry()
        app.state.model_suite_idempotency_registry = registry
    return registry


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


def _run_model_suite_content(
    content: bytes,
    options: DatasetOptions,
    config: ModelSuiteConfig,
    settings: Settings,
) -> ExperimentSuiteResult:
    try:
        bundle = load_dataset(content, options, settings)
        result = run_model_suite(bundle, config)
        save_suite_result(result, settings)
        return result
    except (DatasetError, ExperimentError) as exc:
        raise _dataset_error(exc) from None


def _parse_model_suite_config(
    *,
    models_json: str,
    test_size: float,
    random_state: int,
    drop_duplicates: bool,
    cv_folds: int,
    optimization_metric: Literal["roc_auc", "f1", "recall", "balanced_accuracy"],
    threshold: float,
    n_iter: int,
    use_gpu: bool,
    n_jobs: int,
) -> ModelSuiteConfig:
    try:
        models = json.loads(models_json)
    except json.JSONDecodeError:
        raise _invalid_request_exception() from None
    if not isinstance(models, list) or any(not isinstance(model, str) for model in models):
        raise _invalid_request_exception()
    try:
        return ModelSuiteConfig(
            models=models,
            test_size=test_size,
            random_state=random_state,
            drop_duplicates=drop_duplicates,
            cv_folds=cv_folds,
            optimization_metric=optimization_metric,
            threshold=threshold,
            n_iter=n_iter,
            use_gpu=use_gpu,
            n_jobs=n_jobs,
        )
    except Exception:
        raise _invalid_request_exception() from None


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


def _idempotency_conflict_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "idempotency_conflict",
            "message": "the idempotency key was already used for a different request",
            "request_id": _request_id(),
        },
    )


def _invalid_request_exception() -> HTTPException:
    request_id = _request_id()
    return HTTPException(
        status_code=422,
        detail={
            "code": "invalid_request",
            "message": "request parameters are invalid",
            "request_id": request_id,
        },
    )


def _parse_exclude_columns_inputs(
    *,
    exclude_columns: str | None,
    exclude_columns_json: str | None,
) -> list[str]:
    primary = _parse_exclude_columns_value(exclude_columns)
    legacy = _parse_exclude_columns_value(exclude_columns_json)
    if primary and legacy and primary != legacy:
        raise _invalid_request_exception()
    return primary or legacy


def _parse_exclude_columns_value(raw_value: str | None) -> list[str]:
    if raw_value in {None, ""}:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        raise _invalid_request_exception() from None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _invalid_request_exception()
    return value


def _idempotency_capacity_error() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "code": "idempotency_capacity_reached",
            "message": "the idempotency registry is at capacity",
            "request_id": _request_id(),
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


def _job_capacity_error() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "code": "job_capacity_reached",
            "message": "the job service is at its configured capacity",
            "request_id": _request_id(),
        },
    )


def _job_not_found_error() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "job_not_found",
            "message": "job was not found",
            "request_id": _request_id(),
        },
    )


def _job_result_unavailable_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "job_result_unavailable",
            "message": "job result is not available",
            "request_id": _request_id(),
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


def _default_execute_job(
    *,
    manifest: ExperimentManifest,
    csv_bytes: bytes,
    progress_callback,
    should_stop,
    settings: Settings,
) -> ExperimentSuiteResult:
    if should_stop():
        raise RuntimeError("job execution interrupted")
    bundle = load_dataset(
        csv_bytes,
        DatasetOptions(
            target_column=manifest.target_column,
            target_column_confirmed=True,
            missing_policy=manifest.missing_policy,
            sampling_strategy=manifest.sampling_strategy,
            comparison_mode=manifest.comparison_mode,
            feature_columns=list(manifest.feature_columns),
        ),
        settings,
    )
    if bundle.profile.dataset_id != manifest.dataset_id:
        raise DatasetError(
            "manifest_dataset_mismatch",
            "the uploaded dataset does not match the confirmed manifest",
        )
    config = ModelSuiteConfig(
        models=list(manifest.models),
        test_size=manifest.test_size,
        random_state=manifest.random_state,
        drop_duplicates=False,
        cv_folds=manifest.cv_folds,
        optimization_metric=manifest.optimization_metric,
        threshold=manifest.threshold,
        n_iter=1,
        use_gpu=False,
        n_jobs=1,
    )
    return run_model_suite(
        bundle,
        config,
        experiment_id=create_experiment_id(),
        progress_callback=lambda result, completed, total: progress_callback(
            result.model, completed, total
        ),
    )
