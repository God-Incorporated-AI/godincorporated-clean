"""
Phase 10.1 — pgvector storage foundation for scroll chunk embeddings.

Safe to run repeatedly.
Adds:
- pgvector extension
- scroll_chunks.embedding
- scroll_chunks.embedding_model
- scroll_chunks.embedded_at

This does not backfill embeddings yet.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main import get_db_connection, generate_text_embedding


def main():
    sample = generate_text_embedding("dimension check")
    if not sample:
        raise RuntimeError("Could not generate sample embedding.")

    dimension = len(sample)
    print(f"embedding_dimension = {dimension}")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            cur.execute(f"""
                ALTER TABLE scroll_chunks
                ADD COLUMN IF NOT EXISTS embedding vector({dimension});
            """)

            cur.execute("""
                ALTER TABLE scroll_chunks
                ADD COLUMN IF NOT EXISTS embedding_model text;
            """)

            cur.execute("""
                ALTER TABLE scroll_chunks
                ADD COLUMN IF NOT EXISTS embedded_at timestamptz;
            """)

            cur.execute("""
                SELECT
                    COUNT(*) AS total_chunks,
                    COUNT(embedding) AS embedded_chunks,
                    COUNT(*) - COUNT(embedding) AS missing_embeddings
                FROM scroll_chunks;
            """)
            counts = cur.fetchone()

        conn.commit()

        print("[OK] pgvector extension and embedding columns are present")
        print("embedding_counts =", dict(counts))

    finally:
        conn.close()


if __name__ == "__main__":
    main()
