import importlib
import math
import sys
from types import ModuleType

import pytest
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.svm import SVC

import repro_runner.model_registry as model_registry
from repro_runner.preprocessing import build_preprocessor


def test_registry_contains_all_requested_models():
    assert model_registry.available_model_names() == (
        "logistic_regression",
        "random_forest",
        "xgboost",
        "lightgbm",
        "svm",
        "knn",
        "mlp",
    )


@pytest.mark.parametrize(
    ("name", "expected_class"),
    [
        ("linear_regression", "LinearRegression"),
        ("random_forest", "RandomForestRegressor"),
        ("gradient_boosting", "GradientBoostingRegressor"),
    ],
)
def test_regression_registry_builds_task_specific_estimators(name, expected_class):
    preprocessor = build_preprocessor(
        pd.DataFrame({"feature": [1.0, 2.0]}), ["feature"]
    )

    spec = model_registry.get_model_spec(
        name,
        {},
        task_type="regression",
        random_state=11,
        use_gpu=False,
        preprocessor=preprocessor,
        sampling_strategy="original",
    )

    assert spec.estimator.named_steps["model"].__class__.__name__ == expected_class


def test_same_model_name_resolves_by_task():
    preprocessor = build_preprocessor(
        pd.DataFrame({"feature": [1.0, 2.0]}), ["feature"]
    )

    classifier = model_registry.get_model_spec(
        "random_forest",
        {"0": 20, "1": 20},
        task_type="binary_classification",
        random_state=1,
        use_gpu=False,
        preprocessor=preprocessor,
        sampling_strategy="original",
    )
    regressor = model_registry.get_model_spec(
        "random_forest",
        {},
        task_type="regression",
        random_state=1,
        use_gpu=False,
        preprocessor=preprocessor,
        sampling_strategy="original",
    )

    assert classifier.estimator.named_steps["model"].__class__.__name__ == "RandomForestClassifier"
    assert regressor.estimator.named_steps["model"].__class__.__name__ == "RandomForestRegressor"


@pytest.mark.parametrize("name", ("logistic_regression", "svm", "knn", "mlp"))
def test_scaled_models_build_pipelines_with_non_empty_search_spaces(name):
    spec = model_registry.get_model_spec(
        name,
        {"0": 90, "1": 10},
        random_state=42,
        use_gpu=False,
    )

    assert spec.name == name
    assert isinstance(spec.estimator, Pipeline)
    assert list(spec.estimator.named_steps) == ["scale", "model"]
    assert spec.search_space


def test_random_forest_baseline_contract_and_unscaled_split_are_preserved():
    spec = model_registry.get_model_spec(
        "random_forest",
        {"0": 90, "1": 10},
        random_state=7,
        use_gpu=False,
    )

    assert spec.name == "random_forest"
    assert not isinstance(spec.estimator, Pipeline)
    assert isinstance(spec.estimator, RandomForestClassifier)
    params = spec.estimator.get_params(deep=False)
    assert params["n_estimators"] == 300
    assert params["class_weight"] == "balanced"
    assert params["n_jobs"] == -1
    assert params["random_state"] == 7
    assert spec.feature_importance_kind == "tree"
    assert spec.search_space


def test_preprocessor_is_inside_each_model_pipeline_when_requested():
    frame = pd.DataFrame(
        {"slope": [1.0, 2.0, 3.0, 4.0], "landform": ["A", "B", "A", "C"]}
    )
    preprocessor = build_preprocessor(frame, ["slope", "landform"])

    tree = model_registry.get_model_spec(
        "random_forest",
        {"0": 2, "1": 2},
        random_state=42,
        use_gpu=False,
        preprocessor=preprocessor,
        sampling_strategy="original",
    )
    linear = model_registry.get_model_spec(
        "logistic_regression",
        {"0": 2, "1": 2},
        random_state=42,
        use_gpu=False,
        preprocessor=preprocessor,
        sampling_strategy="class_weight",
    )

    assert list(tree.estimator.named_steps) == ["preprocess", "model"]
    assert list(linear.estimator.named_steps) == ["preprocess", "scale", "model"]
    assert all(key.startswith("model__") for key in tree.search_space)
    assert tree.estimator.named_steps["model"].class_weight is None
    assert linear.estimator.named_steps["model"].class_weight == "balanced"


def test_xgboost_missing_dependency_is_lazy_and_has_stable_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "xgboost", None)
    reloaded = importlib.reload(model_registry)

    assert reloaded.available_model_names()[0] == "logistic_regression"
    random_forest = reloaded.get_model_spec(
        "random_forest",
        {"0": 90, "1": 10},
        random_state=42,
        use_gpu=False,
    )
    assert random_forest.name == "random_forest"

    with pytest.raises(ImportError, match="^xgboost dependency is not installed$"):
        reloaded.get_model_spec(
            "xgboost",
            {"0": 90, "1": 10},
            random_state=42,
            use_gpu=False,
        )


def test_lightgbm_missing_dependency_is_lazy_and_has_stable_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "lightgbm", None)
    reloaded = importlib.reload(model_registry)

    assert reloaded.available_model_names()[-1] == "mlp"
    logistic = reloaded.get_model_spec(
        "logistic_regression",
        {"0": 90, "1": 10},
        random_state=42,
        use_gpu=False,
    )
    assert isinstance(logistic.estimator, Pipeline)

    with pytest.raises(ImportError, match="^lightgbm dependency is not installed$"):
        reloaded.get_model_spec(
            "lightgbm",
            {"0": 90, "1": 10},
            random_state=42,
            use_gpu=False,
        )


