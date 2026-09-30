"""Redis outage must degrade the chat, never crash it."""
import pytest
import redis

from app.config import settings
from app.core import cache


class DeadRedis:
    def __getattr__(self, name):
        raise redis.ConnectionError("redis is down")


@pytest.fixture(autouse=True)
def dead_redis(monkeypatch):
    monkeypatch.setattr(cache, "_client", DeadRedis())


def test_history_and_cache_degrade_quietly():
    assert cache.get_history("s1") == []
    cache.append_history("s1", "user", "hi")  # must not raise
    assert cache.get_cached_answer(None, [0.1] * 32) is None
    cache.set_cached_answer(None, [0.1] * 32, "answer")  # must not raise
    cache.bump_cache_version()  # must not raise


def test_rate_limit_still_enforced_in_process():
    results = [cache.check_rate_limit("fallback-user", limit=3, window_seconds=60) for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_rate_limit_identifiers_are_independent():
    assert cache.check_rate_limit("a", 1, 60) is True
    assert cache.check_rate_limit("b", 1, 60) is True
    assert cache.check_rate_limit("a", 1, 60) is False


def test_daily_message_cap_enforced(monkeypatch):
    monkeypatch.setattr(settings, "daily_llm_message_cap", 2)
    assert cache.reserve_llm_budget(100) is True
    assert cache.reserve_llm_budget(100) is True
    assert cache.reserve_llm_budget(100) is False


def test_daily_token_cap_enforced(monkeypatch):
    monkeypatch.setattr(settings, "daily_token_cap", 1000)
    assert cache.reserve_llm_budget(600) is True
    assert cache.reserve_llm_budget(600) is False  # would exceed 1000
    assert cache.reserve_llm_budget(300) is True


def test_usage_report_and_readiness():
    usage = cache.get_budget_usage()
    assert "llm_messages" in usage and usage["note"].startswith("redis down")
    assert cache.redis_ok() is False


def test_cache_key_depends_on_category_and_version():
    emb = [0.123456] * 32
    k1 = cache._cache_key("0", None, emb)
    assert k1 == cache._cache_key("0", None, emb)
    assert k1 != cache._cache_key("0", "pricing", emb)
    assert k1 != cache._cache_key("1", None, emb)
