from __future__ import annotations

from collections.abc import Mapping, Sequence
from time import perf_counter
from uuid import uuid4

import numpy as np
from sklearn.model_selection import RandomizedSearchCV, StratifiedKFold

from repro_runner.data import DatasetBundle
from repro_runner.metrics import evaluate_classifier, feature_importances
from repro_runner.model_registry import get_model_spec
from repro_runner.schemas import (
    ExperimentSuiteResult,
    ModelRunResult,
    ModelSuiteConfig,
    SplitProvenance,
    ValidationErrorItem,
)
from repro_runner.split import ExperimentError, make_stratified_split, test_set_digest

_SAFE_MISSING_DEPENDENCY_MESSAGES = frozenset(
    {
        "xgboost dependency is not installed",
        "lightgbm dependency is not installed",
    }
)


def run_model_suite(
    bundle: DatasetBundle,
    config: ModelSuiteConfig,
) -> ExperimentSuiteResult:
    features = bundle.frame[bundle.feature_columns].to_numpy(dtype=float)
    target = bundle.frame[bundle.target_column].to_numpy()
    classes = np.sort(np.unique(target))
    train_indices, test_indices = make_stratified_split(
        target,
        test_size=config.test_size,
        random_state=config.random_state,
    )

    x_train, x_test = features[train_indices], features[test_indices]
    y_train, y_test = target[train_indices], target[test_indices]
    training_class_counts = _training_class_counts(y_train)
    _validate_cv_folds(training_class_counts, config.cv_folds)

    split_provenance = SplitProvenance(
        test_size=config.test_size,
        random_state=config.random_state,
        train_rows=len(train_indices),
        test_rows=len(test_indices),
        test_digest=test_set_digest(bundle, test_indices),
    )
    shared_cv = StratifiedKFold(
        n_splits=config.cv_folds,
        shuffle=True,
        random_state=config.random_state,
    )

    results: list[ModelRunResult] = []
    for model_name in config.models:
        try:
            spec = get_model_spec(
                model_name,
                training_class_counts,
                random_state=config.random_state,
                use_gpu=config.use_gpu,
            )
            search = RandomizedSearchCV(
                estimator=spec.estimator,
                param_distributions=spec.search_space,
                n_iter=config.n_iter,
                scoring=config.optimization_metric,
                cv=shared_cv,
                random_state=config.random_state,
                n_jobs=config.n_jobs,
                refit=True,
                error_score="raise",
            )
            started_at = perf_counter()
            search.fit(x_train, y_train)
            fit_seconds = round(perf_counter() - started_at, 3)
            best_estimator = search.best_estimator_
            metrics = evaluate_classifier(
                best_estimator,
                x_test,
                y_test,
                classes,
                config.threshold,
            )
            feature_importance = feature_importances(
                _unwrap_feature_estimator(best_estimator),
                bundle.feature_columns,
            )
            results.append(
                ModelRunResult(
                    model=model_name,
                    status="succeeded",
                    cv_best_score=round(float(search.best_score_), 6),
                    best_params=_json_safe_value(search.best_params_),
                    metrics=metrics,
                    feature_importance=feature_importance,
                    fit_seconds=fit_seconds,
                )
            )
        except ImportError as exc:
            results.append(
                ModelRunResult(
                    model=model_name,
                    status="unavailable",
                    error=ValidationErrorItem(
                        code="missing_dependency",
                        message=_safe_import_error_message(model_name, exc),
                    ),
                )
            )
        except (ValueError, RuntimeError) as exc:
            results.append(
                ModelRunResult(
                    model=model_name,
                    status="failed",
                    error=ValidationErrorItem(
                        code="model_training_failed",
                        message=_safe_model_failure_message(model_name, exc),
                    ),
                )
            )

    successful_results = [result for result in results if result.status == "succeeded"]
    if len(successful_results) == len(config.models):
        status = "succeeded"
    elif successful_results:
        status = "partial"
    else:
        status = "failed"

    return ExperimentSuiteResult(
        experiment_id=f"exp-{uuid4().hex}",
        status=status,
        config=config,
        dataset=bundle.profile,
        split_provenance=split_provenance,
        results=results,
        performance_ranking=_performance_ranking(successful_results),
        reproducibility_status="cv_tuned",
    )


def _training_class_counts(target: np.ndarray) -> dict[str, int]:
    labels, counts = np.unique(target, return_counts=True)
    items = sorted(
        ((str(label), int(count)) for label, count in zip(labels, counts)),
        key=lambda item: item[0],
    )
    return dict(items)


def _validate_cv_folds(class_counts: Mapping[str, int], cv_folds: int) -> None:
    if len(class_counts) != 2 or min(class_counts.values()) < cv_folds:
        raise ExperimentError(
            "invalid_cv_folds",
            "the requested cross-validation folds cannot be represented by the training partition",
        )


def _unwrap_feature_estimator(estimator):
    if hasattr(estimator, "named_steps") and "model" in estimator.named_steps:
        return estimator.named_steps["model"]
    return estimator


def _performance_ranking(results: Sequence[ModelRunResult]) -> list[str]:
    ranked = sorted(
        results,
        key=lambda result: (
            -float(result.metrics.roc_auc),
            -float(result.metrics.f1),
            -float(result.metrics.recall),
        ),
    )
    return [result.model for result in ranked]


def _safe_import_error_message(model_name: str, error: ImportError) -> str:
    message = str(error).strip()
    if message in _SAFE_MISSING_DEPENDENCY_MESSAGES:
        return message
    return f"{model_name} dependency is unavailable"


def _safe_model_failure_message(model_name: str, error: ValueError | RuntimeError) -> str:
    return f"{model_name} model training failed ({type(error).__name__})"


def _json_safe_value(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_json_safe_value(item) for item in value.tolist()]
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_safe_value(item) for item in value]
    return value
