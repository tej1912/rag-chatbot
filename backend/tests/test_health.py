from fastapi.testclient import TestClient

from app.main import app


def test_healthz():
    client = TestClient(app)  # no context manager: DB startup is skipped
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
