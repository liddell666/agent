# Tabular Regression Paper-Reproduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit, backward-compatible tabular regression path with four models, MAE/RMSE/R², persistent jobs, paper comparison, and an isolated Dify candidate app.

**Architecture:** The existing suite lifecycle becomes task-aware while binary classification remains the default. Task-specific validation, split, estimators, metrics, CV, and ranking feed the existing manifest, job, storage, comparison, and release boundaries; regression Dify artifacts are generated separately and imported into a new candidate app.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, pandas, NumPy, scikit-learn, optional XGBoost, PyYAML, pytest 9, Dify 1.16 local services.

## Global Constraints

- Regression is selected only by explicit `task_type="regression"`; never infer it from target cardinality.
- Missing `task_type` means `binary_classification` in requests and old stored artifacts.
- Regression supports only `linear_regression`, `random_forest`, `gradient_boosting`, and `xgboost`.
- Regression metrics are exactly MAE, RMSE, and R²; public MAE/RMSE values are finite and non-negative.
- Regression uses deterministic non-stratified holdout and shuffled K-fold; classification keeps its current stratified behavior.
- Every successful model in one suite shares the same holdout provenance and test digest.
- Raw CSV rows never enter stored artifacts, logs, reports, or LLM nodes.
- Existing directory/file atomicity, Windows retry, idempotency, restart recovery, cancellation, and privacy contracts remain unchanged.
- The current production Dify app and the six user-owned modified DSL files must not be edited, staged, imported over, or promoted.
- Final code verification runs the complete repository suite twice with default pytest configuration and no new warnings.
- Live verification creates and publishes only a new regression candidate app; production promotion is out of scope.

---

## File Responsibility Map

- `src/repro_runner/schemas.py`: task identity, regression models, metrics, defaults, and cross-field validation.
- `src/repro_runner/protocol.py`: task-bound manifest identity and task-specific CV feasibility.
- `src/repro_runner/data.py`: explicit regression target validation and aggregate target summary.
- `src/repro_runner/split.py`: deterministic task-aware holdout selection.
- `src/repro_runner/model_registry.py`: task-aware estimator construction and search spaces.
- `src/repro_runner/metrics.py`: regression metric evaluation and score normalization.
- `src/repro_runner/suite_engine.py`: task-aware CV, fitting, result construction, and performance ranking.
- `src/repro_runner/api.py`, `job_runner.py`: public task input and persistent execution propagation.
- `src/repro_runner/storage.py`: additive task and regression metric serialization with legacy reads.
- `src/repro_runner/compare.py`, `dossier.py`: regression metric aliases and comparison extraction.
- `scripts/build_regression_dsl.py`: deterministic regression-only DSL generation without modifying existing DSLs.
- `dify/paper-comparison-regression-workflow*.yml`: new candidate import artifacts.
- `tests/fixtures/regression_*`: controlled privacy-safe CSV and paper fixtures.

### Task 1: Add Task-Aware Contracts and Manifest Identity

**Files:**
- Modify: `src/repro_runner/schemas.py`
- Modify: `src/repro_runner/protocol.py`
- Test: `tests/repro_runner/test_schemas.py`
- Test: `tests/repro_runner/test_protocol.py`
- Test: `tests/repro_runner/test_compare.py`

**Interfaces:**
- Produces: `TaskType`, `RegressionMetrics`, `REGRESSION_MODEL_NAMES`, and task-aware `ModelSuiteConfig`/`ExperimentManifest`.
- Preserves: absent task type parses as binary classification.
- Produces: manifest digests that bind task type.

- [ ] **Step 1: Write failing schema defaults and compatibility tests**

Add literal assertions that do not reuse production constants:

```python
def test_suite_config_keeps_binary_defaults_when_task_type_is_absent():
    config = ModelSuiteConfig()
    assert config.task_type == "binary_classification"
    assert config.models == [
        "logistic_regression", "random_forest", "xgboost", "lightgbm",
        "svm", "knn", "mlp",
    ]
    assert config.optimization_metric == "roc_auc"
    assert config.threshold == 0.5


def test_suite_config_selects_explicit_regression_defaults():
    config = ModelSuiteConfig(task_type="regression")
    assert config.models == [
        "linear_regression", "random_forest", "gradient_boosting", "xgboost"
    ]
    assert config.optimization_metric == "rmse"
    assert config.threshold is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"task_type": "regression", "models": ["logistic_regression"]}, "model"),
        ({"task_type": "regression", "optimization_metric": "roc_auc"}, "metric"),
        ({"task_type": "regression", "sampling_strategy": "class_weight"}, "sampling"),
        ({"task_type": "binary_classification", "models": ["linear_regression"]}, "model"),
        ({"task_type": "binary_classification", "optimization_metric": "rmse"}, "metric"),
    ],
)
def test_suite_config_rejects_cross_task_options(payload, message):
    with pytest.raises(ValueError, match=message):
        ModelSuiteConfig(**payload)


def test_regression_metrics_reject_invalid_public_values():
    with pytest.raises(ValueError):
        RegressionMetrics(mae=-0.1, rmse=0.2, r2=0.3)
    with pytest.raises(ValueError):
        RegressionMetrics(mae=0.1, rmse=float("nan"), r2=0.3)
    assert RegressionMetrics(mae=1.0, rmse=2.0, r2=-4.0).r2 == -4.0
```

