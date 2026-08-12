"""Compatibility entry point for first-stage forgetting evaluation."""

from __future__ import annotations


def run_forgetting_eval(dataset_path: str | None = None) -> dict[str, float]:
    """Return the first-stage forgetting metric expected by the API route.

    The current first-stage evaluator has no dataset-backed implementation yet;
    retain the route contract with its neutral metric value.
    """

    del dataset_path
    return {"forget_success_rate": 0.0}
