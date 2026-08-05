# V2 Table Experiment Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate `repro-runner` service that validates CSV datasets, runs a deterministic random-forest baseline, stores traceable results, and can be called from a separate Dify workflow.

**Architecture:** Keep `paper-parser` and its existing `/v1/parse` contract unchanged. Add a focused `repro_runner` Python package and a separate Docker Compose service sharing only the project source and experiment-result volume. The service exposes validation, training, result retrieval, and metric-comparison endpoints; Dify calls these endpoints over the existing Docker network and never sends the full CSV to an LLM.

**Tech Stack:** Python 3.12, FastAPI 0.141.1, Pydantic 2.13+, pandas 3.0.5, scikit-learn 1.9.0, NumPy 2.3.5, pytest 9.1.1, Docker Compose.

## Global Constraints

- Accept UTF-8 CSV input only; maximum file size is 100 MB and maximum field count is 256.
- Default target column is `Y_cls`; V2 accepts exactly two non-empty target classes and numeric feature columns.
- Default experiment is stratified `test_size=0.2`, `random_state=42`, `RandomForestClassifier(n_estimators=300, class_weight="balanced", n_jobs=-1)`.
- Results are labeled `baseline_only` and must not be described as exact paper reproduction when paper parameters are incomplete.
- Do not save a copy of the original CSV or place raw rows in logs or JSON results.
- Preserve the existing `paper-parser` image, service name, `/healthz`, and `/v1/parse` behavior.

---

## File map

- Create `src/repro_runner/__init__.py`: package marker and service version.
- Create `src/repro_runner/config.py`: environment-backed limits and storage path.
- Create `src/repro_runner/schemas.py`: request and response models shared by API and engine.
- Create `src/repro_runner/data.py`: CSV loading, schema checks, and profile generation.
- Create `src/repro_runner/engine.py`: deterministic random-forest training and metrics.
- Create `src/repro_runner/storage.py`: experiment directory persistence and safe ID lookup.
- Create `src/repro_runner/compare.py`: paper metric comparison.
- Create `src/repro_runner/api.py`: FastAPI routes and stable error mapping.
- Create `tests/repro_runner/test_data.py`: data validation tests.
- Create `tests/repro_runner/test_engine.py`: deterministic training and metrics tests.
- Create `tests/repro_runner/test_api.py`: endpoint contract tests.
- Create `tests/repro_runner/test_compare.py`: comparison semantics tests.
- Create `Dockerfile.repro`: minimal runtime image for `repro-runner`.
- Create `requirements-repro.lock`: pinned service dependencies.
- Modify `compose.yaml`: add `repro-runner` service and `/data/experiments` volume.
- Modify `.env.example`: add `REPRO_RUNNER_*` settings.
- Create `dify/repro-experiment-workflow.md`: exact Dify node configuration and HTTP mappings.
- Create `scripts/smoke_experiment.ps1`: local/Docker smoke test using the supplied CSV path.
- Modify `docs/configuration-guide.md`: describe starting and checking the new service.

## Task 1: Add the package contracts and configuration

**Files:**
- Create: `src/repro_runner/__init__.py`
- Create: `src/repro_runner/config.py`
- Create: `src/repro_runner/schemas.py`
- Test: `tests/repro_runner/test_data.py`

**Interfaces:**
- `get_settings() -> Settings` returns `max_upload_mb=100`, `max_columns=256`, `default_target_column="Y_cls"`, and `storage_dir=Path("/data/experiments")` unless overridden by environment.
- `DatasetOptions` has `target_column: str = "Y_cls"` and `drop_duplicates: bool = False`.
- `ExperimentConfig` has `model: Literal["random_forest"]`, `test_size: float = 0.2`, `random_state: int = 42`, and `drop_duplicates: bool = False`.
- `DatasetProfile`, `ValidationResponse`, `ExperimentResult`, `ReportedMetricInput`, and `ComparisonResponse` are Pydantic models matching the design document JSON examples.

- [ ] **Step 1: Write the failing contract test**

```python
from repro_runner.config import Settings
from repro_runner.schemas import ExperimentConfig


def test_defaults_match_v2_contract():
    settings = Settings()
    config = ExperimentConfig()
    assert settings.max_upload_mb == 100
    assert settings.max_columns == 256
    assert config.model == "random_forest"
    assert config.test_size == 0.2
    assert config.random_state == 42
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python -m pytest tests/repro_runner/test_data.py::test_defaults_match_v2_contract -q`

