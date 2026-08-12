"""Compatibility entry point for first-stage preference evaluation."""

from __future__ import annotations


def run_preference_eval(dataset_path: str | None = None) -> dict[str, float]:
    """Return the first-stage metrics expected by the API route."""

    del dataset_path
    return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
