import json
import logging
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.config import settings
from app.core import cache, guardrails, llm, nemo_guard, tracing
from app.core.embeddings import embed_query
from app.core.retrieval import search
from app.db.models import ConversationMessage

logger = logging.getLogger("chat_service")

REFUSAL_MESSAGE = f"I can't help with that request. Please ask me something about {settings.bot_company_name}."
GENERIC_ERROR_MESSAGE = "Sorry, something went wrong on my end. Please try again in a moment."
UPSTREAM_BUSY_MESSAGE = (
    "I'm getting rate-limited by an upstream service right now - please wait a few seconds and try again."
)
BUDGET_MESSAGE = (
    "I'm getting a lot of questions right now and can't answer more at the moment. "
    "Please try again later, or contact our support team directly."
)

DEBUG_SENTINEL = "\x00DEBUG\x00"


def handle_chat(
    db: Session, session_id: str, message: str, category: str | None, debug: bool = False
) -> Iterator[str]:
    """
    Public entry point: runs the pipeline and, when Langfuse is enabled,
    records one trace per message after the answer has been streamed.
    Tracing is a silent no-op if disabled and can never break an answer.
    """
    trace = tracing.begin(session_id, message)
    trace.note(category=category)
    out: list[str] = []
    try:
        for delta in _handle_chat(db, session_id, message, category, debug, trace):
            if not delta.startswith(DEBUG_SENTINEL):
                out.append(delta)
            yield delta
    finally:
        # also runs if the client disconnects mid-stream
        trace.finish("".join(out), trace.meta.get("outcome", "unknown"))


