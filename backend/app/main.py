import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import chat, health, ingest
from app.config import DEV_SESSION_SECRET, settings
from app.core import nemo_guard, tracing
from app.db import models  # noqa: F401  (registers tables on Base.metadata)
from app.db.session import Base, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("main")

INIT_LOCK_ID = 727272  # arbitrary constant for pg advisory lock


def init_db() -> None:
    """
    Create pgvector extension + tables. With several uvicorn workers all
    starting at once, an advisory lock makes them take turns instead of
    racing on CREATE TABLE. For real schema changes, move to Alembic.
    """
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": INIT_LOCK_ID})
        try:
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.commit()
            except Exception:
                conn.rollback()
                logger.warning(
                    "Could not CREATE EXTENSION vector automatically - "
                    "enable pgvector on your Postgres host if table creation fails next"
                )
            Base.metadata.create_all(bind=conn)
            conn.commit()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": INIT_LOCK_ID})
            conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.is_production:
        problems = settings.production_problems()
        if problems:
            raise RuntimeError("Refusing to start in production: " + "; ".join(problems))
    else:
        if not settings.admin_api_key:
            logger.warning("ADMIN_API_KEY not set - ingest/delete endpoints will refuse all requests")
        if settings.effective_session_secret == DEV_SESSION_SECRET:
            logger.warning("SESSION_SECRET not set - using an insecure dev secret (fine locally only)")

    init_db()

    if nemo_guard.is_enabled():
        # build NeMo rails off the request path so user #1 doesn't pay for it
        threading.Thread(target=nemo_guard.warmup, daemon=True, name="nemo-warmup").start()

    logger.info(
        "started env=%s guardrails=%s llm_fast=%s llm_complex=%s embeddings=%s",
        settings.app_env,
        settings.guardrails_mode,
        settings.llm_fast_provider,
        settings.llm_complex_provider,
        settings.embedding_provider,
    )
    yield
    tracing.flush()


app = FastAPI(
    title="RAG Chat Assistant",
    version="0.2.0",
    lifespan=lifespan,
    # interactive API docs help in dev, but shouldn't be public in production
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Session-Token", "X-Admin-Key"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


app.include_router(health.router)
app.include_router(chat.router)
app.include_router(ingest.router)
