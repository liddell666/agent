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
    assert service["ports"] == ["127.0.0.1:8001:8001"]
    assert service["volumes"] == [
        "./src:/app/src:ro",
        "./data/experiments:/data/experiments",
    ]
    assert "repro-runner" in service["networks"]["dify"]["aliases"]
    assert service["environment"]["REPRO_RUNNER_MAX_CONCURRENT_EXPERIMENTS"] == "1"
    assert service["mem_limit"] == "4g"
    assert service["cpus"] == 4
    assert "http://localhost:8001/healthz" in " ".join(
        service["healthcheck"]["test"]
    )


def test_repro_runner_image_runs_as_non_root_service():
    dockerfile = Path("Dockerfile.repro").read_text(encoding="utf-8")

    assert "requirements-repro.lock" in dockerfile
    assert "src /app/src" in dockerfile
    assert "USER app" in dockerfile
    assert "--port\", \"8001" in dockerfile


def test_repro_requirements_input_is_kept_with_lock_file():
    requirements_input = Path("requirements-repro.in")

    assert requirements_input.is_file()
    assert "scikit-learn==1.9.0" in requirements_input.read_text(encoding="utf-8")
    assert "requirements-repro.in" in Path("requirements-repro.lock").read_text(
        encoding="utf-8"
    )


def test_dify_workflow_parses_http_body_before_branching():
    workflow = Path("dify/repro-experiment-workflow.md").read_text(encoding="utf-8")

    assert "parse_validation_response" in workflow
    assert "json.loads(body)" in workflow
    assert "validation_ok" in workflow
    assert "{{#parse_validation_response.validation_ok#}} equals true" in workflow
    assert "{{#validate_dataset.body.valid#}}" not in workflow
    assert "training_csv" not in workflow.split("LLM", maxsplit=1)[-1]


def test_dify_error_branches_only_use_outputs_they_create():
    workflow = Path("dify/repro-experiment-workflow.md").read_text(encoding="utf-8")

    for node in (
        "normalize_validation_http_failure",
        "format_validation_rejection",
        "normalize_experiment_http_failure",
        "format_experiment_rejection",
    ):
        assert node in workflow
    assert "Every error branch ends using only variables created on that branch" in workflow
