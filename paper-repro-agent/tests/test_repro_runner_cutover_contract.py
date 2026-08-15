from pathlib import Path


SCRIPT = Path("scripts/switch_repro_runner.ps1")


def test_cutover_script_has_read_only_job_guard_and_reversible_steps() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for required in (
        "queued",
        "running",
        "sqlite3",
        "docker stop",
        "docker rename",
        "repro-runner-legacy-",
        "docker network disconnect",
        "docker network connect",
        "docker compose up -d --no-deps repro-runner",
        "/healthz",
        "REPRO_RUNNER_GIT_COMMIT",
        "REPRO_RUNNER_WORKFLOW_VERSION",
        "-Rollback",
    ):
        assert required in source


def test_cutover_script_never_uses_broad_data_deletion() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()
    assert "docker volume rm" not in source
    assert "docker system prune" not in source
    assert "remove-item" not in source


def test_cutover_script_validates_image_before_and_container_after_switch() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for required in (
        "docker run",
        "Invoke-RestMethod",
        "source_digest",
        "expectedCommit",
        "workflow_version",
        "/data/experiments",
        "Aliases",
        "127.0.0.1",
    ):
        assert required in source


def test_rollback_requires_no_active_jobs_and_restores_alias() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "Sort-Object Name -Descending" in source
    assert "docker rm -f" in source
    assert "docker rename" in source
    assert "--alias" in source
    assert "Assert-NoActiveJobs" in source


def test_image_resolution_handles_compose_without_an_existing_container() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "--images" in source
    assert "docker image inspect" in source