- [ ] **Step 2: Run schema tests and verify RED**

Run:

```powershell
python -m pytest tests/repro_runner/test_schemas.py -q -k "regression or binary_defaults or cross_task"
```

Expected: collection or construction fails because task identity and regression
metrics do not exist.

- [ ] **Step 3: Implement additive schema types and task defaults**

Add the task and model contracts:

```python
TaskType = Literal["binary_classification", "regression"]
RegressionModelName = Literal[
    "linear_regression", "random_forest", "gradient_boosting", "xgboost"
]

REGRESSION_MODEL_NAMES = (
    "linear_regression", "random_forest", "gradient_boosting", "xgboost"
)
REGRESSION_METRIC_NAMES = ("mae", "rmse", "r2")


class RegressionMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mae: float = Field(ge=0.0)
    rmse: float = Field(ge=0.0)
    r2: float

    @model_validator(mode="after")
    def require_finite_values(self) -> "RegressionMetrics":
        if not all(math.isfinite(value) for value in (self.mae, self.rmse, self.r2)):
            raise ValueError("regression metrics must be finite")
        return self
```

Expand `ModelName` with `linear_regression` and `gradient_boosting`. Add
`task_type: TaskType = "binary_classification"` to `DatasetOptions`,
`ModelSuiteConfig`, `ExperimentManifest`, and `ExperimentSuiteResult`. Change
suite/manifest optimization metrics to the union of existing values and
`mae|rmse|r2`; make threshold `float | None`.

Use a `mode="before"` validator on `ModelSuiteConfig` so omitted regression
fields receive regression defaults without changing explicit inputs:

```python
@model_validator(mode="before")
@classmethod
def apply_task_defaults(cls, value):
    data = dict(value or {})
    if data.get("task_type") == "regression":
        data.setdefault("models", list(REGRESSION_MODEL_NAMES))
        data.setdefault("optimization_metric", "rmse")
        data.setdefault("threshold", None)
        data.setdefault("sampling_strategy", "original")
    return data
```

Use an after-validator with literal allowed sets to reject cross-task model,
metric, threshold, and sampling combinations. Keep classification defaults and
validation unchanged.

- [ ] **Step 4: Add manifest identity and old-artifact regressions**

```python
def test_manifest_identity_binds_task_type(valid_diagnostic, confirmed_options):
    binary = create_manifest(valid_diagnostic, confirmed_options, ModelSuiteConfig())
    regression_options = confirmed_options.model_copy(update={"task_type": "regression"})
    regression = create_manifest(
        valid_diagnostic,
        regression_options,
        ModelSuiteConfig(task_type="regression"),
    )
    assert binary.task_type == "binary_classification"
    assert regression.task_type == "regression"
    assert binary.manifest_id != regression.manifest_id


def test_legacy_suite_payload_defaults_to_binary_classification():
    payload = make_suite_result().model_dump(mode="json")
    payload["config"].pop("task_type", None)
    payload.pop("task_type", None)
    loaded = ExperimentSuiteResult.model_validate(payload)
    assert loaded.config.task_type == "binary_classification"
    assert loaded.task_type == "binary_classification"
```

- [ ] **Step 5: Bind task identity in `create_manifest`**

Add `"task_type": suite_config.task_type` to the canonical payload and result.
Reject mismatched `options.task_type` and `suite_config.task_type` before
hashing. For regression, validate total effective rows rather than minority
class size; classification keeps the current class-count check.

- [ ] **Step 6: Run focused compatibility and commit**

