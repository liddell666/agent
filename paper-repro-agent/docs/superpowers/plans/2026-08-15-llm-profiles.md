# LLM Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic DeepSeek and Ollama build profiles to the Dify DSL generator while keeping the existing DeepSeek artifact and workflow contract unchanged.

**Architecture:** Keep one profile registry in scripts/build_multimodel_dsl.py. The existing no-argument builders resolve to the legacy DeepSeek profile; an explicit ollama profile changes only the copied LLM provider/model, app metadata, and marketplace dependency, then writes suffixed YAML artifacts. The extraction prompt, validation code, runner URLs, protocol fields, and output variables are shared by both profiles.

**Tech Stack:** Python 3.12, dataclasses, PyYAML, pytest, Dify YAML DSL, local Ollama model qwen3:8b.

## Global Constraints

- Work only in codex/multi-model-cv; preserve unrelated dirty files.
- Keep the current DeepSeek DSL output as the default and preserve its prompt, schema, validation, protocol, and report behavior.
- Do not implement an implicit DeepSeek-to-Ollama fallback inside one Dify run.
- Do not put an API key, parser token, protocol secret, or Ollama credential in generated DSL or source control.
- Generated profile artifacts must be deterministic and contain blank secret values.
- The local Dify Ollama base URL must be configured outside source control; for the current shared Docker network use http://ollama:11434 without an /api suffix.

---

### Task 1: Define and test the profile registry

**Files:**
- Modify: scripts/build_multimodel_dsl.py:1-125
- Create: tests/test_dify_llm_profiles.py

**Interfaces:**
- Produces LLMProfile, LLM_PROFILES, DEFAULT_LLM_PROFILE, and resolve_llm_profile(profile: str = "deepseek") -> LLMProfile for the builder tasks.
- A profile exposes name, provider, model, dependency, and suffix as immutable fields.

- [ ] **Step 1: Write the failing registry tests.**

Create tests/test_dify_llm_profiles.py:

~~~python
import pytest

from scripts.build_multimodel_dsl import (
    DEFAULT_LLM_PROFILE,
    LLM_PROFILES,
    resolve_llm_profile,
)


def test_profiles_pin_provider_model_and_dependency() -> None:
    assert DEFAULT_LLM_PROFILE == "deepseek"
    assert set(LLM_PROFILES) == {"deepseek", "ollama"}

    deepseek = resolve_llm_profile()
    assert deepseek.provider == "langgenius/deepseek/deepseek"
    assert deepseek.model == "deepseek-v4-flash"
    assert deepseek.suffix == ""
    assert deepseek.dependency["value"]["marketplace_plugin_unique_identifier"].startswith(
        "langgenius/deepseek:0.0.19@"
    )

    ollama = resolve_llm_profile("ollama")
    assert ollama.provider == "langgenius/ollama/ollama"
    assert ollama.model == "qwen3:8b"
    assert ollama.suffix == "-ollama"
    assert ollama.dependency["value"]["marketplace_plugin_unique_identifier"] == (
        "langgenius/ollama:1.0.0@86dd6101fbd9de94e6681782700fa98c8a785c982918e6fe0e3f"
        "d507e15ba3f"
    )


def test_profile_resolution_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="deepseek.*ollama"):
        resolve_llm_profile("missing")
~~~

- [ ] **Step 2: Run the focused tests and verify the expected failure.**

Run:

~~~powershell
python -m pytest -q tests/test_dify_llm_profiles.py
~~~

Expected: collection fails because LLM_PROFILES and resolve_llm_profile do not yet exist.

- [ ] **Step 3: Implement the immutable registry and resolver.**

Add these imports and declarations near the existing DSL path constants:

~~~python
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMProfile:
    name: str
    provider: str
    model: str
    dependency: dict[str, object]
    suffix: str


DEFAULT_LLM_PROFILE = "deepseek"

LLM_PROFILES = {
    "deepseek": LLMProfile(
        name="deepseek",
        provider="langgenius/deepseek/deepseek",
        model="deepseek-v4-flash",
        dependency={
            "current_identifier": None,
            "type": "marketplace",
            "value": {
                "marketplace_plugin_unique_identifier": (
                    "langgenius/deepseek:0.0.19@5b68617c637b62d31e7f33a9f5677b76e88f81868fb04a728e208588564b72ea"
                ),
                "version": None,
            },
        },
        suffix="",
    ),
    "ollama": LLMProfile(
        name="ollama",
        provider="langgenius/ollama/ollama",
        model="qwen3:8b",
        dependency={
            "current_identifier": None,
            "type": "marketplace",
            "value": {
                "marketplace_plugin_unique_identifier": (
                    "langgenius/ollama:1.0.0@86dd6101fbd9de94e6681782700fa98c8a785c982918e6fe0e3f"
                    "d507e15ba3f"
                ),
                "version": None,
            },
        },
        suffix="-ollama",
    ),
}


