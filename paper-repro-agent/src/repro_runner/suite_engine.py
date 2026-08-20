from __future__ import annotations

from collections.abc import Mapping, Sequence
from time import perf_counter
from uuid import uuid4

import numpy as np
from sklearn.model_selection import RandomizedSearchCV, StratifiedKFold

from repro_runner.data import DatasetBundle
from repro_runner.metrics import (
    NUMERIC_METRIC_NAMES,
    evaluate_classifier,
    feature_importances,
    mean_std_over_metrics,
)
from repro_runner.model_registry import get_model_spec
from repro_runner.preprocessing import build_preprocessor, transformed_feature_names
from repro_runner.runtime import runtime_provenance
from repro_runner.schemas import (
    ExperimentMetrics,
    ExperimentSuiteResult,
    ModelRunResult,
    ModelSuiteConfig,
    PreprocessingSummary,
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
    *,
    experiment_id: str | None = None,
    progress_callback=None,
) -> ExperimentSuiteResult:
    features = bundle.frame[bundle.feature_columns].copy(deep=True)
    target = bundle.frame[bundle.target_column].to_numpy()
    classes = np.sort(np.unique(target))
    preprocessor = build_preprocessor(
        features,
        bundle.feature_columns,
        max_cardinality=bundle.max_category_cardinality,
        max_transformed_features=bundle.max_transformed_features,
    )
    train_indices, test_indices = make_stratified_split(
        target,
        test_size=config.test_size,
        random_state=config.random_state,
    )

    x_train = features.iloc[train_indices].reset_index(drop=True)
    x_test = features.iloc[test_indices].reset_index(drop=True)
    y_train, y_test = target[train_indices], target[test_indices]
    training_class_counts = _training_class_counts(y_train)
    _validate_cv_folds(training_class_counts, config.cv_folds)
    _validate_nested_cv_folds(training_class_counts, config.cv_folds)

    split_provenance = SplitProvenance(
        test_size=config.test_size,
        random_state=config.random_state,
        train_rows=len(train_indices),
        test_rows=len(test_indices),
        test_digest=test_set_digest(bundle, test_indices),
    )
    seeds = _experiment_seeds(config.random_state, config.n_seeds)

    results: list[ModelRunResult] = []
    observed_transformed_names: list[str] | None = None
    for model_name in config.models:
        started_at = perf_counter()
        try:
            cv_fold_scores, seed_means, cv_mean, cv_std = _cross_validate_model(
                model_name,
                x_train,
                y_train,
                classes,
                config,
                preprocessor,
                bundle.sampling_strategy,
                seeds,
            )
            spec = get_model_spec(
                model_name,
                training_class_counts,
                random_state=config.random_state,
                use_gpu=config.use_gpu,
                preprocessor=preprocessor,
                sampling_strategy=bundle.sampling_strategy,
            )
            search = RandomizedSearchCV(
                estimator=spec.estimator,
                param_distributions=spec.search_space,
                n_iter=config.n_iter,
                scoring=config.optimization_metric,
                cv=_make_inner_cv(config.cv_folds, config.random_state),
                random_state=config.random_state,
                n_jobs=config.n_jobs,
                refit=True,
                error_score="raise",
            )
            x_fit, y_fit = _resample_training_partition(
                x_train, y_train, bundle.sampling_strategy, config.random_state
            )
            search.fit(x_fit, y_fit)
            best_estimator = search.best_estimator_
            metrics = evaluate_classifier(
                best_estimator,
                x_test,
                y_test,
                classes,
                config.threshold,
            )
            transformed_names = _transformed_names(
                best_estimator, bundle.feature_columns
            )
            if observed_transformed_names is None:
                observed_transformed_names = list(transformed_names)
            feature_importance = feature_importances(
                _unwrap_feature_estimator(best_estimator), transformed_names
            )
            results.append(
                ModelRunResult(
                    model=model_name,
                    status="succeeded",
                    cv_best_score=round(float(search.best_score_), 6),
                    best_params=_json_safe_value(search.best_params_),
                    metrics=metrics,
                    feature_importance=feature_importance,
                    fit_seconds=round(perf_counter() - started_at, 3),
                    split_provenance=split_provenance,
                    cv_mean=cv_mean,
                    cv_std=cv_std,
                    cv_fold_scores=cv_fold_scores,
                    seed_means=seed_means,
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
        if progress_callback is not None:
            progress_callback(results[-1], len(results), len(config.models))

    successful_results = [result for result in results if result.status == "succeeded"]
    if len(successful_results) == len(config.models):
        status = "succeeded"
    elif successful_results:
        status = "partial"
    else:
        status = "failed"

    return ExperimentSuiteResult(
        experiment_id=experiment_id or f"exp-{uuid4().hex}",
        status=status,
        config=config,
        dataset=bundle.profile,
        split_provenance=split_provenance,
        results=results,
        performance_ranking=_performance_ranking(successful_results),
        reproducibility_status="cv_evaluated",
        preprocessing=_preprocessing_summary(
            preprocessor,
            bundle.feature_columns,
            bundle.sampling_strategy,
            transformed_names=observed_transformed_names,
        ),
        runtime=runtime_provenance(config.workflow_version),
    )


def _experiment_seeds(base_seed: int, n_seeds: int) -> list[int]:
    """Derive deterministic, non-overlapping seeds from the base random_state."""
    return [base_seed + offset for offset in range(n_seeds)]


def _cross_validate_model(
    model_name: str,
    x_train,
    y_train,
    classes: np.ndarray,
    config: ModelSuiteConfig,
    preprocessor,
    sampling_strategy: str,
    seeds: Sequence[int],
) -> tuple[dict[str, list[float]], dict[str, list[float]], dict[str, float], dict[str, float]]:
    """Repeated stratified K-fold CV on the training partition.

    For every seed, stratify the training partition into ``cv_folds`` folds.
    Each fold tunes hyperparameters on its own training sub-fold and is scored
    on the held-out validation sub-fold.  Returns fold-level score lists,
    per-seed means, and pooled mean/std.
    """
    all_fold_metrics: list[ExperimentMetrics] = []
    seed_means: dict[str, list[float]] = {name: [] for name in NUMERIC_METRIC_NAMES}
    for seed in seeds:
        fold_metrics: list[ExperimentMetrics] = []
        splitter = StratifiedKFold(
            n_splits=config.cv_folds, shuffle=True, random_state=seed
        )
        for train_idx, val_idx in splitter.split(x_train, y_train):
            x_tr = x_train.iloc[train_idx]
            y_tr = y_train[train_idx]
            x_tr, y_tr = _resample_training_partition(
                x_tr, y_tr, sampling_strategy, seed
            )
            spec = get_model_spec(
                model_name,
                _training_class_counts(y_tr),
                random_state=seed,
                use_gpu=config.use_gpu,
                preprocessor=preprocessor,
                sampling_strategy=sampling_strategy,
            )
            search = RandomizedSearchCV(
                estimator=spec.estimator,
                param_distributions=spec.search_space,
                n_iter=config.n_iter,
                scoring=config.optimization_metric,
                cv=_make_inner_cv(config.cv_folds, seed),
                random_state=seed,
                n_jobs=config.n_jobs,
                refit=True,
                error_score="raise",
            )
            search.fit(x_tr, y_tr)
            fold_metrics.append(
                evaluate_classifier(
                    search.best_estimator_,
                    x_train.iloc[val_idx],
                    y_train[val_idx],
                    classes,
                    config.threshold,
                )
            )
        seed_values = _collect_fold_scores(fold_metrics)
        for name in NUMERIC_METRIC_NAMES:
            seed_means[name].append(
                _round_public(float(np.mean(seed_values[name])))
            )
        all_fold_metrics.extend(fold_metrics)

    cv_mean, cv_std = mean_std_over_metrics(all_fold_metrics)
    cv_fold_scores = _collect_fold_scores(all_fold_metrics)
    return cv_fold_scores, seed_means, cv_mean, cv_std


def _collect_fold_scores(
    metrics_list: Sequence[ExperimentMetrics],
) -> dict[str, list[float]]:
    collected: dict[str, list[float]] = {name: [] for name in NUMERIC_METRIC_NAMES}
    for metrics in metrics_list:
        for name in NUMERIC_METRIC_NAMES:
            collected[name].append(float(getattr(metrics, name)))
    return collected


def _resample_training_partition(x, y, sampling_strategy: str, seed: int):
    """Apply balanced undersampling to a training partition without touching y.

    Validation and test partitions are never passed through this helper, which
    keeps the resampling strategy fold-safe: the class balance is corrected only
    inside each training fold and the final training fit.
    """
    if sampling_strategy != "balanced_undersample":
        return x, y
    labels = np.unique(y)
    if len(labels) < 2:
        return x, y
    class_counts = {label: int(np.sum(y == label)) for label in labels}
    minority_count = min(class_counts.values())
    rng = np.random.RandomState(seed)
    keep_indices: list[np.ndarray] = []
    for label in labels:
        indices = np.where(y == label)[0]
        if len(indices) > minority_count:
            indices = rng.choice(indices, size=minority_count, replace=False)
        keep_indices.append(indices)
    keep = np.sort(np.concatenate(keep_indices))
    return x.iloc[keep].reset_index(drop=True), y[keep]


def _make_inner_cv(cv_folds: int, seed: int) -> StratifiedKFold:
    return StratifiedKFold(n_splits=cv_folds, shuffle=True, random_state=seed)


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


def _validate_nested_cv_folds(class_counts: Mapping[str, int], cv_folds: int) -> None:
    """Reject outer splits that cannot support the configured inner CV folds."""
    minimum_inner_class_count = min(
        count - ((count + cv_folds - 1) // cv_folds)
        for count in class_counts.values()
    )
    if minimum_inner_class_count < cv_folds:
        raise ExperimentError(
            "invalid_nested_cv_folds",
            "the requested cross-validation folds cannot be represented by nested training partitions",
        )


def _unwrap_feature_estimator(estimator):
    if hasattr(estimator, "named_steps") and "model" in estimator.named_steps:
        return estimator.named_steps["model"]
    return estimator


def _transformed_names(estimator, fallback: Sequence[str]) -> list[str]:
    if hasattr(estimator, "named_steps") and "preprocess" in estimator.named_steps:
        return transformed_feature_names(estimator.named_steps["preprocess"])
    return list(fallback)


def _preprocessing_summary(
    preprocessor,
    feature_columns: Sequence[str],
    sampling_strategy: str,
    *,
    transformed_names: Sequence[str] | None,
) -> PreprocessingSummary:
    names = list(transformed_names or feature_columns)
    numeric_columns: list[str] = []
    categorical_columns: list[str] = []
    if hasattr(preprocessor, "transformers"):
        for name, _, columns in preprocessor.transformers:
            if name == "numeric":
                numeric_columns.extend(str(column) for column in columns)
            elif name == "categorical":
                categorical_columns.extend(str(column) for column in columns)
    return PreprocessingSummary(
        numeric_columns=numeric_columns,
        categorical_columns=categorical_columns,
        transformed_feature_names=names,
        sampling_strategy=sampling_strategy,
    )


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


def _round_public(value: float) -> float:
    return round(float(value), 6)


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