```powershell
python -m pytest tests/repro_runner/test_schemas.py tests/repro_runner/test_protocol.py tests/repro_runner/test_compare.py -q
git diff --check
git add -- src/repro_runner/schemas.py src/repro_runner/protocol.py tests/repro_runner/test_schemas.py tests/repro_runner/test_protocol.py tests/repro_runner/test_compare.py
git commit -m "feat: add task-aware regression contracts"
```

Expected: all selected tests pass and legacy classification tests remain green.

### Task 2: Validate Regression Data and Produce Shared Splits

**Files:**
- Modify: `src/repro_runner/data.py`
- Modify: `src/repro_runner/split.py`
- Modify: `src/repro_runner/schemas.py`
- Test: `tests/repro_runner/test_data.py`
- Test: `tests/repro_runner/test_split.py`
- Create: `tests/fixtures/regression_mixed.csv`
- Create: `tests/fixtures/regression_invalid_target.csv`

**Interfaces:**
- Consumes: `DatasetOptions.task_type`.
- Produces: `DatasetBundle.task_type`, optional aggregate `RegressionTargetSummary`, and `make_task_split(target, task_type, test_size, random_state)`.

- [ ] **Step 1: Add deterministic regression fixtures and failing diagnosis tests**

Create a 40-row mixed fixture with columns `x1`, `x2`, `region`, and continuous
`target`; values are deterministic and contain no identifiers or sensitive
data. Create an invalid fixture with one non-numeric target token.

Add:

```python
def test_regression_diagnosis_returns_only_aggregate_target_summary(settings):
    options = DatasetOptions(
        task_type="regression", target_column="target", target_column_confirmed=True
    )
    result = diagnose_dataset(_fixture("regression_mixed.csv"), options, settings)
    assert result.valid is True
    assert result.dataset.class_counts == {}
    assert result.dataset.target_summary.count == 40
    assert result.dataset.target_summary.minimum == pytest.approx(3.5)
    serialized = result.model_dump_json()
    assert "region-a" not in serialized


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (_fixture("regression_invalid_target.csv"), "non_numeric_regression_target"),
        (b"x,target\n1,5\n2,5\n" * 10, "constant_regression_target"),
    ],
)
def test_regression_diagnosis_rejects_invalid_targets(content, code, settings):
    options = DatasetOptions(
        task_type="regression", target_column="target", target_column_confirmed=True
    )
    result = diagnose_dataset(content, options, settings)
    assert result.valid is False
    assert code in [item.code for item in result.errors]
```

- [ ] **Step 2: Verify data RED**

```powershell
python -m pytest tests/repro_runner/test_data.py -q -k regression
```

Expected: FAIL because regression target validation and summary do not exist.

- [ ] **Step 3: Implement task-aware target validation**

Add:

```python
class RegressionTargetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=0)
    minimum: float
    maximum: float
    mean: float
    standard_deviation: float = Field(ge=0.0)
```

Add `target_summary: RegressionTargetSummary | None = None` to dataset profile
types and `task_type` to `DatasetBundle`. Implement:

```python
def _prepare_regression_target(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().any():
        raise DatasetError(
            "non_numeric_regression_target", "regression target must be numeric"
        )
    values = numeric.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise DatasetError(
            "non_finite_regression_target", "regression target must be finite"
        )
    if len(values) < 20:
        raise DatasetError(
            "insufficient_regression_rows", "regression requires at least 20 rows"
        )
    if np.unique(values).size < 2:
        raise DatasetError(
            "constant_regression_target", "regression target must vary"
        )
    return pd.Series(values, index=series.index, name=series.name)
```

Route classification to `_validate_target_classes` and regression to this
helper. Store the converted numeric target back into the prepared frame. Build
the summary only from aggregate NumPy statistics with population standard
deviation (`ddof=0`). Emit empty class maps for regression.

- [ ] **Step 4: Add failing split tests**

```python
def test_regression_split_is_deterministic_and_not_stratified():
    target = np.linspace(1.0, 40.0, 40)
    first = make_task_split(target, "regression", test_size=0.2, random_state=7)
    second = make_task_split(target, "regression", test_size=0.2, random_state=7)
    assert np.array_equal(first[0], second[0])
    assert np.array_equal(first[1], second[1])
    assert len(first[1]) == 8


def test_binary_task_split_keeps_both_classes():
    target = np.array([0] * 20 + [1] * 20)
    train, test = make_task_split(
        target, "binary_classification", test_size=0.2, random_state=7
    )
    assert set(target[train]) == {0, 1}
    assert set(target[test]) == {0, 1}
```

- [ ] **Step 5: Implement the split dispatcher**