def test_boosting_models_use_safe_finite_class_imbalance_ratio(monkeypatch):
    created = {}

    class FakeXGBClassifier:
        def __init__(self, **kwargs):
            created["xgboost"] = kwargs

    class FakeLGBMClassifier:
        def __init__(self, **kwargs):
            created["lightgbm"] = kwargs

    xgboost_module = ModuleType("xgboost")
    xgboost_module.XGBClassifier = FakeXGBClassifier
    lightgbm_module = ModuleType("lightgbm")
    lightgbm_module.LGBMClassifier = FakeLGBMClassifier

    monkeypatch.setitem(sys.modules, "xgboost", xgboost_module)
    monkeypatch.setitem(sys.modules, "lightgbm", lightgbm_module)
    reloaded = importlib.reload(model_registry)

    for name in ("xgboost", "lightgbm"):
        spec = reloaded.get_model_spec(
            name,
            {"0": 9, "1": 0},
            random_state=42,
            use_gpu=False,
        )
        kwargs = created[name]
        assert spec.search_space
        assert not isinstance(spec.estimator, Pipeline)
        assert math.isfinite(kwargs["scale_pos_weight"])


def test_boosting_models_use_sorted_binary_labels_for_scale_pos_weight(monkeypatch):
    created = {}

    class FakeXGBClassifier:
        def __init__(self, **kwargs):
            created["xgboost"] = kwargs

    class FakeLGBMClassifier:
        def __init__(self, **kwargs):
            created["lightgbm"] = kwargs

    xgboost_module = ModuleType("xgboost")
    xgboost_module.XGBClassifier = FakeXGBClassifier
    lightgbm_module = ModuleType("lightgbm")
    lightgbm_module.LGBMClassifier = FakeLGBMClassifier

    monkeypatch.setitem(sys.modules, "xgboost", xgboost_module)
    monkeypatch.setitem(sys.modules, "lightgbm", lightgbm_module)
    reloaded = importlib.reload(model_registry)

    xgboost_spec = reloaded.get_model_spec(
        "xgboost",
        {"2": 9, "3": 3},
        random_state=42,
        use_gpu=True,
    )
    lightgbm_spec = reloaded.get_model_spec(
        "lightgbm",
        {"no": 9, "yes": 3},
        random_state=42,
        use_gpu=True,
    )

    assert xgboost_spec.search_space
    assert created["xgboost"]["scale_pos_weight"] == pytest.approx(3.0)
    assert created["xgboost"]["device"] == "cuda"
    assert lightgbm_spec.search_space
    assert created["lightgbm"]["scale_pos_weight"] == pytest.approx(3.0)
    assert created["lightgbm"]["device_type"] == "gpu"


@pytest.mark.parametrize(
    "class_counts",
    (
        {"0": 9},
        {"0": 9, "1": 3, "2": 1},
        {"0": 9, "1": -1},
    ),
)
def test_boosting_models_reject_malformed_binary_class_counts(monkeypatch, class_counts):
    class FakeXGBClassifier:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    xgboost_module = ModuleType("xgboost")
    xgboost_module.XGBClassifier = FakeXGBClassifier

    monkeypatch.setitem(sys.modules, "xgboost", xgboost_module)
    reloaded = importlib.reload(model_registry)

    with pytest.raises(ValueError, match="binary class_counts"):
        reloaded.get_model_spec(
            "xgboost",
            class_counts,
            random_state=42,
            use_gpu=False,
        )


def test_knn_and_mlp_use_bounded_runtime_settings():
    knn = model_registry.get_model_spec(
        "knn",
        {"0": 90, "1": 10},
        random_state=42,
        use_gpu=False,
    )
    mlp = model_registry.get_model_spec(
        "mlp",
        {"0": 90, "1": 10},
        random_state=42,
        use_gpu=False,
    )

    assert isinstance(knn.estimator.named_steps["model"], KNeighborsClassifier)
    assert knn.estimator.named_steps["model"].get_params(deep=False)["n_jobs"] == 4
    assert isinstance(mlp.estimator.named_steps["model"], MLPClassifier)
    mlp_params = mlp.estimator.named_steps["model"].get_params(deep=False)
    assert mlp_params["early_stopping"] is True
    assert mlp_params["random_state"] == 42
    assert mlp_params["max_iter"] == 300


def test_svm_uses_balanced_probability_classifier():
    spec = model_registry.get_model_spec(
        "svm",
        {"0": 90, "1": 10},
        random_state=13,
        use_gpu=False,
    )

    classifier = spec.estimator.named_steps["model"]
    assert isinstance(classifier, SVC)
    params = classifier.get_params(deep=False)
    assert params["probability"] is True
    assert params["class_weight"] == "balanced"
    assert params["random_state"] == 13


def test_unknown_model_name_has_clear_error():
    with pytest.raises(ValueError, match="unknown model name: does-not-exist"):
        model_registry.get_model_spec(
            "does-not-exist",
            {"0": 90, "1": 10},
            random_state=42,
            use_gpu=False,
        )
