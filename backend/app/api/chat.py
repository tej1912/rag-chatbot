from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core import cache
from app.core.security import client_ip, issue_session, verify_session_token, verify_turnstile
from app.db.session import get_db
from app.schemas.chat import ChatRequest, SessionRequest
from app.services.chat_service import DEBUG_SENTINEL, handle_chat

router = APIRouter(prefix="/v1", tags=["chat"])


def _too_many(detail: str = "rate_limited") -> HTTPException:
    return HTTPException(status_code=429, detail=detail, headers={"Retry-After": "30"})


@router.post("/session")
def create_session(req: SessionRequest, request: Request):
    """
    Issue a signed, server-generated session token. The client can no longer
    choose its own session id, so rotating ids no longer bypasses rate limits.
    Turnstile (if configured) is verified here, once per session.
    """
    ip = client_ip(request)
    if not cache.check_rate_limit(f"newsession:{ip}", settings.rate_limit_new_session_per_minute, 60):
        raise _too_many()
    if not verify_turnstile(req.turnstile_token, ip):
        raise HTTPException(status_code=403, detail="captcha_failed")
    session_id, token, ttl = issue_session()
    return {"session_id": session_id, "token": token, "expires_in": ttl}


@router.post("/chat")
def chat(
    req: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    x_session_token: str | None = Header(default=None),
):
    session_id = verify_session_token(x_session_token)
    if not session_id:
        raise HTTPException(status_code=401, detail="invalid_session")

    ip = client_ip(request)
    if (
        not cache.check_rate_limit(f"ip:{ip}", settings.rate_limit_ip_per_minute, 60)
        or not cache.check_rate_limit(f"ipday:{ip}", settings.rate_limit_ip_per_day, 86400)
        or not cache.check_rate_limit(f"sess:{session_id}", settings.rate_limit_per_minute, 60)
    ):
        raise _too_many()

    def event_stream():
        for delta in handle_chat(db, session_id, req.message, req.category, debug=req.debug):
            # debug payloads travel as a separate SSE event, not answer text
            if delta.startswith(DEBUG_SENTINEL):
                yield f"event: debug\ndata: {delta[len(DEBUG_SENTINEL):]}\n\n"
                continue
            # SSE data lines must be newline-free; escape so multi-line output
            # doesn't break the stream framing
            yield f"data: {delta.replace(chr(10), chr(92) + 'n')}\n\n"
        yield "event: done\ndata: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
