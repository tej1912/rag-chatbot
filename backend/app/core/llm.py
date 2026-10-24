"""
LLM router: picks a "fast" or "complex" tier, each backed by a configurable
provider (groq | claude). This is what lets you run 100% on Groq's free
tier during dev, then flip LLM_COMPLEX_PROVIDER=claude in .env for
production quality — no code changes.
"""
from collections.abc import Iterator

from app.config import settings

SYSTEM_PROMPT = f"""You are the support assistant for {settings.bot_company_name} ({settings.bot_topics}), embedded on its website.
Answer ONLY using the provided context. If the context doesn't contain the
answer, say you're not sure and offer to connect the user with support —
never invent an answer.

Formatting rules — the frontend renders basic markdown, so:
- Keep answers SHORT. 2-4 sentences for a simple question. Never pad with
  filler or repeat the question back.
- Use a bullet list (lines starting with "- ") only when listing 3+ items
  (like plan features) — plain sentences otherwise.
- Use **bold** only for a genuinely key term or number, sparingly.
- No headers, no long preambles, no "Certainly! Here's..." — answer
  directly.
"""

MAX_ANSWER_TOKENS = 350

REWRITE_SYSTEM_PROMPT = (
    "You rewrite the LAST user message of a support chat into ONE standalone question. "
    "Use earlier messages only to resolve references like 'it', 'that', 'the second one'. "
    "Keep the user's meaning. If the message is already standalone, return it unchanged. "
    "Output only the question - no quotes, no explanation."
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) - used for spend caps, not billing."""
    return max(1, len(text) // 4)


def _build_prompt(question: str, context_chunks: list[str], history: list[dict]) -> list[dict]:
    context_block = "\n\n---\n\n".join(context_chunks) if context_chunks else "(no relevant context found)"
    messages = list(history)
    messages.append(
        {
            "role": "user",
            "content": f"Context:\n{context_block}\n\nQuestion: {question}",
        }
    )
    return messages


def route_tier(question: str, retrieval_score: float) -> str:
    """
    Simple heuristic router — swap for a real classifier or a cheap LLM
    call if you need smarter routing later.
    Complex tier triggers on: long question, low retrieval confidence
    (model needs to reason more / hedge), or explicit escalation language.
    """
    long_question = len(question) > 220
    low_confidence = retrieval_score < settings.retrieval_confidence_threshold
    escalation_words = ("compare", "explain why", "difference between", "recommend")
    needs_reasoning = any(w in question.lower() for w in escalation_words)

    if long_question or low_confidence or needs_reasoning:
        return "complex"
    return "fast"


def stream_completion(
    question: str,
    context_chunks: list[str],
    history: list[dict],
    tier: str,
) -> Iterator[str]:
    """Yields text deltas for SSE streaming."""
    provider = settings.llm_fast_provider if tier == "fast" else settings.llm_complex_provider
    messages = _build_prompt(question, context_chunks, history)

    if provider == "claude":
        yield from _stream_claude(messages, tier)
    else:
        yield from _stream_groq(messages, tier)


def _stream_groq(messages: list[dict], tier: str) -> Iterator[str]:
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key, timeout=settings.llm_timeout_seconds, max_retries=1)
    model = settings.groq_fast_model if tier == "fast" else settings.groq_complex_model

    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        stream=True,
        temperature=0.2,
        max_tokens=MAX_ANSWER_TOKENS,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def _stream_claude(messages: list[dict], tier: str) -> Iterator[str]:
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=1)
    model = settings.claude_fast_model if tier == "fast" else settings.claude_complex_model

    with client.messages.stream(
        model=model,
        max_tokens=MAX_ANSWER_TOKENS,
        system=SYSTEM_PROMPT,
        messages=messages,
        temperature=0.2,
    ) as stream:
        for text in stream.text_stream:
            yield text


def complete_short(system: str, user: str, max_tokens: int = 100) -> str:
    """Small non-streaming completion on the FAST tier (query rewriting etc.)."""
    provider = settings.llm_fast_provider
    if provider == "claude":
        from anthropic import Anthropic

        client = Anthropic(
            api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=1
        )
        resp = client.messages.create(
            model=settings.claude_fast_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=0,
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    from groq import Groq

    client = Groq(api_key=settings.groq_api_key, timeout=settings.llm_timeout_seconds, max_retries=1)
    resp = client.chat.completions.create(
        model=settings.groq_fast_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()


def rewrite_standalone(question: str, history: list[dict]) -> str:
    """
    Turn a follow-up ("how many days is that?") into a standalone question
    ("how many days is the refund window?") so retrieval and the answer cache
    work. No history -> returned unchanged with zero LLM calls. Any failure
    falls back to the original question. The result is used ONLY for retrieval
    and cache keys, never shown to the user or fed to the answering prompt.
    """
    if not history:
        return question
    convo = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:300]}"
        for m in history[-4:]
    )
    user_prompt = f"Conversation:\n{convo}\n\nLast user message: {question}\n\nStandalone question:"
    try:
        rewritten = complete_short(REWRITE_SYSTEM_PROMPT, user_prompt, max_tokens=80)
    except Exception:
        return question
    rewritten = rewritten.strip().splitlines()[0].strip().strip('"') if rewritten.strip() else ""
    if not rewritten or len(rewritten) > 400:
        return question
    return rewritten
