"""Manual Local R6D private cache processing; inventory-only by default.

No Oracle, aggregate writer, scheduler, or direct inference provider import.
"""
from datetime import datetime, timezone
import math
import uuid

from services.temple_context_review_cache import (
    inspect_private_review_window, persist_private_temple_review_cache,
    review_cache_state,
)


def _check_source(get_connection, verify_database, expected_database, item,
                  sanitizer_model, deidentification_version,
                  aggregation_version, embedding_model,
                  review_contract_fingerprint=None):
    """Revalidate before provider use; cache writer locks source at commit."""
    conn = get_connection()
    try:
        conn.set_session(readonly=True, isolation_level='READ COMMITTED')
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_database() AS name, inet_server_addr() AS host, "
                "current_setting('transaction_read_only') AS readonly"
            )
            identity = cur.fetchone()
            verify_database(expected_database=expected_database,
                            database=identity['name'], host=identity['host'],
                            readonly=identity['readonly'], access='read')
            cur.execute("""
                SELECT i.user_id, i.question_text, i.created_at,
                       c.source_fingerprint_salt, c.source_fingerprint,
                       c.deidentification_version, c.aggregation_version,
                       c.sanitizer_model, c.review_contract_fingerprint,
                       c.review_status, c.theme_label,
                       c.theme_summary, c.theme_embedding_model, c.embedded_at,
                       (c.theme_embedding IS NOT NULL) AS has_embedding
                FROM public.oracle_interactions AS i
                LEFT JOIN public.temple_context_question_review_cache AS c
                  ON c.interaction_id = i.id
                WHERE i.id = %s::uuid
            """, (str(uuid.UUID(str(item['interaction_id']))),))
            row = cur.fetchone()
            if (row is None or row['user_id'] is None
                    or uuid.UUID(str(row['user_id'])) != uuid.UUID(str(item['user_id']))
                    or row['question_text'] != item['question_text']
                    or row['created_at'] != item['created_at']):
                raise ValueError('R6D private source changed')
            cache = row if row['source_fingerprint_salt'] is not None else None
            current = review_cache_state(
                item['question_text'], cache, sanitizer_model=sanitizer_model,
                deidentification_version=deidentification_version,
                aggregation_version=aggregation_version, embedding_model=embedding_model,
                review_contract_fingerprint=review_contract_fingerprint)
            if current != item['review_state']:
                raise ValueError('R6D private cache state changed')
            if current == 'needs_embedding' and (
                    row['theme_label'] != item['theme_label']
                    or row['theme_summary'] != item['theme_summary']
                    or row['source_fingerprint'] != item['source_fingerprint']
                    or str(row['source_fingerprint_salt']) !=
                       str(item['source_fingerprint_salt'])):
                raise ValueError('R6D approved theme changed')
    finally:
        try:
            if not conn.closed:
                conn.rollback()
        finally:
            conn.close()


