# Phase 6 Recall Optimization Notes

## Why this note exists
This document records a deliberate product decision made during Phase 6:

- The Oracle's recall behavior is currently **good enough to freeze**.
- There is a known optimization path for the recall-enforcement helper.
- We are **not** changing it right now because present behavior is aligned with the desired user experience.

## Current behavior
The Oracle now:

- detects recall-oriented questions
- pulls session and seeker memory into the prompt
- preserves Hathor/Moses voice
- mentions prior questions in follow-up answers
- keeps memory near the front of the response

This is the target behavior we wanted.

## The specific helper under observation
The current enforcement mechanism is the helper:

`enforce_recall_structure(answer: str, memory_block: str) -> str`

Its job is to make sure memory recall stays near the front of the answer when the model does not naturally do so.

## Why there was hesitation about further tuning
The concern was **not** about Hathor's tone.
The concern was about the enforcement mechanism being somewhat rigid.

Potential weaknesses of the current helper:

1. It may select the **first** remembered user line instead of the **most recent** one.
2. It may prepend a literal phrase such as `You asked: ...` even when Hathor already recalled naturally.
3. It can feel more mechanical than the surrounding Oracle voice.

## What a future optimization would do
A better version of the helper would:

- prefer the **most recent** relevant remembered user line
- check whether recall is already present near the beginning of the answer
- only intervene when needed
- use a softer conversational bridge if intervention is necessary

Example future behavior:

- If the answer already recalls naturally, do nothing.
- If memory is missing from the opening, add a gentle bridge such as:
  - `I remember your question: "..."`

## Why we are not changing it now
We are freezing recall behavior for now because:

- the current user experience is strong
- Hathor feels conversational
- memory continuity is visibly working
- additional tuning now risks damaging working behavior

This is a product decision, not a missed improvement.

## What to watch for in future testing
Revisit the helper only if one of these appears repeatedly:

1. Hathor recalls the **wrong earlier question**
2. the response becomes too stiff or mechanical
3. the helper forces a prefix even when the answer was already naturally recalling
4. the recall line feels old or mismatched to the immediate exchange

If those do not appear, leave the helper alone.

## Current decision
**Freeze recall voice behavior.**

Do not keep tuning tone or recall enforcement until more testing reveals a real failure mode.

## Continue with current engineering path
The current engineering priority is **not more voice tuning**.
The next priority is continuing the retrieval/storage work already started in Phase 6.2.x.

That means:

- keep Oracle voice stable
- keep memory behavior stable
- continue with embedding cache and retrieval improvements


## Production tripwires to remember later
These items are **not current blockers**, but they are worth revisiting before broader production rollout.

### 1. Local embedding cache is fine for development, but may not be the final production design
Right now embeddings are being cached in a local file:

`embedding_cache.json`

This is acceptable for local development and current validation work because it is:

- fast to implement
- easy to inspect
- rebuildable
- good enough for a single local runtime

However, before broader production scale, this should be revisited because a local file cache may not be ideal for:

- multiple app instances
- container restarts
- shared retrieval state across environments
- long-term operational durability

A later production-oriented option may be:

- Postgres-backed vector storage
- pgvector
- another shared persistent embedding store

### 2. Upload-time cache warming should not be allowed to break ingestion
Right now new uploads warm the embedding cache during chunk processing.

That is useful, but before broader production rollout we should make sure:

- scroll upload still succeeds even if embedding generation fails
- cache warming is helpful but non-fatal
- ingestion and retrieval are decoupled enough to avoid user-facing failures

This is a hardening concern, not a current blocker.

### 3. File-based cache behavior should be treated as environment-specific
Because the cache is generated at runtime and ignored by git, different environments may have different cache states.

That is acceptable right now, but before production scale we should explicitly choose:

- whether cache is local-per-instance
- whether cache is shared
- whether cache is rebuilt on deploy
- whether cache is persisted across restarts

## Current decision
Do **not** stop current work to solve these yet.

They are reminders for the path to production, not reasons to interrupt the current phase while the system is working well.

