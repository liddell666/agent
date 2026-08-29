from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError


CANDIDATE_APP_ID = "17fe51d4-091f-4729-87ee-3c0a2e920918"
OLLAMA_PROVIDER = "langgenius/ollama/ollama"
OLLAMA_MODEL = "qwen3:8b"
LOCAL_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_TIMEOUT_SECONDS = 120
SCHEMA = "ollama-readiness/v1"
SYNTHETIC_PROMPT = "Reply with exactly: ready"
ALLOWED_FAILURE_PHASES = {"transport", "validation", "prerequisite"}
ALLOWED_FAILURE_CATEGORIES = {
    "connection_error",
    "empty_response",
    "http_error",
    "incomplete_response",
    "invalid_json",
    "invalid_result",
    "prerequisite_failed",
    "provider_error",
    "subprocess_failed",
    "timeout",
    "unexpected_error",
}


def _safe_duration(start: float, end: float, timeout: float) -> float:
    return round(min(max(end - start, 0.0), max(timeout, 0.0)), 6)


def _response_metadata(content: str) -> dict[str, object]:
    encoded = content.encode("utf-8")
    return {
        "response_nonempty": True,
        "response_length": len(content),
        "response_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _empty_response_metadata() -> dict[str, object]:
    return {
        "response_nonempty": False,
        "response_length": 0,
        "response_sha256": None,
    }


def failed_probe(
    *,
    provider: str,
    mode: str,
    duration_seconds: float,
    phase: str,
    category: str,
) -> dict[str, object]:
    safe_phase = phase if phase in ALLOWED_FAILURE_PHASES else "validation"
    safe_category = category if category in ALLOWED_FAILURE_CATEGORIES else "unexpected_error"
    return {
        "status": "failed",
        "provider": provider,
        "model": OLLAMA_MODEL,
        "mode": mode,
        "duration_seconds": duration_seconds,
        **_empty_response_metadata(),
        "failure_phase": safe_phase,
        "failure_category": safe_category,
    }


def _successful_probe(
    *,
    provider: str,
    mode: str,
    duration_seconds: float,
    response: str | None = None,
    response_metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    metadata = dict(response_metadata or _response_metadata(response or ""))
    return {
        "status": "ready",
        "provider": provider,
        "model": OLLAMA_MODEL,
        "mode": mode,
        "duration_seconds": duration_seconds,
        **metadata,
    }


def probe_local_ollama(
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    start = monotonic()
    phase = "transport"
    category = "connection_error"
    try:
        payload = json.dumps(
            {
                "model": OLLAMA_MODEL,
                "prompt": SYNTHETIC_PROMPT,
                "stream": False,
                "think": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            LOCAL_OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener(request, timeout=timeout) as response:
            raw_body = response.read()
        phase = "validation"
        category = "invalid_json"
        document = json.loads(raw_body)
        if not isinstance(document, dict):
            raise ValueError("response must be an object")
        if document.get("error"):
            category = "provider_error"
            raise RuntimeError("provider reported an error")
        content = document.get("response")
        if document.get("done") is not True:
            category = "incomplete_response"
            raise RuntimeError("response did not finish")
        if not isinstance(content, str) or not content.strip():
            category = "empty_response"
            raise RuntimeError("response was empty")
        duration = _safe_duration(start, monotonic(), timeout)
        return _successful_probe(
            provider="ollama",
            mode="direct_http",
            duration_seconds=duration,
            response=content,
        )
    except HTTPError:
        phase, category = "transport", "http_error"
    except (TimeoutError, subprocess.TimeoutExpired):
        phase, category = "transport", "timeout"
    except URLError:
        phase, category = "transport", "connection_error"
    except json.JSONDecodeError:
        phase, category = "validation", "invalid_json"
    except Exception:
        pass
    duration = _safe_duration(start, monotonic(), timeout)
    return failed_probe(
        provider="ollama",
        mode="direct_http",
        duration_seconds=duration,
        phase=phase,
        category=category,
    )


DIFY_CHILD_SOURCE = f'''import contextlib
import hashlib
import io
import json
import sys

APP_ID = {CANDIDATE_APP_ID!r}
PROVIDER = {OLLAMA_PROVIDER!r}
MODEL = {OLLAMA_MODEL!r}
PROMPT = {SYNTHETIC_PROMPT!r}

def main():
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            from app import create_app
            from core.model_manager import ModelManager
            from core.model_runtime.entities.message_entities import UserPromptMessage
            from core.model_runtime.entities.model_entities import ModelType
            from extensions.ext_database import db
            from models.model import App

            flask_app = create_app()
            with flask_app.app_context():
                candidate = db.session.get(App, APP_ID)
                if candidate is None:
                    raise RuntimeError("candidate not found")
                model_instance = ModelManager.for_tenant(candidate.tenant_id).get_model_instance(
                    candidate.tenant_id,
                    PROVIDER,
                    ModelType.LLM,
                    MODEL,
                )
                result = model_instance.invoke_llm(
                    prompt_messages=[UserPromptMessage(content=PROMPT)],
                    model_parameters={{"think": False}},
                    tools=[],
                    stop=[],
                    stream=False,
                    user="ollama-readiness",
                )
                completion = getattr(getattr(result, "message", None), "content", None)
                if not isinstance(completion, str) or not completion.strip():
                    raise RuntimeError("empty completion")
        encoded = completion.encode("utf-8")
        print(json.dumps({{
            "ok": True,
            "response_nonempty": True,
            "response_length": len(completion),
            "response_sha256": hashlib.sha256(encoded).hexdigest(),
        }}, sort_keys=True))
        return 0
    except BaseException:
        print(json.dumps({{"ok": False}}, sort_keys=True))
        return 2

raise SystemExit(main())
'''


def probe_dify_model_boundary(
    *,
    runner: Callable[..., Any] = subprocess.run,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    start = monotonic()
    phase = "transport"
    category = "subprocess_failed"
    try:
        completed = runner(
            [
                "docker",
                "exec",
                "docker-api-1",
                "/app/api/.venv/bin/python",
                "-c",
                DIFY_CHILD_SOURCE,
            ],
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("child failed")
        phase, category = "validation", "invalid_result"
        document = json.loads(completed.stdout)
        if not isinstance(document, dict) or document.get("ok") is not True:
            raise ValueError("invalid child result")
        nonempty = document.get("response_nonempty")
        length = document.get("response_length")
        digest = document.get("response_sha256")
        if nonempty is not True or not isinstance(length, int) or length <= 0:
            category = "empty_response"
            raise ValueError("empty child result")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("invalid response digest")
        duration = _safe_duration(start, monotonic(), timeout)
        return _successful_probe(
            provider=OLLAMA_PROVIDER,
            mode="dify_model_boundary",
            duration_seconds=duration,
            response_metadata={
                "response_nonempty": True,
                "response_length": length,
                "response_sha256": digest,
            },
        )
    except subprocess.TimeoutExpired:
        phase, category = "transport", "timeout"
    except json.JSONDecodeError:
        phase, category = "validation", "invalid_result"
    except Exception:
        pass
    duration = _safe_duration(start, monotonic(), timeout)
    return failed_probe(
        provider=OLLAMA_PROVIDER,
        mode="dify_model_boundary",
        duration_seconds=duration,
        phase=phase,
        category=category,
    )


def build_evidence(
    local_probe: Mapping[str, object],
    dify_probe: Mapping[str, object] | None,
    *,
    timestamp: str | None = None,
) -> dict[str, object]:
    def sanitize(
        probe: Mapping[str, object],
        *,
        provider: str,
        mode: str,
    ) -> dict[str, object]:
        status = probe.get("status")
        if status not in {"ready", "failed", "not_run"}:
            status = "failed"
        duration = probe.get("duration_seconds")
        safe_duration = (
            round(min(max(float(duration), 0.0), DEFAULT_TIMEOUT_SECONDS), 6)
            if isinstance(duration, (int, float)) and not isinstance(duration, bool)
            else 0.0
        )
        length = probe.get("response_length")
        digest = probe.get("response_sha256")
        response_valid = (
            probe.get("response_nonempty") is True
            and isinstance(length, int)
            and not isinstance(length, bool)
            and length > 0
            and isinstance(digest, str)
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
        )
        safe: dict[str, object] = {
            "status": status,
            "provider": provider,
            "model": OLLAMA_MODEL,
            "mode": mode,
            "duration_seconds": safe_duration,
            **(
                {
                    "response_nonempty": True,
                    "response_length": length,
                    "response_sha256": digest,
                }
                if response_valid
                else _empty_response_metadata()
            ),
        }
        if status != "ready":
            phase = probe.get("failure_phase")
            category = probe.get("failure_category")
            safe["failure_phase"] = (
                phase if phase in ALLOWED_FAILURE_PHASES else "validation"
            )
            safe["failure_category"] = (
                category if category in ALLOWED_FAILURE_CATEGORIES else "unexpected_error"
            )
        return safe

    local_safe = sanitize(local_probe, provider="ollama", mode="direct_http")
    if dify_probe is None:
        dify_probe = failed_probe(
            provider=OLLAMA_PROVIDER,
            mode="dify_model_boundary",
            duration_seconds=0.0,
            phase="prerequisite",
            category="prerequisite_failed",
        )
        dify_probe["status"] = "not_run"
    dify_safe = sanitize(
        dify_probe,
        provider=OLLAMA_PROVIDER,
        mode="dify_model_boundary",
    )
    probes: dict[str, object] = {
        "local_ollama": local_safe,
        "dify_model_boundary": dify_safe,
    }
    ready = dify_probe is not None and all(
        probe.get("status") == "ready" for probe in (local_safe, dify_safe)
    )
    return {
        "schema": SCHEMA,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "ready" if ready else "failed",
        "probes": probes,
    }


def write_evidence_atomic(path: Path, evidence: Mapping[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(evidence, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def main(
    argv: Sequence[str] | None = None,
    *,
    local_probe_fn: Callable[[], dict[str, object]] = probe_local_ollama,
    dify_probe_fn: Callable[[], dict[str, object]] = probe_dify_model_boundary,
) -> int:
    parser = argparse.ArgumentParser(description="Run privacy-safe Ollama readiness probes.")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)

    local_probe = local_probe_fn()
    dify_probe = None
    if local_probe.get("status") == "ready":
        dify_probe = dify_probe_fn()
    evidence = build_evidence(local_probe, dify_probe)
    write_evidence_atomic(arguments.output, evidence)
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
