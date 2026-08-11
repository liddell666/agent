# Multi-Model CV Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Add a reproducible seven-model experiment suite that uses one shared stratified holdout and training-only five-fold cross-validation, while preserving the existing single random-forest endpoint.

**Architecture:** Keep \`/v1/run-experiment\` as the backward-compatible random-forest baseline. Add \`/v1/run-model-suite\` backed by a model registry, a shared split helper, and a sequential suite engine; each model is tuned only on the outer training partition and evaluated once on the same outer test partition. Add a suite comparison endpoint and a new Dify DSL generated from the current workflow so the existing published workflow is not overwritten.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, pandas 3, NumPy 2, scikit-learn 1.9, XGBoost, LightGBM, pytest, PyYAML, Dify DSL, Docker Compose.

## Global Constraints

- All successful models use the same \`test_size=0.2\`, \`random_state=42\`, stratified outer split, and \`test_digest\`.
- Cross-validation is limited to the outer training partition and uses \`StratifiedKFold(n_splits=5, shuffle=True, random_state=42)\`.
- The default search scorer is \`roc_auc\`; allowed alternatives are \`f1\`, \`recall\`, and \`balanced_accuracy\`.
- The final paper-comparison threshold remains \`0.5\` unless the request explicitly changes it.
- The suite runs models sequentially and defaults to CPU; \`use_gpu=false\` remains the default.
- XGBoost and LightGBM are runtime dependencies of the repro runner image; missing optional imports are reported per model and do not abort other models.
- The existing \`/v1/run-experiment\` contract, stored \`ExperimentResult\`, and published workflow files remain backward-compatible.
- No CSV source rows, test rows, model binaries, API keys, or raw upload contents may be persisted or placed in Dify prompts.
- Every implementation task follows TDD: write a focused failing test, run it, implement the smallest passing change, rerun focused tests, then commit.

---

## File Map

Create or modify only the following project files for the feature:

- \`src/repro_runner/schemas.py\`: suite configuration, per-model result, suite result, and suite comparison contracts.
- \`src/repro_runner/split.py\`: reusable stratified outer split and test-set digest helpers.
- \`src/repro_runner/metrics.py\`: common probability-to-metrics and feature-importance helpers.
- \`src/repro_runner/model_registry.py\`: seven model factories, preprocessing pipelines, search spaces, and optional-dependency handling.
- \`src/repro_runner/suite_engine.py\`: sequential CV search, refit, evaluation, ranking, and failure isolation.
- \`src/repro_runner/engine.py\`: adapt the existing single-model path to use the shared split/metric helpers without changing its public result.
- \`src/repro_runner/compare.py\`: suite metric comparison and explicit provenance reason codes.
- \`src/repro_runner/storage.py\`: persistence and loading of \`ExperimentSuiteResult\`.
- \`src/repro_runner/api.py\`: \`/v1/run-model-suite\`, suite retrieval, and suite comparison endpoints.
- \`requirements-repro.in\` and \`requirements-repro.lock\`: pinned XGBoost/LightGBM runtime dependencies.
- \`tests/repro_runner/test_suite_schemas.py\`, \`test_split.py\`, \`test_metrics.py\`, \`test_model_registry.py\`, \`test_suite_engine.py\`, \`test_suite_api.py\`, \`test_suite_compare.py\`, and existing regression tests.
- \`dify/code/experiment_workflow.py\`: deterministic normalization for suite form inputs and suite failures.
- \`dify/code/comparison_workflow.py\`: suite request/response formatting and multi-model report formatting.
- \`scripts/build_multimodel_dsl.py\`: deterministic generator for the new Dify DSL.
- \`dify/paper-comparison-multimodel-workflow.yml\`: generated Dify workflow; do not overwrite the current workflow.
- \`dify/paper-comparison-multimodel-workflow.md\`: import, input, rollback, and verification guide.
- \`tests/test_dify_multimodel_dsl.py\` and \`tests/test_dify_multimodel_code.py\`: DSL and embedded-code contract tests.
- \`scripts/smoke_multimodel.ps1\`: runner health and local-file smoke check without secrets.
- \`docs/superpowers/sdd/task-multimodel-live-verification.md\`: live Dify verification record after implementation.

## Task 1: Define the suite contracts first

**Files:**
- Modify: \`src/repro_runner/schemas.py:13-190\`
- Test: \`tests/repro_runner/test_schemas.py\`
- Create: \`tests/repro_runner/test_suite_schemas.py\`

**Interfaces:**
- Produces \`ModelName\`, \`DEFAULT_MODEL_NAMES\`, \`ModelSuiteConfig\`, \`ModelRunResult\`, \`ExperimentSuiteResult\`, \`SuiteComparisonRequest\`, and \`SuiteComparisonResponse\`.
- Later \`run_model_suite(bundle, config)\` consumes \`ModelSuiteConfig\` and returns \`ExperimentSuiteResult\`.

- [ ] **Step 1: Write failing schema tests.**

~~~python
import pytest
from pydantic import ValidationError

from repro_runner.schemas import (
    DEFAULT_MODEL_NAMES,
    ExperimentSuiteResult,
    ModelSuiteConfig,
)


def test_suite_defaults_enable_all_seven_models_and_cv():
    config = ModelSuiteConfig()
    assert config.models == list(DEFAULT_MODEL_NAMES)
    assert config.test_size == 0.2
    assert config.random_state == 42
    assert config.cv_folds == 5
    assert config.optimization_metric == "roc_auc"
    assert config.threshold == 0.5
    assert config.n_iter == 8
    assert config.use_gpu is False


def test_suite_rejects_duplicate_models_and_unknown_scorer():
    with pytest.raises(ValidationError):
        ModelSuiteConfig(models=["random_forest", "random_forest"])
    with pytest.raises(ValidationError):
        ModelSuiteConfig(optimization_metric="accuracy")


def test_suite_result_allows_partial_model_failure():
    payload = {
        "experiment_id": "exp-20260811T000000Z-abcdef12",
        "status": "partial",
        "config": ModelSuiteConfig().model_dump(),
        "dataset": {
            "rows": 4, "effective_rows": 4, "features": 1, "target": "Y_cls",
            "missing_values": 0, "duplicate_rows": 0,
            "class_counts": {"0": 2, "1": 2},
            "class_ratios": {"0": 0.5, "1": 0.5},
            "column_names": ["x", "Y_cls"],
            "column_types": {"x": "int64", "Y_cls": "int64"},
            "numeric_ranges": {"x": [0.0, 3.0]},
            "dataset_id": "sha256:" + "0" * 64,
        },
        "split_provenance": {
            "test_size": 0.2, "random_state": 42,
            "train_rows": 3, "test_rows": 1,
            "test_digest": "sha256:" + "1" * 64,
        },
        "results": [],
        "performance_ranking": [],
        "reproducibility_status": "cv_tuned",
    }
    assert ExperimentSuiteResult.model_validate(payload).status == "partial"
~~~

- [ ] **Step 2: Run the focused tests and confirm the contract is missing.**

Run from \`C:\Users\17716\Documents\arcgis\paper-repro-agent\`:

~~~powershell
pytest -q tests/repro_runner/test_suite_schemas.py
~~~

Expected: collection or import failure because the suite types do not exist.

- [ ] **Step 3: Add the exact suite Pydantic types.**

Add these definitions after \`ExperimentResult\` and before the comparison request types:

~~~python
from typing import Any, Literal

ModelName = Literal[
    "logistic_regression", "random_forest", "xgboost", "lightgbm",
    "svm", "knn", "mlp",
]
DEFAULT_MODEL_NAMES: tuple[str, ...] = (
    "logistic_regression", "random_forest", "xgboost", "lightgbm",
    "svm", "knn", "mlp",
)


class ModelSuiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[ModelName] = Field(
        default_factory=lambda: list(DEFAULT_MODEL_NAMES), min_length=1
    )
    test_size: float = Field(default=0.2, ge=0.1, le=0.5)
    random_state: int = Field(default=42, ge=0)
    drop_duplicates: bool = False
    cv_folds: int = Field(default=5, ge=3, le=10)
    optimization_metric: Literal[
        "roc_auc", "f1", "recall", "balanced_accuracy"
    ] = "roc_auc"
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    n_iter: int = Field(default=8, ge=1, le=32)
    use_gpu: bool = False
    n_jobs: int = Field(default=4, ge=1, le=16)

    @model_validator(mode="after")
    def reject_duplicate_models(self) -> "ModelSuiteConfig":
        if len(self.models) != len(set(self.models)):
            raise ValueError("models must not contain duplicates")
        return self


class ModelRunResult(BaseModel):
    model: ModelName
    status: Literal["succeeded", "unavailable", "failed"]
    cv_best_score: float | None = None
    best_params: dict[str, Any] = Field(default_factory=dict)
    metrics: ExperimentMetrics | None = None
    feature_importance: list[FeatureImportance] = Field(default_factory=list)
    fit_seconds: float | None = None
    error: ValidationErrorItem | None = None


class ExperimentSuiteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    status: Literal["succeeded", "partial", "failed"]
    config: ModelSuiteConfig
    dataset: DatasetProfile
    split_provenance: SplitProvenance
    results: list[ModelRunResult] = Field(default_factory=list)
    performance_ranking: list[ModelName] = Field(default_factory=list)
    reproducibility_status: Literal["cv_tuned"] = "cv_tuned"


class SuiteComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    reported_metrics: list[ReportedMetricInput] = Field(default_factory=list)


class SuiteComparisonItem(ComparisonItem):
    model: ModelName


class SuiteComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    items: list[SuiteComparisonItem] = Field(default_factory=list)
    paper_reference_metric: str | None = None
~~~

- [ ] **Step 4: Run schema tests and the existing schema suite.**

~~~powershell
pytest -q tests/repro_runner/test_suite_schemas.py tests/repro_runner/test_schemas.py
~~~

Expected: all tests pass.

- [ ] **Step 5: Commit the contract.**

~~~powershell
git add src/repro_runner/schemas.py tests/repro_runner/test_suite_schemas.py tests/repro_runner/test_schemas.py
git commit -m "feat: define multi-model suite contracts"
~~~

## Task 2: Extract a shared split and metric evaluation layer

**Files:**
- Create: \`src/repro_runner/split.py\`
- Create: \`src/repro_runner/metrics.py\`
- Modify: \`src/repro_runner/engine.py:7-163\`
- Test: \`tests/repro_runner/test_split.py\`
- Test: \`tests/repro_runner/test_metrics.py\`
- Modify: \`tests/repro_runner/test_engine.py:1-170\`

**Interfaces:**
- \`make_stratified_split(target, test_size, random_state) -> tuple[np.ndarray, np.ndarray]\`.
- \`test_set_digest(bundle, test_indices) -> str\`.
- \`evaluate_classifier(classifier, x_test, y_test, classes, threshold) -> ExperimentMetrics\`.
- \`feature_importances(classifier, feature_columns) -> list[FeatureImportance]\`.
- The existing \`run_random_forest\` continues to return the same metrics and provenance fields.

- [ ] **Step 1: Add failing tests for one shared split and deterministic metrics.**

~~~python
import numpy as np
import pandas as pd

from repro_runner.config import Settings
from repro_runner.data import load_dataset
from repro_runner.metrics import evaluate_classifier
from repro_runner.schemas import DatasetOptions
from repro_runner.split import make_stratified_split, test_set_digest


def test_shared_split_is_deterministic_and_stratified():
    target = np.array([0, 1] * 50)
    first = make_stratified_split(target, 0.2, 42)
    second = make_stratified_split(target, 0.2, 42)
    assert np.array_equal(first[0], second[0])
    assert np.array_equal(first[1], second[1])
    assert set(target[first[0]]) == {0, 1}
    assert set(target[first[1]]) == {0, 1}


def test_test_digest_changes_when_test_indices_change():
    frame = pd.DataFrame({"x": range(8), "Y_cls": [0, 1] * 4})
    bundle = load_dataset(
        frame.to_csv(index=False).encode(), DatasetOptions(), Settings()
    )
    first = test_set_digest(bundle, np.array([0, 1, 2]))
    second = test_set_digest(bundle, np.array([2, 3, 4]))
    assert first.startswith("sha256:")
    assert first != second
~~~

- [ ] **Step 2: Run the focused tests to verify the helpers are absent.**

~~~powershell
pytest -q tests/repro_runner/test_split.py tests/repro_runner/test_metrics.py
~~~

Expected: import failure for \`repro_runner.split\` and \`repro_runner.metrics\`.

- [ ] **Step 3: Implement the shared helpers.**

\`split.py\` must use the existing \`train_test_split\` behavior and preserve the current invalid-split error contract:

~~~python
def make_stratified_split(target, test_size, random_state):
    try:
        return train_test_split(
            np.arange(len(target)),
            test_size=test_size,
            stratify=target,
            random_state=random_state,
        )
    except ValueError as exc:
        raise ExperimentError(
            "invalid_split",
            "the requested stratified test split cannot represent both target classes",
        ) from exc
~~~

Move the current digest payload unchanged into \`test_set_digest\`. \`metrics.py\` must select the sorted second class as the positive class, apply \`threshold\` to \`predict_proba()\`, calculate the six existing metrics and confusion matrix, and return the existing \`ExperimentMetrics\` model. For estimators exposing only \`decision_function\`, use that score for ROC-AUC and use \`score >= 0\` for the fallback threshold.

- [ ] **Step 4: Refactor only the implementation internals of \`run_random_forest\`.**

Keep its public signature and fixed forest constructor unchanged:

~~~python
def run_random_forest(bundle: DatasetBundle, config: ExperimentConfig) -> ExperimentResult:
    train_indices, test_indices = make_stratified_split(
        target, config.test_size, config.random_state
    )
    metrics = evaluate_classifier(
        classifier, x_test, y_test, classes, threshold=0.5
    )
~~~

Keep the old feature-importance rounding helper behavior and update the existing patch-based test to patch the shared helper boundary instead of changing the baseline constructor.

- [ ] **Step 5: Run focused and regression tests.**

~~~powershell
pytest -q tests/repro_runner/test_split.py tests/repro_runner/test_metrics.py tests/repro_runner/test_engine.py
~~~

Expected: all tests pass, including the same-seed and fixed-forest-parameter tests.

- [ ] **Step 6: Commit the shared layer.**

~~~powershell
git add src/repro_runner/split.py src/repro_runner/metrics.py src/repro_runner/engine.py tests/repro_runner/test_split.py tests/repro_runner/test_metrics.py tests/repro_runner/test_engine.py
git commit -m "refactor: share split and metric evaluation logic"
~~~

## Task 3: Add the model registry and locked runtime dependencies

**Files:**
- Create: \`src/repro_runner/model_registry.py\`
- Modify: \`requirements-repro.in\`
- Regenerate: \`requirements-repro.lock\`
- Test: \`tests/repro_runner/test_model_registry.py\`
- Modify: \`tests/repro_runner/test_compose_contract.py\`

**Interfaces:**
- \`ModelSpec\` exposes \`name\`, \`estimator\`, \`search_space\`, \`optional_dependency\`, and \`feature_importance_kind\`.
- \`get_model_spec(name, class_counts, random_state, use_gpu) -> ModelSpec\`.
- \`available_model_names() -> tuple[str, ...]\`.

- [ ] **Step 1: Add failing registry tests.**

~~~python
import sys
import pytest
from sklearn.pipeline import Pipeline

from repro_runner.model_registry import available_model_names, get_model_spec


def test_registry_contains_all_requested_models():
    assert available_model_names() == (
        "logistic_regression", "random_forest", "xgboost", "lightgbm",
        "svm", "knn", "mlp",
    )


@pytest.mark.parametrize("name", available_model_names())
def test_registry_builds_pipeline_or_explicit_optional_dependency(name):
    try:
        spec = get_model_spec(
            name, {"0": 90, "1": 10}, random_state=42, use_gpu=False
        )
    except ImportError as error:
        assert name in {"xgboost", "lightgbm"}
        assert str(error)
        return
    assert isinstance(spec.estimator, Pipeline)
    assert spec.search_space
    assert spec.name == name


def test_lightgbm_missing_dependency_has_stable_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "lightgbm", None)
    with pytest.raises(ImportError, match="lightgbm"):
        get_model_spec(
            "lightgbm", {"0": 90, "1": 10}, random_state=42, use_gpu=False
        )
~~~

- [ ] **Step 2: Run the registry tests and confirm the module is absent.**

~~~powershell
pytest -q tests/repro_runner/test_model_registry.py
~~~

Expected: import failure for \`repro_runner.model_registry\`.

- [ ] **Step 3: Add the exact dependencies to the input lock.**

Append these package names to \`requirements-repro.in\`, then resolve them through the existing pip-compile workflow:

~~~text
xgboost
lightgbm
~~~

Run:

~~~powershell
pip-compile --output-file=requirements-repro.lock --strip-extras requirements-repro.in
~~~

Expected: the lock contains resolved XGBoost and LightGBM versions compatible with Python 3.12, and the input file remains the source of truth.

- [ ] **Step 4: Implement the registry with bounded search spaces.**

Use \`Pipeline([("scale", StandardScaler()), ("model", estimator)])\` for LR, SVM, KNN and MLP. Use unscaled estimators for RF, XGBoost and LightGBM. The registry must construct class balancing from the provided training counts and set every estimator's \`random_state\` where supported.

Use lazy imports for optional packages:

~~~python
def _xgboost_spec(class_counts, random_state, use_gpu):
    try:
        from xgboost import XGBClassifier
    except ImportError as exc:
        raise ImportError("xgboost dependency is not installed") from exc
    negative = class_counts.get("0", 0)
    positive = class_counts.get("1", 0)
    estimator = XGBClassifier(
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        scale_pos_weight=negative / positive,
        random_state=random_state,
        n_jobs=4,
        device="cuda" if use_gpu else "cpu",
    )
    return ModelSpec(
        name="xgboost",
        estimator=estimator,
        search_space={
            "n_estimators": [200, 400],
            "max_depth": [3, 5],
            "learning_rate": [0.03, 0.1],
            "subsample": [0.8, 1.0],
        },
        optional_dependency="xgboost",
        feature_importance_kind="tree",
    )
~~~

Implement analogous bounded spaces for all seven models, with \`probability=True\` for SVM, \`early_stopping=True\` for MLP, and no hidden parallel execution beyond configured \`n_jobs\`.

- [ ] **Step 5: Run registry, dependency, and compose contract tests.**

~~~powershell
pytest -q tests/repro_runner/test_model_registry.py tests/repro_runner/test_compose_contract.py
~~~

Expected: all tests pass and the repro requirements include both external boosting libraries.

- [ ] **Step 6: Commit the registry and dependency lock.**

~~~powershell
git add src/repro_runner/model_registry.py requirements-repro.in requirements-repro.lock tests/repro_runner/test_model_registry.py tests/repro_runner/test_compose_contract.py
git commit -m "feat: add seven-model registry and runtime dependencies"
~~~

## Task 4: Implement the sequential CV suite engine

**Files:**
- Create: \`src/repro_runner/suite_engine.py\`
- Test: \`tests/repro_runner/test_suite_engine.py\`

**Interfaces:**
- \`run_model_suite(bundle: DatasetBundle, config: ModelSuiteConfig) -> ExperimentSuiteResult\`.
- The function creates one outer split, invokes \`RandomizedSearchCV\` once per requested model, and returns partial results when some models fail.

- [ ] **Step 1: Write failing suite-engine tests.**

~~~python
import pandas as pd

from repro_runner.config import Settings
from repro_runner.data import load_dataset
from repro_runner.schemas import DatasetOptions, ModelSuiteConfig
from repro_runner.suite_engine import run_model_suite


def bundle():
    frame = pd.DataFrame({
        "x1": [0, 1, 0, 1] * 30,
        "x2": [1, 1, 0, 0] * 30,
        "Y_cls": [0, 1, 0, 1] * 30,
    })
    return load_dataset(
        frame.to_csv(index=False).encode(), DatasetOptions(), Settings()
    )


def test_suite_uses_one_test_digest_for_all_successful_models():
    result = run_model_suite(
        bundle(),
        ModelSuiteConfig(
            models=["logistic_regression", "random_forest"], n_iter=1
        ),
    )
    successful = [item for item in result.results if item.status == "succeeded"]
    assert len(successful) == 2
    assert result.split_provenance.test_digest.startswith("sha256:")
    assert result.performance_ranking


def test_suite_continues_after_one_model_failure(monkeypatch):
    import repro_runner.suite_engine as suite_engine

    original = suite_engine.get_model_spec

    def fail_xgboost(name, *args, **kwargs):
        if name == "xgboost":
            raise ImportError("xgboost dependency is not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(suite_engine, "get_model_spec", fail_xgboost)
    result = run_model_suite(
        bundle(),
        ModelSuiteConfig(
            models=["xgboost", "logistic_regression"], n_iter=1
        ),
    )
    assert result.status == "partial"
    assert {item.status for item in result.results} == {
        "unavailable", "succeeded"
    }
~~~

- [ ] **Step 2: Run the suite tests to verify the engine is absent.**

~~~powershell
pytest -q tests/repro_runner/test_suite_engine.py
~~~

Expected: import failure for \`repro_runner.suite_engine\`.

- [ ] **Step 3: Implement the outer split and CV loop.**

The core loop must follow this shape:

~~~python
train_indices, test_indices = make_stratified_split(
    target, config.test_size, config.random_state
)
cv = StratifiedKFold(
    n_splits=config.cv_folds,
    shuffle=True,
    random_state=config.random_state,
)
for model_name in config.models:
    started = perf_counter()
    try:
        spec = get_model_spec(
            model_name, train_class_counts,
            config.random_state, config.use_gpu
        )
        search = RandomizedSearchCV(
            estimator=spec.estimator,
            param_distributions=spec.search_space,
            n_iter=config.n_iter,
            scoring=config.optimization_metric,
            cv=cv,
            random_state=config.random_state,
            n_jobs=config.n_jobs,
            refit=True,
            error_score="raise",
        )
        search.fit(x_train, y_train)
        metrics = evaluate_classifier(
            search.best_estimator_, x_test, y_test,
            classes, config.threshold
        )
        results.append(ModelRunResult(
            model=model_name,
            status="succeeded",
            cv_best_score=round(float(search.best_score_), 6),
            best_params=_json_safe(search.best_params_),
            metrics=metrics,
            feature_importance=feature_importances(
                search.best_estimator_, bundle.feature_columns
            ),
            fit_seconds=round(perf_counter() - started, 3),
        ))
    except ImportError as exc:
        results.append(
            unavailable_result(
                model_name, "missing_dependency", str(exc), started
            )
        )
    except (ValueError, RuntimeError) as exc:
        results.append(
            failed_result(
                model_name, "model_training_failed", str(exc), started
            )
        )
~~~

Use a shared CV configuration with \`StratifiedKFold\`; preserve deterministic seeds. Rank successful results by \`(metrics.roc_auc, metrics.f1, metrics.recall)\` descending, preserve request order for ties, and set status to \`succeeded\`, \`partial\`, or \`failed\` based on success count.

- [ ] **Step 4: Add real-data-sized safety checks.**

Validate before creating searches that \`cv_folds <= min(class_counts.values())\` in the outer training set. Raise \`ExperimentError(code="invalid_cv_folds", ...)\`; do not silently reduce folds. Validate that at least one model is requested and that the outer split contains both classes.

- [ ] **Step 5: Run focused suite and all runner engine tests.**

~~~powershell
pytest -q tests/repro_runner/test_suite_engine.py tests/repro_runner/test_engine.py
~~~

Expected: all tests pass; the suite test demonstrates one shared digest and partial failure isolation.

- [ ] **Step 6: Commit the suite engine.**

~~~powershell
git add src/repro_runner/suite_engine.py tests/repro_runner/test_suite_engine.py
git commit -m "feat: run tuned multi-model experiment suites"
~~~

## Task 5: Persist and expose the model suite through FastAPI

**Files:**
- Modify: \`src/repro_runner/storage.py:20-180\`
- Modify: \`src/repro_runner/api.py:1-430\`
- Test: \`tests/repro_runner/test_suite_api.py\`
- Modify: \`tests/repro_runner/test_api.py\`

**Interfaces:**
- \`POST /v1/run-model-suite\` accepts multipart CSV plus suite form fields and returns \`ExperimentSuiteResult\`.
- \`GET /v1/model-suites/{experiment_id}\` returns the persisted \`ExperimentSuiteResult\`.
- \`POST /v1/compare-model-suite-result\` is added in Task 6; this task only reserves storage and suite execution.

- [ ] **Step 1: Write failing API tests for success, invalid config, persistence, and idempotency.**

~~~python
def test_run_model_suite_returns_shared_provenance(client):
    response = client.post(
        "/v1/run-model-suite",
        data={
            "models_json": '["logistic_regression"]',
            "cv_folds": "3",
            "n_iter": "1",
            "idempotency_key": "suite-one",
        },
        files={"file": ("data.csv", _csv(), "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["experiment_id"].startswith("exp-")
    assert body["split_provenance"]["test_digest"].startswith("sha256:")
    assert body["results"][0]["model"] == "logistic_regression"


def test_run_model_suite_rejects_malformed_models_json(client):
    response = client.post(
        "/v1/run-model-suite",
        data={"models_json": "not-json"},
        files={"file": ("data.csv", _csv(), "text/csv")},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_run_model_suite_same_idempotency_key_replays(client):
    request = {
        "data": {
            "models_json": '["logistic_regression"]',
            "n_iter": "1",
            "idempotency_key": "suite-replay",
        },
        "files": {"file": ("data.csv", _csv(), "text/csv")},
    }
    first = client.post("/v1/run-model-suite", **request)
    second = client.post("/v1/run-model-suite", **request)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_persisted_suite_can_be_loaded(client):
    result = client.post(
        "/v1/run-model-suite",
        data={"models_json": '["logistic_regression"]', "n_iter": "1"},
        files={"file": ("data.csv", _csv(), "text/csv")},
    ).json()
    fetched = client.get(f"/v1/model-suites/{result['experiment_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == result
~~~

- [ ] **Step 2: Run the API tests and confirm the route is absent.**

~~~powershell
pytest -q tests/repro_runner/test_suite_api.py
~~~

Expected: route-not-found or import failure before implementation.

- [ ] **Step 3: Add suite storage functions.**

Add \`save_suite_result(result: ExperimentSuiteResult, settings: Settings) -> str\` and \`load_suite_result(experiment_id: str, settings: Settings) -> ExperimentSuiteResult\`. Reuse the existing safe experiment ID and atomic JSON functions, but validate stored payloads with \`ExperimentSuiteResult.model_validate\`. Do not make \`load_result\` accept suite payloads; keep the old endpoint's contract strict.

- [ ] **Step 4: Add form parsing and the suite route.**

Parse \`models_json\` with \`json.loads\`, require a JSON array of strings, then construct \`ModelSuiteConfig\` from form values. Use the existing bounded upload reader and admission limiter. The route must call:

~~~python
result = await run_in_threadpool(
    _run_model_suite_content, content, options, config, settings
)
~~~

\`_run_model_suite_content\` validates the dataset once, calls \`run_model_suite\`, persists the result, and maps \`DatasetError\`, \`ExperimentError\`, and unexpected exceptions to the existing sanitized HTTP errors. The idempotency fingerprint must include file bytes, dataset options, and the complete \`ModelSuiteConfig.model_dump(mode="json")\`.

- [ ] **Step 5: Run focused API and full runner tests.**

~~~powershell
pytest -q tests/repro_runner/test_suite_api.py tests/repro_runner/test_api.py
~~~

Expected: all existing single-model API tests and all new suite API tests pass.

- [ ] **Step 6: Commit the API and storage layer.**

~~~powershell
git add src/repro_runner/storage.py src/repro_runner/api.py tests/repro_runner/test_suite_api.py tests/repro_runner/test_api.py
git commit -m "feat: expose and persist multi-model suites"
~~~

## Task 6: Add suite comparison and correct provenance reason codes

**Files:**
- Modify: \`src/repro_runner/compare.py:1-160\`
- Modify: \`src/repro_runner/api.py:240-330\`
- Modify: \`src/repro_runner/schemas.py:130-210\`
- Test: \`tests/repro_runner/test_compare.py\`
- Create: \`tests/repro_runner/test_suite_compare.py\`

**Interfaces:**
- \`compare_suite_metrics(result: ExperimentSuiteResult, reported: Iterable[ReportedMetricInput | dict]) -> SuiteComparisonResponse\`.
- \`POST /v1/compare-model-suite-result\` accepts \`SuiteComparisonRequest\` and returns one comparison item per successful model and selected paper metric.

- [ ] **Step 1: Write failing comparison tests.**

~~~python
def test_suite_comparison_returns_one_item_per_successful_model(suite_result):
    response = compare_suite_metrics(
        suite_result,
        [{
            "name": "accuracy", "reported_value": 0.951,
            "dataset": "full sample", "split": "full sample",
        }],
    )
    assert {item.model for item in response.items} == {
        "logistic_regression", "random_forest"
    }
    assert all(item.absolute_difference is not None for item in response.items)
    assert all(item.comparable is False for item in response.items)


def test_missing_dataset_identity_is_not_reported_as_missing_metric(suite_result):
    item = compare_suite_metrics(
        suite_result,
        [{
            "name": "accuracy", "reported_value": 0.951,
            "dataset": "full sample", "split": "full sample",
        }],
    ).items[0]
    assert item.reason == "paper metric is missing dataset identity"
    assert item.absolute_difference is not None


def test_dataset_identity_mismatch_has_distinct_reason(suite_result):
    item = compare_suite_metrics(
        suite_result,
        [{
            "name": "accuracy", "reported_value": 0.951,
            "dataset": "test", "split": "test",
            "dataset_id": "sha256:" + "9" * 64,
        }],
    ).items[0]
    assert item.reason == (
        "paper metric dataset identity differs from the independent dataset"
    )
~~~

- [ ] **Step 2: Run focused comparison tests and confirm the suite function is absent.**

~~~powershell
pytest -q tests/repro_runner/test_suite_compare.py tests/repro_runner/test_compare.py
~~~

Expected: import failure for \`compare_suite_metrics\` or \`SuiteComparisonResponse\` before implementation.

- [ ] **Step 3: Implement suite comparison.**

For each successful \`ModelRunResult\`, normalize the requested paper metric name, read the corresponding value from \`ExperimentMetrics\`, calculate \`absolute_difference=abs(independent-paper)\`, and calculate the signed relative difference \`(independent-paper)/abs(paper)\` when the paper value is nonzero. Use the same provenance checks as \`compare_metrics\`, but include the suite's shared split provenance. Preserve arithmetic differences even when \`comparable=false\`.

Add the new API route using \`load_suite_result\`, then map not-found, corrupt-result, and comparison exceptions through existing safe error helpers.

- [ ] **Step 4: Run comparison and API regression tests.**

~~~powershell
pytest -q tests/repro_runner/test_suite_compare.py tests/repro_runner/test_compare.py tests/repro_runner/test_suite_api.py
~~~

Expected: all tests pass, and the old single-experiment comparison behavior is unchanged.

- [ ] **Step 5: Commit suite comparison.**

~~~powershell
git add src/repro_runner/schemas.py src/repro_runner/compare.py src/repro_runner/api.py tests/repro_runner/test_compare.py tests/repro_runner/test_suite_compare.py
git commit -m "feat: compare every suite model with paper metrics"
~~~

## Task 7: Update deterministic Dify helpers and generate a separate DSL

**Files:**
- Modify: \`dify/code/experiment_workflow.py\`
- Modify: \`dify/code/comparison_workflow.py\`
- Create: \`scripts/build_multimodel_dsl.py\`
- Create: \`dify/paper-comparison-multimodel-workflow.yml\`
- Create: \`tests/test_dify_multimodel_code.py\`
- Create: \`tests/test_dify_multimodel_dsl.py\`

**Interfaces:**
- \`normalize_suite_inputs(models_json, cv_folds, optimization_metric, n_iter, use_gpu, drop_duplicates) -> dict\` returns safe form strings.
- \`build_suite_comparison_request(dossier_json, suite_json) -> dict\` returns \`suite_comparison_request_ok\`, \`suite_comparison_request_json\`, and safe errors.
- \`format_suite_comparison_report(dossier_json, validation_json, suite_json, comparison_json, assessment_json) -> dict\` returns the existing six string output names.
- \`build_multimodel_dsl() -> dict\` and \`write_multimodel_dsl(path: Path) -> None\` generate the new workflow deterministically.

- [ ] **Step 1: Write failing helper tests.**

~~~python
import json

from dify.code.experiment_workflow import normalize_suite_inputs
from dify.code.comparison_workflow import build_suite_comparison_request


def test_normalize_suite_inputs_returns_safe_defaults():
    result = normalize_suite_inputs(
        '["logistic_regression", "random_forest"]',
        5, "roc_auc", 8, False, True,
    )
    assert result == {
        "models_json_text": '["logistic_regression", "random_forest"]',
        "cv_folds_text": "5",
        "optimization_metric_text": "roc_auc",
        "n_iter_text": "8",
        "use_gpu_text": "false",
        "drop_duplicates_text": "true",
    }


def test_suite_request_only_forwards_experiment_id_and_reported_metrics():
    dossier = json.dumps({"metrics": [{
        "normalized_name": "accuracy", "supported": True,
        "ambiguous": False, "reported_value": 0.951,
        "dataset": "full sample", "split": "full sample",
    }]})
    suite = json.dumps({
        "experiment_id": "exp-20260811T000000Z-abcdef12",
        "results": [],
    })
    result = build_suite_comparison_request(dossier, suite)
    assert result["suite_comparison_request_ok"] is True
    request = json.loads(result["suite_comparison_request_json"])
    assert request["experiment_id"].startswith("exp-")
    assert request["reported_metrics"][0]["reported_value"] == 0.951
~~~

- [ ] **Step 2: Run the helper tests and confirm the functions are absent.**

~~~powershell
pytest -q tests/test_dify_multimodel_code.py
~~~

Expected: import failure for the new helpers.

- [ ] **Step 3: Implement safe helper functions.**

Reuse the existing JSON-object safety rules. \`normalize_suite_inputs\` must not echo malformed arbitrary payloads; invalid JSON is handled by the Dify validation branch and produces a stable \`invalid_request\` string. \`build_suite_comparison_request\` selects only supported, unambiguous metrics with numeric reported values and never includes uploaded CSV or PDF text.

- [ ] **Step 4: Add the DSL generator tests.**

~~~python
from pathlib import Path
import yaml

DSL = Path(__file__).parents[1] / "dify" / "paper-comparison-multimodel-workflow.yml"


def document():
    return yaml.safe_load(DSL.read_text(encoding="utf-8"))


def test_multimodel_dsl_has_suite_urls_and_inputs():
    data = [
        node["data"] for node in document()["workflow"]["graph"]["nodes"]
    ]
    urls = {item["url"] for item in data if item["type"] == "http-request"}
    assert "http://repro-runner:8001/v1/run-model-suite" in urls
    assert "http://repro-runner:8001/v1/compare-model-suite-result" in urls
    text = DSL.read_text(encoding="utf-8")
    assert "models_json" in text
    assert "cv_folds" in text


def test_multimodel_dsl_keeps_one_output_and_six_strings():
    ends = [
        node for node in document()["workflow"]["graph"]["nodes"]
        if node["data"]["type"] == "end"
    ]
    assert len(ends) == 1
    assert {item["variable"] for item in ends[0]["data"]["outputs"]} == {
        "dossier_json", "validation_json", "experiment_json",
        "comparison_json", "assessment_json", "markdown_report",
    }
    assert all(
        item["value_type"] == "string"
        for item in ends[0]["data"]["outputs"]
    )
~~~

- [ ] **Step 5: Build the new DSL from the existing workflow graph.**

\`build_multimodel_dsl.py\` loads \`dify/paper-comparison-workflow.yml\`, deep-copies the graph, replaces only the experiment request/response/compare path, adds the six suite inputs with design defaults, and preserves parser, dossier, validation, retry, aggregation, and one-output behavior. It updates URLs to:

~~~text
http://repro-runner:8001/v1/run-model-suite
http://repro-runner:8001/v1/compare-model-suite-result
~~~

The generator remaps node and edge IDs deterministically, writes UTF-8 YAML, and never contains a token or an \`sk-\` value. A second run must produce byte-identical YAML.

- [ ] **Step 6: Run Dify code and DSL tests.**

~~~powershell
python scripts/build_multimodel_dsl.py
pytest -q tests/test_dify_multimodel_code.py tests/test_dify_multimodel_dsl.py tests/test_dify_comparison_dsl.py
~~~

Expected: the generated DSL exists, parses as YAML, has one Output node, and all existing comparison DSL tests remain green.

- [ ] **Step 7: Commit the Dify helper and generated workflow.**

~~~powershell
git add dify/code/experiment_workflow.py dify/code/comparison_workflow.py scripts/build_multimodel_dsl.py dify/paper-comparison-multimodel-workflow.yml tests/test_dify_multimodel_code.py tests/test_dify_multimodel_dsl.py
git commit -m "feat: add Dify multi-model comparison workflow"
~~~

## Task 8: Add operator documentation and smoke validation

**Files:**
- Create: \`dify/paper-comparison-multimodel-workflow.md\`
- Create: \`scripts/smoke_multimodel.ps1\`
- Modify: \`README.md\` if it exists; otherwise create it only if the repository has no top-level runner guide.

**Interfaces:**
- The guide documents exact inputs, defaults, output ranking, dependency behavior, and rollback to the current workflow.
- The smoke script accepts a local CSV path and runner base URL, checks health, and contains no secrets.

- [ ] **Step 1: Add a parameterized smoke script.**

~~~powershell
param(
  [Parameter(Mandatory=$true)][string]$CsvPath,
  [Parameter(Mandatory=$false)][string]$BaseUrl = "http://localhost:8001"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $CsvPath)) { throw "CSV not found: $CsvPath" }
$health = Invoke-RestMethod "$BaseUrl/healthz"
if ($health.status -ne "ok") { throw "runner health check failed" }
Write-Host "Runner healthy; upload the PDF and CSV through the multimodel Dify UI."
~~~

- [ ] **Step 2: Write the operator guide.**

Document the seven default model names, \`models_json\`, \`cv_folds=5\`, \`optimization_metric=roc_auc\`, \`n_iter=8\`, \`drop_duplicates\`, and the fact that paper differences are reference comparisons unless provenance matches. Document that the existing V3 workflow remains the rollback target and the new DSL is imported separately until live verification passes.

- [ ] **Step 3: Run documentation safety checks.**

~~~powershell
rg -n "api[_-]?key|sk-[A-Za-z0-9]|PARSER_API_TOKEN|models_json|cv_folds|run-model-suite" dify/paper-comparison-multimodel-workflow.md scripts/smoke_multimodel.ps1 README.md
~~~

Expected: only environment-variable names and documented input names appear; no secret value appears.

- [ ] **Step 4: Commit documentation and smoke tooling.**

~~~powershell
git add dify/paper-comparison-multimodel-workflow.md scripts/smoke_multimodel.ps1 README.md
git commit -m "docs: document multi-model CV workflow"
~~~

## Task 9: Run the repository verification gate and live Dify test

**Files:**
- Test: all \`tests/**/*.py\`
- Inspect: \`requirements-repro.lock\`, \`dify/paper-comparison-multimodel-workflow.yml\`, \`compose.yaml\`, \`Dockerfile.repro\`
- Record: \`docs/superpowers/sdd/task-multimodel-live-verification.md\`

- [ ] **Step 1: Run focused runner and Dify tests.**

~~~powershell
pytest -q tests/repro_runner/test_suite_schemas.py tests/repro_runner/test_split.py tests/repro_runner/test_metrics.py tests/repro_runner/test_model_registry.py tests/repro_runner/test_suite_engine.py tests/repro_runner/test_suite_compare.py tests/repro_runner/test_suite_api.py tests/test_dify_multimodel_code.py tests/test_dify_multimodel_dsl.py tests/test_dify_comparison_dsl.py
~~~

Expected: all focused tests pass.

- [ ] **Step 2: Run the complete test suite.**

~~~powershell
pytest -q
~~~

Expected: all pre-existing tests plus the new tests pass; only the repository's existing warnings are allowed.

- [ ] **Step 3: Check generated files and dependency declarations.**

~~~powershell
python scripts/build_multimodel_dsl.py
git diff --check
rg -n "sk-[A-Za-z0-9]|api[_-]?key\s*[:=]" dify/paper-comparison-multimodel-workflow.yml
~~~

Expected: deterministic generated DSL, no whitespace errors, and no secret-like values.

- [ ] **Step 4: Build the repro runner image and check health.**

~~~powershell
docker compose build repro-runner
docker compose up -d repro-runner
Invoke-RestMethod http://localhost:8001/healthz
~~~

Expected: image build succeeds, the container starts as the non-root \`app\` user, and health returns \`{"status":"ok"}\`.

- [ ] **Step 5: Run a two-model live API smoke test before the full seven-model test.**

Use the actual CSV path passed to the smoke script and call \`/v1/run-model-suite\` with:

~~~json
{
  "models_json": "[\"logistic_regression\",\"random_forest\"]",
  "cv_folds": "5",
  "n_iter": "1",
  "optimization_metric": "roc_auc",
  "random_state": "42",
  "test_size": "0.2"
}
~~~

Expected: both models succeed, share one \`test_digest\`, and return AUC/Accuracy/F1/Recall.

- [ ] **Step 6: Run the full seven-model real CSV experiment.**

Submit the original paper PDF/dossier, the same training CSV, and \`metric_overrides_json\` selecting the paper random-forest accuracy at threshold 0.5. Record suite ID, model states, metrics, CV scores, fit seconds, shared digest, and paper difference rankings in \`docs/superpowers/sdd/task-multimodel-live-verification.md\`.

Expected: every model returns a result or a clear \`unavailable\`/\`failed\` entry; a model failure does not erase other model results; the report labels current paper comparison as non-strict when sample counts or split protocol differ.

- [ ] **Step 7: Run the failure matrix and verify rollback.**

Run these cases separately and restore the app/version after each: malformed \`models_json\`, invalid CV folds, missing XGBoost dependency in a test container, invalid CSV, suite HTTP timeout, suite comparison timeout, and the existing single-model V3 workflow. Every failure must end in the one Output node with six strings, and the current V3 URL must remain usable.

- [ ] **Step 8: Commit live verification notes.**

~~~powershell
git add docs/superpowers/sdd/task-multimodel-live-verification.md
git commit -m "docs: record multi-model workflow verification"
~~~

## Self-Review Checklist

- Spec coverage: Tasks 1-2 cover shared configuration, split, metric calculation, and old RF compatibility; Task 3 covers all seven models and dependencies; Task 4 covers CV and ranking; Tasks 5-6 cover persistence, API, comparison, and reason codes; Tasks 7-8 cover Dify integration, documentation, and smoke tooling; Task 9 covers full verification and live rollback.
- Placeholder scan: every implementation step has concrete files, commands, expected output, and no unresolved placeholder text.
- Type consistency: \`ModelSuiteConfig\` is consumed by \`run_model_suite\`; \`ExperimentSuiteResult\` is persisted and loaded by suite API routes; \`SuiteComparisonRequest\`/\`SuiteComparisonResponse\` are used by the suite comparison route and Dify request/response helpers.
- Compatibility: the old \`ExperimentConfig\`, \`ExperimentResult\`, \`/v1/run-experiment\`, \`/v1/experiments/{id}\`, and current Dify DSL remain in place.
- Resource safety: suite execution is sequential, bounded by \`n_iter\`, \`cv_folds\`, \`n_jobs\`, and the existing one-job admission limiter.
