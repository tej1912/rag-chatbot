from fastapi.testclient import TestClient

from app.api import chat as chat_api
from app.config import settings
from app.core import security
from app.db.session import get_db
from app.main import app
from app.services.chat_service import DEBUG_SENTINEL

client = TestClient(app)  # not used as a context manager -> startup/DB init is skipped
ADMIN = {"X-Admin-Key": settings.admin_api_key}


def new_token():
    r = client.post("/v1/session", json={})
    assert r.status_code == 200
    return r.json()["token"]


def test_liveness():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_session_issue():
    body = client.post("/v1/session", json={}).json()
    assert body["expires_in"] == settings.session_token_ttl_seconds
    assert security.verify_session_token(body["token"]) == body["session_id"]


def test_new_session_rate_limited(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_new_session_per_minute", 2)
    codes = [client.post("/v1/session", json={}).status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]


def test_session_requires_captcha_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "turnstile_secret", "secret")
    assert client.post("/v1/session", json={}).status_code == 403


def test_chat_requires_valid_session():
    assert client.post("/v1/chat", json={"message": "hi"}).status_code == 401
    r = client.post("/v1/chat", json={"message": "hi"}, headers={"X-Session-Token": "v1.forged.9999999999.abc"})
    assert r.status_code == 401


def test_chat_validates_message():
    h = {"X-Session-Token": new_token()}
    assert client.post("/v1/chat", json={"message": ""}, headers=h).status_code == 422
    assert client.post("/v1/chat", json={"message": "x" * 2001}, headers=h).status_code == 422


def _stub_chat(monkeypatch, chunks):
    monkeypatch.setattr(chat_api, "handle_chat", lambda db, sid, msg, cat, debug=False: iter(chunks))
    app.dependency_overrides[get_db] = lambda: None


def test_chat_sse_framing(monkeypatch):
    _stub_chat(monkeypatch, ["Hello\nworld", DEBUG_SENTINEL + '{"confidence": 0.9}', "!"])
    try:
        r = client.post("/v1/chat", json={"message": "hi there"}, headers={"X-Session-Token": new_token()})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "data: Hello\\nworld\n\n" in r.text  # newline escaped, frame intact
    assert 'event: debug\ndata: {"confidence": 0.9}\n\n' in r.text
    assert "data: !\n\n" in r.text
    assert r.text.rstrip().endswith("data: [DONE]")


def test_chat_per_session_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    _stub_chat(monkeypatch, ["ok"])
    h = {"X-Session-Token": new_token()}
    try:
        codes = [client.post("/v1/chat", json={"message": "hello there"}, headers=h).status_code for _ in range(4)]
    finally:
        app.dependency_overrides.clear()
    assert codes == [200, 200, 429, 429]


def test_rotating_sessions_does_not_dodge_ip_limit(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_ip_per_minute", 3)
    _stub_chat(monkeypatch, ["ok"])
    try:
        codes = [
            client.post("/v1/chat", json={"message": "hello there"}, headers={"X-Session-Token": new_token()}).status_code
            for _ in range(5)
        ]
    finally:
        app.dependency_overrides.clear()
    assert codes == [200, 200, 200, 429, 429]


def test_429_carries_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    r = client.post("/v1/chat", json={"message": "hello there"}, headers={"X-Session-Token": new_token()})
    assert r.status_code == 429 and r.headers["retry-after"] == "30"


def test_admin_endpoints_need_key():
    assert client.post("/v1/ingest", json={"title": "t", "text": "x"}).status_code == 401
    assert client.get("/v1/documents").status_code == 401
    assert client.delete("/v1/documents/00000000-0000-0000-0000-000000000000").status_code == 401
    assert client.get("/v1/admin/usage").status_code == 401
    assert client.post("/v1/ingest", json={"title": "t", "text": "x"}, headers={"X-Admin-Key": "nope"}).status_code == 401


def test_admin_key_passes_auth_layer():
    # valid key + invalid body -> 422 means the auth dependency let it through
    assert client.post("/v1/ingest", json={"title": ""}, headers=ADMIN).status_code == 422
    r = client.get("/v1/admin/usage", headers=ADMIN)
    assert r.status_code == 200 and "llm_messages" in r.json()


def test_admin_unconfigured_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "")
    assert client.get("/v1/admin/usage", headers=ADMIN).status_code == 503


def test_security_headers_present():
    r = client.get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_cors_preflight_allows_session_header():
    r = client.options(
        "/v1/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-session-token",
        },
    )
    assert r.status_code == 200
    assert "x-session-token" in r.headers["access-control-allow-headers"].lower()
