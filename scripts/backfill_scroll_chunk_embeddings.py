"""
Phase 10.1 — Backfill scroll chunk embeddings into pgvector.

Safe, resumable batch script.
It only processes scroll_chunks where embedding IS NULL.

Usage examples:
  python scripts/backfill_scroll_chunk_embeddings.py --limit 10 --corpus-layer canonical
  python scripts/backfill_scroll_chunk_embeddings.py --limit 100 --corpus-layer canonical
  python scripts/backfill_scroll_chunk_embeddings.py --limit 100
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main import get_db_connection, generate_text_embedding


EMBEDDING_MODEL = "text-embedding-3-small"
VALID_CORPUS_LAYERS = {"canonical", "personal", "community"}


def vector_literal(values):
    return "[" + ",".join(str(float(v)) for v in values) + "]"


def fetch_rows(limit, corpus_layer=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            params = []
            where = [
                "c.embedding IS NULL",
                "c.chunk_text IS NOT NULL",
                "btrim(c.chunk_text) <> ''"
            ]

            if corpus_layer:
                where.append("s.corpus_layer = %s")
                params.append(corpus_layer)

            params.append(limit)

            cur.execute(
                f"""
                SELECT
                    c.id,
                    c.chunk_text,
                    s.original_filename,
                    s.corpus_layer
                FROM scroll_chunks c
                JOIN scrolls s ON c.scroll_id = s.id
                WHERE {' AND '.join(where)}
                ORDER BY c.id
                LIMIT %s
                """,
                params
            )
            return cur.fetchall()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--corpus-layer", choices=sorted(VALID_CORPUS_LAYERS), default=None)
    parser.add_argument("--commit-every", type=int, default=10)
    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    rows = fetch_rows(args.limit, args.corpus_layer)

    print("requested_limit =", args.limit)
    print("corpus_layer =", args.corpus_layer or "any")
    print("rows_to_process =", len(rows))

    if not rows:
        print("[OK] no missing embeddings found for this selection")
        return

    conn = get_db_connection()
    processed = 0

    try:
        with conn.cursor() as cur:
            for row in rows:
                chunk_id = row["id"]
                text = (row["chunk_text"] or "").strip()

                print(
                    f"[EMBED] chunk_id={chunk_id} "
                    f"layer={row['corpus_layer']} "
                    f"file={row['original_filename']}"
                )

                embedding = generate_text_embedding(text[:2000])
                if not embedding:
                    print(f"[SKIP] chunk_id={chunk_id} empty embedding")
                    continue

                cur.execute(
                    """
                    UPDATE scroll_chunks
                    SET
                        embedding = %s::vector,
                        embedding_model = %s,
                        embedded_at = NOW()
                    WHERE id = %s
                      AND embedding IS NULL
                    """,
                    (
                        vector_literal(embedding),
                        EMBEDDING_MODEL,
                        chunk_id
                    )
                )

                processed += 1

                if processed % args.commit_every == 0:
                    conn.commit()
                    print(f"[COMMIT] processed={processed}")

        conn.commit()
        print(f"[OK] backfill complete processed={processed}")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
