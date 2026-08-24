import numpy as np
import pandas as pd

from repro_runner.config import Settings
from repro_runner.data import load_dataset
from repro_runner.schemas import DatasetOptions
from repro_runner.split import make_stratified_split, make_task_split, test_set_digest


def test_shared_split_is_deterministic_and_stratified():
    target = np.array([0, 1] * 50)

    first = make_stratified_split(target, 0.2, 42)
    second = make_stratified_split(target, 0.2, 42)

    assert np.array_equal(first[0], second[0])
    assert np.array_equal(first[1], second[1])
    assert set(target[first[0]]) == {0, 1}
    assert set(target[first[1]]) == {0, 1}


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


def test_test_digest_changes_when_test_indices_change():
    frame = pd.DataFrame({"x": range(8), "Y_cls": [0, 1] * 4})
    bundle = load_dataset(
        frame.to_csv(index=False).encode(), DatasetOptions(), Settings()
    )

    first = test_set_digest(bundle, np.array([0, 1, 2]))
    second = test_set_digest(bundle, np.array([2, 3, 4]))

    assert first.startswith("sha256:")
    assert first != second