Expected: FAIL because `repro_runner` does not exist.

- [ ] **Step 3: Implement the contracts**

```python
# src/repro_runner/config.py
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPRO_RUNNER_", extra="ignore")
    max_upload_mb: int = 100
    max_columns: int = 256
    default_target_column: str = "Y_cls"
    storage_dir: Path = Path("/data/experiments")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

Implement the Pydantic models with `extra="forbid"`, `test_size` constrained to `0.1 <= value <= 0.5`, non-negative `random_state`, and the exact response field names in the design.

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `python -m pytest tests/repro_runner/test_data.py::test_defaults_match_v2_contract -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/repro_runner tests/repro_runner/test_data.py
git commit -m "feat: add experiment service contracts"
```

## Task 2: Implement CSV loading, validation, and profiling

**Files:**
- Create: `src/repro_runner/data.py`
- Modify: `tests/repro_runner/test_data.py`

**Interfaces:**
- `DatasetError(code: str, message: str)` is the public validation exception.
- `DatasetBundle` contains `frame: pandas.DataFrame`, `target_column: str`, `feature_columns: list[str]`, and `profile: DatasetProfile`.
- `load_dataset(content: bytes, options: DatasetOptions, settings: Settings) -> DatasetBundle` rejects content over the configured byte limit, files with more than 256 columns, missing target columns, single-class targets, missing values, and non-numeric features.
- `profile_dataset(frame: pandas.DataFrame, target_column: str, warnings: list[str]) -> DatasetProfile` counts rows, features, missing values, duplicate rows, class counts, class ratios, and numeric ranges without returning raw rows.

- [ ] **Step 1: Add failing tests for the supplied dataset shape and invalid inputs**

```python
from pathlib import Path
import pytest
from repro_runner.config import Settings
from repro_runner.data import DatasetError, load_dataset
from repro_runner.schemas import DatasetOptions

CSV = Path(r"E:\论文复现\成果\2training_samples_15180.csv")


def test_supplied_dataset_profile():
    if not CSV.exists():
        pytest.skip("supplied CSV is only available on the local Windows host")
    bundle = load_dataset(CSV.read_bytes(), DatasetOptions(), Settings())
    assert bundle.profile.rows == 15180
    assert bundle.profile.features == 16
    assert bundle.profile.missing_values == 0
    assert bundle.profile.duplicate_rows == 66
    assert bundle.profile.class_counts == {"0": 13800, "1": 1380}


def test_missing_target_is_rejected():
    content = b"x,y\n1,0\n2,1\n"
    try:
        load_dataset(content, DatasetOptions(), Settings())
    except DatasetError as exc:
        assert exc.code == "missing_target_column"
    else:
        raise AssertionError("expected DatasetError")
```

- [ ] **Step 2: Run tests and verify the new tests fail**

Run: `python -m pytest tests/repro_runner/test_data.py -q`

Expected: FAIL because the loader is not implemented.

- [ ] **Step 3: Implement bounded CSV parsing and profiling**

Use `io.BytesIO`, `pandas.read_csv`, and `pd.to_numeric` checks. Reject empty bytes, parser errors, missing/duplicate column names, more than 256 columns, a missing target, fewer than two rows per class, any missing value, and any non-numeric feature. Do not include the file name in a filesystem path. Emit the warning `dataset contains duplicate rows` when duplicates are present and preserve the `drop_duplicates` option in the bundle profile only after creating the pre-cleaning profile.

- [ ] **Step 4: Run the data tests and verify they pass**

Run: `python -m pytest tests/repro_runner/test_data.py -q`

Expected: PASS, including the supplied CSV assertions and invalid-input cases.

- [ ] **Step 5: Commit**

```text
git add src/repro_runner/data.py tests/repro_runner/test_data.py
git commit -m "feat: validate and profile experiment csv data"
```

## Task 3: Implement deterministic random-forest training

**Files:**
- Create: `src/repro_runner/engine.py`
- Create: `tests/repro_runner/test_engine.py`

**Interfaces:**
- `run_random_forest(bundle: DatasetBundle, config: ExperimentConfig) -> ExperimentResult` performs a stratified split, fits the configured forest, computes metrics, and returns sorted feature importances.
- `run_random_forest` uses `train_test_split(..., stratify=y, random_state=config.random_state)` and `RandomForestClassifier(n_estimators=300, class_weight="balanced", n_jobs=-1, random_state=config.random_state)`.
- Metrics are `roc_auc`, `accuracy`, `balanced_accuracy`, `precision`, `recall`, `f1`, and `confusion_matrix`; binary metrics use the sorted second class as the positive class.

- [ ] **Step 1: Write failing deterministic-engine tests**

```python
import pandas as pd
from repro_runner.data import load_dataset
from repro_runner.engine import run_random_forest
from repro_runner.schemas import DatasetOptions, ExperimentConfig
from repro_runner.config import Settings


