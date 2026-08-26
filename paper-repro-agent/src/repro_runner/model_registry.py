from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sklearn.base import clone
from sklearn.ensemble import (
    GradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

FeatureImportanceKind = Literal["coef", "tree", "none"]

_MODEL_NAMES: tuple[str, ...] = (
    "logistic_regression",
    "random_forest",
    "xgboost",
    "lightgbm",
    "svm",
    "knn",
    "mlp",
)

REGRESSION_SEARCH_SPACES: dict[str, dict[str, list[Any]]] = {
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


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    estimator: Any
    search_space: dict[str, list[Any]]
    optional_dependency: str | None
    feature_importance_kind: FeatureImportanceKind


def available_model_names() -> tuple[str, ...]:
    return _MODEL_NAMES


def get_model_spec(
    name: str,
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    *,
    task_type: str = "binary_classification",
    preprocessor=None,
    sampling_strategy: str | None = None,
) -> ModelSpec:
    _validate_sampling_strategy(sampling_strategy)
    if task_type not in {"binary_classification", "regression"}:
        raise ValueError("unsupported_task_type")
    if task_type == "regression" and sampling_strategy != "original":
        raise ValueError("unsupported_sampling_strategy")
    try:
        factory = _MODEL_FACTORIES[task_type][name]
    except KeyError as exc:
        raise ValueError(f"unknown model name: {name}") from exc
    return factory(
        class_counts=class_counts,
        random_state=random_state,
        use_gpu=use_gpu,
        preprocessor=preprocessor,
        sampling_strategy=sampling_strategy,
    )


def _logistic_regression_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    del class_counts, use_gpu
    estimator = _model_pipeline(
        LogisticRegression(
            class_weight=_class_weight(sampling_strategy),
            solver="liblinear",
            max_iter=1000,
            random_state=random_state,
        ),
        preprocessor=preprocessor,
        scale=True,
    )
    return ModelSpec(
        name="logistic_regression",
        estimator=estimator,
        search_space={"model__C": [0.01, 0.1, 1.0, 10.0]},
        optional_dependency=None,
        feature_importance_kind="coef",
    )


def _random_forest_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    del class_counts, use_gpu
    estimator = _model_pipeline(
        RandomForestClassifier(
            n_estimators=300,
            class_weight=_class_weight(sampling_strategy),
            n_jobs=-1,
            random_state=random_state,
        ),
        preprocessor=preprocessor,
        scale=False,
    )
    return ModelSpec(
        name="random_forest",
        estimator=estimator,
        search_space=_model_search_space(
            {
                "n_estimators": [300, 500],
                "max_depth": [None, 10, 20],
                "min_samples_leaf": [1, 2, 4],
                "max_features": ["sqrt", "log2"],
            },
            preprocessor,
        ),
        optional_dependency=None,
        feature_importance_kind="tree",
    )


def _xgboost_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    try:
        from xgboost import XGBClassifier
    except ImportError as exc:
        raise ImportError("xgboost dependency is not installed") from exc

    estimator = _model_pipeline(
        XGBClassifier(
            objective="binary:logistic",
            eval_metric="logloss",
            tree_method="hist",
            scale_pos_weight=_scale_pos_weight(class_counts, sampling_strategy),
            random_state=random_state,
            n_jobs=4,
            device="cuda" if use_gpu else "cpu",
        ),
        preprocessor=preprocessor,
        scale=False,
    )
    return ModelSpec(
        name="xgboost",
        estimator=estimator,
        search_space=_model_search_space(
            {
                "n_estimators": [200, 400],
                "max_depth": [3, 5],
                "learning_rate": [0.03, 0.1],
                "subsample": [0.8, 1.0],
            },
            preprocessor,
        ),
        optional_dependency="xgboost",
        feature_importance_kind="tree",
    )


def _lightgbm_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    try:
        from lightgbm import LGBMClassifier
    except ImportError as exc:
        raise ImportError("lightgbm dependency is not installed") from exc

    estimator = _model_pipeline(
        LGBMClassifier(
            objective="binary",
            scale_pos_weight=_scale_pos_weight(class_counts, sampling_strategy),
            random_state=random_state,
            n_jobs=4,
            device_type="gpu" if use_gpu else "cpu",
        ),
        preprocessor=preprocessor,
        scale=False,
    )
    return ModelSpec(
        name="lightgbm",
        estimator=estimator,
        search_space=_model_search_space(
            {
                "n_estimators": [200, 400],
                "num_leaves": [31, 63],
                "learning_rate": [0.03, 0.1],
                "subsample": [0.8, 1.0],
            },
            preprocessor,
        ),
        optional_dependency="lightgbm",
        feature_importance_kind="tree",
    )


def _svm_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    del class_counts, use_gpu
    estimator = _model_pipeline(
        SVC(
            probability=True,
            class_weight=_class_weight(sampling_strategy),
            random_state=random_state,
        ),
        preprocessor=preprocessor,
        scale=True,
    )
    return ModelSpec(
        name="svm",
        estimator=estimator,
        search_space={
            "model__C": [0.1, 1.0, 10.0],
            "model__gamma": ["scale", 0.01, 0.1],
        },
        optional_dependency=None,
        feature_importance_kind="none",
    )


def _knn_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    del class_counts, random_state, use_gpu, sampling_strategy
    estimator = _model_pipeline(
        KNeighborsClassifier(n_jobs=4),
        preprocessor=preprocessor,
        scale=True,
    )
    return ModelSpec(
        name="knn",
        estimator=estimator,
        search_space={
            "model__n_neighbors": [3, 5, 9, 15],
            "model__weights": ["uniform", "distance"],
        },
        optional_dependency=None,
        feature_importance_kind="none",
    )


def _mlp_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    del class_counts, use_gpu, sampling_strategy
    estimator = _model_pipeline(
        MLPClassifier(
            early_stopping=True,
            max_iter=300,
            random_state=random_state,
        ),
        preprocessor=preprocessor,
        scale=True,
    )
    return ModelSpec(
        name="mlp",
        estimator=estimator,
        search_space={
            "model__hidden_layer_sizes": [(64,), (128,), (64, 32)],
            "model__alpha": [0.0001, 0.001, 0.01],
            "model__learning_rate_init": [0.0005, 0.001, 0.01],
        },
        optional_dependency=None,
        feature_importance_kind="none",
    )


def _linear_regression_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    del class_counts, random_state, use_gpu, sampling_strategy
    return ModelSpec(
        name="linear_regression",
        estimator=_model_pipeline(
            LinearRegression(), preprocessor=preprocessor, scale=True
        ),
        search_space={},
        optional_dependency=None,
        feature_importance_kind="coef",
    )


def _random_forest_regressor_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    del class_counts, use_gpu, sampling_strategy
    return ModelSpec(
        name="random_forest",
        estimator=_model_pipeline(
            RandomForestRegressor(
                n_estimators=100, n_jobs=-1, random_state=random_state
            ),
            preprocessor=preprocessor,
            scale=False,
        ),
        search_space=_regression_search_space("random_forest", preprocessor),
        optional_dependency=None,
        feature_importance_kind="tree",
    )


def _gradient_boosting_regressor_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    del class_counts, use_gpu, sampling_strategy
    return ModelSpec(
        name="gradient_boosting",
        estimator=_model_pipeline(
            GradientBoostingRegressor(random_state=random_state),
            preprocessor=preprocessor,
            scale=False,
        ),
        search_space=_regression_search_space("gradient_boosting", preprocessor),
        optional_dependency=None,
        feature_importance_kind="tree",
    )


def _xgboost_regressor_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
    preprocessor,
    sampling_strategy: str | None,
) -> ModelSpec:
    del class_counts, sampling_strategy
    try:
        from xgboost import XGBRegressor
    except ImportError as exc:
        raise ImportError("xgboost dependency is not installed") from exc

    return ModelSpec(
        name="xgboost",
        estimator=_model_pipeline(
            XGBRegressor(
                objective="reg:squarederror",
                tree_method="hist",
                random_state=random_state,
                n_jobs=4,
                device="cuda" if use_gpu else "cpu",
            ),
            preprocessor=preprocessor,
            scale=False,
        ),
        search_space=_regression_search_space("xgboost", preprocessor),
        optional_dependency="xgboost",
        feature_importance_kind="tree",
    )
def _model_pipeline(estimator, *, preprocessor, scale: bool):
    steps = []
    if preprocessor is not None:
        steps.append(("preprocess", clone(preprocessor)))
    if scale:
        steps.append(("scale", StandardScaler()))
    steps.append(("model", estimator))
    if len(steps) == 1:
        return estimator
    return Pipeline(steps)


def _model_search_space(
    values: dict[str, list[Any]], preprocessor
) -> dict[str, list[Any]]:
    if preprocessor is None:
        return values
    return {f"model__{key}": options for key, options in values.items()}


def _regression_search_space(name: str, preprocessor) -> dict[str, list[Any]]:
    values = REGRESSION_SEARCH_SPACES[name]
    if preprocessor is not None:
        return values
    return {key.removeprefix("model__"): options for key, options in values.items()}


def _validate_sampling_strategy(sampling_strategy: str | None) -> None:
    if sampling_strategy not in {
        None,
        "original",
        "class_weight",
        "balanced_undersample",
    }:
        raise ValueError("unsupported_sampling_strategy")


def _class_weight(sampling_strategy: str | None) -> str | None:
    return "balanced" if sampling_strategy in {None, "class_weight"} else None


def _scale_pos_weight(
    class_counts: Mapping[str, int], sampling_strategy: str | None
) -> float:
    negative, positive = _binary_label_counts(class_counts)
    if sampling_strategy in {"original", "balanced_undersample"}:
        return 1.0
    return float(max(negative, 1) / max(positive, 1))


def _binary_label_counts(class_counts: Mapping[str, int]) -> tuple[int, int]:
    if len(class_counts) != 2:
        raise ValueError(
            "binary class_counts must contain exactly two labels with "
            "non-negative integer counts"
        )

    sorted_items = sorted(class_counts.items())
    counts: list[int] = []
    for _, raw_count in sorted_items:
        if not isinstance(raw_count, int) or raw_count < 0:
            raise ValueError(
                "binary class_counts must contain exactly two labels with "
                "non-negative integer counts"
            )
        counts.append(raw_count)

    return counts[0], counts[1]


_MODEL_FACTORIES = {
    "binary_classification": {
        "logistic_regression": _logistic_regression_spec,
        "random_forest": _random_forest_spec,
        "xgboost": _xgboost_spec,
        "lightgbm": _lightgbm_spec,
        "svm": _svm_spec,
        "knn": _knn_spec,
        "mlp": _mlp_spec,
    },
    "regression": {
        "linear_regression": _linear_regression_spec,
        "random_forest": _random_forest_regressor_spec,
        "gradient_boosting": _gradient_boosting_regressor_spec,
        "xgboost": _xgboost_regressor_spec,
    },
}
