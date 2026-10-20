"""R6D pure assembly from a complete private review window.

No database, providers, scheduling, or Oracle integration.
Contributor identities are transient and never returned.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True, slots=True)
class CachedShadowPreview:
    summary: dict
    aggregate_rows: tuple = field(repr=False)
    wave_result: dict = field(repr=False)
    source_revision: int | None = field(default=None, repr=False)
    review_contract_fingerprint: str | None = field(default=None, repr=False)
    publication_contract_fingerprint: str | None = field(default=None, repr=False)
    window_ended_at: datetime | None = field(default=None, repr=False)
    evaluated_at: datetime | None = field(default=None, repr=False)

    def public_summary(self):
        return dict(self.summary)


def prepare_cached_shadow_preview(
    *, source_row_count, approved_source_count, rejected_source_count,
    active_seekers_28d, observations, theme_embeddings, embedding_model,
    deidentification_version, aggregation_version, window_ended_at,
    evaluation_at, policy, cluster_themes, build_snapshot,
    score_snapshot, evaluate_waves,
):
    """Return only reviewed aggregates; incomplete input returns no rows."""
    for name, count in (
        ("source", source_row_count),
        ("approved", approved_source_count),
        ("rejected", rejected_source_count),
        ("active", active_seekers_28d),
    ):
        if type(count) is not int or count < 0:
            raise ValueError("R6D invalid " + name + " count")
    if (approved_source_count + rejected_source_count > source_row_count
            or active_seekers_28d > source_row_count):
        raise ValueError("R6D inconsistent private window counts")
    if not isinstance(observations, list) or not isinstance(theme_embeddings, dict):
        raise ValueError("R6D invalid private window inputs")
    for name, value in (
        ("embedding model", embedding_model),
        ("deidentification version", deidentification_version),
        ("aggregation version", aggregation_version),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("R6D missing " + name)
    for name, value in (("window end", window_ended_at),
                        ("evaluation", evaluation_at)):
        if (not isinstance(value, datetime) or value.tzinfo is None
                or value.utcoffset() is None):
            raise ValueError("R6D timezone-aware " + name + " required")
    end = window_ended_at.astimezone(timezone.utc)
    evaluated = evaluation_at.astimezone(timezone.utc)
    if not end <= evaluated <= datetime.now(timezone.utc):
        raise ValueError("R6D invalid evaluation/window times")
    if (policy.version != "r6d-wave-v2"
            or policy.active_seeker_lookback_days != 28
            or policy.momentum_window_days != 14
            or policy.wave_baseline_days != 14
            or policy.wave_window_hours != 48
            or policy.minimum_active_seekers != 500
            or policy.minimum_live_wave_contributors != 50
            or policy.surge_lift_ratio != 2.0
            or policy.live_oracle_enabled
            or policy.automatic_scheduling_enabled):
        raise ValueError("R6D policy guard failed")

    summary = dict(
        policy_version=policy.version, source_count_28d=source_row_count,
        active_seekers_28d=active_seekers_28d,
        approved_count=approved_source_count,
        rejected_count=rejected_source_count,
        pending_count=source_row_count-approved_source_count-rejected_source_count,
        theme_count=0, qualifying_wave_count=0,
        snapshot_preserved=True, oracle_activation_permitted=False,
        automatic_scheduling_enabled=False,
    )

    def stopped(status):
        return CachedShadowPreview(dict(summary, status=status), (), {},
                                   window_ended_at=end, evaluated_at=evaluated)

    if summary["pending_count"]:
        return stopped("cache_incomplete")
    if len(observations) != approved_source_count:
        raise ValueError("R6D approved observation count mismatch")
    if not observations and theme_embeddings:
        raise ValueError("R6D empty observations contain unexpected theme embeddings")
    if source_row_count == 0:
        return stopped("no_eligible_activity")
    if not observations:
        return stopped("no_approved_themes")
    if not theme_embeddings:
        raise ValueError("R6D missing approved theme embeddings")

    clusters = cluster_themes(
        theme_embeddings, similarity_threshold=policy.similarity_threshold
    )
    recent_start = end - timedelta(days=policy.momentum_window_days)
    snapshots = build_snapshot(
        clusters, observations,
        minimum_contributors=policy.minimum_shadow_contributors,
        recent_window_started_at=recent_start,
        recent_window_ended_at=end,
    )
    if not snapshots:
        raise ValueError("R6D missing aggregate snapshots")

    rows = []
    for snap in snapshots:
        representative = min(snap["theme_keys"])
        if representative not in theme_embeddings or "\n" not in representative:
            raise ValueError("R6D missing reviewed theme representative")
        label, summary_text = representative.split("\n", 1)
        if not label.strip() or label != label.strip() or not summary_text.strip():
            raise ValueError("R6D malformed reviewed representative")
        scores = score_snapshot(
            snap, evaluation_at=evaluated,
            frequency_weight=policy.frequency_weight,
            acceleration_weight=policy.acceleration_weight,
            recency_half_life_seconds=policy.recency_half_life_days * 86400,
        )
        rows.append(dict(
            theme_label=label, theme_summary=summary_text,
            theme_embedding=theme_embeddings[representative],
            theme_embedding_model=embedding_model,
            observation_count=snap["observation_count"],
            contributor_count=snap["contributor_count"],
            prior_observation_count=snap["prior_observation_count"],
            prior_contributor_count=snap["prior_contributor_count"],
            prior_window_started_at=snap["prior_window_started_at"],
            prior_window_ended_at=snap["prior_window_ended_at"],
            recent_observation_count=snap["recent_observation_count"],
            recent_contributor_count=snap["recent_contributor_count"],
            recent_window_started_at=snap["recent_window_started_at"],
            recent_window_ended_at=snap["recent_window_ended_at"],
            momentum_score=scores["momentum_score"],
            acceleration_score=scores["acceleration_score"],
            first_observed_at=snap["first_observed_at"],
            last_observed_at=snap["last_observed_at"],
            last_computed_at=evaluated,
            deidentification_version=deidentification_version,
            aggregation_version=aggregation_version,
            status=snap["status"], daily=snap["daily"],
        ))

    waves = evaluate_waves(
        clusters, observations, active_seekers_28d=active_seekers_28d,
        window_ended_at=end, source_row_count=source_row_count,
        reviewed_source_row_count=approved_source_count+rejected_source_count,
    )
    if (waves.get("status") != "evaluated"
            or waves.get("oracle_activation_permitted") is not False
            or waves.get("automatic_scheduling_enabled") is not False
            or type(waves.get("qualifying_wave_count")) is not int):
        raise ValueError("R6D invalid shadow wave result")
    public_rows = tuple(row for row in rows if row["status"] == "shadow")
    return CachedShadowPreview(
        dict(summary, status="complete_shadow_preview",
             theme_count=len(public_rows),
             qualifying_wave_count=waves["qualifying_wave_count"]),
        public_rows, waves,
        window_ended_at=end, evaluated_at=evaluated,
    )
