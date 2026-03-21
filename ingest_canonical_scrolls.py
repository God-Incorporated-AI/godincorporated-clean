import os
import hashlib
import fitz  # PyMuPDF
from main import get_db_connection
from docx import Document

CANONICAL_DIR = "canonical_corpus"
CHUNK_SIZE = 1000
OVERLAP = 150
CANONICAL_SESSION_ID = "00000000-0000-0000-0000-000000000000"

def extract_txt(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def extract_rtf(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    # crude RTF strip
    import re
    text = re.sub(r"{\\.*?}", "", text)
    text = re.sub(r"\\[a-z]+\d*", "", text)

    return text

def extract_pdf(path):
    try:
        doc = fitz.open(path)
        pages = []

        for page in doc:
            text = page.get_text("text")
            if text:
                pages.append(text)

        return "\n".join(pages)

    except Exception as e:
        print(f"PDF read error: {e}")
        return ""

def extract_docx(path):

    doc = Document(path)

    paragraphs = []

    for p in doc.paragraphs:
        if p.text:
            paragraphs.append(p.text)

    return "\n".join(paragraphs)
def normalize_text(text):

    text = text.replace("\r", "\n")

    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    while "  " in text:
        text = text.replace("  ", " ")

    return text.strip()


def chunk_text(text):

    chunks = []
    start = 0
    length = len(text)

    while start < length:

        end = start + CHUNK_SIZE
        chunk = text[start:end]

        chunks.append(chunk)

        start += CHUNK_SIZE - OVERLAP

    return chunks

def ensure_canonical_session(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (id)
            VALUES (%s)
            ON CONFLICT (id) DO NOTHING
            """,
            (CANONICAL_SESSION_ID,)
        )
    conn.commit()

def ingest_file(conn, filepath, filename):

    print(f"\nProcessing: {filename}")

    ext = filename.lower()

    if ext.endswith(".txt"):
        text = extract_txt(filepath)

    elif ext.endswith(".pdf"):
        text = extract_pdf(filepath)

    elif ext.endswith(".rtf"):
        text = extract_rtf(filepath)

    elif ext.endswith(".docx"):
        text = extract_docx(filepath)

    else:
        print("Unsupported format")
        return

    text = normalize_text(text)

    if not text.strip():
        print("Empty file — skipped")
        return

    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    word_count = len(text.split())

    with conn.cursor() as cur:

        cur.execute(
            "SELECT id FROM scrolls WHERE content_hash = %s",
            (text_hash,)
        )

        if cur.fetchone():
            print("Already ingested")
            return

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
            RETURNING id
            """,
            (
                CANONICAL_SESSION_ID,
                None,
                "file",
                filename,
                "application/pdf" if filename.endswith(".pdf") else "text/plain",
                filename,
                text,
                text_hash,
                word_count,
                "canonical"
            )
        )

        scroll_id = str(cur.fetchone()["id"])

        chunks = chunk_text(text)

        for i, chunk in enumerate(chunks):

            cur.execute(
                """
                INSERT INTO scroll_chunks
                (scroll_id, chunk_index, chunk_text)
                VALUES (%s,%s,%s)
                """,
                (scroll_id, i, chunk)
            )

        print(f"Ingested {filename}")
        print(f"Words: {word_count}")
        print(f"Chunks: {len(chunks)}")


def ingest():

    conn = get_db_connection()
    ensure_canonical_session(conn)

        # --- Continue ingestion ---
    for filename in os.listdir(CANONICAL_DIR):

        if filename.startswith("."):
            continue

        filepath = os.path.join(CANONICAL_DIR, filename)

        try:
            ingest_file(conn, filepath, filename)

        except Exception as e:
            conn.rollback()
            print(f"Failed: {filename}")
            print("Error:", repr(e))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    ingest()