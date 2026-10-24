"""
Layer 1 guardrails: free, instant regex checks (injection, PII, length) plus
smalltalk replies. Runs BEFORE the optional NeMo LLM check (nemo_guard.py).
"""
import re

from app.config import settings

INJECTION_PATTERNS = [
    # "ignore / disregard / forget (all|any|the|your) (previous|prior|above) instructions|rules|prompt"
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,25}\b(instructions?|rules?|prompts?|guidelines?|restrictions?)\b",
    r"\bforget (everything|all|your|previous|the above)\b",
    r"\byou are now\b",
    r"\b(system|developer|hidden|initial) (prompt|message|instructions?)\b",
    r"\breveal your (prompt|instructions|rules)\b",
    r"\bact as (if|though)\b",
    r"\bpretend (to be|you are|you're)\b",
    r"\bjailbreak\b",
    r"\bdeveloper mode\b",
    r"\bdo anything now\b",
]
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)

PII_PATTERNS = [
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN-like
    r"\b(?:\d[ -]*?){13,16}\b",  # card-number-like
]
_PII_RE = re.compile("|".join(PII_PATTERNS))


def check_input(text: str) -> tuple[bool, str | None]:
    """Returns (allowed, reason_if_blocked)."""
    if _INJECTION_RE.search(text):
        return False, "prompt_injection_suspected"
    if len(text) > 4000:
        return False, "input_too_long"
    return True, None


def check_output(text: str) -> tuple[bool, str | None]:
    if _PII_RE.search(text):
        return False, "pii_leak_suspected"
    return True, None


FALLBACK_MESSAGE = (
    "I'm not confident I have the right information for that. "
    "I'll connect you with our support team so you get an accurate answer."
)


def low_confidence_fallback(confidence: float, threshold: float) -> bool:
    return confidence < threshold


# ---- smalltalk: canned replies, no retrieval / LLM cost ----
# Without this, "hi" scores as low-confidence retrieval and gets the
# "I'm not confident" fallback, which feels broken.
_GREETING_RE = re.compile(r"^\s*(hi+|hello+|hey+|hola|namaste|good (morning|afternoon|evening))\W*$", re.I)
_THANKS_RE = re.compile(r"^\s*(thanks?( you)?|thx|ty|thank u)\W*$", re.I)
_BYE_RE = re.compile(r"^\s*(bye+|goodbye|see you|cya)\W*$", re.I)


def smalltalk_reply(text: str) -> str | None:
    if _GREETING_RE.match(text):
        return (
            f"Hi! I can answer questions about {settings.bot_company_name} - {settings.bot_topics}. "
            "What would you like to know?"
        )
    if _THANKS_RE.match(text):
        return "You're welcome! Let me know if there's anything else I can help with."
    if _BYE_RE.match(text):
        return "Take care! I'm here whenever you have another question."
    return None
