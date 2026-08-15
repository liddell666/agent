from pathlib import Path


def test_build_helper_derives_commit_and_calls_compose_without_editing_source() -> None:
    helper = Path("scripts/build_repro_runner.ps1").read_text(encoding="utf-8")

    assert "$env:REPRO_RUNNER_GIT_COMMIT" in helper
    assert "$env:REPRO_RUNNER_WORKFLOW_VERSION" in helper
    assert "docker compose build repro-runner" in helper