def collect_private_temple_review_increment(
    *, get_connection, verify_database, expected_database, window_ended_at,
    sanitizer_model, deidentification_version, aggregation_version,
    embedding_model, policy, extract_theme=None, embed_theme=None,
    per_run_source_budget=1, allow_provider_calls=False,
    authorize_cache_writes=False, get_writer_connection=None,
):
    """One bounded pass; each authorized cache write commits independently."""
    if authorize_cache_writes and not callable(get_writer_connection):
        raise ValueError("STOP: R6D restricted cache writer required")

    if (expected_database != 'godinc_dev' or not callable(get_connection)
            or not callable(verify_database)):
        raise ValueError('R6D Local database authority required')
    if (not isinstance(window_ended_at, datetime)
            or window_ended_at.tzinfo is None
            or window_ended_at.utcoffset() is None
            or window_ended_at.astimezone(timezone.utc) > datetime.now(timezone.utc)):
        raise ValueError('R6D invalid window time')
    if (type(allow_provider_calls) is not bool
            or type(authorize_cache_writes) is not bool
            or (authorize_cache_writes and not allow_provider_calls)):
        raise ValueError('R6D invalid explicit authorization')
    if (policy.version != 'r6d-wave-v2' or policy.momentum_window_days != 14
            or policy.legacy_source_row_budget != 50
            or policy.live_oracle_enabled or policy.automatic_scheduling_enabled):
        raise ValueError('R6D policy changed')
    if (type(per_run_source_budget) is not int or not 1 <= per_run_source_budget
            <= policy.legacy_source_row_budget):
        raise ValueError('R6D source budget exceeded')
    if any(not isinstance(v, str) or not v.strip() for v in (
            sanitizer_model, deidentification_version,
            aggregation_version, embedding_model)):
        raise ValueError('R6D cache version missing')
    if allow_provider_calls and (not callable(extract_theme)
                                 or not callable(embed_theme)):
        raise ValueError('R6D provider adapters required')

    provenance_kwargs = {}

    if allow_provider_calls:
        from pathlib import Path as _Path
        from config.temple_context_publication_contract import (
            resolve_temple_context_contract,
        )

        main_source = (
            _Path(__file__).resolve().parents[1] / "main.py"
        )



        resolved = resolve_temple_context_contract(
            main_source_path=main_source,
            sanitizer_model=sanitizer_model,
            deidentification_version=deidentification_version,
            aggregation_version=aggregation_version,
            embedding_model=embedding_model,
            policy=policy,
        )

        review_fp = resolved["review_contract_fingerprint"]
        if (type(review_fp) is not str
                or len(review_fp) != 64
                or any(c not in "0123456789abcdef"
                       for c in review_fp)):
            raise ValueError("R6D invalid review authority")

        provenance_kwargs = {
            "review_contract_fingerprint": review_fp
        }

    conn = get_connection()
    try:
        inventory = inspect_private_review_window(
            conn, expected_database=expected_database,
            verify_database=verify_database, window_ended_at=window_ended_at,
            sanitizer_model=sanitizer_model,
            deidentification_version=deidentification_version,
            aggregation_version=aggregation_version,
            embedding_model=embedding_model,
            per_run_source_budget=per_run_source_budget,
            maximum_source_budget=policy.legacy_source_row_budget,
            momentum_window_days=policy.momentum_window_days,
            **provenance_kwargs)
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()

    info, items = inventory.public_summary(), inventory.private_work_items
    if (len(items) > per_run_source_budget or len(items) !=
            info['selected_review_count'] + info['selected_embedding_count']):
        raise ValueError('R6D invalid pending work count')
    result = dict(info, status='inventory_only', selected_source_count=len(items),
                  processed_count=0, reviewed_count=0, embedding_count=0,
                  provider_approved_count=0, provider_rejected_count=0,
                  cache_writes=0, snapshot_preserved=True,
                  oracle_activation_permitted=False,
                  automatic_scheduling_enabled=False)
    if not allow_provider_calls:
        return result
    result['status'] = ('incremental_cache_updated' if authorize_cache_writes
                        else 'provider_preview_only') if items else 'no_pending_work'
    for item in items:
        phase = 'source_recheck'
        try:
            if item['review_state'] not in ('missing_review', 'needs_embedding'):
                raise ValueError('R6D invalid private work state')
            _check_source(get_connection, verify_database, expected_database, item,
                          sanitizer_model, deidentification_version,
                          aggregation_version, embedding_model,
                          **provenance_kwargs)
            shared = dict(
                          verify_database=verify_database,
                          expected_database=expected_database,
                          interaction_id=item['interaction_id'],
                          expected_user_id=item['user_id'],
                          question_text=item['question_text'],
                          created_at=item['created_at'],
                          sanitizer_model=sanitizer_model,
                          deidentification_version=deidentification_version,
                          aggregation_version=aggregation_version)
            shared.update(provenance_kwargs)
            if item['review_state'] == 'missing_review':
                phase = 'privacy_review'
                review = extract_theme(item['question_text'],
                    forbidden_identifiers={'user_id': str(item['user_id'])})
                keys = {'safe_to_aggregate', 'theme_label', 'theme_summary',
                        'reason', 'deidentification_version', 'aggregation_version'}
                if (not isinstance(review, dict) or set(review) != keys
                        or type(review['safe_to_aggregate']) is not bool
                        or review['deidentification_version'] != deidentification_version
                        or review['aggregation_version'] != aggregation_version
                        or not isinstance(review['reason'], str)
                        or not review['reason'].strip()
                        or not isinstance(review['theme_label'], str)
                        or not isinstance(review['theme_summary'], str)
                        or (review['safe_to_aggregate'] and not
                            (review['theme_label'].strip() and
                             review['theme_summary'].strip()))
                        or (not review['safe_to_aggregate'] and
                            (review['theme_label'] or review['theme_summary']))):
                    raise ValueError('R6D invalid private review')
                result['reviewed_count'] += 1
                result['provider_approved_count' if review['safe_to_aggregate']
                       else 'provider_rejected_count'] += 1
                write_args = dict(shared, operation='review', extraction_result=review)
                expected = {'review_saved', 'previous_review_preserved'}
            else:
                phase = 'embedding'
                label, summary = item['theme_label'], item['theme_summary']
                if not isinstance(label, str) or not isinstance(summary, str) or not (
                        label.strip() and summary.strip()):
                    raise ValueError('R6D invalid approved theme')
                vector = embed_theme(label.strip() + '\n' + summary.strip())
                if (not isinstance(vector, list) or len(vector) != 1536
                        or any(type(n) not in (int, float) or
                               not math.isfinite(float(n)) for n in vector)
                        or not any(float(n) != 0 for n in vector)):
                    raise ValueError('R6D invalid embedding')
                result['embedding_count'] += 1
                write_args = dict(shared, operation='embedding', theme_label=label,
                                  theme_summary=summary, embedding_model=embedding_model,
                                  embedding=vector)
                expected = {'embedding_saved', 'previous_embedding_preserved'}
            phase = 'cache_write'
            if authorize_cache_writes:
                outcome = persist_private_temple_review_cache(
                    get_connection=get_writer_connection,
                    **write_args, authorize_writes=True)
                if (not isinstance(outcome, dict) or outcome.get('status') not in expected
                        or type(outcome.get('database_write')) is not bool
                        or outcome['database_write'] !=
                           outcome['status'].endswith('_saved')):
                    raise ValueError('R6D cache writer outcome mismatch')
                result['cache_writes'] += int(outcome['database_write'])
            result['processed_count'] += 1
        except Exception:
            # Provider errors can include private question text: suppress details.
            return dict(result, status='failed_closed', failure_phase=phase)
    return result
