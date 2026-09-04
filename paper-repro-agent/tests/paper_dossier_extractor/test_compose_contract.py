from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_compose_declares_internal_dossier_extractor():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["paper-dossier-extractor"]

    assert service["container_name"] == "paper-dossier-extractor"
    assert service["build"] == {
        "context": ".",
        "dockerfile": "Dockerfile.extractor",
    }
    assert service["env_file"] == ".env"
    assert "ports" not in service
    assert service["networks"]["dify"]["aliases"] == ["paper-dossier-extractor"]
    assert service["environment"]["PAPER_DOSSIER_EXTRACTOR_OLLAMA_BASE_URL"] == (
        "http://ollama:11434"
    )
    assert service["volumes"] == ["./src:/app/src:ro"]
    assert service["restart"] == "unless-stopped"
    assert service["init"] is True
    assert service["mem_limit"] == "2g"
    assert service["cpus"] == 2
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "http://localhost:8002/healthz" in " ".join(
        service["healthcheck"]["test"]
    )


def test_extractor_environment_template_has_only_non_secret_placeholders():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    values = {
        line.split("=", maxsplit=1)[0]: line.split("=", maxsplit=1)[1]
        for line in lines
        if "=" in line and not line.lstrip().startswith("#")
    }

    token = values["PAPER_DOSSIER_EXTRACTOR_API_TOKEN"]
    assert token.startswith("replace-with-")
    assert "at-least-32-characters" in token
    assert values["PAPER_DOSSIER_EXTRACTOR_OLLAMA_BASE_URL"] == (
        "http://ollama:11434"
    )


def test_extractor_image_is_pinned_to_a_non_root_runtime_contract():
    dockerfile = (ROOT / "Dockerfile.extractor").read_text(encoding="utf-8")

    assert "COPY requirements-repro.lock /tmp/requirements-repro.lock" in dockerfile
    assert "pip install --requirement /tmp/requirements-repro.lock" in dockerfile
    assert "COPY --chown=app:app src /app/src" in dockerfile
    assert "groupadd --system app" in dockerfile
    assert "useradd --system --gid app" in dockerfile
    assert "USER app" in dockerfile
    assert "EXPOSE 8002" in dockerfile
    assert (
        'CMD ["uvicorn", "paper_dossier_extractor.api:app", "--host", '
        '"0.0.0.0", "--port", "8002", "--workers", "1"]'
    ) in dockerfile
