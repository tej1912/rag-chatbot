import io
import logging

from sqlalchemy.orm import Session

from app.core import cache
from app.core.chunking import chunk_document
from app.core.embeddings import embed_texts
from app.db.models import Chunk, Document

logger = logging.getLogger("ingest_service")

EMBED_BATCH_SIZE = 64


def _embed_in_batches(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        vectors.extend(embed_texts(texts[i : i + EMBED_BATCH_SIZE]))
    return vectors


def _delete_existing(db: Session, title: str, url: str | None) -> int:
    """Delete previous version(s) of this document. Match on url, else title (url-less docs)."""
    q = db.query(Document.id)
    if url:
        q = q.filter(Document.url == url)
    else:
        q = q.filter(Document.url.is_(None), Document.title == title)
    ids = [row[0] for row in q.all()]
    if ids:
        db.query(Chunk).filter(Chunk.document_id.in_(ids)).delete(synchronize_session=False)
        db.query(Document).filter(Document.id.in_(ids)).delete(synchronize_session=False)
    return len(ids)


def ingest_document(
    db: Session,
    title: str,
    text: str,
    url: str | None,
    category: str | None,
    replace: bool = True,
) -> tuple[Document, int]:
    """
    chunk -> embed -> (delete old version) -> insert new, in ONE transaction.
    Embedding happens BEFORE touching the DB, so an embedding failure never
    destroys the previous version of the document. Returns (doc, replaced_count).
    """
    pieces = chunk_document(text)

    # header context is prepended to the text that gets embedded (retrieval
    # matches "Section: X\n<chunk>", not the bare chunk)
    embed_inputs = [
        f"{p.header_context}\n{p.content}" if p.header_context else p.content for p in pieces
    ]
    vectors = _embed_in_batches(embed_inputs) if embed_inputs else []

    replaced = _delete_existing(db, title, url) if replace else 0

    doc = Document(title=title, url=url, category=category)
    db.add(doc)
    db.flush()
    for piece, vector in zip(pieces, vectors):
        db.add(
            Chunk(
                document_id=doc.id,
                content=piece.content,
                header_context=piece.header_context,
                embedding=vector,
            )
        )
    db.commit()
    db.refresh(doc)

    cache.bump_cache_version()  # cached answers may now be stale
    return doc, replaced


def delete_document(db: Session, document_id) -> bool:
    doc = db.get(Document, document_id)
    if not doc:
        return False
    db.query(Chunk).filter(Chunk.document_id == doc.id).delete(synchronize_session=False)
    db.delete(doc)
    db.commit()
    cache.bump_cache_version()
    return True


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """
    Page-by-page text, each page prefixed '# Page N' so chunking keeps page
    context. Returns "" for scanned/image-only PDFs (no OCR here).
    """
    import pdfplumber

    pages_text = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            page_text = (page.extract_text() or "").strip()
            if page_text:
                pages_text.append(f"# Page {i}\n{page_text}")
    return "\n\n".join(pages_text)


def ingest_pdf(
    db: Session, title: str, pdf_bytes: bytes, category: str | None
) -> tuple[Document, int] | None:
    text = extract_pdf_text(pdf_bytes)
    if not text:
        return None
    return ingest_document(db, title, text, url=None, category=category, replace=True)