```python
def make_task_split(target, task_type, test_size, random_state):
    if task_type == "binary_classification":
        return make_stratified_split(target, test_size, random_state)
    try:
        return train_test_split(
            np.arange(len(target)), test_size=test_size, random_state=random_state
        )
    except ValueError as exc:
        raise ExperimentError(
            "invalid_split", "the requested regression split is not feasible"
        ) from exc
```

- [ ] **Step 6: Run data/split/classification compatibility and commit**

```powershell
python -m pytest tests/repro_runner/test_data.py tests/repro_runner/test_split.py tests/test_end_to_end_general_binary.py -q
git diff --check
git add -- src/repro_runner/data.py src/repro_runner/split.py src/repro_runner/schemas.py tests/repro_runner/test_data.py tests/repro_runner/test_split.py tests/fixtures/regression_mixed.csv tests/fixtures/regression_invalid_target.csv
git commit -m "feat: validate tabular regression datasets"
```

### Task 3: Train and Evaluate the Four Regression Models

**Files:**
- Modify: `src/repro_runner/model_registry.py`
- Modify: `src/repro_runner/metrics.py`
- Modify: `src/repro_runner/suite_engine.py`
- Test: `tests/repro_runner/test_model_registry.py`
- Test: `tests/repro_runner/test_metrics.py`
- Test: `tests/repro_runner/test_suite_engine.py`

**Interfaces:**
- Produces: task-aware `get_model_spec(name, class_counts, *, task_type, random_state, use_gpu, preprocessor, sampling_strategy)`, `evaluate_regressor(model, X_test, y_test)`, CV, and ranking.
- Consumes: task-aware bundle/config and split from Tasks 1–2.

- [ ] **Step 1: Add failing estimator registry tests**

```python
@pytest.mark.parametrize(
    ("name", "expected_class"),
    [
        ("linear_regression", "LinearRegression"),
        ("random_forest", "RandomForestRegressor"),
        ("gradient_boosting", "GradientBoostingRegressor"),
    ],
)
def test_regression_registry_builds_task_specific_estimators(name, expected_class, preprocessor):
    spec = get_model_spec(
        name,
        {},
        task_type="regression",
        random_state=11,
        use_gpu=False,
        preprocessor=preprocessor,
        sampling_strategy="original",
    )
    assert spec.estimator.named_steps["model"].__class__.__name__ == expected_class


def test_same_model_name_resolves_by_task(preprocessor):
    classifier = get_model_spec(
        "random_forest", {"0": 20, "1": 20},
        task_type="binary_classification", random_state=1, use_gpu=False,
        preprocessor=preprocessor, sampling_strategy="original",
    )
    regressor = get_model_spec(
        "random_forest", {}, task_type="regression", random_state=1,
        use_gpu=False, preprocessor=preprocessor, sampling_strategy="original",
    )
    assert classifier.estimator.named_steps["model"].__class__.__name__ == "RandomForestClassifier"
    assert regressor.estimator.named_steps["model"].__class__.__name__ == "RandomForestRegressor"
```

- [ ] **Step 2: Verify registry RED and implement regression specs**

Run the two tests and expect missing `task_type` support. Then add bounded
specs:

```python
REGRESSION_SEARCH_SPACES = {
    "linear_regression": {},
    "random_forest": {
        "model__n_estimators": [100, 200],
        "model__max_depth": [None, 6, 12],
        "model__min_samples_leaf": [1, 2, 4],
    },
    "gradient_boosting": {
        "model__n_estimators": [100, 200],
        "model__learning_rate": [0.03, 0.1],
        "model__max_depth": [2, 3],
    },
    "xgboost": {
        "model__n_estimators": [100, 200],
        "model__learning_rate": [0.03, 0.1],
        "model__max_depth": [3, 6],
        "model__subsample": [0.8, 1.0],
    },
}
```

Use `LinearRegression`, `RandomForestRegressor`,
`GradientBoostingRegressor`, and `XGBRegressor(objective="reg:squarederror")`.
Keep optional-import errors safe and preserve classification construction.

- [ ] **Step 3: Add failing regression metric tests**

