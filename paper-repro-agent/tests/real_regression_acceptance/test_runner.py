from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from real_regression_acceptance.acquisition import AcquiredCase
from real_regression_acceptance.dify_client import WorkflowOutcome
from real_regression_acceptance.models import AcceptanceCase
from real_regression_acceptance.runner import CheckpointStore, run_case


DIGEST = "sha256:" + "a" * 64
TEST_DIGEST = "sha256:" + "c" * 64


def _case() -> AcceptanceCase:
    return AcceptanceCase.model_validate(
        {
            "id": "energy-efficiency",
            "paper": {
                "url": "https://sources.example/paper.pdf",
                "sha256": DIGEST,
                    "media_type": "application/pdf",
                    "max_bytes": 1000,
                    "attribution": "Fixture paper",
                    "rights": "Fixture terms",
            },
            "dataset": {
                "url": "https://sources.example/data.zip",
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


def _acquired(tmp_path: Path) -> AcquiredCase:
    paper = tmp_path / "paper.pdf"
    csv = tmp_path / "dataset.csv"
    paper.write_bytes(b"%PDF-1.7")
    csv.write_text("x,target\n" + "\n".join(f"{i},{i}" for i in range(20)), encoding="utf-8")
    return AcquiredCase(paper, csv, DIGEST, DIGEST, 20)


class FakeClient:
    def __init__(self, *, token: str = "protocol-secret-sentinel") -> None:
        self.token = token
        self.uploads: list[tuple[Path, str]] = []
        self.calls: list[tuple[dict[str, object], str]] = []

    @staticmethod
    def file_input(file_id: str) -> dict[str, str]:
        return {
            "transfer_method": "local_file",
            "upload_file_id": file_id,
            "type": "document",
        }

    def upload_file(self, path: Path, user: str) -> str:
        self.uploads.append((path, user))
        return f"file-{len(self.uploads)}"

    def run(self, inputs: dict[str, object], user: str) -> WorkflowOutcome:
        self.calls.append((inputs, user))
        if inputs["run_mode"] == "prepare":
            return WorkflowOutcome(
                run_id="80c9fa4f-2693-4142-8af3-52f15e5aba17",
                status="succeeded",
                outputs={
                    "protocol_token": self.token,
                    "validation_json": json.dumps({"valid": True, "task_type": "regression"}),
                },
                elapsed_seconds=1.0,
            )
        return WorkflowOutcome(
            run_id="16a241e7-9cf6-466b-8731-3a7cd2395180",
            status="succeeded",
            outputs={
                "experiment_json": json.dumps(
                    {
                        "experiment_id": "exp-20260828T000000Z-abc123",
                        "status": "succeeded",
                        "task_type": "regression",
                        "split_provenance": {"test_digest": TEST_DIGEST},
                        "performance_ranking": ["linear_regression"],
                        "results": [
                            {
                                "model": "linear_regression",
                                "status": "succeeded",
                                "metrics": {"mae": 1.0, "rmse": 1.2, "r2": 0.9},
                            }
                        ],
                    }
                ),
                "assessment_json": json.dumps(
                    {
                        "strict_status": "not_comparable",
                        "approximate_status": "materially_different",
                        "strict_reason_codes": ["paper_dataset_identity_missing"],
                    }
                ),
                "comparison_json": "{\"items\":[]}",
                "dossier_json": "{}",
                "markdown_report": "safe aggregate report",
            },
            elapsed_seconds=2.0,
        )


def test_run_case_prepares_then_confirms_declared_regression_choices(tmp_path: Path) -> None:
    client = FakeClient()
    checkpoint = CheckpointStore(tmp_path / "checkpoint.json")

    result = run_case(_case(), _acquired(tmp_path), client, checkpoint)

    assert result.status == "succeeded"
    assert result.task_type == "regression"
    assert result.test_digest == TEST_DIGEST
    assert result.metrics == {"linear_regression": {"mae": 1.0, "rmse": 1.2, "r2": 0.9}}
    assert result.model_statuses == {"linear_regression": "succeeded"}
    assert result.performance_ranking == ("linear_regression",)
    assert result.strict_status == "not_comparable"
    assert result.approximate_status == "materially_different"
    prepare = client.calls[0][0]
    assert prepare["run_mode"] == "prepare"
    assert prepare["target_column"] == "target"
    assert prepare["test_size"] == 0.2
    assert prepare["random_state"] == 42
    assert prepare["models_json"] == '["linear_regression","random_forest","gradient_boosting","xgboost"]'
    assert prepare["cv_folds"] == "5"
    assert prepare["optimization_metric"] == "rmse"
    confirm = client.calls[1][0]
    assert confirm["run_mode"] == "run"
    assert confirm["confirm_protocol"] is True
    assert confirm["protocol_token"] == "protocol-secret-sentinel"

    checkpoint_text = checkpoint.path.read_text(encoding="utf-8")
    assert "protocol-secret-sentinel" not in checkpoint_text
    assert "safe aggregate report" not in checkpoint_text
    assert json.loads(checkpoint_text)["terminal_status"] == "succeeded"


def test_run_case_classifies_missing_protocol_token_without_confirming(tmp_path: Path) -> None:
    client = FakeClient(token="")

    result = run_case(
        _case(),
        _acquired(tmp_path),
        client,
        CheckpointStore(tmp_path / "checkpoint.json"),
    )

    assert result.status == "failed"
    assert result.failure_code == "evidence_ambiguous"
    assert len(client.calls) == 1


def test_checkpoint_contains_only_allowlisted_progress_fields(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    store = CheckpointStore(path)

    store.write(
        case_id="energy-efficiency",
        phase="prepare",
        run_id="80c9fa4f-2693-4142-8af3-52f15e5aba17",
        terminal_status="running",
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "case_id": "energy-efficiency",
        "phase": "prepare",
        "run_id": "80c9fa4f-2693-4142-8af3-52f15e5aba17",
        "terminal_status": "running",
    }
