from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
import yaml

from real_regression_acceptance.acquisition import AcquiredCase
from real_regression_acceptance.cli import main
from real_regression_acceptance.models import SafeCaseResult


APP_ID = "17fe51d4-091f-4729-87ee-3c0a2e920918"
DIGEST = "sha256:" + "a" * 64
TEST_DIGEST = "sha256:" + "b" * 64


def test_repository_entry_point_runs_without_installed_package() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/run_real_regression_acceptance.py", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0
    assert "real-regression-acceptance" in completed.stdout


def _registry(tmp_path: Path) -> Path:
    cases = []
    for index in range(5):
        cases.append(
            {
                "id": f"case-{index}",
                "paper": {
                    "url": f"https://sources.example/case-{index}.pdf",
                    "sha256": DIGEST,
                    "media_type": "application/pdf",
                    "max_bytes": 1000,
                    "attribution": "Fixture paper",
                    "rights": "Fixture terms",
                },
                "dataset": {
                    "url": f"https://sources.example/case-{index}.zip",
                    "sha256": DIGEST,
                    "media_type": "application/zip",
                    "max_bytes": 1000,
                    "attribution": "Fixture dataset",
                    "rights": "Fixture terms",
                },
                "transform": {"member": "data.csv", "format": "csv"},
                "target_column": "target",
                "task_type": "regression",
            }
        )
    path = tmp_path / "registry.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "candidate_app_id": APP_ID,
                "cases": cases,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _success(case_id: str) -> SafeCaseResult:
    return SafeCaseResult(
        case_id=case_id,
        paper_digest=DIGEST,
        dataset_digest=DIGEST,
        task_type="regression",
        status="succeeded",
        metrics={"linear_regression": {"rmse": 1.0}},
        model_statuses={"linear_regression": "succeeded"},
        strict_status="not_comparable",
        strict_reason_codes=("paper_dataset_identity_missing",),
        test_digest=TEST_DIGEST,
        elapsed_seconds=1.0,
    )


def test_pin_prints_identity_not_remote_body(capsys: pytest.CaptureFixture[str]) -> None:
    body = b"%PDF-1.7\nprivate paper body sentinel"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=body, request=request)
    )

    exit_code = main(
        [
            "pin",
            "--url",
            "https://sources.example/paper.pdf",
            "--media-type",
            "application/pdf",
            "--max-bytes",
            "1000",
        ],
        transport=transport,
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "sha256:" + sha256(body).hexdigest() in output
    assert "private paper body sentinel" not in output


def test_acquire_prints_only_safe_case_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paper = tmp_path / "paper.pdf"
    csv = tmp_path / "data.csv"
    paper.write_bytes(b"%PDF-private-paper-sentinel")
    csv.write_text("raw,row,sentinel\n", encoding="utf-8")

    def fake_acquirer(case, cache_root, transport=None):
        return AcquiredCase(paper, csv, case.paper.sha256, case.dataset.sha256, 20)

    exit_code = main(
        ["acquire", "--registry", str(_registry(tmp_path)), "--cache", str(tmp_path / "cache")],
        acquirer=fake_acquirer,
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "verified=5" in output
    assert "private-paper-sentinel" not in output
    assert "raw,row,sentinel" not in output


def test_run_requires_api_key_before_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DIFY_REGRESSION_API_KEY", raising=False)

    exit_code = main(
        ["run", "--registry", str(_registry(tmp_path)), "--output", str(tmp_path / "out.json")]
    )

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "DIFY_REGRESSION_API_KEY is required"


def test_evaluate_rewrites_canonical_safe_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "evidence.json"
    payload = {
        "schema_version": 1,
        "candidate_app_id": APP_ID,
        "results": [_success(f"case-{index}").model_dump(mode="json") for index in range(5)],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(["evaluate", "--input", str(path)])

    assert exit_code == 0
    output = json.loads(path.read_text(encoding="utf-8"))
    assert output["evaluation"]["passed"] is True
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert '  "candidate_app_id"' in path.read_text(encoding="utf-8")
    assert json.loads(capsys.readouterr().out)["passed"] is True
