"""Private R6D review-cache window reader; no provider or write path.

Never serialize private work items to aggregate output or logs.
"""
import datetime as _dt
import hashlib as _hashlib
import hmac as _hmac
import uuid as _uuid
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PrivateWindowInventory:
    source_count: int
    ready_count: int
    rejected_count: int
    missing_review_count: int
    needs_embedding_count: int
    selected_review_count: int
    selected_embedding_count: int
    private_work_items: tuple = field(repr=False)

    def public_summary(self):
        return {
            "source_count_28d": self.source_count,
            "ready_approved_count": self.ready_count,
            "reviewed_rejected_count": self.rejected_count,
            "missing_review_count": self.missing_review_count,
            "needs_embedding_count": self.needs_embedding_count,
            "selected_review_count": self.selected_review_count,
            "selected_embedding_count": self.selected_embedding_count,
            "complete": self.source_count == self.ready_count + self.rejected_count,
        }


def fingerprint_question(question_text, salt):
    if not isinstance(question_text, str):
        raise ValueError("R6D invalid private review fingerprint input")
    try:
        canonical_salt = _uuid.UUID(str(salt))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("R6D invalid private review fingerprint salt") from exc
    return _hashlib.sha256(
        canonical_salt.bytes + b"\x00" + question_text.encode("utf-8")
    ).hexdigest()


