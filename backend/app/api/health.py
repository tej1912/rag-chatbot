from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from app.core import cache
from app.core.security import require_admin
from app.db.session import engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    """Liveness: process is up. Cheap, no dependencies."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(response: Response):
    """Readiness: can we actually serve? DB is required; Redis only degrades."""
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    redis_ok = cache.redis_ok()

    if not db_ok:
        response.status_code = 503
    status = "ok" if db_ok and redis_ok else ("degraded" if db_ok else "down")
    return {"status": status, "database": db_ok, "redis": redis_ok}


@router.get("/v1/admin/usage", dependencies=[Depends(require_admin)], tags=["admin"])
def usage():
    """Today's global LLM message count + estimated tokens vs. caps."""
    return cache.get_budget_usage()
