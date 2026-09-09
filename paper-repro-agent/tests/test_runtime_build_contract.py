from pathlib import Path


def test_build_helper_derives_commit_and_calls_compose_without_editing_source() -> None:
    helper = Path("scripts/build_repro_runner.ps1").read_text(encoding="utf-8")

    assert "$env:REPRO_RUNNER_GIT_COMMIT" in helper
    assert "$env:REPRO_RUNNER_WORKFLOW_VERSION" in helper
    assert "docker compose" in helper
    assert "build repro-runner" in helper


def test_build_helper_keeps_empty_override_compatible() -> None:
    source = Path("scripts/build_repro_runner.ps1").read_text(encoding="utf-8")
    assert '[string]$ComposeOverrideFile = ""' in source
    assert "if (-not [string]::IsNullOrWhiteSpace($ComposeOverrideFile))" in source


def test_parser_build_reuses_verified_wheels_across_network_retries() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "--mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert "PIP_NO_CACHE_DIR" not in dockerfile
    assert "PIP_DEFAULT_TIMEOUT=120" in dockerfile
    assert "PIP_RETRIES=10" in dockerfile