```python
def test_evaluate_regressor_returns_natural_public_metrics():
    estimator = LinearRegression().fit([[0], [1], [2], [3]], [0, 2, 4, 6])
    metrics = evaluate_regressor(estimator, [[4], [5]], np.array([8.0, 11.0]))
    assert metrics.mae == pytest.approx(0.5)
    assert metrics.rmse == pytest.approx(np.sqrt(0.5))
    assert metrics.r2 == pytest.approx(0.5)
    assert metrics.mae >= 0
    assert metrics.rmse >= 0


def test_regression_metric_summary_never_exposes_negative_error_scores():
    rows = [
        RegressionMetrics(mae=1.0, rmse=2.0, r2=0.5),
        RegressionMetrics(mae=3.0, rmse=4.0, r2=-0.5),
    ]
    mean, std = mean_std_over_metrics(rows)
    assert mean == {"mae": 2.0, "rmse": 3.0, "r2": 0.0}
    assert all(value >= 0 for key, value in mean.items() if key != "r2")
```

- [ ] **Step 4: Implement regression metric evaluation**

```python
def evaluate_regressor(regressor, x_test, y_test) -> RegressionMetrics:
    predicted = np.asarray(regressor.predict(x_test), dtype=float)
    if not np.isfinite(predicted).all():
        raise ValueError("regression predictions must be finite")
    return RegressionMetrics(
        mae=_round_metric(mean_absolute_error(y_test, predicted)),
        rmse=_round_metric(math.sqrt(mean_squared_error(y_test, predicted))),
        r2=_round_metric(r2_score(y_test, predicted)),
    )
```

Make `mean_std_over_metrics` derive metric names from the concrete metrics type,
not the classification-only constant.

- [ ] **Step 5: Add a real regression suite RED test**

```python
def test_regression_suite_trains_core_models_with_shared_provenance(regression_bundle):
    config = ModelSuiteConfig(
        task_type="regression",
        models=["linear_regression", "random_forest", "gradient_boosting"],
        optimization_metric="rmse",
        cv_folds=3,
        n_iter=1,
        n_jobs=1,
        random_state=13,
    )
    result = run_model_suite(regression_bundle, config)
    assert result.status == "succeeded"
    assert result.performance_ranking
    assert all(item.metrics.rmse >= 0 for item in result.results)
    assert len({item.split_provenance.test_digest for item in result.results}) == 1
    assert all(set(item.cv_fold_scores) == {"mae", "rmse", "r2"} for item in result.results)
```

- [ ] **Step 6: Refactor suite execution by task**

Use `make_task_split`. For regression use `KFold(shuffle=True,
random_state=seed)`, reject non-original sampling, and validate that every outer
training fold can support the inner `cv_folds`. Map optimization scorers:

```python
SCORERS = {
    "mae": "neg_mean_absolute_error",
    "rmse": "neg_root_mean_squared_error",
    "r2": "r2",
}
```

For empty search spaces, fit the estimator directly instead of constructing a
zero-distribution `RandomizedSearchCV`; record `{}` as best params. Convert
negative internal MAE/RMSE best scores to positive public values. Dispatch to
`evaluate_classifier` or `evaluate_regressor`. Implement ranking:

```python
def _performance_ranking(results, optimization_metric):
    reverse = optimization_metric == "r2" or optimization_metric in {
        "roc_auc", "f1", "recall", "balanced_accuracy"
    }
    return [
        item.model for item in sorted(
            results,
            key=lambda item: float(getattr(item.metrics, optimization_metric)),
            reverse=reverse,
        )
    ]
```

- [ ] **Step 7: Run real model coverage and commit**

```powershell
python -m pytest tests/repro_runner/test_model_registry.py tests/repro_runner/test_metrics.py tests/repro_runner/test_suite_engine.py -q
python -m pytest tests/test_end_to_end_general_binary.py -q
git diff --check
git add -- src/repro_runner/model_registry.py src/repro_runner/metrics.py src/repro_runner/suite_engine.py tests/repro_runner/test_model_registry.py tests/repro_runner/test_metrics.py tests/repro_runner/test_suite_engine.py
git commit -m "feat: run task-aware regression model suites"
```

### Task 4: Carry Regression Through API, Jobs, Storage, and Comparison

**Files:**
- Modify: `src/repro_runner/api.py`
- Modify: `src/repro_runner/job_runner.py`
- Modify: `src/repro_runner/storage.py`
- Modify: `src/repro_runner/compare.py`
- Modify: `src/repro_runner/dossier.py`
- Test: `tests/repro_runner/test_suite_api.py`
- Test: `tests/repro_runner/test_job_api.py`
- Test: `tests/repro_runner/test_job_runner.py`
- Test: `tests/repro_runner/test_compare.py`
- Test: `tests/repro_runner/test_dossier.py`
- Create: `tests/test_end_to_end_regression.py`

**Interfaces:**
- Consumes: task-aware contracts/engine.
- Produces: one synchronous and persistent asynchronous regression lifecycle on existing routes.

