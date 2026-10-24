# RAG Chat Assistant - Project Reference

A retrieval-augmented chatbot backend and embeddable widget, built to be
**reused across multiple websites**, not tied to any one client. You point
it at a set of ingested documents (text or PDF) and it answers visitor
questions grounded in that content only - it does not scrape the web, it
does not invent answers, and it says "not sure" when nothing relevant was
found. Each deployment (one Postgres + Redis + backend) serves one website's
content; running it for a second website means a second deployment with its
own database, not a shared one, since there is currently no per-tenant
separation inside a single database.

---

## 1. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend framework | FastAPI (Python) | async, streams responses over SSE |
| Relational + vector DB | PostgreSQL + pgvector extension | one database instead of Postgres plus a separate vector DB |
| Cache / session / rate-limit store | Redis | fast, short-lived data; app degrades gracefully if Redis is down |
| Embeddings | `BAAI/bge-small-en-v1.5` via `fastembed`, baked into the Docker image | free forever, no external API, no rate limit |
| LLM (answers) | Groq (`openai/gpt-oss-20b` / `openai/gpt-oss-120b`) or Anthropic Claude, picked per environment | tiered: cheap/fast model for simple questions, stronger model for complex ones |
| Guardrail LLM (optional) | Groq, via NVIDIA NeMo Guardrails | free software; the LLM call itself is not free |
| PDF text extraction | `pdfplumber` | page-by-page text, feeds the same chunker as pasted text |
| Frontend | Vanilla JS widget, no framework | drops into any existing site with a two-line script tag |
| Containerization | Docker + docker-compose | same environment locally and in production |
| Tests | `pytest` | see section 6 |

---

## 2. Repository layout

```
rag-chatbot/
├── backend/
│   ├── app/
│   │   ├── main.py              FastAPI app: startup checks, CORS, security headers, DB init
│   │   ├── config.py            All settings (reads .env), production-mode validation
│   │   ├── core/
│   │   │   ├── chunking.py      Splits ingested text into retrievable pieces
│   │   │   ├── embeddings.py    Turns text into vectors (local model by default)
│   │   │   ├── retrieval.py     Vector + keyword search over stored chunks
│   │   │   ├── llm.py           Talks to Groq/Claude; tiered routing; follow-up rewriting
│   │   │   ├── guardrails.py    Regex input/output checks, smalltalk replies
│   │   │   ├── nemo_guard.py    Optional LLM-based input guardrail (NeMo Guardrails)
│   │   │   ├── security.py      Admin auth, signed session tokens, captcha check
│   │   │   └── cache.py         Redis: history, answer cache, rate limits, spend caps
│   │   ├── db/
│   │   │   ├── models.py        Postgres tables: documents, chunks, conversation_messages
│   │   │   └── session.py       DB engine/session setup
│   │   ├── api/
│   │   │   ├── chat.py          POST /v1/session, POST /v1/chat (SSE)
│   │   │   ├── ingest.py        Admin-only: ingest text/PDF, list/delete documents
│   │   │   └── health.py        /healthz, /readyz, /v1/admin/usage
│   │   ├── schemas/chat.py      Request/response validation
│   │   └── services/
│   │       ├── chat_service.py    Orchestrates one chat turn end to end
│   │       └── ingest_service.py  Orchestrates chunk -> embed -> store (and PDF text extraction)
│   ├── tests/                    pytest suite (see section 6)
│   ├── scripts/init_db.sql       Enables the pgvector Postgres extension
│   ├── Dockerfile                Production image (bakes in the embedding model)
│   └── requirements.txt
├── frontend/widget/               Embeddable chat widget (HTML/CSS/JS demo page)
├── eval/                          RAGAS offline answer-quality evaluation harness
├── docker-compose.yml             Local dev environment
├── .env.example                  Every setting, documented inline
├── README.md                      Setup, API reference, deploy checklist
└── PROJECT.md                     This file
```

---

## 3. How a chat message is answered

```
Browser widget
  -> POST /v1/session           (issues a signed session token; optional captcha)
  -> POST /v1/chat               with X-Session-Token header
       Rate limits checked first: per-session/min, per-IP/min, per-IP/day
       1. Regex guardrail          - blocks obvious prompt-injection phrasing, free & instant
       2. Smalltalk check          - "hi"/"thanks"/"bye" get a canned reply, no LLM call
       3. NeMo guardrail           - optional LLM-based check (GUARDRAILS_MODE=nemo)
       4. Follow-up rewrite        - "how many does it have?" -> a standalone question,
                                     using recent history; skipped if there is no history
       5. Embed the question       - locally, free
       6. Answer cache check       - Redis; a near-duplicate recent question returns instantly
       7. Retrieval                - vector similarity (pgvector) + Postgres full-text search,
                                     merged; returns top chunks + a confidence score
       8. Confidence fallback      - below threshold -> "not sure" message, no LLM call at all
       9. Daily budget check       - refuses before an LLM call if the daily cap is already hit
      10. LLM call                 - fast or complex tier, streamed back over SSE
      11. Output guardrail         - screens for leaked PII before the answer is cached
      12. Persistence              - Redis (recent history) + Postgres (permanent log)
```

Ingestion (loading content in) is a separate, admin-only flow:

