# Tabular Regression Paper-Reproduction Design

**Date:** 2026-08-24

**Status:** Approved by delegated recommendation

## Purpose

Extend the existing privacy-preserving paper-reproduction workflow from binary
classification to ordinary tabular regression. A user must be able to confirm
that a CSV target is continuous, run a focused regression model suite, compare
MAE, RMSE, and R² against paper evidence, and complete the flow through an
isolated Dify candidate app.

The change is additive. Existing requests, manifests, stored results, Dify
applications, and binary-classification behavior remain compatible.

## Scope

The first regression release supports:

- explicit `task_type="regression"` selection;
- finite numeric continuous targets in UTF-8 tabular CSV files;
- numeric and supported low-cardinality categorical features through the
  existing preprocessing boundary;
- `linear_regression`, `random_forest`, `gradient_boosting`, and `xgboost`;
- MAE, RMSE, and R² for holdout evaluation and cross-validation;
- synchronous suite execution, persistent asynchronous jobs, result storage,
  paper comparison, and an isolated Dify candidate app;
- deterministic controlled PDF/CSV end-to-end acceptance.

## Non-Goals

- Automatic inference of classification versus regression from target values.
- Multiclass or multilabel classification.
- Time-series, grouped, spatial, or nested cross-validation.
- Raster, vector, image, text, or survival-analysis inputs.
- MAPE, MSLE, MedAE, quantile loss, uncertainty intervals, or prediction
  intervals.
- Hyperparameter parity with every source paper or large-scale AutoML.
- Modification or promotion of the current production binary-classification
  Dify app.

## Approaches Considered

### Task-aware shared pipeline — selected

Add task identity to the existing contracts and route validation, models,
metrics, cross-validation, and ranking through task-specific implementations.
Jobs, storage, provenance, privacy boundaries, and API lifecycle remain shared.
This avoids duplicating the reliable infrastructure while keeping binary
behavior as the default.

### Separate regression endpoints and schemas

This would isolate the first implementation, but it would duplicate manifest,
job, persistence, comparison, and Dify logic. The two paths would be likely to
diverge and would make a later multiclass extension harder.

### Generic task plug-in engine first

A fully registered task/model/metric architecture would be flexible, but the
up-front refactor is larger than the regression feature and would put the
working production path at unnecessary risk.

## Public Contract

### Task identity

Introduce:

```python
TaskType = Literal["binary_classification", "regression"]
```

`DatasetOptions`, `ModelSuiteConfig`, `ExperimentManifest`, persisted suite
results, comparison responses, and relevant API inputs carry `task_type`.
Missing `task_type` always means `binary_classification`, including when old
JSON artifacts are loaded.

The paper dossier may suggest a task type, but it cannot confirm it. The Dify
candidate presents regression explicitly and sends `task_type="regression"`.
No target-cardinality heuristic may silently select regression.

### Task-specific defaults and validation

When `task_type` is absent, current classification defaults remain unchanged.
When it is regression and the caller omits task-specific fields:

```json
{
  "models": [
    "linear_regression",
    "random_forest",
    "gradient_boosting",
    "xgboost"
  ],
  "optimization_metric": "rmse",
  "threshold": null
}
```

Classification-only model names and metrics are rejected for regression;
regression-only names and metrics are rejected for classification. Sampling
strategies other than `original` are rejected for regression. The decision
threshold remains meaningful only for binary classification.

The manifest digest includes `task_type`, so otherwise identical classification
and regression protocols cannot share an identity.

### Metrics

Keep the existing `ExperimentMetrics` classification contract and add:

```python
class RegressionMetrics(BaseModel):
    mae: float
    rmse: float
    r2: float
```

MAE and RMSE must be finite and non-negative. R² must be finite and may be
negative. Result models accept the appropriate metrics contract and validate
that it matches `config.task_type`.

All public MAE/RMSE values use their natural non-negative scale. Internal
scikit-learn negative scorers are converted before they enter results,
cross-validation summaries, storage, or API responses.

## Data Validation and Preprocessing

Regression diagnosis and execution require:

- an explicitly confirmed target column;
- a numeric target whose parsed values are all finite after the selected
  missing-value policy;
- at least 20 effective rows;
- enough training rows for the selected `cv_folds` after the holdout split;
- at least two distinct target values;
- no class balancing or stratified sampling.

The diagnostic response retains aggregate-only privacy. For regression it adds
an optional target summary containing count, minimum, maximum, mean, and
standard deviation. Classification class counts and ratios remain unchanged;
regression emits empty class maps.

Feature planning, categorical encoding, imputation, duplicate handling, feature
name derivation, and privacy-safe dataset hashing reuse existing boundaries.
Datetime, high-cardinality free text, ID-like fields, and unsupported columns
retain their existing risk or rejection behavior.

## Split and Cross-Validation

Regression uses one deterministic, non-stratified holdout split controlled by
the existing `test_size` and `random_state`. Every successful model receives
the same `SplitProvenance` and test digest.

Cross-validation uses shuffled `KFold` with the configured seed. It never uses
`StratifiedKFold`. Multi-seed execution remains supported, with the same fold
membership per model for a given seed.

Grid search uses task-appropriate scorers:

- MAE: `neg_mean_absolute_error` internally, exposed as positive MAE;
- RMSE: negative RMSE internally, exposed as positive RMSE;
- R²: `r2` directly.

## Model Registry

The registry resolves a model name together with `task_type`; a bare model name
is not enough to construct an estimator.

