"""D1 research HTTP endpoints remain intentionally private until an auth boundary exists."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


WORKSPACE_PARENT = str(Path(__file__).resolve().parents[2])
if WORKSPACE_PARENT not in sys.path:
    sys.path.insert(0, WORKSPACE_PARENT)

from os_agent_memory.api.routes_research import router
from os_agent_memory.api.server import app


def test_public_app_does_not_register_research_endpoints() -> None:
    client = TestClient(app)
    approval = client.post(
        "/research/approvals",
        json={"requester_id": "untrusted"},
    )
    verification = client.get("/research/verifications/unknown")

    assert approval.status_code == 404
    assert verification.status_code == 404
    assert all(
        not getattr(route, "path", "").startswith("/research")
        for route in app.routes
    )


def test_research_router_remains_available_for_future_internal_wiring() -> None:
    paths = {route.path for route in router.routes}

    assert paths == {
        "/research/approvals",
        "/research/approvals/{package_id}",
        "/research/approvals/{package_id}/decision",
        "/research/verifications",
        "/research/verifications/{run_id}",
    }


def test_routes_research_has_no_execution_dependencies() -> None:
    source = Path("api/routes_research.py").read_text(encoding="utf-8")
    forbidden = (
        "import subprocess",
        "from subprocess",
        "os.system",
        "import httpx",
        "import requests",
        "from openai",
    )
    assert not any(term in source.lower() for term in forbidden)
