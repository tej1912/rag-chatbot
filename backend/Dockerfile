FROM python:3.11-slim

WORKDIR /app

# gcc/g++ are needed to build 'annoy' (a NeMo Guardrails dependency)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image: no download on first request, no
# volume needed, and it works on hosts with ephemeral disks (Railway/Render).
ENV EMBEDDING_CACHE_DIR=/models
RUN mkdir -p /models && python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')"

# run as a non-root user
RUN useradd --create-home --uid 10001 app && chown -R app:app /models /app
COPY --chown=app:app . .
USER app

EXPOSE 8000

# Production command: no --reload, several workers, honours $PORT (Railway/
# Render set it), trusts the platform proxy's X-Forwarded-For so per-IP rate
# limits see real client IPs. Each worker loads the ~300MB embedding model:
# on a 512MB host set WEB_CONCURRENCY=1.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips '*'"]
