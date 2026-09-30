import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.models import Chunk, Document
from app.db.session import get_db
from app.schemas.chat import IngestRequest
from app.services.ingest_service import delete_document, ingest_document, ingest_pdf

MAX_PDF_BYTES = 20 * 1024 * 1024

# every route here needs the X-Admin-Key header
router = APIRouter(prefix="/v1", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/ingest")
def ingest(req: IngestRequest, db: Session = Depends(get_db)):
    doc, replaced = ingest_document(db, req.title, req.text, req.url, req.category, req.replace)
    return {"document_id": str(doc.id), "title": doc.title, "chunks": len(doc.chunks), "replaced": replaced}


@router.post("/ingest/pdf")
async def ingest_pdf_endpoint(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    category: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are accepted")

    pdf_bytes = await file.read(MAX_PDF_BYTES + 1)
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF too large (max 20MB)")

    doc_title = title or file.filename.rsplit(".", 1)[0]
    result = ingest_pdf(db, doc_title, pdf_bytes, category)
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Could not extract any text from this PDF (likely scanned/image-only - needs OCR, not supported yet)",
        )
    doc, replaced = result
    return {"document_id": str(doc.id), "title": doc.title, "chunks": len(doc.chunks), "replaced": replaced}


@router.get("/documents")
def list_documents(db: Session = Depends(get_db)):
    rows = (
        db.query(Document, func.count(Chunk.id))
        .outerjoin(Chunk, Chunk.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.created_at.desc())
        .all()
    )
    return [
        {
            "id": str(d.id),
            "title": d.title,
            "url": d.url,
            "category": d.category,
            "chunks": n,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d, n in rows
    ]


@router.delete("/documents/{document_id}")
def remove_document(document_id: uuid.UUID, db: Session = Depends(get_db)):
    if not delete_document(db, document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": str(document_id)}
