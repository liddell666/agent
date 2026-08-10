import asyncio
import json
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from repro_runner import api
from repro_runner.config import Settings, get_settings


def _csv(rows: int = 40) -> bytes:
    data = ["x1,x2,Y_cls"]
    for index in range(rows):
        data.append(f"{index % 2},{(index // 2) % 2},{index % 2}")
    return ("\n".join(data) + "\n").encode()


def _dossier() -> bytes:
    return json.dumps(
        {
            "title": "Minimal Paper",
            "research_problem": "Binary classification.",
            "task_type": "classification",
            "datasets": [],
            "methods": [],
            "metrics": [
                {
                    "name": "AUC",
                    "reported_value": "91%",
                    "evidence": [
                        {
                            "page": 1,
                            "source_text": "AUC is 91%.",
                            "source": "paper",
                        }
                    ],
                }
            ],
            "gaps": [],
        }
    ).encode()


@pytest.fixture
def client(tmp_path) -> TestClient:
    settings = Settings(storage_dir=tmp_path, max_upload_mb=1)
    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()


@pytest.fixture
def client_with_raised_dossier_limits(tmp_path) -> TestClient:
    settings = Settings(
        storage_dir=tmp_path,
        max_upload_mb=1,
        max_dossier_mb=20,
        max_metric_overrides_kb=256,
    )
    api.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()


