"""
Embedding client — provider chosen via EMBEDDING_PROVIDER env var.

"local" runs a small open-source model (BAAI/bge-small-en-v1.5, 384-dim)
inside the backend container via fastembed (ONNX, no GPU/torch needed).
No API key, no rate limit, no per-request cost — recommended default for
free-tier development and for production if you don't need a top-tier
embedding model. "openai" and "voyage" remain available if you want a
hosted model instead.
"""
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"
VOYAGE_EMBED_URL = "https://api.voyageai.com/v1/embeddings"

_local_model = None  # lazy-loaded singleton, avoids reloading per-request


def _get_local_model():
    global _local_model
    if _local_model is None:
        from fastembed import TextEmbedding

        _local_model = TextEmbedding(
            model_name="BAAI/bge-small-en-v1.5", cache_dir=settings.embedding_cache_dir
        )
    return _local_model


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def embed_texts(texts: list[str]) -> list[list[float]]:
    """Returns one embedding vector per input text, same order."""
    if settings.embedding_provider == "voyage":
        return _embed_voyage(texts)
    if settings.embedding_provider == "openai":
        return _embed_openai(texts)
    return _embed_local(texts)


def _embed_local(texts: list[str]) -> list[list[float]]:
    model = _get_local_model()
    # fastembed returns a generator of numpy arrays
    return [vec.tolist() for vec in model.embed(texts)]


def _embed_openai(texts: list[str]) -> list[list[float]]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not set — required for embedding_provider=openai")
    resp = httpx.post(
        OPENAI_EMBED_URL,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={"model": settings.embedding_model, "input": texts},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return [d["embedding"] for d in data]


def _embed_voyage(texts: list[str]) -> list[list[float]]:
    if not settings.voyage_api_key:
        raise RuntimeError("VOYAGE_API_KEY not set — required for embedding_provider=voyage")
    resp = httpx.post(
        VOYAGE_EMBED_URL,
        headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
        json={"model": settings.embedding_model, "input": texts, "input_type": "document"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return [d["embedding"] for d in data]


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