def resolve_llm_profile(profile: str = DEFAULT_LLM_PROFILE) -> LLMProfile:
    try:
        return LLM_PROFILES[profile]
    except KeyError:
        allowed = ", ".join(sorted(LLM_PROFILES))
        raise ValueError(f"unknown LLM profile {profile!r}; expected one of: {allowed}") from None
~~~

Do not mutate the nested dependency dictionary in later tasks; always pass it through deepcopy.

- [ ] **Step 4: Run the focused tests and verify they pass.**

Run the same pytest command. Expected: 2 passed.

- [ ] **Step 5: Commit the isolated registry change.**

~~~powershell
git add scripts/build_multimodel_dsl.py tests/test_dify_llm_profiles.py
git commit -m "feat: add explicit llm profiles"
~~~

### Task 2: Apply profiles without changing the default workflow

**Files:**
- Modify: scripts/build_multimodel_dsl.py:1335-1600
- Modify: scripts/build_multimodel_dsl.py:2202-2990
- Modify: tests/test_dify_llm_profiles.py
- Test: tests/test_dify_job_workflow.py
- Test: tests/test_dify_multimodel_dsl.py

**Interfaces:**
- Change build_prepare_dsl() to build_prepare_dsl(profile: str = DEFAULT_LLM_PROFILE) -> dict.
- Change build_multimodel_dsl() to build_multimodel_dsl(profile: str = DEFAULT_LLM_PROFILE) -> dict; the run-only workflow has no LLM node but receives profile metadata for a complete import bundle.
- Change build_merged_dsl() to build_merged_dsl(profile: str = DEFAULT_LLM_PROFILE) -> dict.
- Existing callers with no arguments keep the current DeepSeek artifact shape and names.

- [ ] **Step 1: Add failing builder tests for both profiles.**

Append these tests to tests/test_dify_llm_profiles.py:

~~~python
import yaml

from scripts.build_multimodel_dsl import (
    build_merged_dsl,
    build_prepare_dsl,
)


def _nodes(document: dict) -> list[dict]:
    return document["workflow"]["graph"]["nodes"]


def _node(document: dict, title: str) -> dict:
    return next(node for node in _nodes(document) if node["data"]["title"] == title)


def test_default_builder_remains_deepseek_compatible() -> None:
    default = _node(build_prepare_dsl(), "extract_paper_dossier")["data"]["model"]
    explicit = _node(build_prepare_dsl("deepseek"), "extract_paper_dossier")["data"]["model"]
    assert default == explicit
    assert default["provider"] == "langgenius/deepseek/deepseek"
    assert default["name"] == "deepseek-v4-flash"


def test_ollama_profile_changes_only_provider_metadata() -> None:
    document = build_prepare_dsl("ollama")
    model = _node(document, "extract_paper_dossier")["data"]["model"]
    assert model["provider"] == "langgenius/ollama/ollama"
    assert model["name"] == "qwen3:8b"
    assert _node(document, "extract_paper_dossier")["data"]["prompt_template"] == _node(
        build_prepare_dsl(), "extract_paper_dossier"
    )["data"]["prompt_template"]
    assert document["app"]["name"].endswith("-ollama")
    assert document["workflow"]["name"].endswith("-ollama")
    assert document["dependencies"][0]["value"]["marketplace_plugin_unique_identifier"].startswith(
        "langgenius/ollama:1.0.0@"
    )


def test_ollama_bundle_has_no_secret_values() -> None:
    for document in (build_prepare_dsl("ollama"), build_merged_dsl("ollama")):
        serialized = yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=4096)
        assert "PARSER_API_TOKEN: replace" not in serialized
        assert "DIFY_PROTOCOL_SECRET: replace" not in serialized
        for item in document["workflow"]["environment_variables"]:
            if item["name"] in {"PARSER_API_TOKEN", "DIFY_PROTOCOL_SECRET"}:
                assert item["value"] == ""
                assert item["value_type"] == "secret"
~~~

- [ ] **Step 2: Run the focused tests and confirm the expected failure.**

~~~powershell
python -m pytest -q tests/test_dify_llm_profiles.py
~~~

Expected: the new calls with a profile argument fail because the builders still accept no arguments and do not rewrite the copied model.

- [ ] **Step 3: Add profile application helpers and thread the profile through builders.**

Add these helpers before build_prepare_dsl:

~~~python
def _apply_profile_metadata(document: dict, profile: LLMProfile) -> dict:
    if profile.name == DEFAULT_LLM_PROFILE:
        return document
    document["dependencies"] = [deepcopy(profile.dependency)]
    document["app"]["name"] = f"{document['app']['name']}{profile.suffix}"
    document["workflow"]["name"] = f"{document['workflow']['name']}{profile.suffix}"
    return document


