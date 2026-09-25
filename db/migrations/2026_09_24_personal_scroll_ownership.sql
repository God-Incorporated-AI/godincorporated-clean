-- Phase: personal scroll ownership isolation
--
-- Personal seeker uploads are ownership-isolated source records.
-- content_hash remains useful metadata, but must not merge unrelated owners.
-- Community Corpus deduplication remains separate and is not changed here.

ALTER TABLE scrolls
    DROP CONSTRAINT IF EXISTS unique_scroll_hash;

DROP INDEX IF EXISTS unique_scroll_hash;

CREATE INDEX IF NOT EXISTS idx_scrolls_content_hash
    ON scrolls (content_hash);

-- Personal chunks/vectors belong to their source scroll. Removing a personal
-- source must therefore remove its chunk rows as part of the same lifecycle.
DO $$
DECLARE
    fk_name text;
BEGIN
    FOR fk_name IN
        SELECT DISTINCT con.conname
        FROM pg_constraint con
        JOIN pg_class rel
          ON rel.oid = con.conrelid
        JOIN pg_namespace nsp
          ON nsp.oid = rel.relnamespace
        JOIN pg_class ref
          ON ref.oid = con.confrelid
        JOIN pg_attribute att
          ON att.attrelid = rel.oid
         AND att.attnum = ANY(con.conkey)
        WHERE con.contype = 'f'
          AND nsp.nspname = current_schema()
          AND rel.relname = 'scroll_chunks'
          AND ref.relname = 'scrolls'
          AND att.attname = 'scroll_id'
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I DROP CONSTRAINT %I',
            current_schema(),
            'scroll_chunks',
            fk_name
        );
    END LOOP;
END
$$;

ALTER TABLE scroll_chunks
    ADD CONSTRAINT scroll_chunks_scroll_id_fkey
    FOREIGN KEY (scroll_id)
    REFERENCES scrolls(id)
    ON DELETE CASCADE;