def _handle_chat(
    db: Session, session_id: str, message: str, category: str | None, debug: bool, trace
) -> Iterator[str]:
    """
    Generator yielding response text chunks for SSE.

    Order (cheapest / most security-critical first):
      1. regex input guard          2. smalltalk (canned, free)
      3. NeMo LLM input guard       4. follow-up -> standalone question
      5. embed + answer cache       6. hybrid retrieval
      7. low-confidence fallback    8. daily budget reservation
      9. tiered LLM stream         10. output guard, cache, history, persist

    Rate limits (session/IP) are enforced in the API layer before this runs.
    Every external call is wrapped so a failure yields a visible message
    instead of silently ending the stream.
    """
    # 1. regex guard
    allowed, reason = guardrails.check_input(message)
    if not allowed:
        logger.info("input blocked by regex guard: %s", reason)
        trace.note(outcome="blocked_regex", reason=reason)
        yield REFUSAL_MESSAGE
        return

    # 2. smalltalk
    reply = guardrails.smalltalk_reply(message)
    if reply:
        trace.note(outcome="smalltalk")
        yield reply
        return

    # 3. NeMo Guardrails (LLM self-check) - no-op unless GUARDRAILS_MODE=nemo
    allowed, reason = nemo_guard.check_input(message)
    if not allowed:
        logger.info("input blocked by NeMo guard: %s", reason)
        trace.note(outcome="blocked_nemo", reason=reason)
        yield REFUSAL_MESSAGE
        return

    # 4. standalone question (follow-ups only; costs 0 calls when no history)
    history = cache.get_history(session_id)
    standalone = llm.rewrite_standalone(message, history)
    trace.note(standalone=standalone)

    # 5. embed + answer cache (keyed on the standalone question + category)
    try:
        query_embedding = embed_query(standalone)
    except Exception:
        logger.exception("embed_query failed for message=%r", message)
        trace.note(outcome="error_embedding")
        yield UPSTREAM_BUSY_MESSAGE
        return

    cached = cache.get_cached_answer(category, query_embedding)
    if cached:
        trace.note(outcome="cache_hit")
        yield cached
        cache.append_history(session_id, "user", message)
        cache.append_history(session_id, "assistant", cached)
        _persist(db, session_id, message, cached, retrieval_score=None)
        return

    # 6. retrieval
    trace.mark("retrieval_start")
    try:
        results, confidence = search(db, standalone, category, query_embedding)
    except Exception:
        logger.exception("retrieval failed for message=%r", message)
        trace.note(outcome="error_retrieval")
        yield GENERIC_ERROR_MESSAGE
        return

    trace.mark("retrieval_end")
    trace.retrieval(
        [{"doc": r.document_title, "section": r.header_context, "score": round(r.score, 4),
          "preview": r.content[:100]} for r in results],
        confidence,
    )

    # observability: exactly what was retrieved, for every question
    logger.info(
        "retrieval | question=%r standalone=%r confidence=%.3f chunks=%s",
        message,
        standalone,
        confidence,
        [
            {"doc": r.document_title, "section": r.header_context, "score": round(r.score, 4),
             "preview": r.content[:100]}
            for r in results
        ],
    )

    # 7. low-confidence fallback - skip the LLM entirely
    if not results or guardrails.low_confidence_fallback(confidence, settings.retrieval_confidence_threshold):
        trace.note(outcome="low_confidence_fallback")
        yield guardrails.FALLBACK_MESSAGE
        _persist(db, session_id, message, guardrails.FALLBACK_MESSAGE, retrieval_score=confidence)
        return

    tier = llm.route_tier(message, confidence)
    context_chunks = [
        f"[{r.document_title}] {r.header_context + ': ' if r.header_context else ''}{r.content}"
        for r in results
    ]

    # 8. global daily spend cap - refuse BEFORE paying for the call
    estimated_in = llm.estimate_tokens(
        llm.SYSTEM_PROMPT + message + "".join(context_chunks) + "".join(m["content"] for m in history)
    )
    if not cache.reserve_llm_budget(estimated_in + llm.MAX_ANSWER_TOKENS):
        logger.warning("daily LLM budget exhausted; refusing message")
        trace.note(outcome="budget_exhausted")
        yield BUDGET_MESSAGE
        return

    if debug and not settings.is_production:
        yield DEBUG_SENTINEL + json.dumps(
            {
                "confidence": round(confidence, 4),
                "tier": tier,
                "standalone": standalone,
                "chunks": [
                    {"doc": r.document_title, "section": r.header_context, "score": round(r.score, 4)}
                    for r in results
                ],
            }
        )

    # 9. stream from the LLM
    full_answer = ""
    model = settings.groq_fast_model if tier == "fast" else settings.groq_complex_model
    if (settings.llm_fast_provider if tier == "fast" else settings.llm_complex_provider) == "claude":
        model = settings.claude_fast_model if tier == "fast" else settings.claude_complex_model
    trace.generation(model, tier, estimated_in)
    trace.mark("llm_start")
    try:
        for delta in llm.stream_completion(message, context_chunks, history, tier):
            full_answer += delta
            yield delta
    except Exception:
        logger.exception("LLM streaming failed (tier=%s) for message=%r", tier, message)
        trace.note(outcome="error_llm")
        if not full_answer:
            yield UPSTREAM_BUSY_MESSAGE
        return  # partial answer already streamed stays visible; nothing cached

    trace.mark("llm_end")
    if not full_answer.strip():
        trace.note(outcome="error_empty_answer")
        yield GENERIC_ERROR_MESSAGE
        return

    # 10. output guard (post-hoc: gates caching; already streamed this turn)
    ok, _ = guardrails.check_output(full_answer)
    trace.note(outcome="answered", output_guard_ok=ok)
    if ok:
        cache.set_cached_answer(category, query_embedding, full_answer)

    cache.append_history(session_id, "user", message)
    cache.append_history(session_id, "assistant", full_answer)
    _persist(db, session_id, message, full_answer, retrieval_score=confidence)


def _persist(db: Session, session_id: str, question: str, answer: str, retrieval_score: float | None) -> None:
    try:
        db.add(ConversationMessage(session_id=session_id, role="user", content=question))
        db.add(
            ConversationMessage(
                session_id=session_id, role="assistant", content=answer, retrieval_score=retrieval_score
            )
        )
        db.commit()
    except Exception:
        # never lose the user's answer because history logging failed
        logger.exception("failed to persist conversation for session=%s", session_id)
        db.rollback()