- `linear_regression`: `LinearRegression`, no artificial search space.
- `random_forest`: `RandomForestRegressor` with bounded tree/depth/leaf search.
- `gradient_boosting`: `GradientBoostingRegressor` with bounded estimator,
  learning-rate, depth, and loss-compatible search.
- `xgboost`: `XGBRegressor` when available, with CPU-safe defaults and the
  existing optional GPU policy where supported.

One unavailable or failed optional model produces its existing structured
per-model state and does not discard successful results.

Linear feature importance uses absolute coefficient magnitude. Tree and
boosting models use their native feature importances. Importance remains
aggregate-only and aligned with transformed feature names.

## Ranking and Paper Comparison

Performance ranking is task- and metric-aware:

- MAE and RMSE sort ascending;
- R² sorts descending;
- failed and unavailable models never enter the ranking.

Paper-closeness ranking continues to use finite differences, independent of
performance direction. The dossier normalizer adds unambiguous aliases for:

- MAE, mean absolute error, 平均绝对误差;
- RMSE, root mean squared error, 均方根误差;
- R²/R2/R-squared, coefficient of determination, 决定系数.

Unsupported metrics remain preserved as paper evidence but do not participate
in numeric comparison. Model qualifiers must still match; a paper metric tied
to a different model cannot be silently compared against all models.

Strict comparability and approximate numeric similarity remain separate. The
candidate report must not call a numerically close result an exact reproduction
when dataset, split, model, or provenance differ.

## Persistence and Compatibility

The existing experiment directory layout and filenames remain unchanged.
Stored configuration and result payloads add `task_type`; regression metrics
use the new metric shape. Loaders must continue to parse existing artifacts
whose configuration has no task type as binary classification.

No migration rewrites existing files. Atomic directory publication, bounded
Windows publication retry, exclusive update-temporary ownership, and cleanup
semantics remain unchanged.

## API and Job Lifecycle

The existing diagnosis, suite execution, manifest, job admission, polling,
wait-result, cancellation, restart recovery, and comparison routes remain the
single lifecycle. Relevant form or JSON inputs accept `task_type`, defaulting
to binary classification.

Idempotency and manifest identity include task type. A classification request
cannot replay a regression result even if its CSV bytes and other settings are
identical.

Error responses remain privacy-safe. Regression-specific errors use stable
codes for non-numeric targets, non-finite targets, insufficient rows,
constant targets, invalid models, invalid metrics, and invalid sampling.

## Dify Candidate

Create a new candidate app derived from the current release workflow rather
than modifying production. Its visible contract is explicitly regression:

1. upload a paper PDF and training CSV;
2. confirm the target column and regression task;
3. extract and normalize MAE/RMSE/R² evidence;
4. create and confirm a regression manifest;
5. submit and wait for the persistent job;
6. compare paper and independent metrics;
7. render model status, performance ranking, paper closeness, strict
   comparability, approximate similarity, warnings, and provenance.

The workflow sends raw CSV bytes only to the local runner. LLM nodes receive
paper evidence and aggregate result contracts, never CSV rows.

Candidate creation, import, publication, and verification use the existing
release-integrity and explicit rollback mechanisms. No production promotion is
authorized by this design.

## Acceptance Data

No existing local paper/CSV pair with regression metrics was found during
design discovery. Release acceptance therefore uses a deterministic controlled
fixture:

- a locally generated small PDF describing a frozen regression protocol and
  explicit MAE, RMSE, and R² values;
- a deterministic CSV with numeric and low-cardinality categorical features,
  a continuous target, and no sensitive data;
- expected structural outcomes derived independently and frozen in tests.

This fixture validates the complete technical path but is not presented as a
scientific reproduction claim. A real domain paper and matching dataset remain
a later validation input, not a reason to weaken the candidate release gate.

## Testing Strategy

Implementation follows strict RED/GREEN TDD and is divided into independently
reviewable layers:

1. additive schemas, defaults, task-model-metric validation, manifest identity,
   and old-artifact compatibility;
2. regression diagnosis, target validation, preprocessing, deterministic split,
   and shared provenance;
3. four estimators, CV score normalization, metrics, feature importance,
   per-model failure isolation, and direction-aware ranking;
4. persistence, synchronous API, asynchronous jobs, restart recovery,
   idempotency, and comparison;
5. dossier aliases, generated DSL contracts, candidate release, and controlled
   PDF/CSV end-to-end verification.

Each layer runs focused compatibility tests. The final code candidate must pass
the complete repository suite twice with the default pytest configuration on
Windows, with no new warnings. The candidate Dify run must reach a terminal
success state and preserve task type, manifest identity, shared test digest,
all three regression metrics, model statuses, and privacy-safe artifacts.

## Acceptance Criteria

- Existing classification requests and stored results remain byte-contract
  compatible except for additive fields in newly written artifacts.
- Regression cannot be selected implicitly.
- All four regression model names are accepted only for regression and produce
  deterministic structured outcomes.
- MAE/RMSE are never exposed as negative scorer values.
- Performance ranking respects lower-is-better versus higher-is-better.
- All successful models share one holdout provenance and test digest.
- Regression manifests and idempotency keys cannot collide with classification.
- Atomic storage and asynchronous restart contracts remain green.
- The full repository suite passes twice on Windows with no new warning.
- An isolated Dify candidate completes the controlled regression PDF/CSV run.
- The production binary-classification Dify app and the six user-owned DSL
  modifications remain untouched.
