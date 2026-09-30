"""
Hybrid retrieval: dense (pgvector cosine) + sparse (keyword ILIKE) results
merged by reciprocal rank fusion. Reranker step is a no-op stub until
RERANK_ENABLED=true + COHERE_API_KEY is set — flip that on once retrieval
quality needs the extra step.
"""
import re
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.core.embeddings import embed_query
from app.db.models import Chunk, Document

TOP_K_DENSE = 20
TOP_K_SPARSE = 20
TOP_K_FINAL = 5
RRF_K = 60  # standard reciprocal-rank-fusion constant


@dataclass
class RetrievedChunk:
    content: str
    header_context: str | None
    document_title: str
    score: float  # fused rank score, higher = better; also used as confidence proxy


def _dense_search(db: Session, query_embedding: list[float], category: str | None) -> list[tuple[Chunk, float]]:
    q = (
        db.query(Chunk, Chunk.embedding.cosine_distance(query_embedding).label("distance"))
        .join(Document)
    )
    if category:
        q = q.filter(Document.category == category)
    rows = q.order_by("distance").limit(TOP_K_DENSE).all()
    # cosine_distance: 0 = identical. Convert to a similarity score.
    return [(chunk, 1 - distance) for chunk, distance in rows]


def _sparse_search(db: Session, query: str, category: str | None) -> list[tuple[Chunk, float]]:
    """
    Keyword leg of hybrid search: Postgres full-text search with OR semantics
    (any query word can match), ranked by ts_rank_cd. Tokens are restricted to
    [a-z0-9] so the tsquery string can never contain operators.
    """
    tokens = re.findall(r"[a-z0-9]{3,}", query.lower())[:8]
    if not tokens:
        return []
    ts_query = func.to_tsquery("english", " | ".join(tokens))
    ts_vector = func.to_tsvector("english", Chunk.content)
    rank = func.ts_rank_cd(ts_vector, ts_query).label("rank")
    q = db.query(Chunk, rank).join(Document).filter(ts_vector.op("@@")(ts_query))
    if category:
        q = q.filter(Document.category == category)
    rows = q.order_by(rank.desc()).limit(TOP_K_SPARSE).all()
    return [(chunk, float(score)) for chunk, score in rows]


def _fuse(dense: list[tuple[Chunk, float]], sparse: list[tuple[Chunk, float]]) -> list[tuple[Chunk, float]]:
    scores: dict[str, float] = {}
    chunks_by_id: dict[str, Chunk] = {}

    for rank, (chunk, _) in enumerate(dense):
        cid = str(chunk.id)
        scores[cid] = scores.get(cid, 0) + 1 / (RRF_K + rank + 1)
        chunks_by_id[cid] = chunk

    for rank, (chunk, _) in enumerate(sparse):
        cid = str(chunk.id)
        scores[cid] = scores.get(cid, 0) + 1 / (RRF_K + rank + 1)
        chunks_by_id[cid] = chunk

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(chunks_by_id[cid], score) for cid, score in ranked]


def _rerank(query: str, candidates: list[tuple[Chunk, float]]) -> list[tuple[Chunk, float]]:
    if not settings.rerank_enabled or not settings.cohere_api_key or not candidates:
        return candidates

    import httpx

    docs = [c.content for c, _ in candidates]
    resp = httpx.post(
        "https://api.cohere.com/v2/rerank",
        headers={"Authorization": f"Bearer {settings.cohere_api_key}"},
        json={"model": "rerank-v3.5", "query": query, "documents": docs, "top_n": TOP_K_FINAL},
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json()["results"]
    return [(candidates[r["index"]][0], r["relevance_score"]) for r in results]


def search(
    db: Session, query: str, category: str | None = None, query_embedding: list[float] | None = None
) -> tuple[list[RetrievedChunk], float]:
    """
    Returns (top-k chunks, confidence). Confidence is the best raw dense
    cosine similarity (0-1 range) — used by guardrails/routing, kept
    separate from the RRF fusion score (which is only meaningful for
    ranking, not as an absolute confidence number).
    """
    if query_embedding is None:
        query_embedding = embed_query(query)
    dense = _dense_search(db, query_embedding, category)
    sparse = _sparse_search(db, query, category)
    confidence = dense[0][1] if dense else 0.0

    fused = _fuse(dense, sparse)[: max(TOP_K_FINAL * 2, TOP_K_FINAL)]
    final = _rerank(query, fused)[:TOP_K_FINAL]

    results = [
        RetrievedChunk(
            content=chunk.content,
            header_context=chunk.header_context,
            document_title=chunk.document.title,
            score=score,
        )
        for chunk, score in final
    ]
    return results, confidence
