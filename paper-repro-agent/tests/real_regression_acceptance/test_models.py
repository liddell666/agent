from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from real_regression_acceptance.models import (
    CorpusEvaluation,
    SafeCaseResult,
    load_registry,
)


APP_ID = "17fe51d4-091f-4729-87ee-3c0a2e920918"
DIGEST = "sha256:" + "a" * 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _case(case_id: str = "energy-efficiency") -> dict[str, object]:
    return {
        "id": case_id,
        "paper": {
            "url": f"https://papers.example/{case_id}.pdf",
            "sha256": DIGEST,
            "media_type": "application/pdf",
            "max_bytes": 20_000_000,
            "attribution": "Example paper authors",
            "rights": "Publisher open-access terms",
        },
        "dataset": {
            "url": f"https://datasets.example/{case_id}.zip",
            "sha256": DIGEST,
            "media_type": "application/zip",
            "max_bytes": 50_000_000,
            "attribution": "Example institutional dataset",
            "rights": "CC BY 4.0",
        },
        "transform": {
            "member": "dataset/data.csv",
            "format": "csv",
            "delimiter": ",",
            "rename_columns": {"Y": "target"},
            "drop_columns": [],
        },
        "target_column": "target",
        "task_type": "regression",
        "paper_metric_overrides": [],
    }


def _registry(case_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "candidate_app_id": APP_ID,
        "cases": [_case(f"case-{index}") for index in range(case_count)],
    }


def _write(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "registry.yml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_load_registry_returns_strict_typed_contract(tmp_path: Path) -> None:
    registry = load_registry(_write(tmp_path, _registry()), require_live_corpus=False)

    assert registry.schema_version == 1
    assert registry.candidate_app_id == APP_ID
    assert registry.cases[0].task_type == "regression"
    assert registry.cases[0].paper.sha256.startswith("sha256:")
    assert registry.cases[0].paper.attribution == "Example paper authors"
    assert registry.cases[0].dataset.rights == "CC BY 4.0"
    assert registry.cases[0].transform.drop_columns == ()


def test_live_registry_requires_exactly_five_cases(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly five"):
        load_registry(_write(tmp_path, _registry(4)))

    registry = load_registry(_write(tmp_path, _registry(5)))
    assert len(registry.cases) == 5


def test_registry_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    payload = _registry(5)
    payload["cases"][1]["id"] = payload["cases"][0]["id"]  # type: ignore[index]

    with pytest.raises(ValueError, match="unique"):
        load_registry(_write(tmp_path, payload))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda item: item["paper"].update(url="http://papers.example/paper.pdf"), "HTTPS"),
        (lambda item: item["paper"].update(sha256="sha256:" + "A" * 64), "sha256"),
        (lambda item: item["paper"].update(attribution=""), "attribution"),
        (lambda item: item["dataset"].update(rights=""), "rights"),
        (lambda item: item.update(target_column=""), "target_column"),
        (lambda item: item.update(task_type="binary_classification"), "task_type"),
        (lambda item: item["transform"].update(member="../secret.csv"), "unsafe"),
        (lambda item: item["transform"].update(member="/absolute.csv"), "unsafe"),
    ],
)
def test_registry_rejects_unsafe_or_invalid_values(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    payload = _registry()
    changed = deepcopy(payload["cases"][0])  # type: ignore[index]
    mutate(changed)
    payload["cases"][0] = changed  # type: ignore[index]

    with pytest.raises((ValueError, ValidationError), match=message):
        load_registry(_write(tmp_path, payload), require_live_corpus=False)


def test_safe_result_and_evaluation_forbid_untrusted_payload_fields() -> None:
    result = SafeCaseResult(
        case_id="energy-efficiency",
        paper_digest=DIGEST,
        dataset_digest=DIGEST,
        task_type="regression",
        status="succeeded",
        metrics={"linear_regression": {"rmse": 1.2}},
        model_statuses={"linear_regression": "succeeded"},
        test_digest=DIGEST,
        elapsed_seconds=1.5,
    )
    evaluation = CorpusEvaluation(
        passed=True,
        completed_count=4,
        total_count=5,
        false_strict_count=0,
        failure_counts={"evidence_ambiguous": 1},
        gate_errors=(),
    )

    assert result.metrics["linear_regression"]["rmse"] == 1.2
    assert evaluation.passed is True
    with pytest.raises(ValidationError):
        SafeCaseResult.model_validate({**result.model_dump(), "paper_text": "secret"})


def test_live_registry_pins_the_five_approved_cases() -> None:
    registry = load_registry(REPOSITORY_ROOT / "real_world" / "regression_cases.yml")

    assert [case.id for case in registry.cases] == [
        "energy-efficiency",
        "concrete-strength",
        "wine-quality-red",
        "appliances-energy",
        "real-estate-valuation",
    ]
    assert all(case.paper.attribution and case.paper.rights for case in registry.cases)
    assert all(case.dataset.attribution and case.dataset.rights for case in registry.cases)