def test_same_seed_produces_same_metrics():
    frame = pd.DataFrame({"x1": [0, 1, 0, 1] * 30, "x2": [1, 1, 0, 0] * 30,
                          "Y_cls": [0, 1, 0, 1] * 30})
    csv = frame.to_csv(index=False).encode()
    bundle = load_dataset(csv, DatasetOptions(), Settings())
    first = run_random_forest(bundle, ExperimentConfig())
    second = run_random_forest(bundle, ExperimentConfig())
    assert first.metrics == second.metrics
    assert first.reproducibility_status == "baseline_only"


def test_feature_importances_are_sorted_and_sum_to_one():
    frame = pd.DataFrame({"signal": [0, 0, 1, 1] * 40, "noise": [1, 0, 1, 0] * 40,
                          "Y_cls": [0, 0, 1, 1] * 40})
    bundle = load_dataset(frame.to_csv(index=False).encode(), DatasetOptions(), Settings())
    result = run_random_forest(bundle, ExperimentConfig())
    values = [item.importance for item in result.feature_importance]
    assert values == sorted(values, reverse=True)
    assert abs(sum(values) - 1.0) < 1e-6
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m pytest tests/repro_runner/test_engine.py -q`

Expected: FAIL because `run_random_forest` is not implemented.

- [ ] **Step 3: Implement the engine**

Convert `bundle.frame[bundle.feature_columns]` to a NumPy matrix, split with stratification, fit the forest, calculate `predict_proba`-based ROC AUC plus threshold-0.5 metrics, and serialize the confusion matrix as nested integer lists. Sort feature importances by descending importance and then feature name. Round public floats to six decimals. Set `status="succeeded"`, `model="random_forest"`, and `reproducibility_status="baseline_only"`.

- [ ] **Step 4: Run the engine tests and verify they pass**

Run: `python -m pytest tests/repro_runner/test_engine.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/repro_runner/engine.py tests/repro_runner/test_engine.py
git commit -m "feat: run deterministic random forest baseline"
```

## Task 4: Add result storage and metric comparison

**Files:**
- Create: `src/repro_runner/storage.py`
- Create: `src/repro_runner/compare.py`
- Create: `tests/repro_runner/test_compare.py`

**Interfaces:**
- `create_experiment_id() -> str` returns an `exp-` prefix plus UTC timestamp and random suffix.
- `save_result(result: ExperimentResult, settings: Settings) -> str` creates `<storage_dir>/<experiment_id>/result.json`, `config.json`, and `dataset_profile.json` without storing the CSV bytes.
- `load_result(experiment_id: str, settings: Settings) -> ExperimentResult` rejects path traversal and missing IDs with `ResultNotFoundError`.
- `ReportedMetricInput` has `name: str`, `reported_value: float | str | None`, `dataset: str | None`, and `split: str | None`.
- `compare_metrics(result: ExperimentResult, reported: list[ReportedMetricInput]) -> ComparisonResponse` matches normalized metric names and returns `comparable=false` when dataset or split qualifiers are absent or different.

- [ ] **Step 1: Write failing storage and comparison tests**

```python
def test_comparison_reports_absolute_and_relative_difference():
    result = make_result(metric_name="roc_auc", value=0.90, dataset="test", split="test")
    response = compare_metrics(result, [{"name": "AUC", "reported_value": 0.91,
                                         "dataset": "test", "split": "test"}])
    item = response.items[0]
    assert item.comparable is True
    assert item.absolute_difference == -0.01
    assert item.paper_value == 0.91
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m pytest tests/repro_runner/test_compare.py -q`

Expected: FAIL because storage and comparison functions are not implemented.

- [ ] **Step 3: Implement safe storage and comparison**

Use `secrets.token_hex` for the suffix, `Path.resolve()` plus a prefix check for reads, UTF-8 JSON with `ensure_ascii=False`, and atomic temporary-file replacement for writes. Normalize `auc` and `roc_auc` to the same comparison key; parse numeric strings with `float`; calculate `independent - paper` and `(independent - paper) / abs(paper)` when the denominator is nonzero.

- [ ] **Step 4: Run tests and verify they pass**

Run: `python -m pytest tests/repro_runner/test_compare.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/repro_runner/storage.py src/repro_runner/compare.py tests/repro_runner/test_compare.py
git commit -m "feat: persist experiments and compare paper metrics"
```

## Task 5: Expose the FastAPI service

**Files:**
- Create: `src/repro_runner/api.py`
- Create: `tests/repro_runner/test_api.py`

**Interfaces:**
- `GET /healthz -> {"status":"ok"}`.
- `POST /v1/validate-dataset` accepts `file` and optional `target_column`, returns `ValidationResponse`.
- `POST /v1/run-experiment` accepts `file`, `target_column`, `test_size`, `random_state`, `drop_duplicates`, and `model`, returns `ExperimentResult`.
- `GET /v1/experiments/{experiment_id}` returns a stored `ExperimentResult`.
- `POST /v1/compare-result` accepts `experiment_id` plus reported metrics and returns `ComparisonResponse`.
- All validation failures use stable `detail.code` values and a generated `request_id`; training failures do not expose tracebacks.

- [ ] **Step 1: Write failing endpoint tests**

```python
from fastapi.testclient import TestClient
from repro_runner.api import app

