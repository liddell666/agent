from pathlib import Path

import yaml


def test_compose_declares_repro_runner():
    document = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    service = document["services"]["repro-runner"]

    assert service["container_name"] == "repro-runner"
    assert service["build"] == {
        "context": ".",
        "dockerfile": "Dockerfile.repro",
    }
    assert service["env_file"] == ".env"
    assert service["environment"]["REPRO_RUNNER_STORAGE_DIR"] == (
        "/data/experiments"
    )
    assert "8001" in service["ports"][0]
    assert service["volumes"] == [
        "./src:/app/src:ro",
        "./data/experiments:/data/experiments",
    ]
    assert "repro-runner" in service["networks"]["dify"]["aliases"]
    assert "http://localhost:8001/healthz" in " ".join(
        service["healthcheck"]["test"]
    )


def test_repro_runner_image_runs_as_non_root_service():
    dockerfile = Path("Dockerfile.repro").read_text(encoding="utf-8")

    assert "requirements-repro.lock" in dockerfile
    assert "src /app/src" in dockerfile
    assert "USER app" in dockerfile
    assert "--port\", \"8001" in dockerfile