- [ ] **Step 1: Add failing API propagation and idempotency tests**

```python
def test_run_model_suite_accepts_explicit_regression(client, regression_csv):
    response = client.post(
        "/v1/run-model-suite",
        data={
            "task_type": "regression",
            "target_column": "target",
            "models_json": '["linear_regression","random_forest"]',
            "optimization_metric": "rmse",
            "cv_folds": "3",
            "n_iter": "1",
            "n_jobs": "1",
        },
        files={"file": ("regression.csv", regression_csv, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["task_type"] == "regression"
    assert body["config"]["task_type"] == "regression"
    assert set(body["results"][0]["metrics"]) == {"mae", "rmse", "r2"}


def test_idempotency_does_not_replay_across_task_types(client, regression_csv):
    regression = _post_suite(client, regression_csv, task_type="regression", key="same")
    binary = _post_suite(client, regression_csv, task_type="binary_classification", key="same")
    assert regression.status_code == 200
    assert binary.status_code == 409
```

- [ ] **Step 2: Propagate task type through routes and job execution**

Add an annotated form field with default `binary_classification` to diagnosis
and suite routes. Pass it into `DatasetOptions` and `_parse_model_suite_config`.
The parser accepts the expanded metric literals and uses task-specific defaults
from `ModelSuiteConfig` rather than duplicating them.

`_default_execute_job` reconstructs `DatasetOptions` and `ModelSuiteConfig` from
the manifest including `task_type`; it never infers from the target.

- [ ] **Step 3: Add failing storage round-trip tests**

```python
def test_regression_suite_storage_round_trip_is_privacy_safe(tmp_path, regression_result):
    settings = Settings(storage_dir=tmp_path)
    save_suite_result(regression_result, settings)
    loaded = load_suite_result(regression_result.experiment_id, settings)
    assert loaded == regression_result
    payload = json.loads((tmp_path / regression_result.experiment_id / "result.json").read_text())
    assert payload["task_type"] == "regression"
    assert set(payload["results"][0]["metrics"]) == {"mae", "rmse", "r2"}
    assert "raw_rows" not in json.dumps(payload)


def test_legacy_classification_result_without_task_type_still_loads(tmp_path):
    result = make_suite_result()
    payload = storage._suite_result_payload(result)
    payload.pop("task_type", None)
    payload["config"].pop("task_type", None)
    _write_legacy_result(tmp_path, result.experiment_id, payload)
    assert load_suite_result(result.experiment_id, Settings(storage_dir=tmp_path)).task_type == "binary_classification"
```

- [ ] **Step 4: Serialize task and concrete metrics explicitly**

Add task type to suite/config payloads. Replace classification-only metric
serialization with:

```python
def _metrics_payload(metrics):
    if isinstance(metrics, RegressionMetrics):
        return {"mae": metrics.mae, "rmse": metrics.rmse, "r2": metrics.r2}
    return {
        "roc_auc": metrics.roc_auc,
        "accuracy": metrics.accuracy,
        "balanced_accuracy": metrics.balanced_accuracy,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1": metrics.f1,
        "confusion_matrix": metrics.confusion_matrix,
    }
```

Do not change atomic publication helpers.

- [ ] **Step 5: Add dossier alias and comparison tests**

```python
@pytest.mark.parametrize(
    ("label", "normalized"),
    [
        ("MAE", "mae"),
        ("mean absolute error", "mae"),
        ("平均绝对误差", "mae"),
        ("RMSE", "rmse"),
        ("均方根误差", "rmse"),
        ("R²", "r2"),
        ("coefficient of determination", "r2"),
        ("决定系数", "r2"),
    ],
)
def test_regression_metric_aliases(label, normalized):
    assert normalize_metric_name(label) == normalized


def test_regression_comparison_uses_natural_metric_values(regression_result):
    response = compare_suite_metrics(
        regression_result,
        [ReportedMetricInput(name="RMSE", reported_value=2.0, model="random_forest")],
    )
    item = next(item for item in response.items if item.model == "random_forest")
    assert item.independent_value >= 0
    assert item.absolute_difference == pytest.approx(abs(item.independent_value - 2.0))
```

Extend comparison metric extraction for `RegressionMetrics`; keep model
qualifiers and finite-number checks. Dossier metrics whose task is unsupported
remain evidence but do not compare.

- [ ] **Step 6: Add persistent end-to-end regression test**

