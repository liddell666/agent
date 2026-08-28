from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping

import httpx


CANDIDATE_APP_ID = "17fe51d4-091f-4729-87ee-3c0a2e920918"
FILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class DifyClientError(RuntimeError):
    """A redacted Dify transport or response failure."""


@dataclass(frozen=True)
class WorkflowOutcome:
    run_id: str
    status: str
    outputs: dict[str, object]
    elapsed_seconds: float


class DifyWorkflowClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        expected_app_id: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if expected_app_id != CANDIDATE_APP_ID:
            raise ValueError("candidate App identity does not match the acceptance boundary")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("Dify API key is required")
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise ValueError("Dify base URL is invalid")
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._transport = transport

    def _client(self, *, workflow: bool = False) -> httpx.Client:
        timeout = (
            httpx.Timeout(900.0, connect=15.0)
            if workflow
            else httpx.Timeout(60.0, connect=15.0)
        )
        return httpx.Client(
            headers=self._headers,
            timeout=timeout,
            transport=self._transport,
        )

    @staticmethod
    def file_input(file_id: str) -> dict[str, str]:
        if FILE_ID_RE.fullmatch(file_id) is None:
            raise ValueError("Dify upload ID is invalid")
        return {
            "transfer_method": "local_file",
            "upload_file_id": file_id,
            "type": "document",
        }

    def upload_file(self, path: Path, user: str) -> str:
        try:
            with path.open("rb") as stream, self._client() as client:
                response = client.post(
                    f"{self._base_url}/v1/files/upload",
                    data={"user": user},
                    files={"file": (path.name, stream, "application/octet-stream")},
                )
        except (OSError, httpx.HTTPError) as exc:
            raise DifyClientError("file_upload_failed status=unavailable") from exc
        if response.status_code not in {200, 201}:
            raise DifyClientError(f"file_upload_failed status={response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DifyClientError(
                f"file_upload_response_invalid status={response.status_code}"
            ) from exc
        file_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(file_id, str) or FILE_ID_RE.fullmatch(file_id) is None:
            raise DifyClientError(
                f"file_upload_response_invalid status={response.status_code}"
            )
        return file_id

    def run(self, inputs: Mapping[str, object], user: str) -> WorkflowOutcome:
        payload = {
            "inputs": dict(inputs),
            "response_mode": "blocking",
            "user": user,
        }
        try:
            with self._client(workflow=True) as client:
                response = client.post(
                    f"{self._base_url}/v1/workflows/run",
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise DifyClientError("workflow_request_failed status=unavailable") from exc
        if response.status_code != 200:
            raise DifyClientError(
                f"workflow_request_failed status={response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise DifyClientError("workflow_response_invalid status=200") from exc
        if not isinstance(body, dict):
            raise DifyClientError("workflow_response_invalid status=200")
        data = body.get("data")
        run_id = body.get("workflow_run_id")
        if not isinstance(data, dict):
            raise DifyClientError("workflow_response_invalid status=200")
        outputs = data.get("outputs")
        status = data.get("status")
        elapsed = data.get("elapsed_time", 0.0)
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(status, str)
            or not isinstance(outputs, dict)
            or not isinstance(elapsed, (int, float))
        ):
            raise DifyClientError("workflow_response_invalid status=200")
        return WorkflowOutcome(
            run_id=run_id,
            status=status,
            outputs=dict(outputs),
            elapsed_seconds=max(0.0, float(elapsed)),
        )
