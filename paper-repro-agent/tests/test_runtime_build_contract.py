from pathlib import Path

import yaml


def test_repro_dockerfile_declares_and_exports_build_identity() -> None:
    dockerfile = Path("Dockerfile.repro").read_text(encoding="utf-8")

    assert "ARG REPRO_RUNNER_GIT_COMMIT=unknown" in dockerfile
    assert "ARG REPRO_RUNNER_WORKFLOW_VERSION=unknown" in dockerfile
    assert "REPRO_RUNNER_GIT_COMMIT=${REPRO_RUNNER_GIT_COMMIT}" in dockerfile
    assert "REPRO_RUNNER_WORKFLOW_VERSION=${REPRO_RUNNER_WORKFLOW_VERSION}" in dockerfile


def test_compose_passes_runtime_identity_to_repro_build() -> None:
    document = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    build = document["services"]["repro-runner"]["build"]

    assert build["args"] == {
        "REPRO_RUNNER_GIT_COMMIT": "${REPRO_RUNNER_GIT_COMMIT:-unknown}",
        "REPRO_RUNNER_WORKFLOW_VERSION": "${REPRO_RUNNER_WORKFLOW_VERSION:-multimodel-0.8.0}",
    }


def test_build_helper_derives_commit_and_calls_compose_without_editing_source() -> None:
    helper = Path("scripts/build_repro_runner.ps1").read_text(encoding="utf-8")

    assert "$env:REPRO_RUNNER_GIT_COMMIT" in helper
    assert "$env:REPRO_RUNNER_WORKFLOW_VERSION" in helper
    assert "docker compose build repro-runner" in helper
