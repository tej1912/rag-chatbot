"""
Redis does these jobs (see architecture doc):
1. conversation memory  - last N turns per session, TTL-bound
2. semantic cache        - cached answers for repeat questions
3. rate limiting          - sliding windows per session / per IP
4. daily spend caps       - global message + estimated-token counters

Every function is Redis-failure-safe: if Redis is unreachable the chat keeps
working in a degraded mode (no history, no cache, in-process rate limits)
instead of returning errors. In-process fallbacks are per-worker, so limits
are looser than with Redis - acceptable for a short outage.
"""
import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

import redis

from app.config import settings

logger = logging.getLogger("cache")

_client = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)

HISTORY_MAX_TURNS = 6

# ---- in-process fallbacks (used only while Redis is down) ----
_mem_lock = threading.Lock()
_mem_windows: dict[str, list[float]] = defaultdict(list)
_mem_counters: dict[str, float] = defaultdict(float)
_last_redis_warning = 0.0


def _warn_redis_down(exc: Exception) -> None:
    """Log Redis outages at most once per 30s so logs don't flood."""
    global _last_redis_warning
    now = time.time()
    if now - _last_redis_warning > 30:
        _last_redis_warning = now
        logger.warning("Redis unavailable, running degraded: %s", exc)


# ---- conversation memory ----

def get_history(session_id: str) -> list[dict]:
    try:
        raw = _client.get(f"history:{session_id}")
        return json.loads(raw) if raw else []
    except Exception as exc:
        _warn_redis_down(exc)
        return []


def append_history(session_id: str, role: str, content: str) -> None:
    try:
        history = get_history(session_id)
        history.append({"role": role, "content": content})
        history = history[-HISTORY_MAX_TURNS:]
        _client.set(f"history:{session_id}", json.dumps(history), ex=settings.session_ttl_seconds)
    except Exception as exc:
        _warn_redis_down(exc)


# ---- semantic cache ----
# Cache key = hash of (content version, category, coarse embedding of the
# STANDALONE question). Keying on the standalone (history-resolved) question
# means a follow-up like "how many days is that?" is never served another
# user's answer, and category keeps filtered searches apart. The version
# counter is bumped on every ingest/delete so stale answers vanish instantly.
# NOTE: rounding 32 dims is a cheap approximation - it behaves close to
# exact-match caching, not true semantic similarity.

CACHE_VERSION_KEY = "semcache_version"


def _cache_version() -> str:
    try:
        return _client.get(CACHE_VERSION_KEY) or "0"
    except Exception as exc:
        _warn_redis_down(exc)
        return "0"


def bump_cache_version() -> None:
    """Invalidate every cached answer (call after ingest/delete)."""
    try:
        _client.incr(CACHE_VERSION_KEY)
    except Exception as exc:
        _warn_redis_down(exc)


def _cache_key(version: str, category: str | None, embedding: list[float]) -> str:
    rounded = [round(x, 2) for x in embedding[:32]]
    payload = json.dumps([version, category or "", rounded])
    return "semcache:" + hashlib.sha256(payload.encode()).hexdigest()


def get_cached_answer(category: str | None, embedding: list[float]) -> str | None:
    try:
        return _client.get(_cache_key(_cache_version(), category, embedding))
    except Exception as exc:
        _warn_redis_down(exc)
        return None


def set_cached_answer(category: str | None, embedding: list[float], answer: str, ttl: int = 3600) -> None:
    try:
        _client.set(_cache_key(_cache_version(), category, embedding), answer, ex=ttl)
    except Exception as exc:
        _warn_redis_down(exc)


# ---- rate limiting (sliding window via sorted set) ----

def check_rate_limit(identifier: str, limit: int, window_seconds: int = 60) -> bool:
    """Returns True if the request is allowed. Counts this request."""
    key = f"ratelimit:{identifier}:{window_seconds}"
    now = time.time()
    window_start = now - window_seconds
    try:
        pipe = _client.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {f"{now}:{time.monotonic_ns()}": now})
        pipe.expire(key, window_seconds + 5)
        _, count, _, _ = pipe.execute()
        return count < limit
    except Exception as exc:
        _warn_redis_down(exc)
        with _mem_lock:
            hits = [t for t in _mem_windows[key] if t > window_start]
            allowed = len(hits) < limit
            hits.append(now)
            _mem_windows[key] = hits
            return allowed


# ---- global daily spend caps ----

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def reserve_llm_budget(estimated_tokens: int) -> bool:
    """
    Reserve one LLM-answered message + estimated tokens against today's
    global caps. Returns False when either cap is already reached, so the
    caller can refuse BEFORE paying for the call.
    """
    day = _today()
    msg_key, tok_key = f"budget:{day}:msgs", f"budget:{day}:tokens"
    try:
        msgs = _client.incr(msg_key)
        _client.expire(msg_key, 172800)
        if msgs > settings.daily_llm_message_cap:
            _client.decr(msg_key)
            return False
        tokens = int(_client.get(tok_key) or 0)
        if tokens + estimated_tokens > settings.daily_token_cap:
            _client.decr(msg_key)
            return False
        _client.incrby(tok_key, estimated_tokens)
        _client.expire(tok_key, 172800)
        return True
    except Exception as exc:
        _warn_redis_down(exc)
        with _mem_lock:
            if _mem_counters[msg_key] + 1 > settings.daily_llm_message_cap:
                return False
            if _mem_counters[tok_key] + estimated_tokens > settings.daily_token_cap:
                return False
            _mem_counters[msg_key] += 1
            _mem_counters[tok_key] += estimated_tokens
            return True


def get_budget_usage() -> dict:
    """Today's usage - for health/admin endpoints."""
    day = _today()
    msg_key, tok_key = f"budget:{day}:msgs", f"budget:{day}:tokens"
    try:
        return {
            "day": day,
            "llm_messages": int(_client.get(msg_key) or 0),
            "estimated_tokens": int(_client.get(tok_key) or 0),
            "message_cap": settings.daily_llm_message_cap,
            "token_cap": settings.daily_token_cap,
        }
    except Exception as exc:
        _warn_redis_down(exc)
        return {
            "day": day,
            "llm_messages": int(_mem_counters[msg_key]),
            "estimated_tokens": int(_mem_counters[tok_key]),
            "message_cap": settings.daily_llm_message_cap,
            "token_cap": settings.daily_token_cap,
            "note": "redis down, in-process counters",
        }


def redis_ok() -> bool:
    try:
        return bool(_client.ping())
    except Exception:
        return False