```
Text or PDF
  -> chunk (recursive, section/header-aware, FAQ-pair-aware, page-aware for PDFs)
  -> embed each chunk locally
  -> store in Postgres (documents + chunks tables)
  -> bump the answer-cache version, so stale cached answers stop being served
```

Re-ingesting a document with the same URL (or the same title, if it has no
URL) **replaces** the previous version instead of creating a duplicate.

---

## 4. Reusing this for a different website

The deployment's identity lives in three env variables - `BOT_COMPANY_NAME`,
`BOT_TOPICS`, `BOT_COMPANY_SUMMARY` (see `.env.example`). They drive the
refusal message, greeting, system prompt and the NeMo guardrail policy.
Currently set for **SmartBusiness Solutions** (Check Printing and Time &
Attendance). For another site: change those three, set the widget `title` /
`welcome`, and ingest that site's documents.
Everything else - chunking, retrieval, the widget, the API - has no
knowledge of what content it's serving.

To stand up a second, separate deployment for another site:

1. Run a second copy of this stack (its own Postgres, Redis, and backend
   container - `docker compose -p sitename up -d`, or a second cloud project).
2. Give it its own `.env` (its own `ADMIN_API_KEY`, `SESSION_SECRET`,
   `CORS_ORIGINS` set to that site's domain).
3. Ingest that site's own content via `/v1/ingest` and `/v1/ingest/pdf`.
4. Point that site's copy of `widget.js` at this deployment's URL.

There is currently no multi-tenant mode (one database serving several
unrelated sites at once, filtered by an account/tenant ID) - each site gets
its own full deployment. That would be a real feature to build if you end
up running many of these, rather than something to fake with a workaround.

---

## 5. Security and cost controls (why they exist)

- **Admin key required** for ingest/delete/list/usage endpoints, and it
  **fails closed**: if the key isn't configured, those endpoints refuse
  everything rather than defaulting to open.
- **Server-issued session tokens**: a visitor can't invent their own session
  ID to dodge the per-session rate limit; the token is signed and checked
  on every chat request.
- **Rate limits at three levels** (per session, per IP per minute, per IP
  per day) so no single visitor or script can flood the bot.
- **Global daily spend cap** on LLM messages and estimated tokens - the hard
  ceiling on your LLM bill regardless of what happens upstream.
- **Production mode refuses to start** if the admin key, session secret, or
  CORS origin are left at insecure defaults - this is meant to catch a
  misconfigured deploy before it goes live, not just log a warning.
- **Redis outage degrades, does not crash**: history and caching stop
  working, but the chat itself keeps answering.

---

## 6. Running the tests

The test suite (`backend/tests/`) exists and has never been run against
your real environment - here's exactly how.

### One-time setup

```powershell
cd rag-chatbot\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pytest
```

### Run everything

```powershell
pytest
```

You do **not** need Docker, Postgres, Redis, or any real API key running
for this - every external call (LLM, embeddings, Redis) is stubbed out in
the tests themselves. One test (`test_cache_fallback.py`) intentionally
points at a Redis address nothing is listening on, specifically to prove
the app degrades instead of crashing when Redis is unreachable.

### Useful variations

```powershell
pytest -v                          # verbose: one line per test
pytest tests/test_chat_service.py  # just one file
pytest -k "rate_limit"             # just tests with "rate_limit" in the name
pytest -x                          # stop at the first failure
```

### What's covered vs. not

Covered: chunking logic, security (session tokens, admin auth, captcha),
Redis-down fallback behavior, the full chat orchestration logic (guardrails
-> retrieval -> LLM -> caching, with every branch), and the API layer
(auth, rate limits, SSE framing, CORS).

Not covered (needs a real environment to test): actual SQL against a real
Postgres, real LLM answer quality, the browser widget itself, and NeMo
Guardrails against real Groq output (only tested against a mock server so
far, and also tested manually in earlier troubleshooting).

### When to run these

Run `pytest` after any code change to `backend/app/`, before rebuilding the
Docker image - it catches logic mistakes in seconds instead of finding them
after a slow rebuild. It is not yet wired into an automated CI pipeline
(see section 7).

---

## 7. What's next

Roughly in order of what matters before wider use:

1. **Deploy it somewhere reachable** (Railway/Render/Fly.io) instead of
   `localhost` - see the README's deploy checklist.
2. **Switch to Claude for the LLM** in production (`LLM_FAST_PROVIDER` /
   `LLM_COMPLEX_PROVIDER=claude`) - Groq's free tier has no uptime
   guarantee, which matters once real visitors depend on it.
3. **An admin dashboard** for cost/errors/low-confidence questions. Fastest
   path: Langfuse (free, LLM call tracing + cost) + Sentry (free, errors) +
   Metabase pointed at the Postgres database (free, dashboards, no code).
4. **Human handoff**: right now the "not sure" fallback message doesn't
   actually connect to anyone - wire it to an email or a support-ticket
   webhook.
5. **Alembic migrations** - schema changes currently require wiping the
   database; a real migration tool avoids that going forward.
6. **A fallback LLM provider** if Groq fails outright, and a multilingual
   embedding model if visitors write in languages other than English (the
   current model is English-only).
7. **CI**: run `pytest` automatically on every push, before it reaches Docker.
