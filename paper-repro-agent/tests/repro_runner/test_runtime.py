import json
import re

from repro_runner.api import healthz
from repro_runner.config import Settings
from repro_runner.runtime import runtime_provenance
from repro_runner.schemas import (
    DatasetProfile,
    ExperimentMetrics,
    ExperimentSuiteResult,
    ModelRunResult,
    ModelSuiteConfig,
    SplitProvenance,
)
import repro_runner.storage as storage


DATASET_ID = "sha256:" + "a" * 64
TEST_DIGEST = "sha256:" + "b" * 64


def _metrics() -> ExperimentMetrics:
    return ExperimentMetrics(
        roc_auc=0.9,
        accuracy=0.8,
        balanced_accuracy=0.75,
        precision=0.7,
        recall=0.6,
        f1=0.64,
        confusion_matrix=[[4, 1], [1, 4]],
    )


def _suite_result() -> ExperimentSuiteResult:
    config = ModelSuiteConfig(
        models=["random_forest"],
        workflow_version="multimodel-0.8.0",
        cv_folds=3,
        n_iter=1,
        n_jobs=1,
    )
    return ExperimentSuiteResult(
        experiment_id="exp-20260814T010203Z-runtime",
        status="succeeded",
        config=config,
        dataset=DatasetProfile(
            rows=10,
            effective_rows=10,
            features=2,
            target="Y_cls",
            missing_values=0,
            duplicate_rows=0,
            dataset_id=DATASET_ID,
        ),
        split_provenance=SplitProvenance(
            test_size=0.2,
            random_state=42,
            train_rows=8,
            test_rows=2,
            test_digest=TEST_DIGEST,
        ),
        results=[
            ModelRunResult(
                model="random_forest",
                status="succeeded",
                metrics=_metrics(),
            )
        ],
        performance_ranking=["random_forest"],
        runtime=runtime_provenance(config.workflow_version),
    )


def test_runtime_provenance_uses_safe_defaults_and_build_overrides(monkeypatch):
    monkeypatch.delenv("REPRO_RUNNER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("REPRO_RUNNER_WORKFLOW_VERSION", raising=False)

    default = runtime_provenance()

    assert default.service_version == "0.2.0"
    assert default.git_commit == "unknown"
    assert default.workflow_version == "unknown"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", default.source_digest)

    monkeypatch.setenv("REPRO_RUNNER_GIT_COMMIT", "abc123")
    monkeypatch.setenv("REPRO_RUNNER_WORKFLOW_VERSION", "multimodel-0.8.0")
    overridden = runtime_provenance()

    assert overridden.git_commit == "abc123"
    assert overridden.workflow_version == "multimodel-0.8.0"
    assert overridden.source_digest == default.source_digest


def test_healthz_exposes_runner_provenance(monkeypatch):
    monkeypatch.setenv("REPRO_RUNNER_GIT_COMMIT", "health-commit")
    monkeypatch.setenv("REPRO_RUNNER_WORKFLOW_VERSION", "multimodel-0.8.0")

    payload = healthz()

    assert payload["status"] == "ok"
    assert payload["service_version"] == "0.2.0"
    assert payload["git_commit"] == "health-commit"
    assert payload["workflow_version"] == "multimodel-0.8.0"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", payload["source_digest"])


def test_suite_result_and_config_persist_runtime_provenance():
    result = _suite_result()

    payload = storage._suite_result_payload(result)
    stored_config = storage._stored_suite_config_payload(result)

    assert payload["runtime"] == result.runtime.model_dump(mode="json")
    assert payload["config"]["workflow_version"] == "multimodel-0.8.0"
    assert stored_config["workflow_version"] == "multimodel-0.8.0"
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
