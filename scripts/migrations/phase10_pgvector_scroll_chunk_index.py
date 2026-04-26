"""
Phase 10.1 — Add pgvector index for scroll chunk embeddings.

Safe to run repeatedly.
Creates an HNSW cosine index over scroll_chunks.embedding.

Note:
CREATE INDEX CONCURRENTLY cannot run inside a transaction,
so this script uses autocommit.
"""

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main import get_db_connection


INDEX_NAME = "idx_scroll_chunks_embedding_hnsw_cosine"


def main():
    conn = get_db_connection()
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS embedded_chunks
                FROM scroll_chunks
                WHERE embedding IS NOT NULL;
            """)
            print("embedded_chunks =", cur.fetchone()["embedded_chunks"])

            cur.execute(f"""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
                ON scroll_chunks
                USING hnsw (embedding vector_cosine_ops)
                WHERE embedding IS NOT NULL;
            """)

            cur.execute("""
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'scroll_chunks'
                  AND indexname = %s;
            """, (INDEX_NAME,))

            row = cur.fetchone()
            print("[OK] index present =", dict(row) if row else None)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