def _valid_review_fingerprint(value):
    return (type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def review_cache_state(question_text, cache, *, sanitizer_model,
                       deidentification_version, aggregation_version,
                       embedding_model, review_contract_fingerprint=None):
    if (review_contract_fingerprint is not None
            and not _valid_review_fingerprint(review_contract_fingerprint)):
        raise ValueError("R6D invalid review provenance")

    if cache is None or cache.get("source_fingerprint_salt") is None:
        return "missing_review"

    salt = cache["source_fingerprint_salt"]
    fingerprint = cache.get("source_fingerprint")

    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        return "missing_review"

    valid_source = _hmac.compare_digest(
        fingerprint, fingerprint_question(question_text, salt)
    )
    valid_contract = (
        cache.get("sanitizer_model") == sanitizer_model
        and cache.get("deidentification_version") == deidentification_version
        and cache.get("aggregation_version") == aggregation_version
        and (review_contract_fingerprint is None
             or cache.get("review_contract_fingerprint")
                == review_contract_fingerprint)
    )

    if not valid_source or not valid_contract:
        return "missing_review"

    status = cache.get("review_status")

    if status == "rejected":
        if (cache.get("theme_label") is not None
                or cache.get("theme_summary") is not None
                or cache.get("has_embedding") is not False):
            raise ValueError("R6D malformed rejected cache entry")
        return "rejected"

    if status != "approved":
        raise ValueError("R6D unexpected private cache review status")

    if (not isinstance(cache.get("theme_label"), str)
            or not cache["theme_label"].strip()
            or not isinstance(cache.get("theme_summary"), str)
            or not cache["theme_summary"].strip()):
        raise ValueError("R6D malformed approved cache entry")

    if (cache.get("has_embedding") is True
            and cache.get("theme_embedding_model") == embedding_model
            and cache.get("embedded_at") is not None):
        return "ready"

    return "needs_embedding"


def inspect_private_review_window(conn, *, expected_database,
                                   verify_database, window_ended_at,
                                   sanitizer_model, deidentification_version,
                                   aggregation_version, embedding_model,
                                   per_run_source_budget, maximum_source_budget,
                                   momentum_window_days,
                                   on_source=None, on_ready=None, on_revision=None,
                                   review_contract_fingerprint=None):
    if (not isinstance(window_ended_at, _dt.datetime)
            or window_ended_at.tzinfo is None
            or window_ended_at.utcoffset() is None):
        raise ValueError("R6D window end must be timezone-aware")

    if (type(maximum_source_budget) is not int or maximum_source_budget < 1
            or type(per_run_source_budget) is not int
            or not 1 <= per_run_source_budget <= maximum_source_budget):
        raise ValueError("R6D invalid per-run source budget")

    if type(momentum_window_days) is not int or momentum_window_days < 1:
        raise ValueError("R6D invalid momentum window")

    for label, value in (
        ("model", sanitizer_model),
        ("deid", deidentification_version),
        ("aggregation", aggregation_version),
        ("embedding", embedding_model),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("R6D invalid cache version: " + label)

    if on_source is not None and not callable(on_source):
        raise ValueError("R6D invalid on_source callback")
    if on_ready is not None and not callable(on_ready):
        raise ValueError("R6D invalid on_ready callback")
    if on_revision is not None and not callable(on_revision):
        raise ValueError("R6D invalid on_revision callback")
    end = window_ended_at.astimezone(_dt.timezone.utc)
    if end > _dt.datetime.now(_dt.timezone.utc):
        raise ValueError("R6D future window end")

    start = end - _dt.timedelta(days=2 * momentum_window_days)
    states = {
        name: 0 for name in (
            "ready", "rejected", "missing_review", "needs_embedding"
        )
    }
    selected = []

    try:
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_database() AS name, inet_server_addr() AS host, "
                "current_setting('transaction_read_only') AS readonly"
            )
            identity = cur.fetchone()

            verify_database(
                expected_database=expected_database,
                database=identity["name"],
                host=identity["host"],
                readonly=identity["readonly"],
                access="read",
            )

        # Optional publication evidence, captured in this same
        # read-only REPEATABLE READ source transaction.
        if on_revision is not None:
            with conn.cursor() as revision_cur:
                revision_cur.execute("""
                    SELECT source_revision
                    FROM public.temple_context_revision_state
                    WHERE singleton_id = 1
                """)
                revision_row = revision_cur.fetchone()
                if revision_row is None:
                    raise ValueError("R6D source revision ledger missing")
                revision_value = revision_row["source_revision"]
                if type(revision_value) is not int or revision_value < 0:
                    raise ValueError("R6D invalid source revision")
                on_revision(revision_value)

        with conn.cursor(name="r6d_private_cache_stream") as cur:
            cur.itersize = 200
            cur.execute("""
                SELECT i.id AS interaction_id,
                       i.user_id,
                       i.question_text,
                       i.created_at,
                       c.source_fingerprint_salt,
                       c.source_fingerprint,
                       c.deidentification_version,
                       c.aggregation_version,
                       c.sanitizer_model,
                       c.review_contract_fingerprint,
                       c.review_status,
                       c.theme_label,
                       c.theme_summary,
                       c.theme_embedding_model,
                       c.embedded_at,
                       (c.theme_embedding IS NOT NULL) AS has_embedding,
                       CASE WHEN %s THEN c.theme_embedding::text ELSE NULL END
                           AS embedding_text
                FROM public.oracle_interactions AS i
                LEFT JOIN public.temple_context_question_review_cache AS c
                  ON c.interaction_id = i.id
                WHERE i.user_id IS NOT NULL
                  AND i.question_text IS NOT NULL
                  AND BTRIM(i.question_text) <> ''
                  AND i.created_at >= %s
                  AND i.created_at < %s
                ORDER BY i.created_at, i.id
            """, (on_ready is not None, start, end))

            for row in cur:
                cache = (
                    row if row["source_fingerprint_salt"] is not None
                    else None
                )

                status = review_cache_state(
                    row["question_text"],
                    cache,
                    sanitizer_model=sanitizer_model,
                    deidentification_version=deidentification_version,
                    aggregation_version=aggregation_version,
                    embedding_model=embedding_model,
                    review_contract_fingerprint=review_contract_fingerprint,
                )

                states[status] += 1
                if on_source is not None:
                    on_source(row["user_id"])
                if status == "ready" and on_ready is not None:
                    on_ready((row["theme_label"], row["theme_summary"],
                              row["embedding_text"], row["user_id"],
                              row["created_at"]))

                if (
                    status in ("missing_review", "needs_embedding")
                    and len(selected) < per_run_source_budget
                ):
                    selected.append({
                        "interaction_id": row["interaction_id"],
                        "user_id": row["user_id"],
                        "question_text": row["question_text"],
                        "created_at": row["created_at"],
                        "review_state": status,
                        "theme_label": (
                            row["theme_label"]
                            if status == "needs_embedding" else None
                        ),
                        "theme_summary": (
                            row["theme_summary"]
                            if status == "needs_embedding" else None
                        ),
                        "source_fingerprint_salt": (
                            row["source_fingerprint_salt"]
                            if status == "needs_embedding" else None
                        ),
                        "source_fingerprint": (
                            row["source_fingerprint"]
                            if status == "needs_embedding" else None
                        ),
                    })
    finally:
        conn.rollback()
        conn.close()

    return PrivateWindowInventory(
        source_count=sum(states.values()),
        ready_count=states["ready"],
        rejected_count=states["rejected"],
        missing_review_count=states["missing_review"],
        needs_embedding_count=states["needs_embedding"],
        selected_review_count=sum(
            item["review_state"] == "missing_review" for item in selected
        ),
        selected_embedding_count=sum(
            item["review_state"] == "needs_embedding" for item in selected
        ),
        private_work_items=tuple(selected),
    )


def persist_private_temple_review_cache(
    *,
    get_connection,
    verify_database,
    expected_database,
    interaction_id,
    expected_user_id,
    question_text,
    created_at,
    sanitizer_model,
    deidentification_version,
    aggregation_version,
    operation,
    extraction_result=None,
    theme_label=None,
    theme_summary=None,
    embedding_model=None,
    embedding=None,
    authorize_writes=False,
    review_contract_fingerprint=None,
):
    """Persist one private cache state for an unchanged locked interaction.

    The caller must use the accepted independent privacy reviewer.
    No providers are called here. No private source enters the result.
    """
    import math as _math

    if authorize_writes is not True:
        raise ValueError("R6D cache write requires explicit authorization")
    if operation not in ("review", "embedding"):
        raise ValueError("R6D unsupported cache operation")

    for value in (
        expected_database, sanitizer_model, deidentification_version,
        aggregation_version,
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("R6D missing cache write contract")

    if not isinstance(question_text, str) or not question_text.strip():
        raise ValueError("R6D invalid cache source")

    if (not isinstance(created_at, _dt.datetime)
            or created_at.tzinfo is None or created_at.utcoffset() is None):
        raise ValueError("R6D cache source timestamp must be aware")

    try:
        source_id = _uuid.UUID(str(interaction_id))
        owner_id = _uuid.UUID(str(expected_user_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("R6D invalid private interaction authority") from exc

    if operation == "review":
        if (embedding is not None or embedding_model is not None
                or theme_label is not None or theme_summary is not None):
            raise ValueError("R6D review cannot include an embedding")

        if not isinstance(extraction_result, dict) or set(extraction_result) != {
            "safe_to_aggregate", "theme_label", "theme_summary", "reason",
            "deidentification_version", "aggregation_version",
        }:
            raise ValueError("R6D extraction result contract mismatch")

        is_approved = extraction_result["safe_to_aggregate"]
        if type(is_approved) is not bool:
            raise ValueError("R6D extraction approval must be boolean")

        if (extraction_result["deidentification_version"]
                != deidentification_version
                or extraction_result["aggregation_version"]
                != aggregation_version):
            raise ValueError("R6D review version mismatch")

        if (not isinstance(extraction_result["reason"], str)
                or not extraction_result["reason"].strip()):
            raise ValueError("R6D invalid privacy-review reason")

        approved_label = extraction_result["theme_label"]
        approved_summary = extraction_result["theme_summary"]

        if (not isinstance(approved_label, str)
                or not isinstance(approved_summary, str)):
            raise ValueError("R6D invalid privacy-reviewed theme")

        if is_approved:
            if not approved_label.strip() or not approved_summary.strip():
                raise ValueError("R6D approved theme is empty")
            approved_label = approved_label.strip()
            approved_summary = approved_summary.strip()
        else:
            if approved_label or approved_summary:
                raise ValueError("R6D rejected review includes a theme")
            approved_label = None
            approved_summary = None

    else:
        if extraction_result is not None:
            raise ValueError("R6D embedding cannot rewrite review")

        if (not isinstance(theme_label, str) or not theme_label.strip()
                or not isinstance(theme_summary, str)
                or not theme_summary.strip()
                or not isinstance(embedding_model, str)
                or not embedding_model.strip()):
            raise ValueError("R6D invalid embedding authority")

        if (not isinstance(embedding, list) or len(embedding) != 1536
                or any(
                    type(value) not in (int, float)
                    or not _math.isfinite(float(value))
                    for value in embedding
                )
                or not any(float(value) != 0 for value in embedding)):
            raise ValueError("R6D invalid embedding vector")

        vector_text = "[" + ",".join(
            format(float(value), ".17g") for value in embedding
        ) + "]"

    if authorize_writes and not _valid_review_fingerprint(
        review_contract_fingerprint
    ):
        raise ValueError("R6D cache write requires review provenance")

    from pathlib import Path as _Path
    from config.temple_context_publication_contract import (
        resolve_temple_context_contract,
    )
    from config.temple_momentum_policy import TEMPLE_MOMENTUM_POLICY

    verified_contract = resolve_temple_context_contract(
        main_source_path=(
            _Path(__file__).resolve().parents[1] / "main.py"
        ),
        sanitizer_model=sanitizer_model,
        deidentification_version=deidentification_version,
        aggregation_version=aggregation_version,
        embedding_model=embedding_model or "text-embedding-3-small",
        policy=TEMPLE_MOMENTUM_POLICY,
    )

    if (
        review_contract_fingerprint
        != verified_contract["review_contract_fingerprint"]
    ):
        raise ValueError("R6D unverified private review provenance")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_database() AS name, inet_server_addr() AS host, "
                "current_setting('transaction_read_only') AS readonly"
            )
            identity = cur.fetchone()

            verify_database(
                expected_database=expected_database,
                database=identity["name"],
                host=identity["host"],
                readonly=identity["readonly"],
                access="write",
            )

            # Revalidate and lock the private source through the restricted
            # four-argument helper; do not grant this writer source UPDATE.
            cur.execute("""
                SELECT public._r6d_lock_review_source(
                    %s::uuid, %s::uuid, %s, %s::timestamptz
                ) AS locked
            """, (str(source_id), str(owner_id), question_text, created_at))
            locked = cur.fetchone()
            if locked is None or locked["locked"] is not True:
                raise ValueError("R6D private source changed or was deleted")

            cur.execute("""
                SELECT source_fingerprint_salt, source_fingerprint,
                       deidentification_version, aggregation_version,
                       sanitizer_model, review_contract_fingerprint,
                       review_status, theme_label,
                       theme_summary, theme_embedding_model, embedded_at,
                       (theme_embedding IS NOT NULL) AS has_embedding
                FROM public.temple_context_question_review_cache
                WHERE interaction_id = %s::uuid
                FOR UPDATE
            """, (str(source_id),))
            cached = cur.fetchone()

            state = review_cache_state(
                question_text, cached,
                sanitizer_model=sanitizer_model,
                deidentification_version=deidentification_version,
                aggregation_version=aggregation_version,
                embedding_model=embedding_model or "not-requested",
                review_contract_fingerprint=review_contract_fingerprint,
            )

            if operation == "review":
                if state != "missing_review":
                    outcome = "previous_review_preserved"
                    wrote = False
                else:
                    salt = _uuid.uuid4()
                    source_hash = fingerprint_question(question_text, salt)

                    cur.execute("""
                        INSERT INTO public.temple_context_question_review_cache (
                            interaction_id, source_fingerprint_salt,
                            source_fingerprint, deidentification_version,
                            aggregation_version, sanitizer_model,
                            review_contract_fingerprint, review_status,
                            theme_label, theme_summary,
                            theme_embedding, theme_embedding_model, embedded_at,
                            reviewed_at
                        ) VALUES (
                            %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s,
                            NULL, NULL, NULL, NOW()
                        )
                        ON CONFLICT (interaction_id) DO UPDATE SET
                            source_fingerprint_salt = EXCLUDED.source_fingerprint_salt,
                            source_fingerprint = EXCLUDED.source_fingerprint,
                            deidentification_version = EXCLUDED.deidentification_version,
                            aggregation_version = EXCLUDED.aggregation_version,
                            sanitizer_model = EXCLUDED.sanitizer_model,
                            review_contract_fingerprint = EXCLUDED.review_contract_fingerprint,
                            review_status = EXCLUDED.review_status,
                            theme_label = EXCLUDED.theme_label,
                            theme_summary = EXCLUDED.theme_summary,
                            theme_embedding = NULL,
                            theme_embedding_model = NULL,
                            embedded_at = NULL,
                            reviewed_at = NOW()
                    """, (
                        str(source_id), str(salt), source_hash,
                        deidentification_version, aggregation_version,
                        sanitizer_model, review_contract_fingerprint,
                        "approved" if is_approved else "rejected",
                        approved_label, approved_summary,
                    ))

                    outcome = "review_saved"
                    wrote = True

            elif state == "ready":
                if (cached["theme_label"] != theme_label
                        or cached["theme_summary"] != theme_summary):
                    raise ValueError("R6D embedding theme changed")
                outcome = "previous_embedding_preserved"
                wrote = False

            else:
                if (state != "needs_embedding"
                        or cached["theme_label"] != theme_label
                        or cached["theme_summary"] != theme_summary):
                    raise ValueError(
                        "R6D embedding lacks a matching approved review"
                    )

                cur.execute("""
                    UPDATE public.temple_context_question_review_cache
                    SET theme_embedding = %s::vector,
                        theme_embedding_model = %s,
                        embedded_at = NOW()
                    WHERE interaction_id = %s::uuid
                """, (vector_text, embedding_model, str(source_id)))

                if cur.rowcount != 1:
                    raise ValueError("R6D cache embedding row changed")

                outcome = "embedding_saved"
                wrote = True

        if wrote:
            conn.commit()
        else:
            conn.rollback()

        return {"status": outcome, "database_write": wrote}

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
