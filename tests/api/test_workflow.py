from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.app import create_app
from packages.configuration.settings import Settings


def test_public_demo_api_is_not_mounted() -> None:
    app = create_app(Settings(infrastructure_checks=False))
    with TestClient(app) as client:
        response = client.get("/api/v1/demo/workflow/replays/approved")

    assert response.status_code == 404
