from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Callable, Sequence

import httpx

from .acquisition import AcquiredCase, AcquisitionError, acquire_case
from .dify_client import CANDIDATE_APP_ID, DifyWorkflowClient
from .evidence import evidence_payload, evaluate_corpus, scan_for_leaks
from .models import SafeCaseResult, load_registry
from .runner import CheckpointStore, run_case


DEFAULT_CACHE = Path(".real-world-cache")
DEFAULT_DIFY_URL = "http://localhost"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="real-regression-acceptance")
    commands = parser.add_subparsers(dest="command", required=True)

    pin = commands.add_parser("pin")
    pin.add_argument("--url", required=True)
    pin.add_argument(
        "--media-type",
        required=True,
        choices=("application/pdf", "application/zip"),
    )
    pin.add_argument("--max-bytes", type=int, required=True)

    acquire = commands.add_parser("acquire")
    acquire.add_argument("--registry", type=Path, required=True)
    acquire.add_argument("--cache", type=Path, default=DEFAULT_CACHE)

    run = commands.add_parser("run")
    run.add_argument("--registry", type=Path, required=True)
    run.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--base-url", default=DEFAULT_DIFY_URL)
    run.add_argument("--resume", action="store_true")

    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--input", type=Path, required=True)
    return parser


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _atomic_json(path: Path, payload: object) -> None:
    if scan_for_leaks(payload):
        raise ValueError("privacy_gate_failed")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(_canonical_json(payload), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _pin(
    *,
    url: str,
    media_type: str,
    max_bytes: int,
    transport: httpx.BaseTransport | None,
) -> dict[str, object]:
    if not url.startswith("https://") or max_bytes < 1 or max_bytes > 50_000_000:
        raise AcquisitionError("pin_options_invalid")
    body = bytearray()
    try:
        with httpx.Client(
            follow_redirects=True,
            max_redirects=5,
            timeout=httpx.Timeout(60.0, connect=10.0),
            transport=transport,
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                if response.url.scheme != "https":
                    raise AcquisitionError("insecure_redirect")
                final_url = str(response.url)
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise AcquisitionError("source_too_large")
    except AcquisitionError:
        raise
    except httpx.HTTPError as exc:
        raise AcquisitionError("source_download_failed") from exc
    signature = "pdf" if body.startswith(b"%PDF-") else "zip" if body.startswith(b"PK") else "invalid"
    expected_signature = "pdf" if media_type == "application/pdf" else "zip"
    if signature != expected_signature:
        raise AcquisitionError("source_signature_invalid")
    return {
        "byte_count": len(body),
        "final_url": final_url,
        "media_type": media_type,
        "sha256": "sha256:" + sha256(body).hexdigest(),
        "signature": signature,
    }


def _load_evidence(path: Path) -> tuple[str, list[SafeCaseResult]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("evidence_invalid")
    candidate_app_id = payload.get("candidate_app_id")
    raw_results = payload.get("results")
    if candidate_app_id != CANDIDATE_APP_ID or not isinstance(raw_results, list):
        raise ValueError("evidence_invalid")
    return candidate_app_id, [SafeCaseResult.model_validate(item) for item in raw_results]


def _existing_results(path: Path, *, resume: bool) -> dict[str, SafeCaseResult]:
    if not resume or not path.exists():
        return {}
    _, results = _load_evidence(path)
    return {result.case_id: result for result in results}


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    acquirer: Callable[..., AcquiredCase] = acquire_case,
    client_factory: Callable[..., DifyWorkflowClient] = DifyWorkflowClient,
) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "pin":
            print(
                _canonical_json(
                    _pin(
                        url=arguments.url,
                        media_type=arguments.media_type,
                        max_bytes=arguments.max_bytes,
                        transport=transport,
                    )
                ),
                end="",
            )
            return 0

        if arguments.command == "evaluate":
            candidate_app_id, results = _load_evidence(arguments.input)
            evaluation = evaluate_corpus(results)
            payload = evidence_payload(
                results,
                evaluation,
                candidate_app_id=candidate_app_id,
            )
            _atomic_json(arguments.input, payload)
            print(_canonical_json(evaluation.model_dump(mode="json")), end="")
            return 0 if evaluation.passed else 1

        if arguments.command == "run":
            api_key = os.environ.get("DIFY_REGRESSION_API_KEY")
            if not api_key:
                print("DIFY_REGRESSION_API_KEY is required", file=sys.stderr)
                return 2

        registry = load_registry(arguments.registry)
        if arguments.command == "acquire":
            for case in registry.cases:
                acquirer(case, arguments.cache, transport)
            print(f"verified={len(registry.cases)}")
            return 0

        client = client_factory(
            base_url=arguments.base_url,
            api_key=api_key,
            expected_app_id=registry.candidate_app_id,
            transport=transport,
        )
        existing = _existing_results(arguments.output, resume=arguments.resume)
        results: list[SafeCaseResult] = []
        checkpoint_root = arguments.output.parent / ".real-regression-checkpoints"
        for case in registry.cases:
            previous = existing.get(case.id)
            if (
                previous is not None
                and previous.paper_digest == case.paper.sha256
                and previous.dataset_digest == case.dataset.sha256
            ):
                results.append(previous)
                continue
            acquired = acquirer(case, arguments.cache, transport)
            results.append(
                run_case(
                    case,
                    acquired,
                    client,
                    CheckpointStore(checkpoint_root / f"{case.id}.json"),
                )
            )
        evaluation = evaluate_corpus(results)
        payload = evidence_payload(
            results,
            evaluation,
            candidate_app_id=registry.candidate_app_id,
        )
        _atomic_json(arguments.output, payload)
        print(_canonical_json(evaluation.model_dump(mode="json")), end="")
        return 0 if evaluation.passed else 1
    except (AcquisitionError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
