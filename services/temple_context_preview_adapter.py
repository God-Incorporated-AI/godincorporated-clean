"""R6D private-window to shadow-preview adapter.

Manual read-only preview. No provider calls, cache writes, aggregate
persistence, scheduling, or Oracle prompt integration.
Contributor identifiers are transient and never returned.
"""
import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone

from services.temple_context_review_cache import inspect_private_review_window
from services.temple_context_snapshot_assembly import prepare_cached_shadow_preview


def preview_cached_temple_shadow_window(
    *, get_connection, verify_database, expected_database,
    window_ended_at, evaluation_at, sanitizer_model,
    deidentification_version, aggregation_version, embedding_model,
    policy, cluster_themes, build_snapshot, score_snapshot, evaluate_waves,
    capture_source_revision=False,
):
    """Read one consistent 28-day window and assemble a shadow preview."""
    if type(capture_source_revision) is not bool:
        raise ValueError('R6D invalid revision capture switch')
    if not callable(get_connection) or not callable(verify_database):
        raise ValueError('R6D requires authorized database functions')
    for value in (window_ended_at, evaluation_at):
        if (not isinstance(value, datetime) or value.tzinfo is None
                or value.utcoffset() is None):
            raise ValueError('R6D requires aware preview timestamps')
    end = window_ended_at.astimezone(timezone.utc)
    evaluated = evaluation_at.astimezone(timezone.utc)
    if not end <= evaluated <= datetime.now(timezone.utc):
        raise ValueError('R6D invalid preview time window')
    if (policy.version != 'r6d-wave-v2'
            or policy.active_seeker_lookback_days != 28
            or policy.momentum_window_days != 14
            or policy.wave_baseline_days != 14
            or policy.wave_window_hours != 48
            or policy.minimum_active_seekers != 500
            or policy.minimum_live_wave_contributors != 50
            or policy.surge_lift_ratio != 2.0
            or policy.legacy_source_row_budget != 50
            or policy.live_oracle_enabled
            or policy.automatic_scheduling_enabled):
        raise ValueError('R6D preview policy changed')

    source_count = 0
    source_revision = None
    contributors = set()
    observations = []
    vectors = {}
    vector_hashes = {}

    def canonical_owner(value):
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('R6D invalid authenticated source owner') from None

    def on_source(user_id):
        nonlocal source_count
        source_count += 1
        contributors.add(canonical_owner(user_id))

    def on_ready(item):
        if not isinstance(item, tuple) or len(item) != 5:
            raise ValueError('R6D invalid ready callback')
        label, summary, encoded, user_id, observed_at = item
        if (not isinstance(label, str) or not label.strip()
                or not isinstance(summary, str) or not summary.strip()
                or not isinstance(encoded, str)):
            raise ValueError('R6D invalid reviewed theme')
        if (not isinstance(observed_at, datetime) or observed_at.tzinfo is None
                or observed_at.utcoffset() is None):
            raise ValueError('R6D invalid observation time')
        at = observed_at.astimezone(timezone.utc)
        if not end - timedelta(days=28) <= at < end:
            raise ValueError('R6D observation outside source window')
        try:
            vector = json.loads(encoded)
        except (ValueError, TypeError):
            raise ValueError('R6D malformed cached embedding') from None
        if (not isinstance(vector, list) or len(vector) != 1536
                or any(type(v) not in (int, float) or not math.isfinite(float(v))
                       for v in vector)
                or not any(float(v) != 0 for v in vector)):
            raise ValueError('R6D invalid cached embedding')
        key = label.strip() + '\n' + summary.strip()
        digest = hashlib.sha256(encoded.encode('utf-8')).digest()
        if key not in vectors or digest < vector_hashes[key]:
            vectors[key] = [float(v) for v in vector]
            vector_hashes[key] = digest
        observations.append({
            'theme_key': key,
            'contributor_key': canonical_owner(user_id),
            'observed_at': at,
        })

    def on_revision(value):
        nonlocal source_revision
        if (source_revision is not None
                or type(value) is not int or value < 0):
            raise ValueError('R6D invalid captured source revision')
        source_revision = value

    contract = None
    if capture_source_revision:
        from pathlib import Path as _Path
        from config.temple_context_publication_contract import (
            resolve_temple_context_contract,
        )

        contract = resolve_temple_context_contract(
            main_source_path=(
                _Path(__file__).resolve().parents[1] / "main.py"
            ),
            sanitizer_model=sanitizer_model,
            deidentification_version=deidentification_version,
            aggregation_version=aggregation_version,
            embedding_model=embedding_model,
            policy=policy,
        )

    revision_callback = (
        {
            'on_revision': on_revision,
            'review_contract_fingerprint':
                contract['review_contract_fingerprint'],
        }
        if capture_source_revision else {}
    )

    conn = get_connection()
    try:
        inventory = inspect_private_review_window(
            conn, expected_database=expected_database,
            verify_database=verify_database, window_ended_at=end,
            sanitizer_model=sanitizer_model,
            deidentification_version=deidentification_version,
            aggregation_version=aggregation_version,
            embedding_model=embedding_model,
            per_run_source_budget=policy.legacy_source_row_budget,
            maximum_source_budget=policy.legacy_source_row_budget,
            momentum_window_days=policy.momentum_window_days,
            on_source=on_source, on_ready=on_ready,
            **revision_callback,
        )
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()

    if capture_source_revision and source_revision is None:
        raise ValueError('R6D source revision not captured')

    info = inventory.public_summary()
    if (source_count != info['source_count_28d']
            or len(observations) != info['ready_approved_count']
            or source_count != sum(info[k] for k in (
                'ready_approved_count', 'reviewed_rejected_count',
                'missing_review_count', 'needs_embedding_count',
            ))):
        raise ValueError('R6D inconsistent private source coverage')

    from dataclasses import replace as _replace
    assembled = prepare_cached_shadow_preview(
        source_row_count=source_count,
        approved_source_count=info['ready_approved_count'],
        rejected_source_count=info['reviewed_rejected_count'],
        active_seekers_28d=len(contributors),
        observations=observations, theme_embeddings=vectors,
        embedding_model=embedding_model,
        deidentification_version=deidentification_version,
        aggregation_version=aggregation_version,
        window_ended_at=end, evaluation_at=evaluated, policy=policy,
        cluster_themes=cluster_themes, build_snapshot=build_snapshot,
        score_snapshot=score_snapshot, evaluate_waves=evaluate_waves,
    )
    return _replace(
        assembled,
        source_revision=source_revision,
        review_contract_fingerprint=(
            contract['review_contract_fingerprint']
            if contract is not None else None
        ),
        publication_contract_fingerprint=(
            contract['publication_contract_fingerprint']
            if contract is not None else None
        ),
    )