client = TestClient(app)


def test_healthz_is_public():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_validate_dataset_returns_profile(tmp_path):
    csv = b"x1,x2,Y_cls\n0,1,0\n1,0,1\n0,0,0\n1,1,1\n"
    response = client.post("/v1/validate-dataset", files={"file": ("data.csv", csv, "text/csv")})
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["dataset"]["target"] == "Y_cls"


def test_run_experiment_returns_id_and_metrics():
    csv = b"x1,x2,Y_cls\n0,1,0\n1,0,1\n0,0,0\n1,1,1\n" * 40
    response = client.post("/v1/run-experiment", files={"file": ("data.csv", csv, "text/csv")})
    assert response.status_code == 200
    body = response.json()
    assert body["experiment_id"].startswith("exp-")
    assert "roc_auc" in body["metrics"]
```

- [ ] **Step 2: Run endpoint tests and verify they fail**

Run: `python -m pytest tests/repro_runner/test_api.py -q`

Expected: FAIL because the API module and routes are not implemented.

- [ ] **Step 3: Implement routes and exception mapping**

Use `UploadFile.read(max_bytes + 1)` to enforce the limit before parsing. Run the CPU-bound `run_random_forest` through `run_in_threadpool`. Use dependency-injected `Settings`. Map `DatasetError` to HTTP 422 with `detail={"code": exc.code, "message": exc.message, "request_id": ...}` and `ResultNotFoundError` to HTTP 404. Save successful results before returning them.

- [ ] **Step 4: Run all package tests and verify they pass**

Run: `python -m pytest tests/repro_runner -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/repro_runner/api.py tests/repro_runner/test_api.py
git commit -m "feat: expose experiment service api"
```

## Task 6: Build and run the separate Docker service

**Files:**
- Create: `Dockerfile.repro`
- Create: `requirements-repro.lock`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Create: `tests/repro_runner/test_compose_contract.py`

**Interfaces:**
- Compose service name is `repro-runner`, container port is 8001, and its network alias is `repro-runner`.
- The service mounts `./src:/app/src:ro` and `./data/experiments:/data/experiments`.
- `REPRO_RUNNER_STORAGE_DIR=/data/experiments` is the container default.

- [ ] **Step 1: Write the failing compose contract test**

```python
from pathlib import Path
import yaml


def test_compose_declares_repro_runner():
    document = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    service = document["services"]["repro-runner"]
    assert service["container_name"] == "repro-runner"
    assert "repro-runner" in service["networks"]["dify"]["aliases"]
    assert "8001" in service["ports"][0]
