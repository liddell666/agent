from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from real_regression_acceptance.dify_client import (
    CANDIDATE_APP_ID,
    DifyClientError,
    DifyWorkflowClient,
)


def test_client_uploads_file_and_runs_blocking_workflow_without_exposing_key(
    tmp_path: Path,
) -> None:
    secret = "app-secret-key-sentinel"
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                request.headers.get("authorization", ""),
            )
        )
        if request.url.path == "/v1/files/upload":
            return httpx.Response(201, json={"id": "file-123"}, request=request)
        payload = json.loads(request.content)
        assert payload["response_mode"] == "blocking"
        assert payload["inputs"]["paper_pdf"] == {
            "transfer_method": "local_file",
            "upload_file_id": "file-123",
            "type": "document",
        }
        return httpx.Response(
            200,
            json={
                "workflow_run_id": "80c9fa4f-2693-4142-8af3-52f15e5aba17",
                "data": {
                    "status": "succeeded",
                    "outputs": {"validation_json": '{"valid":true}'},
                    "elapsed_time": 1.25,
                },
            },
            request=request,
        )

    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.7\nfixture")
    client = DifyWorkflowClient(
        base_url="http://localhost",
        api_key=secret,
        expected_app_id=CANDIDATE_APP_ID,
        transport=httpx.MockTransport(handler),
    )

    file_id = client.upload_file(paper, "acceptance-energy")
    outcome = client.run(
        {
            "paper_pdf": client.file_input(file_id),
            "run_mode": "prepare",
        },
        "acceptance-energy",
    )

    assert outcome.status == "succeeded"
    assert outcome.outputs == {"validation_json": '{"valid":true}'}
    assert all(item[2] == f"Bearer {secret}" for item in seen)
    assert secret not in repr(outcome)


def test_client_redacts_remote_error_body_and_api_key() -> None:
    secret = "app-secret-key-sentinel"
    body_secret = "remote-body-secret-sentinel"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, text=body_secret, request=request)
    )
    client = DifyWorkflowClient(
        base_url="http://localhost",
        api_key=secret,
        expected_app_id=CANDIDATE_APP_ID,
        transport=transport,
    )

    with pytest.raises(DifyClientError) as raised:
        client.run({}, "acceptance-energy")

    message = str(raised.value)
    assert "workflow_request_failed" in message
    assert "status=500" in message
    assert secret not in message
    assert body_secret not in message


def test_client_rejects_wrong_candidate_identity_before_request() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={}, request=request)

    with pytest.raises(ValueError, match="candidate App identity"):
        DifyWorkflowClient(
            base_url="http://localhost",
            api_key="secret",
            expected_app_id="00000000-0000-0000-0000-000000000000",
            transport=httpx.MockTransport(handler),
        )

    assert called is False


def test_client_rejects_malformed_success_payload_without_echoing_it() -> None:
    sentinel = "malformed-payload-secret"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"data": {"status": "succeeded", "outputs": sentinel}},
            request=request,
        )
    )
    client = DifyWorkflowClient(
        base_url="http://localhost",
        api_key="secret",
        expected_app_id=CANDIDATE_APP_ID,
        transport=transport,
    )

    with pytest.raises(DifyClientError) as raised:
        client.run({}, "acceptance-energy")

    assert str(raised.value) == "workflow_response_invalid status=200"
    assert sentinel not in str(raised.value)
