-- Enables the pgvector extension. SQLAlchemy (via Base.metadata.create_all
-- in app startup) creates the actual tables; this just ensures the
-- extension exists before that happens.
CREATE EXTENSION IF NOT EXISTS vector;
