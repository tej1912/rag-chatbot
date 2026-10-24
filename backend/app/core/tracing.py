"""
Optional Langfuse tracing (free cloud tier or self-hosted). OFF by default.

One Langfuse trace per chat message, built AFTER the answer is finished so the
hot path only collects a few values in memory:
  trace  "chat"        input/output, session id, outcome tag, metadata
  span   "retrieval"   chunks retrieved + best similarity (confidence)
  gen    "answer"      model, tier, estimated tokens, latency

Rules this module follows:
- Disabled / misconfigured / Langfuse down -> everything is a silent no-op.
  Tracing must never break or slow a chat answer (the SDK sends in a
  background thread).
- LANGFUSE_CAPTURE_CONTENT=false sends only metadata (no question/answer
  text) - use this if you don't want visitor messages leaving your server.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import settings

logger = logging.getLogger("tracing")

MAX_TEXT = 2000

_client = None
_client_lock = threading.Lock()
_disabled_logged = False


def _get_client():
    """Lazy singleton. Returns None when tracing is off or cannot start."""
    global _client, _disabled_logged
    if not settings.langfuse_enabled:
        return None
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            if not _disabled_logged:
                _disabled_logged = True
                logger.error("LANGFUSE_ENABLED=true but LANGFUSE_PUBLIC_KEY/SECRET_KEY missing - tracing disabled")
            return None
        try:
            from langfuse import Langfuse

            _client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            logger.info("Langfuse tracing on (host=%s, capture_content=%s)",
                        settings.langfuse_host, settings.langfuse_capture_content)
        except Exception:
            logger.exception("Langfuse init failed - tracing disabled")
            _disabled_logged = True
            return None
    return _client


def _clip(text: str | None) -> str | None:
    if text is None:
        return None
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "..."


class Trace:
    """Collects facts about one chat message; sends them in finish()."""

    def __init__(self, session_id: str, message: str):
        self.session_id = session_id
        self.message = message
        self.meta: dict = {}
        self.chunks: list[dict] | None = None
        self.llm: dict | None = None
        self._t0 = time.time()
        self._marks: dict[str, float] = {}

    # -- cheap, in-memory only (safe on the hot path) --
    def note(self, **meta) -> None:
        self.meta.update(meta)

    def mark(self, name: str) -> None:
        self._marks[name] = time.time()

    def retrieval(self, chunks: list[dict], confidence: float) -> None:
        self.chunks = chunks
        self.meta["confidence"] = round(confidence, 4)

    def generation(self, model: str, tier: str, input_tokens: int) -> None:
        self.llm = {"model": model, "tier": tier, "input_tokens": input_tokens}

    # -- send --
    def finish(self, output: str, outcome: str) -> None:
        client = _get_client()
        if client is None:
            return
        try:
            self._send(client, output, outcome)
        except Exception:
            logger.exception("Langfuse send failed (ignored)")

    def _send(self, client, output: str, outcome: str) -> None:
        from app.core.llm import estimate_tokens

        capture = settings.langfuse_capture_content
        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=time.time() - self._t0)

        trace = client.trace(
            name="chat",
            session_id=self.session_id,
            input=_clip(self.message) if capture else None,
            output=_clip(output) if capture else None,
            metadata={**self.meta, "outcome": outcome, "latency_ms": int((time.time() - self._t0) * 1000)},
            tags=[outcome, settings.app_env],
        )

        if self.chunks is not None:
            r_start = self._marks.get("retrieval_start")
            r_end = self._marks.get("retrieval_end")
            trace.span(
                name="retrieval",
                start_time=datetime.fromtimestamp(r_start, timezone.utc) if r_start else start,
                end_time=datetime.fromtimestamp(r_end, timezone.utc) if r_end else end,
                input=_clip(self.meta.get("standalone")) if capture else None,
                output=self.chunks,  # doc title, section, score, short preview
                metadata={"confidence": self.meta.get("confidence")},
            )

        if self.llm is not None:
            g_start = self._marks.get("llm_start")
            g_end = self._marks.get("llm_end")
            trace.generation(
                name="answer",
                model=self.llm["model"],
                start_time=datetime.fromtimestamp(g_start, timezone.utc) if g_start else start,
                end_time=datetime.fromtimestamp(g_end, timezone.utc) if g_end else end,
                output=_clip(output) if capture else None,
                usage={"input": self.llm["input_tokens"], "output": estimate_tokens(output)},
                metadata={"tier": self.llm["tier"], "token_counts": "estimated (~4 chars/token)"},
            )


def begin(session_id: str, message: str) -> Trace:
    return Trace(session_id, message)


def flush() -> None:
    """Send anything still queued (call on shutdown)."""
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            logger.exception("Langfuse flush failed (ignored)")
