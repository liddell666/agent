from __future__ import annotations

import hashlib
import json

import numpy as np
from sklearn.model_selection import train_test_split

from repro_runner.data import DatasetBundle


class ExperimentError(ValueError):
    """A safe experiment error that can be mapped to a stable API response."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def make_stratified_split(
    target: np.ndarray, test_size: float, random_state: int
) -> tuple[np.ndarray, np.ndarray]:
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


def test_set_digest(bundle: DatasetBundle, test_indices: np.ndarray) -> str:
    """Hash held-out feature rows plus labels without retaining their contents."""
    columns = [*bundle.feature_columns, bundle.target_column]
    held_out = bundle.frame.iloc[test_indices][columns]
    payload = {
        "columns": columns,
        "rows": [
            [_digest_value(value) for value in row]
            for row in held_out.itertuples(index=False, name=None)
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


test_set_digest.__test__ = False


def _digest_value(value: object) -> object:
    """Convert pandas/numpy scalars to JSON-safe values for deterministic hashing."""
    if isinstance(value, np.generic):
        return value.item()
    return value