Model it after `tests/test_end_to_end_general_binary.py`: diagnose the fixture,
create a regression manifest, admit a job, poll to terminal, fetch the stored
result, compare RMSE, replay the same manifest idempotently, and assert no CSV
row appears in JSON or storage. Use `linear_regression` and `random_forest`,
`cv_folds=3`, `n_iter=1`, `n_jobs=1` for bounded runtime.

- [ ] **Step 7: Run API/job/storage/comparison coverage and commit**

```powershell
python -m pytest tests/repro_runner/test_suite_api.py tests/repro_runner/test_job_api.py tests/repro_runner/test_job_runner.py tests/repro_runner/test_compare.py tests/repro_runner/test_dossier.py tests/test_end_to_end_regression.py tests/test_end_to_end_general_binary.py -q
git diff --check
git add -- src/repro_runner/api.py src/repro_runner/job_runner.py src/repro_runner/storage.py src/repro_runner/compare.py src/repro_runner/dossier.py tests/repro_runner/test_suite_api.py tests/repro_runner/test_job_api.py tests/repro_runner/test_job_runner.py tests/repro_runner/test_compare.py tests/repro_runner/test_dossier.py tests/test_end_to_end_regression.py
git commit -m "feat: persist and compare regression suites"
```

### Task 5: Generate Isolated Regression Dify Artifacts

**Files:**
- Create: `scripts/build_regression_dsl.py`
- Create: `tests/test_dify_regression_dsl.py`
- Create: `tests/test_dify_regression_code.py`
- Create: `dify/paper-comparison-regression-workflow.yml`
- Create: `dify/paper-comparison-regression-workflow-ollama.yml`
- Create: `tests/fixtures/regression-paper.pdf`
- Modify: `dify/paper-dossier-system-prompt.md`

**Interfaces:**
- Consumes: existing multimodel builder helpers and task-aware runner contract.
- Produces: deterministic new DSL files only; existing six modified DSL paths remain untouched.

- [ ] **Step 1: Add failing deterministic builder contract tests**

```python
def test_regression_builder_is_deterministic_and_does_not_mutate_source(tmp_path):
    source_before = SOURCE_DSL.read_bytes()
    first = build_regression_dsl("deepseek")
    second = build_regression_dsl("deepseek")
    assert first == second
    assert SOURCE_DSL.read_bytes() == source_before


def test_regression_workflow_has_explicit_task_and_models():
    document = build_regression_dsl("deepseek")
    serialized = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    assert 'task_type: regression' in serialized
    assert "linear_regression" in serialized
    assert "gradient_boosting" in serialized
    assert "logistic_regression" not in serialized
    assert "optimization_metric" in serialized
    assert "rmse" in serialized
```

- [ ] **Step 2: Implement a regression-only transformation**

Import reusable pure helpers from `build_multimodel_dsl`, deep-copy its clean
committed source graph, and change only candidate metadata, visible labels,
task input/defaults, known model/metric allowlists, manifest preparation,
request payload, comparison parsing, and report formatting. Define:

```python
REGRESSION_MODELS = (
    "linear_regression", "random_forest", "gradient_boosting", "xgboost"
)
REGRESSION_METRICS = ("mae", "rmse", "r2")
WORKFLOW_VERSION = "regression-1.0.0"


def build_regression_dsl(profile="deepseek") -> dict:
    document = deepcopy(build_merged_dsl(profile))
    document["app"]["name"] = "Paper comparison Regression Candidate"
    _set_explicit_regression_inputs(document)
    _replace_regression_code_nodes(document)
    _validate_regression_graph(document)
    return document
```

The builder must not write unless called through its CLI. CLI output targets
only `paper-comparison-regression-workflow.yml` and its Ollama sibling.

- [ ] **Step 3: Add code-node unit tests**

Extract embedded functions using the existing test helper pattern and assert:

- malformed model/metric inputs become safe defaults without echoing input;
- task type is always exactly `regression`;
- manifest confirmation rejects any altered task type;
- report formatting shows MAE/RMSE/R² and both rankings;
- lower error is not described as worse and negative R² remains visible;
- backend arbitrary error strings are redacted;
- CSV rows and workflow secret never appear in outputs.

- [ ] **Step 4: Extend the dossier prompt narrowly**

Add MAE/RMSE/R² aliases and instruct the model to emit
`task_type="regression"` only when paper evidence explicitly identifies a
continuous-target regression problem. It must emit `uncertain` otherwise.
Do not change existing classification extraction rules.

- [ ] **Step 5: Create controlled fixtures**

Generate `regression-paper.pdf` from a short, non-copyrighted local fixture
document that explicitly states a frozen train/test protocol and MAE=1.5,
RMSE=2.0, R²=0.80. The fixture must state that it is synthetic acceptance data.
Keep the PDF below 100 KB and verify extracted text contains the three literals.