def _apply_llm_node_profile(node: dict, profile: LLMProfile) -> None:
    model = deepcopy(node["data"].get("model", {}))
    model["provider"] = profile.provider
    model["name"] = profile.model
    node["data"]["model"] = model
~~~

Update the signatures and first lines:

~~~python
def build_prepare_dsl(profile: str = DEFAULT_LLM_PROFILE) -> dict:
    """Compose the PDF-to-protocol workflow for one explicit LLM profile."""
    resolved_profile = resolve_llm_profile(profile)
    source = _load_source()
~~~

Immediately after the existing prompt-reference replacement loop, call:

~~~python
    _apply_llm_node_profile(extract, resolved_profile)
~~~

Before returning the prepare document, call:

~~~python
    return _apply_profile_metadata(document, resolved_profile)
~~~

Apply the same resolver/metadata helper to build_multimodel_dsl and build_merged_dsl. build_merged_dsl(profile) must call build_prepare_dsl(profile) and build_multimodel_dsl(profile), then apply metadata to the final merged document after its graph is assembled. Leave build_prepare_dsl_legacy() unchanged for backward compatibility with its existing tests.

- [ ] **Step 4: Regenerate and run all DSL-focused tests.**

Run:

~~~powershell
python scripts/build_multimodel_dsl.py
python -m pytest -q tests/test_dify_llm_profiles.py tests/test_dify_job_workflow.py tests/test_dify_multimodel_dsl.py
~~~

Expected: all focused tests pass, and the no-argument generated files still expose DeepSeek with the same prompt and protocol fields.

- [ ] **Step 5: Commit the builder contract.**

~~~powershell
git add scripts/build_multimodel_dsl.py tests/test_dify_llm_profiles.py dify/paper-comparison-prepare-workflow.yml dify/paper-comparison-multimodel-workflow.yml dify/paper-comparison-merged-workflow.yml
git commit -m "feat: make dify llm profile explicit"
~~~

### Task 3: Add deterministic Ollama artifacts and CLI output

**Files:**
- Modify: scripts/build_multimodel_dsl.py:2980-3020
- Create: dify/paper-comparison-prepare-workflow-ollama.yml
- Create: dify/paper-comparison-multimodel-workflow-ollama.yml
- Create: dify/paper-comparison-merged-workflow-ollama.yml
- Modify: tests/test_dify_llm_profiles.py

**Interfaces:**
- Add write_profile_dsls(profile: str, output_dir: Path = PROJECT_ROOT / "dify") -> tuple[Path, Path, Path].
- Add CLI options --profile {deepseek,ollama} and --output-dir; no arguments continue writing the existing three default files.

- [ ] **Step 1: Write failing CLI/output tests.**

Add:

~~~python
from pathlib import Path

from scripts.build_multimodel_dsl import write_profile_dsls


def test_ollama_output_paths_are_suffixed_and_deterministic(tmp_path: Path) -> None:
    first = write_profile_dsls("ollama", tmp_path)
    first_bytes = tuple(path.read_bytes() for path in first)
    second = write_profile_dsls("ollama", tmp_path)
    assert tuple(path.name for path in first) == (
        "paper-comparison-multimodel-workflow-ollama.yml",
        "paper-comparison-prepare-workflow-ollama.yml",
        "paper-comparison-merged-workflow-ollama.yml",
    )
    assert tuple(path.read_bytes() for path in second) == first_bytes


def test_default_output_paths_have_no_profile_suffix(tmp_path: Path) -> None:
    paths = write_profile_dsls("deepseek", tmp_path)
    assert tuple(path.name for path in paths) == (
        "paper-comparison-multimodel-workflow.yml",
        "paper-comparison-prepare-workflow.yml",
        "paper-comparison-merged-workflow.yml",
    )
~~~

- [ ] **Step 2: Run the focused tests and verify the expected failure.**

~~~powershell
python -m pytest -q tests/test_dify_llm_profiles.py
~~~

Expected: import fails because write_profile_dsls is not defined.

- [ ] **Step 3: Implement the writer and CLI.**

Use this output naming rule and keep the existing writer functions as wrappers for the default profile:

~~~python
def _profile_path(path: Path, profile: LLMProfile) -> Path:
    if not profile.suffix:
        return path
    return path.with_name(f"{path.stem}{profile.suffix}{path.suffix}")


