import pytest
from fastapi import HTTPException

from app.config import settings
from app.core import security


def test_session_roundtrip():
    session_id, token, ttl = security.issue_session()
    assert ttl == settings.session_token_ttl_seconds
    assert security.verify_session_token(token) == session_id


def test_sessions_are_unique():
    assert security.issue_session()[0] != security.issue_session()[0]


def test_tampered_token_rejected():
    session_id, token, _ = security.issue_session()
    parts = token.split(".")
    forged_session = ".".join([parts[0], "attacker-chosen-id", parts[2], parts[3]])
    assert security.verify_session_token(forged_session) is None
    forged_expiry = ".".join([parts[0], parts[1], str(int(parts[2]) + 99999), parts[3]])
    assert security.verify_session_token(forged_expiry) is None
    assert security.verify_session_token(token[:-1] + ("0" if token[-1] != "0" else "1")) is None


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "session_token_ttl_seconds", -10)
    _, token, _ = security.issue_session()
    assert security.verify_session_token(token) is None


@pytest.mark.parametrize("bad", [None, "", "garbage", "v1.a.b", "v2.a.123.sig", "v1.a.notint.sig"])
def test_garbage_tokens_rejected(bad):
    assert security.verify_session_token(bad) is None


def test_token_signed_with_other_secret_rejected(monkeypatch):
    _, token, _ = security.issue_session()
    monkeypatch.setattr(settings, "session_secret", "a-completely-different-secret-value-32chars!!")
    assert security.verify_session_token(token) is None


def test_admin_key_required():
    with pytest.raises(HTTPException) as e:
        security.require_admin(None)
    assert e.value.status_code == 401
    with pytest.raises(HTTPException) as e:
        security.require_admin("wrong-key")
    assert e.value.status_code == 401
    assert security.require_admin(settings.admin_api_key) is None


def test_admin_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "")
    with pytest.raises(HTTPException) as e:
        security.require_admin("anything")
    assert e.value.status_code == 503


def test_turnstile_skipped_when_unconfigured():
    assert security.verify_turnstile(None, "1.2.3.4") is True


def test_turnstile_fails_closed_without_token(monkeypatch):
    monkeypatch.setattr(settings, "turnstile_secret", "secret")
    assert security.verify_turnstile(None, "1.2.3.4") is False


def test_production_config_validation(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "admin_api_key", "short")
    monkeypatch.setattr(settings, "session_secret", "")
    monkeypatch.setattr(settings, "cors_origins", "*")
    problems = settings.production_problems()
    assert len(problems) == 3
    monkeypatch.setattr(settings, "admin_api_key", "x" * 30)
    monkeypatch.setattr(settings, "session_secret", "y" * 40)
    monkeypatch.setattr(settings, "cors_origins", "https://thehelloq.com, https://www.thehelloq.com")
    assert settings.production_problems() == []
    assert settings.cors_origin_list == ["https://thehelloq.com", "https://www.thehelloq.com"]
