import os
import hashlib
from main import get_db_connection

CANONICAL_DIR = "canonical_scrolls"

def ingest():

    conn = get_db_connection()

    for filename in os.listdir(CANONICAL_DIR):

        if not filename.endswith(".txt"):
            continue

        path = os.path.join(CANONICAL_DIR, filename)

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

        if not text.strip():
            print(f"Skipping empty file: {filename}")
            continue

        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        word_count = len(text.split())

        with conn.cursor() as cur:

            # Prevent duplicates
            cur.execute(
                "SELECT id FROM scrolls WHERE content_hash = %s",
                (text_hash,)
            )

            if cur.fetchone():
                print(f"Already ingested: {filename}")
                continue

            cur.execute(
                """
                INSERT INTO scrolls (
                    session_id,
                    user_id,
                    source_type,
                    original_filename,
                    mime_type,
                    storage_ref,
                    content_text,
                    content_hash,
                    word_count,
                    corpus_layer
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    "00000000-0000-0000-0000-000000000000",
                    None,
                    "file",
                    filename,
                    "text/plain",
                    filename,
                    text,
                    text_hash,
                    word_count,
                    "canonical"
                )
            )

            print(f"Ingested: {filename} ({word_count} words)")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    ingest()