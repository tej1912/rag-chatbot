import os

# must be set BEFORE the app modules are imported (settings read env at import)
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key-0123456789abcdef")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-0123456789abcdef0123")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")  # nothing listens here -> exercises the fallback path
os.environ.setdefault("GUARDRAILS_MODE", "basic")

import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.core import cache  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Fresh in-process counters per test and generous limits unless a test lowers them."""
    cache._mem_windows.clear()
    cache._mem_counters.clear()
    monkeypatch.setattr(settings, "rate_limit_new_session_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_ip_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_ip_per_day", 100000)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1000)
    yield
