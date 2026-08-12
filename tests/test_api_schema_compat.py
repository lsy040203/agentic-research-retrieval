"""Regression coverage for API schema compatibility."""

from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE_PARENT = str(Path(__file__).resolve().parents[2])
if WORKSPACE_PARENT not in sys.path:
    sys.path.insert(0, WORKSPACE_PARENT)


def test_server_imports_and_exposes_health_endpoint() -> None:
    """All routers' schema imports must allow the API app to start."""

    from os_agent_memory.api.server import app, health

    assert any(
        getattr(route, "path", None) == "/memory/health"
        for route in app.routes
    )

    assert health().data["status"] == "ok"


def test_knowledge_compat_wrapper_preserves_extractor_methods() -> None:
    """The legacy wrapper must not move methods out of KnowledgeExtractor."""

    from os_agent_memory.extractors.knowledge_extractor import (
        KnowledgeExtractor,
        extract_knowledge,
    )

    assert callable(KnowledgeExtractor.extract_templates)
    assert extract_knowledge([]) == []
