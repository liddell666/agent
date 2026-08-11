# Task 4 Report - Sequential training-only cross-validation suite engine

- Date: 2026-08-11
- Commit hash: 42d7e4b
- Scope: Task 4 only (`src/repro_runner/suite_engine.py`, `tests/repro_runner/test_suite_engine.py`)

## RED evidence

Command:

```powershell
$env:PYTHONPATH='.;src'; pytest -q tests/repro_runner/test_suite_engine.py tests/repro_runner/test_engine.py
```

Outcome:

- Failed during collection with `ModuleNotFoundError: No module named 'repro_runner.suite_engine'`.
- This confirmed the new suite-engine tests were exercising missing Task 4 behavior before implementation.

## GREEN evidence

Command:

```powershell
$env:PYTHONPATH='.;src'; pytest -q tests/repro_runner/test_suite_engine.py tests/repro_runner/test_engine.py
```

Outcome:

- Passed: `12 passed in 8.58s`

Adjacent schema verification:

```powershell
$env:PYTHONPATH='.;src'; pytest -q tests/repro_runner/test_suite_schemas.py
```

Outcome:

- Passed: `3 passed in 0.20s`

## Focused regression results

- Added `tests/repro_runner/test_suite_engine.py`.
- Covered one shared outer holdout and digest, training-only CV class counts, shared `StratifiedKFold`, JSON-safe `best_params`, deterministic split/digest behavior, missing-dependency isolation, invalid `cv_folds` pre-search validation, ranking/tie ordering, and pipeline coefficient unwrapping for feature importance.
- Reconfirmed the existing baseline engine tests still pass via `tests/repro_runner/test_engine.py`.

## Self-review

- The engine creates exactly one outer stratified holdout and one shared `StratifiedKFold` per suite run.
- Pre-search validation raises `ExperimentError(code="invalid_cv_folds", ...)` before any model search starts.
- Each requested model gets one sequential `RandomizedSearchCV` using the requested scorer, fold count, random state, and `n_jobs`.
- Model failures are isolated into stable `unavailable` / `failed` entries without aborting later models.
- Successful models share one suite-level split provenance and ranking is stable on ties.
- Logistic-regression pipeline coefficients are preserved by unwrapping the final `model` step before feature-importance extraction.

## Concerns

- The existing model registry still owns estimator-level parallelism defaults for some models; Task 4 does not modify registry behavior by request.
- There is an unrelated pre-existing modification in `.superpowers/sdd/task-3-report.md`; it was left untouched and will not be staged with this commit.
