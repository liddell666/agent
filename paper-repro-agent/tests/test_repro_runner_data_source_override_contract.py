from pathlib import Path


def test_override_targets_only_runner_experiment_data() -> None:
    source = Path("compose.runner-data-source.yaml").read_text(encoding="utf-8")
    assert "repro-runner:" in source
    assert "REPRO_RUNNER_CUTOVER_DATA_SOURCE" in source
    assert "target: /data/experiments" in source
    assert "target: /app/src" not in source


def test_build_helper_accepts_optional_compose_override() -> None:
    source = Path("scripts/build_repro_runner.ps1").read_text(encoding="utf-8")
    assert "ComposeOverrideFile" in source
    assert '"-f"' in source
    assert "docker compose" in source
    assert "build repro-runner" in source