def write_profile_dsls(profile: str, output_dir: Path = PROJECT_ROOT / "dify") -> tuple[Path, Path, Path]:
    resolved_profile = resolve_llm_profile(profile)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_path = _profile_path(output_dir / TARGET_DSL.name, resolved_profile)
    prepare_path = _profile_path(output_dir / PREPARE_DSL.name, resolved_profile)
    merged_path = _profile_path(output_dir / MERGED_DSL.name, resolved_profile)
    run_path.write_text(yaml.safe_dump(build_multimodel_dsl(profile), allow_unicode=True, sort_keys=False, width=4096), encoding="utf-8")
    prepare_path.write_text(yaml.safe_dump(build_prepare_dsl(profile), allow_unicode=True, sort_keys=False, width=4096), encoding="utf-8")
    merged_path.write_text(yaml.safe_dump(build_merged_dsl(profile), allow_unicode=True, sort_keys=False, width=4096), encoding="utf-8")
    return run_path, prepare_path, merged_path
~~~

Replace the module entry point with argparse, defaulting to deepseek and PROJECT_ROOT / "dify". Do not print file contents or environment values.

- [ ] **Step 4: Generate the committed Ollama bundle and verify secret hygiene.**

~~~powershell
python scripts/build_multimodel_dsl.py --profile ollama
python -m pytest -q tests/test_dify_llm_profiles.py tests/test_dify_multimodel_dsl.py tests/test_dify_job_workflow.py
rg -n "sk-|RAW_CSV_SECRET|SECRET_TOKEN|PAPER_PARSER_API_TOKEN|DIFY_PROTOCOL_SECRET: [^"'']" dify/*-ollama.yml
~~~

Expected: deterministic output, all focused tests pass, and the final rg command returns no secret-like match. The two environment variables remain present as blank secret declarations.

- [ ] **Step 5: Commit the profile artifacts.**

~~~powershell
git add scripts/build_multimodel_dsl.py tests/test_dify_llm_profiles.py dify/*-ollama.yml
git commit -m "feat: generate ollama dify workflow bundle"
~~~

### Task 4: Document and perform local Dify acceptance

**Files:**
- Modify: docs/workflow-setup.md
- Modify: dify/paper-comparison-merged-workflow.md

- [ ] **Step 1: Add the operator instructions.**

Document the exact command:

~~~powershell
python scripts/build_multimodel_dsl.py --profile ollama
~~~

Then document importing dify/paper-comparison-merged-workflow-ollama.yml, installing the pinned Ollama marketplace dependency, selecting model qwen3:8b, and setting the provider Base URL to http://ollama:11434 from the Dify network. State that PARSER_API_TOKEN and DIFY_PROTOCOL_SECRET are entered only in the Dify UI and that the DeepSeek artifact remains the default.

- [ ] **Step 2: Run the Ollama health check before Dify.**

~~~powershell
Invoke-RestMethod http://localhost:11434/api/tags
docker run --rm --network docker_default curlimages/curl:8.10.1 http://ollama:11434/api/tags
~~~

Expected: both responses list qwen3:8b; if the temporary curl image is unavailable, use an equivalent read-only request from an existing Dify container and record that substitution.

- [ ] **Step 3: Import and run the local profile without replacing the DeepSeek app.**

Use the existing local Dify UI to import the Ollama merged DSL as a separate app. Configure the two existing environment secrets in the UI, upload tests/fixtures/minimal-paper.pdf and tests/fixtures/general_binary_numeric.csv, execute prepare, then execute run with a short model list containing logistic_regression.

- [ ] **Step 4: Verify the acceptance contract.**

Record only status, duration, validation aggregates, experiment status, model status, comparison status, and runner provenance. Confirm that the LLM node completes, the dossier remains validated, the protocol path succeeds, and the report includes git_commit, source_digest, and workflow_version. Do not record PDF text, CSV rows, API keys, protocol tokens, or full request payloads.

- [ ] **Step 5: Commit documentation after the acceptance notes are complete.**

~~~powershell
git add docs/workflow-setup.md dify/paper-comparison-merged-workflow.md
git commit -m "docs: explain deepseek and ollama workflow profiles"
~~~

### Task 5: Final verification

- [ ] **Step 1: Run profile, DSL, and full regression tests.**

~~~powershell
python -m pytest -q tests/test_dify_llm_profiles.py tests/test_dify_job_workflow.py tests/test_dify_multimodel_dsl.py
python -m pytest -q
~~~

Expected: focused tests pass and the full suite remains green.

- [ ] **Step 2: Regenerate twice and compare all profile bytes.**

Run python scripts/build_multimodel_dsl.py --profile ollama twice and compare the three *-ollama.yml files byte-for-byte. Expected: no differences.

- [ ] **Step 3: Inspect the diff and secret scan.**

~~~powershell
git diff --check
rg -n "sk-[A-Za-z0-9]|RAW_CSV_SECRET|SECRET_TOKEN|PAPER_PARSER_API_TOKEN=[^\r\n]+|DIFY_PROTOCOL_SECRET=[^\r\n]+" dify scripts docs
~~~

Expected: no whitespace errors and no secret-like values.
