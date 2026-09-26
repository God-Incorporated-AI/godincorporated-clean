# SQL schema and migration authority

God Incorporated has two distinct migration eras.

## Historical Alembic chain

Alembic is historical migration authority through revision:

`801c019b3e64`

The genesis revision:

`9d7ccffbfff3`

must always execute the immutable February 6, 2026 schema snapshot:

`sql/archive/0000_initial_canonical_schema_genesis_2026_02_06.sql`

That snapshot is historical evidence and must not be updated to reflect later schema changes.

The file:

`sql/0000_initial_canonical_schema.sql`

evolved after genesis and is retained as legacy bootstrap/reference material. It is not an immutable Alembic revision payload and is not a complete current-schema authority.

## Current forward migration authority

Current forward schema changes belong in:

`db/migrations/`

Those migrations are chronological, environment-specific operations. Local Dev, Staging, and Production are verified and migrated separately.

Do not back-port new schema changes into the frozen Alembic genesis snapshot.

Do not infer current database migration maturity from `alembic_version` alone. The Alembic chain ends at the historical February head; later schema evolution is represented by the dated forward SQL migrations.
