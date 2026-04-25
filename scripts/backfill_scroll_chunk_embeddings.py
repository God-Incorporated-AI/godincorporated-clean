"""
Phase 10.1 — Backfill scroll chunk embeddings into pgvector.

Safe, resumable batch script.
It only processes scroll_chunks where embedding IS NULL.

Usage examples:
  python scripts/backfill_scroll_chunk_embeddings.py --limit 25 --batch-size 25 --corpus-layer canonical
  python scripts/backfill_scroll_chunk_embeddings.py --limit 500 --batch-size 50 --corpus-layer canonical
  python scripts/backfill_scroll_chunk_embeddings.py --limit 100 --batch-size 25
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main import get_db_connection, get_openai_client


EMBEDDING_MODEL = "text-embedding-3-small"
VALID_CORPUS_LAYERS = {"canonical", "personal", "community"}


def vector_literal(values):
    return "[" + ",".join(str(float(v)) for v in values) + "]"


def chunked(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


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


def generate_batch_embeddings(texts):
    client = get_openai_client()
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts
    )

    ordered = sorted(response.data, key=lambda item: item.index)
    return [item.embedding for item in ordered]


def print_counts():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total_chunks,
                    COUNT(embedding) AS embedded_chunks,
                    COUNT(*) - COUNT(embedding) AS missing_embeddings
                FROM scroll_chunks;
            """)
            print("all_embedding_counts =", dict(cur.fetchone()))

            cur.execute("""
                SELECT
                    s.corpus_layer,
                    COUNT(*) AS total_chunks,
                    COUNT(c.embedding) AS embedded_chunks,
                    COUNT(*) - COUNT(c.embedding) AS missing_embeddings
                FROM scroll_chunks c
                JOIN scrolls s ON c.scroll_id = s.id
                GROUP BY s.corpus_layer
                ORDER BY s.corpus_layer;
            """)
            print("embedding_counts_by_layer =")
            for row in cur.fetchall():
                print(dict(row))
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--corpus-layer", choices=sorted(VALID_CORPUS_LAYERS), default=None)
    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")
    if args.batch_size > 100:
        raise SystemExit("--batch-size should be 100 or less for controlled backfill")

    rows = fetch_rows(args.limit, args.corpus_layer)

    print("requested_limit =", args.limit)
    print("batch_size =", args.batch_size)
    print("corpus_layer =", args.corpus_layer or "any")
    print("rows_to_process =", len(rows))

    if not rows:
        print("[OK] no missing embeddings found for this selection")
        print_counts()
        return

    conn = get_db_connection()
    processed = 0

    try:
        with conn.cursor() as cur:
            for batch_number, batch_rows in enumerate(chunked(rows, args.batch_size), start=1):
                texts = [(row["chunk_text"] or "").strip()[:2000] for row in batch_rows]

                print(f"[BATCH] number={batch_number} size={len(batch_rows)}")

                embeddings = generate_batch_embeddings(texts)

                if len(embeddings) != len(batch_rows):
                    raise RuntimeError(
                        f"Embedding count mismatch: rows={len(batch_rows)} embeddings={len(embeddings)}"
                    )

                for row, embedding in zip(batch_rows, embeddings):
                    chunk_id = row["id"]
                    print(
                        f"[UPDATE] chunk_id={chunk_id} "
                        f"layer={row['corpus_layer']} "
                        f"file={row['original_filename']}"
                    )

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

                conn.commit()
                print(f"[COMMIT] processed={processed}")

        print(f"[OK] backfill complete processed={processed}")
        print_counts()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
