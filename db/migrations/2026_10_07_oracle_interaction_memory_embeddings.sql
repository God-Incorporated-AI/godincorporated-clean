BEGIN;

ALTER TABLE oracle_interactions
    ADD COLUMN IF NOT EXISTS memory_embedding vector(1536),
    ADD COLUMN IF NOT EXISTS memory_embedding_model text,
    ADD COLUMN IF NOT EXISTS memory_embedded_at timestamptz;

CREATE INDEX IF NOT EXISTS
    idx_oracle_interactions_memory_embedding_hnsw_cosine
ON oracle_interactions
USING hnsw (memory_embedding vector_cosine_ops)
WHERE memory_embedding IS NOT NULL;

COMMIT;
