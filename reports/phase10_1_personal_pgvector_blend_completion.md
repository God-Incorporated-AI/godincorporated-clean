# Phase 10.1 Personal Pgvector Blend Completion

## Status

Production personal + canonical pgvector retrieval is live and stable.

## Tag

v10.1.0-prod-personal-pgvector-blend-stable

## Confirmed

- Canonical embeddings complete in dev, staging, and production.
- Personal embeddings complete in dev, staging, and production.
- Staging blend tested before production.
- Production blend tested after browser reload/login.
- Registered Theoricus retrieval uses personal_limit=3 and canonical_limit=5.
- Anonymous retrieval remains canonical-only.
- LLaMA Phase1 remains disabled.
- Token usage and prompt budget logging are active.

## Known next work

- Improve frontend handling for 429/502/non-JSON responses.
- Full voice UI flow pass, preferably tested from iPad/mobile.
- Cleanup stale Ollama references and legacy backup/script bones.
- Consider persistent token/cost reporting in Postgres later.
