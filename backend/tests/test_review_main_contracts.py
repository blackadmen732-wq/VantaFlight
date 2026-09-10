"""Regression tests for review-hardened application contracts."""
from __future__ import annotations

from fastapi.testclient import TestClient

from vantaflight.main import create_app


def test_course_identity_includes_safe_volume() -> None:
    app = create_app(db_path=":memory:")
    with TestClient(app) as client:
        common = {
            "seed": 17,
            "mode": "SLALOM",
            "gate_count": 5,
            "length": 50.0,
            "height": 12.0,
            "floor": 0.0,
            "ceiling": 12.0,
            "boundary_margin": 1.5,
        }
        first = client.post(
            "/api/courses/generate", json={**common, "width": 30.0}
        )
        second = client.post(
            "/api/courses/generate", json={**common, "width": 40.0}
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