def test_healthz_is_public(client: TestClient):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_parse_dossier_returns_normalized_metrics(client: TestClient):
    response = client.post(
        "/v1/parse-dossier",
        files={"file": ("paper.json", _dossier(), "application/json")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["metrics"][0]["reported_value"] == 0.91


def test_parse_dossier_returns_validation_error_for_bad_dossier_json(client: TestClient):
    response = client.post(
        "/v1/parse-dossier",
        files={"file": ("paper.json", b"{", "application/json")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"][0]["code"] == "invalid_dossier_json"


def test_parse_dossier_rejects_large_file_without_calling_parser(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    called = False

    def unexpected_parser(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("parser must not be called")

    monkeypatch.setattr(api, "parse_dossier", unexpected_parser)
    response = client.post(
        "/v1/parse-dossier",
        files={
            "file": (
                "large.json",
                b"x" * (5 * 1024 * 1024 + 1),
                "application/json",
            )
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "dossier_file_too_large"
    assert called is False


def test_parse_dossier_hard_cap_cannot_be_raised_by_settings(
    client_with_raised_dossier_limits: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    called = False

    def unexpected_parser(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("parser must not be called")

    monkeypatch.setattr(api, "parse_dossier", unexpected_parser)
    response = client_with_raised_dossier_limits.post(
        "/v1/parse-dossier",
        files={
            "file": (
                "large.json",
                b"x" * (5 * 1024 * 1024 + 1),
                "application/json",
            )
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "dossier_file_too_large"
    assert called is False


def test_parse_dossier_sanitizes_unexpected_parser_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.ERROR, logger="repro_runner.api")

    def fail_parser(*_args, **_kwargs):
        raise RuntimeError("private dossier parser detail")

    monkeypatch.setattr(api, "parse_dossier", fail_parser)
    response = client.post(
        "/v1/parse-dossier",
        files={"file": ("paper.json", _dossier(), "application/json")},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "dossier_parse_failed"
    assert detail["request_id"]
    assert "private dossier parser detail" not in response.text
    assert "dossier parsing failed" in caplog.text
    assert detail["request_id"] in caplog.text
    assert "private dossier parser detail" not in caplog.text
    assert "Traceback" not in caplog.text


def test_parse_dossier_rejects_oversized_metric_overrides_semantically(
    client: TestClient,
):
    response = client.post(
        "/v1/parse-dossier",
        data={"metric_overrides_json": "x" * (64 * 1024 + 1)},
        files={"file": ("paper.json", _dossier(), "application/json")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"][0]["code"] == "invalid_metric_overrides"


def test_metric_override_hard_cap_cannot_be_raised_by_settings(
    client_with_raised_dossier_limits: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    called = False

    def unexpected_parser(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("parser must not be called")

    monkeypatch.setattr(api, "parse_dossier", unexpected_parser)
    response = client_with_raised_dossier_limits.post(
        "/v1/parse-dossier",
        data={"metric_overrides_json": "x" * (64 * 1024 + 1)},
        files={"file": ("paper.json", _dossier(), "application/json")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["errors"][0]["code"] == "invalid_metric_overrides"
    assert called is False


def test_validate_dataset_returns_profile(client: TestClient):
    response = client.post(
        "/v1/validate-dataset",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["dataset"]["target"] == "Y_cls"


def test_run_experiment_returns_id_and_metrics_and_persists_it(client: TestClient):
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 200
    result = response.json()
    fetched = client.get(f"/v1/experiments/{result['experiment_id']}")

    assert result["experiment_id"].startswith("exp-")
    assert "roc_auc" in result["metrics"]
    assert result["reproducibility_status"] == "baseline_only"
    assert fetched.status_code == 200
    assert fetched.json() == result


def test_run_experiment_same_idempotency_key_executes_and_saves_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    train_calls = 0
    save_calls = 0
    original_train = api.run_random_forest
    original_save = api.save_result

    def counted_train(*args, **kwargs):
        nonlocal train_calls
        train_calls += 1
        return original_train(*args, **kwargs)

    def counted_save(*args, **kwargs):
        nonlocal save_calls
        save_calls += 1
        return original_save(*args, **kwargs)

    monkeypatch.setattr(api, "run_random_forest", counted_train)
    monkeypatch.setattr(api, "save_result", counted_save)
    request = {
        "data": {"idempotency_key": "dify-run-123"},
        "files": {"file": ("data.csv", _csv(), "text/csv")},
    }

    first = client.post("/v1/run-experiment", **request)
    second = client.post("/v1/run-experiment", **request)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert train_calls == 1
    assert save_calls == 1


def test_run_experiment_rejects_idempotency_key_reuse_with_different_input(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    train_calls = 0
    original_train = api.run_random_forest

    def counted_train(*args, **kwargs):
        nonlocal train_calls
        train_calls += 1
        return original_train(*args, **kwargs)

    monkeypatch.setattr(api, "run_random_forest", counted_train)
    first = client.post(
        "/v1/run-experiment",
        data={"idempotency_key": "dify-run-conflict"},
        files={"file": ("data.csv", _csv(), "text/csv")},
    )
    conflict = client.post(
        "/v1/run-experiment",
        data={"idempotency_key": "dify-run-conflict", "random_state": "7"},
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["code"] == "idempotency_conflict"
    assert detail["request_id"]
    assert "dify-run-conflict" not in conflict.text
    assert train_calls == 1


def test_run_experiment_without_idempotency_key_remains_independent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    train_calls = 0
    original_train = api.run_random_forest

    def counted_train(*args, **kwargs):
        nonlocal train_calls
        train_calls += 1
        return original_train(*args, **kwargs)

    monkeypatch.setattr(api, "run_random_forest", counted_train)
    request = {"files": {"file": ("data.csv", _csv(), "text/csv")}}

    first = client.post("/v1/run-experiment", **request)
    second = client.post("/v1/run-experiment", **request)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["experiment_id"] != second.json()["experiment_id"]
    assert train_calls == 2


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"x1\n1\n", "missing_target_column"),
        (b"x1,Y_cls\n1,0\n2,0\n", "invalid_target_classes"),
        (b"x1,Y_cls\n1,0\n,1\n", "missing_values"),
        (b"x1,Y_cls\na,0\n2,1\n", "non_numeric_feature"),
        (b"x1,Y_cls\nNaN,0\n2,1\n", "non_finite_numeric_feature"),
        (b"x1,Y_cls\n1,0,extra\n2,1\n", "invalid_csv"),
    ],
)
def test_validate_dataset_returns_200_with_structured_errors(
    client: TestClient, content: bytes, code: str
):
    response = client.post(
        "/v1/validate-dataset",
        files={"file": ("data.csv", content, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["dataset"] is None
    assert body["errors"] == [
        {"code": code, "message": pytest.ANY if hasattr(pytest, "ANY") else body["errors"][0]["message"]}
    ]
    assert body["errors"][0]["message"]


def test_run_experiment_keeps_dataset_failures_as_422(client: TestClient):
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", b"x1\n1\n", "text/csv")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "missing_target_column"


def test_missing_experiment_has_not_found_code_and_request_id(client: TestClient):
    response = client.get("/v1/experiments/exp-20260805T010203Z-deadbeef")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_not_found"
    assert detail["request_id"]


@pytest.mark.parametrize("entrypoint", ["get", "compare"])
def test_corrupt_stored_result_returns_409_not_not_found(
    client: TestClient, tmp_path, entrypoint: str
):
    experiment_id = "exp-20260805T010203Z-deadbeef"
    directory = tmp_path / experiment_id
    directory.mkdir()
    (directory / "result.json").write_bytes(b"{not-json")

    if entrypoint == "get":
        response = client.get(f"/v1/experiments/{experiment_id}")
    else:
        response = client.post(
            "/v1/compare-result",
            json={"experiment_id": experiment_id, "reported_metrics": []},
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_result_incompatible"
    assert detail["request_id"]


def test_compare_result_uses_persisted_experiment(client: TestClient):
    result = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    ).json()
    response = client.post(
        "/v1/compare-result",
        json={
            "experiment_id": result["experiment_id"],
            "reported_metrics": [
                {
                    "name": "AUC",
                    "reported_value": 0.91,
                    "dataset": "test",
                    "split": "test",
                    "dataset_id": result["dataset"]["dataset_id"],
                    "test_size": result["split_provenance"]["test_size"],
                    "random_state": result["split_provenance"]["random_state"],
                    "train_rows": result["split_provenance"]["train_rows"],
                    "test_rows": result["split_provenance"]["test_rows"],
                    "test_digest": result["split_provenance"]["test_digest"],
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["experiment_id"] == result["experiment_id"]
    assert response.json()["items"][0]["comparable"] is True


def test_run_experiment_returns_invalid_split_as_422(client: TestClient):
    response = client.post(
        "/v1/run-experiment",
        files={
            "file": (
                "data.csv",
                b"x,Y_cls\n1,0\n2,0\n3,1\n4,1\n",
                "text/csv",
            )
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_split"


def test_file_larger_than_configured_limit_is_413_with_request_id(client: TestClient):
    response = client.post(
        "/v1/validate-dataset",
        files={"file": ("large.csv", b"x" * (1024 * 1024 + 1), "text/csv")},
    )

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["code"] == "file_too_large"
    assert detail["request_id"]
    assert "traceback" not in response.text.casefold()


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/v1/run-experiment", None),
        ("/v1/run-experiment", {"model": "other"}),
        ("/v1/run-experiment", {"test_size": "0.9"}),
        ("/v1/run-experiment", {"idempotency_key": "x" * 129}),
    ],
)
def test_invalid_request_is_sanitized_with_request_id(
    client: TestClient, path: str, data: dict[str, str] | None
):
    kwargs = {} if data is None else {
        "data": data,
        "files": {"file": ("data.csv", _csv(), "text/csv")},
    }
    response = client.post(path, **kwargs)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_request"
    assert detail["request_id"]
    assert "traceback" not in response.text.casefold()


def test_training_failure_is_sanitized_with_request_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    def fail_training(*_args, **_kwargs):
        raise RuntimeError("private training stack")

    monkeypatch.setattr(api, "run_random_forest", fail_training)
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "experiment_failed"
    assert detail["request_id"]
    assert "private" not in response.text
    assert "traceback" not in response.text.casefold()


def test_requested_experiment_configuration_is_returned(client: TestClient):
    csv = (
        "x1,label\n" + "\n".join(f"{index},{index % 2}" for index in range(80)) + "\n"
    ).encode()
    response = client.post(
        "/v1/run-experiment",
        data={
            "target_column": "label",
            "random_state": "7",
            "drop_duplicates": "true",
        },
        files={"file": ("data.csv", csv, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dataset"]["target"] == "label"
    assert body["config"]["random_state"] == 7
    assert body["config"]["drop_duplicates"] is True
    assert body["dataset"]["effective_rows"] == 80


def test_run_experiment_admits_request_before_reading_or_parsing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    states: list[str] = []
    original_read = api._read_upload
    original_load = api.load_dataset

    async def observed_read(file, settings):
        assert api._get_experiment_limiter().is_saturated(
            settings.max_concurrent_experiments
        )
        states.append("read")
        return await original_read(file, settings)

    def observed_load(*args, **kwargs):
        settings = args[-1]
        assert api._get_experiment_limiter().is_saturated(
            settings.max_concurrent_experiments
        )
        states.append("parse")
        return original_load(*args, **kwargs)

    monkeypatch.setattr(api, "_read_upload", observed_read)
    monkeypatch.setattr(api, "load_dataset", observed_load)
    response = client.post(
        "/v1/run-experiment",
        files={"file": ("data.csv", _csv(), "text/csv")},
    )

    assert response.status_code == 200
    assert states == ["read", "parse"]


def test_async_admission_rejects_saturation_and_recovers_after_cancellation():
    async def scenario():
        settings = Settings(max_concurrent_experiments=1)
        api.app.state.experiment_limiter = api.ExperimentAdmissionLimiter()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hold_slot():
            async with api._admit_experiment(settings):
                entered.set()
                await release.wait()

        holder = asyncio.create_task(hold_slot())
        await entered.wait()
        with pytest.raises(HTTPException) as error:
            async with api._admit_experiment(settings):
                pass
        assert error.value.status_code == 429
        assert error.value.detail["code"] == "experiment_capacity_reached"

        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder

        async with api._admit_experiment(settings):
            pass

    asyncio.run(scenario())


def test_idempotency_registry_joins_concurrent_identical_requests():
    async def scenario():
        registry = api.ExperimentIdempotencyRegistry(max_entries=2)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "shared-result"

        first = asyncio.create_task(registry.execute("same", "fingerprint", operation))
        await started.wait()
        second = asyncio.create_task(registry.execute("same", "fingerprint", operation))
        await asyncio.sleep(0)
        release.set()

        assert await asyncio.gather(first, second) == [
            "shared-result",
            "shared-result",
        ]
        assert calls == 1

    asyncio.run(scenario())


def test_idempotency_registry_is_bounded_and_safe_across_event_loops():
    registry = api.ExperimentIdempotencyRegistry(max_entries=2)
    calls = 0

    async def run_sequence():
        nonlocal calls

        async def operation(value):
            nonlocal calls
            calls += 1
            return value

        await registry.execute("one", "fp-one", lambda: operation("one"))
        await registry.execute("two", "fp-two", lambda: operation("two"))
        await registry.execute("three", "fp-three", lambda: operation("three"))
        return await registry.execute("one", "fp-one", lambda: operation("one-again"))

    assert asyncio.run(run_sequence()) == "one-again"
    assert calls == 4

    async def new_loop():
        async def operation():
            return "new-loop"

        return await registry.execute("loop-key", "loop-fingerprint", operation)

    assert asyncio.run(new_loop()) == "new-loop"