- [ ] **Step 6: Generate, test, and commit new artifacts**

```powershell
python scripts/build_regression_dsl.py --all-profiles
python -m pytest tests/test_dify_regression_dsl.py tests/test_dify_regression_code.py tests/test_dify_multimodel_dsl.py tests/test_dify_multimodel_code.py -q
python -m pytest tests/test_workflow_release_integrity.py -q
git diff --check
git add -- scripts/build_regression_dsl.py tests/test_dify_regression_dsl.py tests/test_dify_regression_code.py dify/paper-comparison-regression-workflow.yml dify/paper-comparison-regression-workflow-ollama.yml tests/fixtures/regression-paper.pdf dify/paper-dossier-system-prompt.md
git commit -m "feat: generate regression candidate workflows"
```

Before staging, verify none of the six existing user-owned DSL paths appears in
the task diff.

### Task 6: Full Verification and Candidate Dify Release

**Files:**
- Create: `.live-artifacts/regression-candidate-manifest.json` (ignored evidence)
- Create: `.live-artifacts/regression-candidate-verification.json` (ignored evidence)
- Modify: `docs/release-workflow.md`

**Interfaces:**
- Consumes: committed code and generated regression DSL.
- Produces: one isolated candidate app UUID, published workflow UUID, backup/rollback identity, release digests, and privacy-safe acceptance evidence.

- [ ] **Step 1: Run two fresh complete repository suites**

```powershell
python -m pytest -q
python -m pytest -q
```

Expected: both runs pass with default `.pytest-tmp`; no warning beyond the
single pre-existing Starlette/httpx deprecation warning.

- [ ] **Step 2: Verify generated artifacts and release manifest offline**

Generate both profiles twice in separate temporary directories and compare all
bytes. Run `scripts/check_workflow_release.py` against the committed DeepSeek
DSL and source set. Require empty drift, valid metadata, safe UUID fields, and
no repository writes beyond ignored `.live-artifacts`.

- [ ] **Step 3: Inspect local services without mutation**

Require runner `/healthz=200`, Dify local health, no queued/running jobs, and a
configured DeepSeek provider. Record only aggregate status. If any gate fails,
stop before candidate creation and report the stable code completion separately.

- [ ] **Step 4: Create and import an isolated candidate app**

Use the accepted local Dify service/API path to create a new workflow app named
`Paper comparison Regression Candidate`. Import only
`dify/paper-comparison-regression-workflow.yml`. Record the returned app UUID
and draft workflow UUID immediately. Never address the production app UUID.

- [ ] **Step 5: Publish with verified release orchestration**

Capture the candidate draft graph, calculate graph/metadata/source digests,
publish it, read the active workflow back, and require identity and digest
equality. Store the exact pre-publication backup identity for explicit rollback.
No automatic rollback occurs after a successful verified publication.

- [ ] **Step 6: Run the controlled PDF/CSV acceptance**

Submit `tests/fixtures/regression-paper.pdf` and
`tests/fixtures/regression_mixed.csv` through the installed candidate. Confirm
target `target`, explicit regression task, and bounded settings
(`cv_folds=3`, `n_iter=1`, `n_jobs=1`). Require:

- terminal job state `succeeded` or `partial` only when XGBoost is explicitly
  unavailable and all other models succeeded;
- non-negative MAE and RMSE plus finite R² for every successful model;
- shared test digest across successful models;
- explicit model statuses and both rankings;
- paper evidence values 1.5, 2.0, and 0.80;
- separate strict-comparability and approximate-similarity conclusions;
- no raw CSV rows, PDF text body, token, cookie, or secret in evidence files.

- [ ] **Step 7: Document candidate operation and final state**

Add the candidate name, input contract, supported models/metrics, safe usage
steps, candidate UUID fields, rollback identity location, and limitation that
the acceptance paper is synthetic to `docs/release-workflow.md`. Do not label
the candidate as production.

- [ ] **Step 8: Final scope review and documentation commit**

```powershell
git diff --check
git status --short
git diff --name-only HEAD
```

Require the six user-owned DSL modifications to remain unstaged and unchanged.
Commit only release documentation:

```powershell
git add -- docs/release-workflow.md
git commit -m "docs: record regression candidate release"
```

Dispatch a final whole-branch review over the merge base through HEAD. Any
Critical or Important finding receives one fix wave, focused tests, re-review,
and another complete suite before branch completion.