```

- [ ] **Step 2: Run the contract test and verify it fails**

Run: `python -m pytest tests/repro_runner/test_compose_contract.py -q`

Expected: FAIL because the service is absent from `compose.yaml`.

- [ ] **Step 3: Add the minimal image and service**

`Dockerfile.repro` installs `requirements-repro.lock`, copies `src`, creates the non-root `app` user, creates `/data/experiments`, and runs:

```text
uvicorn repro_runner.api:app --host 0.0.0.0 --port 8001 --workers 1
```

Add a `repro-runner` Compose service with `build.context: .`, `dockerfile: Dockerfile.repro`, `env_file: .env`, `ports: ["8001:8001"]`, the existing external `docker_default` network, and a healthcheck for `http://localhost:8001/healthz`. Generate `requirements-repro.lock` with pinned direct versions from the global constraints and their resolved transitive dependencies.

- [ ] **Step 4: Run tests and build the image**

Run: `python -m pytest tests/repro_runner/test_compose_contract.py -q`

Expected: PASS.

Run: `docker compose build repro-runner`

Expected: image builds successfully without changing `paper-parser`.

- [ ] **Step 5: Start the service and verify health**

Run: `docker compose up -d repro-runner`; then `Invoke-RestMethod http://localhost:8001/healthz`.

Expected: `status` is `ok` and the container health is `healthy`.

- [ ] **Step 6: Commit**

```text
git add Dockerfile.repro requirements-repro.lock compose.yaml .env.example tests/repro_runner/test_compose_contract.py
git commit -m "feat: add repro runner docker service"
```

## Task 7: Add Dify workflow configuration and smoke test

**Files:**
- Create: `dify/repro-experiment-workflow.md`
- Create: `scripts/smoke_experiment.ps1`
- Modify: `docs/configuration-guide.md`

**Interfaces:**
- Dify workflow input names are `training_csv`, `target_column`, `test_size`, `random_state`, and `drop_duplicates`.
- The validation HTTP node sends `file={{#start.training_csv#}}` to `http://repro-runner:8001/v1/validate-dataset`.
- The run HTTP node sends the same file and config to `http://repro-runner:8001/v1/run-experiment`.
- The workflow output exposes `experiment_json`, `validation_json`, and `markdown_summary`.

- [ ] **Step 1: Add a smoke test that initially fails**

```powershell
$csv = 'E:\论文复现\成果\2training_samples_15180.csv'
$validation = curl.exe -s -X POST -F "file=@$csv" http://localhost:8001/v1/validate-dataset | ConvertFrom-Json
if (-not $validation.valid) { throw "dataset validation failed" }
if ($validation.dataset.rows -ne 15180) { throw "unexpected row count" }
$run = curl.exe -s -X POST -F "file=@$csv" -F "target_column=Y_cls" http://localhost:8001/v1/run-experiment | ConvertFrom-Json
if (-not $run.experiment_id) { throw "missing experiment id" }
if ($run.status -ne 'succeeded') { throw "experiment failed" }
Write-Output ($run | ConvertTo-Json -Depth 8)
```

- [ ] **Step 2: Run the smoke test and verify it fails before integration**

Run: `powershell -ExecutionPolicy Bypass -File scripts/smoke_experiment.ps1`

Expected: FAIL with a connection error when `repro-runner` is not running.

- [ ] **Step 3: Write the Dify workflow instructions and report formatter**

Document the exact start variables, HTTP form-data fields, success/failure branches, and a code-node formatter that prints the status as `baseline_only`, the data profile, metrics, and feature importance. Explain that the file is sent to Python but never placed in an LLM prompt. Add the start/health/build/smoke commands to `docs/configuration-guide.md`.

- [ ] **Step 4: Run the smoke test against the container**

Run: `powershell -ExecutionPolicy Bypass -File scripts/smoke_experiment.ps1`

Expected: validation reports 15180 rows, 16 features, 0 missing values, 66 duplicate rows; training returns `status=succeeded` and a non-empty `experiment_id`.

- [ ] **Step 5: Commit**

```text
git add dify/repro-experiment-workflow.md scripts/smoke_experiment.ps1 docs/configuration-guide.md
git commit -m "docs: configure dify experiment workflow"
```

## Final verification

- [ ] Run `python -m pytest tests/repro_runner -q` and record the passing count.
- [ ] Run the existing parser suite `python -m pytest tests -q` to confirm no regression.
- [ ] Run `docker compose ps` and verify both `paper-parser` and `repro-runner` are healthy.
- [ ] Run `scripts/smoke_experiment.ps1` with the supplied CSV.
- [ ] Inspect one saved `data/experiments/<experiment_id>/result.json` and verify it contains no raw rows or CSV content.
- [ ] Report the first baseline metrics as independent baseline results, not exact paper reproduction.
