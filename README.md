# RAG Chat Assistant

Retrieval-augmented support chatbot for a website. Free-tier friendly in
development, with a security/cost-control layer for production.

## How a message flows

```
widget ──POST /v1/session (captcha once)──► signed session token
widget ──POST /v1/chat + X-Session-Token──► FastAPI
   API layer : token check → per-IP/min → per-IP/day → per-session/min   (429 if exceeded)
   1 regex guard        2 smalltalk (canned, free)
   3 NeMo Guardrails    (optional LLM self-check, GUARDRAILS_MODE=nemo)
   4 follow-up → standalone question (1 tiny LLM call, only if history exists)
   5 embed (local) + answer cache
   6 hybrid retrieval: pgvector cosine + Postgres full-text, fused
   7 confidence below threshold → "not sure" WITHOUT calling the LLM
   8 global daily message/token cap (refuses before paying)
   9 tiered LLM (Groq / Claude) streamed over SSE
  10 output PII check → cache → Redis history → Postgres log
```

## Quick start (local)

1. `cp .env.example .env` and fill in:
   - `GROQ_API_KEY` (free: console.groq.com)
   - `ADMIN_API_KEY` — generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
2. `docker compose up -d --build` (first build is slow: it compiles NeMo's
   `annoy` dependency and bakes the embedding model into the image)
3. `curl http://localhost:8000/readyz` → `{"status":"ok",...}`
4. Ingest content (PowerShell):
   ```powershell
   $h = @{ "X-Admin-Key" = "YOUR_ADMIN_API_KEY" }
   $body = @{ title="Pricing"; url="https://yoursite.com/pricing"; category="pricing"; text="# Free plan`n..." } | ConvertTo-Json
   Invoke-RestMethod -Uri http://localhost:8000/v1/ingest -Method Post -Headers $h -ContentType "application/json" -Body $body
   ```
   Re-running with the same `url` **replaces** the old version (no duplicates)
   and clears cached answers. Write section titles as `# Heading` lines.
5. Open `frontend/widget/index.html` and chat.

Rebuild after code changes with `docker compose down` then `docker compose up -d --build`.
Use `down -v` ONLY when you want to wipe all ingested data.

## API

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /v1/session` | captcha (if configured) | issue signed session token |
| `POST /v1/chat` | `X-Session-Token` | SSE answer stream |
| `POST /v1/ingest` | `X-Admin-Key` | add/replace a document (text) |
| `POST /v1/ingest/pdf` | `X-Admin-Key` | add/replace from PDF (multipart, ≤20MB) |
| `GET /v1/documents` | `X-Admin-Key` | list documents + chunk counts |
| `DELETE /v1/documents/{id}` | `X-Admin-Key` | delete a document |
| `GET /v1/admin/usage` | `X-Admin-Key` | today's LLM messages + est. tokens vs caps |
| `GET /healthz` / `GET /readyz` | none | liveness / readiness (DB required, Redis = degraded) |

PDF upload: `curl.exe -X POST http://localhost:8000/v1/ingest/pdf -H "X-Admin-Key: KEY" -F "file=@C:\path\doc.pdf" -F "title=Terms" -F "category=legal"`

## Security model

- **Admin endpoints fail closed**: no `ADMIN_API_KEY` configured → they refuse everything.
- **Server-issued sessions**: clients can't invent session ids, so rotating ids
  doesn't dodge limits. Optional Cloudflare Turnstile is checked once per session.
- **Rate limits**: per session, per IP (minute + day), per IP for new sessions.
  Redis-backed; falls back to in-process counters if Redis is down.
- **Global daily spend cap** (`DAILY_LLM_MESSAGE_CAP`, `DAILY_TOKEN_CAP`):
  the hard stop on your LLM bill. Trade-off: a flood can exhaust the cap and
  block real users for the day — the per-IP limits and captcha are what keep
  that hard to do.
- **`APP_ENV=production` refuses to start** unless `ADMIN_API_KEY` (24+),
  `SESSION_SECRET` (32+) and a non-`*` `CORS_ORIGINS` are set. `/docs` is off in production.
- Debug retrieval info is dev-only (ignored in production).

### Guardrails: `basic` vs `nemo`

- `GUARDRAILS_MODE=basic` (default): regex only. Free, instant, catches literal attacks.
- `GUARDRAILS_MODE=nemo`: regex first, then **NVIDIA NeMo Guardrails**
  (open-source, free) runs an LLM self-check on every message (~200-400ms).
  The software is free; the **extra LLM call per message is not free** — by
  default it uses Groq's free tier, so it uses up your Groq rate limit twice as fast.
  Failures follow `GUARD_FAIL_MODE` (`open` = allow, `closed` = block).
  The policy prompt lives in `backend/app/core/nemo_guard.py`; it is built from
  the `BOT_COMPANY_NAME` / `BOT_COMPANY_SUMMARY` settings so on-topic questions
  about your products are never over-blocked.
  Only the current message is checked, not multi-turn attack chains.

## Deploy checklist (Railway / Render / Fly)

1. Push repo to GitHub; deploy `backend/` (uses the Dockerfile; honours `$PORT`).
2. Add managed Postgres (pgvector) + Redis; set `DATABASE_URL`, `REDIS_URL`.
   The app runs `CREATE EXTENSION vector` itself; if your host forbids it, enable pgvector in the host dashboard.
3. Set env vars: `APP_ENV=production`, `ADMIN_API_KEY`, `SESSION_SECRET`,
   `CORS_ORIGINS=https://yoursite.com`, `GROQ_API_KEY` / `ANTHROPIC_API_KEY`
   (+ `LLM_FAST_PROVIDER=claude` for reliability), `WEB_CONCURRENCY=1` on ≤512MB hosts.
4. Optional: Turnstile — create a widget at dash.cloudflare.com, put the
   secret in `TURNSTILE_SECRET`, the public site key in `turnstileSiteKey` in the widget config.
5. Ingest your content against the production URL with your admin key.
6. Point the widget `apiBase` at the deployed URL. Watch logs (`retrieval | ... confidence=`) for a week and tune `RETRIEVAL_CONFIDENCE_THRESHOLD`.

## Debugging what was retrieved

Every question logs the standalone question, confidence, and matched chunks:
`docker compose logs backend -f`. In dev, set `debug: true` in the widget
config to see the same in the browser console.

## Tests

```bash
cd backend && pip install -r requirements.txt pytest && pytest
```
Runs without Postgres/Redis/LLM keys (all external calls stubbed; Redis-down
fallback is exercised for real). Not covered by tests: the SQL against a real
Postgres, real LLM output quality, the browser widget.

## Still to do (see docs)

- Admin dashboard (cost/errors) — start with Langfuse + Sentry + Metabase on Postgres
- Human handoff (the "not sure" message doesn't yet create a ticket/email)
- Alembic migrations (schema changes need a wipe for now)
- Fallback LLM provider if Groq fails; multilingual embeddings if users write non-English
