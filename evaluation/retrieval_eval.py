"""Compatibility entry point for first-stage retrieval evaluation."""

from __future__ import annotations


def run_retrieval_eval(dataset_path: str | None = None) -> dict[str, float]:
    """Return the first-stage metric expected by the API route."""

    del dataset_path
    return {"recall_at_k": 0.0}
