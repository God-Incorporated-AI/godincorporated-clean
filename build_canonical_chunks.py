from main import get_db_connection

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200


def chunk_text(text):
    chunks = []
    start = 0
    length = len(text)

    while start < length:
        end = start + CHUNK_SIZE
        chunk = text[start:end]
        chunks.append(chunk)
        start += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks


def build_chunks():

    conn = get_db_connection()

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT id, original_filename, content_text
            FROM scrolls
            WHERE corpus_layer = 'canonical'
            """
        )

        scrolls = cur.fetchall()

        print(f"Found {len(scrolls)} canonical scrolls")

        total_chunks = 0

        for row in scrolls:

            scroll_id = row["id"]
            filename = row["original_filename"]
            text = row["content_text"]

            chunks = chunk_text(text)

            print(f"{filename}: {len(chunks)} chunks")

            for i, chunk in enumerate(chunks):

                cur.execute(
                    """
                    INSERT INTO scroll_chunks (scroll_id, chunk_index, chunk_text)
                    VALUES (%s, %s, %s)
                    """,
                    (scroll_id, i, chunk)
                )

                total_chunks += 1

        conn.commit()

        print(f"Inserted {total_chunks} chunks")

    conn.close()


if __name__ == "__main__":
    build_chunks()