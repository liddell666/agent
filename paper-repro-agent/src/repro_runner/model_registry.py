from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
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
) -> ModelSpec:
    try:
        factory = _MODEL_FACTORIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown model name: {name}") from exc
    return factory(
        class_counts=class_counts,
        random_state=random_state,
        use_gpu=use_gpu,
    )


def _logistic_regression_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
) -> ModelSpec:
    del class_counts, use_gpu
    estimator = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    class_weight="balanced",
                    solver="liblinear",
                    max_iter=1000,
                    random_state=random_state,
                ),
            ),
        ]
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
) -> ModelSpec:
    del class_counts, use_gpu
    estimator = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced",
        n_jobs=-1,
        random_state=random_state,
    )
    return ModelSpec(
        name="random_forest",
        estimator=estimator,
        search_space={
            "n_estimators": [300, 500],
            "max_depth": [None, 10, 20],
            "min_samples_leaf": [1, 2, 4],
            "max_features": ["sqrt", "log2"],
        },
        optional_dependency=None,
        feature_importance_kind="tree",
    )


def _xgboost_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
) -> ModelSpec:
    try:
        from xgboost import XGBClassifier
    except ImportError as exc:
        raise ImportError("xgboost dependency is not installed") from exc

    estimator = XGBClassifier(
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        scale_pos_weight=_scale_pos_weight(class_counts),
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


def _lightgbm_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
) -> ModelSpec:
    try:
        from lightgbm import LGBMClassifier
    except ImportError as exc:
        raise ImportError("lightgbm dependency is not installed") from exc

    estimator = LGBMClassifier(
        objective="binary",
        scale_pos_weight=_scale_pos_weight(class_counts),
        random_state=random_state,
        n_jobs=4,
        device_type="gpu" if use_gpu else "cpu",
    )
    return ModelSpec(
        name="lightgbm",
        estimator=estimator,
        search_space={
            "n_estimators": [200, 400],
            "num_leaves": [31, 63],
            "learning_rate": [0.03, 0.1],
            "subsample": [0.8, 1.0],
        },
        optional_dependency="lightgbm",
        feature_importance_kind="tree",
    )


def _svm_spec(
    class_counts: Mapping[str, int],
    random_state: int,
    use_gpu: bool,
) -> ModelSpec:
    del class_counts, use_gpu
    estimator = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                SVC(
                    probability=True,
                    class_weight="balanced",
                    random_state=random_state,
                ),
            ),
        ]
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
) -> ModelSpec:
    del class_counts, random_state, use_gpu
    estimator = Pipeline(
        [
            ("scale", StandardScaler()),
            ("model", KNeighborsClassifier(n_jobs=4)),
        ]
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
) -> ModelSpec:
    del class_counts, use_gpu
    estimator = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                MLPClassifier(
                    early_stopping=True,
                    max_iter=300,
                    random_state=random_state,
                ),
            ),
        ]
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


def _scale_pos_weight(class_counts: Mapping[str, int]) -> float:
    negative = max(int(class_counts.get("0", 0)), 0)
    positive = max(int(class_counts.get("1", 0)), 0)
    return float(max(negative, 1) / max(positive, 1))


_MODEL_FACTORIES = {
    "logistic_regression": _logistic_regression_spec,
    "random_forest": _random_forest_spec,
    "xgboost": _xgboost_spec,
    "lightgbm": _lightgbm_spec,
    "svm": _svm_spec,
    "knn": _knn_spec,
    "mlp": _mlp_spec,
}
