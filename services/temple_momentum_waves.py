"""Pure, no-provider R6D event-wave evaluator.

Inputs are approved, deidentified themes; contributor keys exist transiently.
No database, scheduler, identity persistence or Oracle-prompt interactions.
"""
import datetime as dt
import math
from config.temple_momentum_policy import TEMPLE_MOMENTUM_POLICY as POLICY


def evaluate_temple_momentum_waves(clusters, observations, *, active_seekers_28d,
                                    window_ended_at, source_row_count,
                                    reviewed_source_row_count):
    if type(active_seekers_28d) is not int or active_seekers_28d < 0:
        raise ValueError("R6D invalid global population")
    if (type(source_row_count) is not int or source_row_count < 0 or
        type(reviewed_source_row_count) is not int or
        not 0 <= reviewed_source_row_count <= source_row_count):
        raise ValueError("R6D invalid source coverage")
    if (not isinstance(window_ended_at, dt.datetime) or
        window_ended_at.tzinfo is None or window_ended_at.utcoffset() is None):
        raise ValueError("R6D requires timezone-aware window end")
    if not isinstance(clusters, list) or not isinstance(observations, list):
        raise ValueError("R6D wave inputs must be lists")
    if len(observations) > reviewed_source_row_count:
        raise ValueError("R6D observations exceed reviewed source rows")

    result = dict(policy_version=POLICY.version, global_population_met=False,
                  themes_evaluated=0, qualifying_wave_count=0,
                  qualifying_waves=[], oracle_activation_permitted=False,
                  automatic_scheduling_enabled=False)
    if reviewed_source_row_count != source_row_count:
        return dict(result, status="incomplete_source_window")
    if (type(POLICY.wave_window_hours) is not int or
        type(POLICY.wave_baseline_days) is not int or
        type(POLICY.minimum_active_seekers) is not int or
        type(POLICY.minimum_live_wave_contributors) is not int or
        POLICY.wave_window_hours <= 0 or POLICY.wave_baseline_days <= 0 or
        POLICY.minimum_active_seekers < 2 or
        POLICY.minimum_live_wave_contributors < 2 or
        POLICY.surge_lift_ratio is None or
        not math.isfinite(float(POLICY.surge_lift_ratio)) or
        POLICY.surge_lift_ratio <= 1):
        raise ValueError("R6D incomplete or altered wave policy")
    end = window_ended_at.astimezone(dt.timezone.utc)
    if end > dt.datetime.now(dt.timezone.utc):
        raise ValueError("R6D wave window cannot end in the future")
    width = dt.timedelta(hours=POLICY.wave_window_hours)
    wave_start = end - width
    baseline_start = wave_start - dt.timedelta(days=POLICY.wave_baseline_days)
    momentum_start = end - dt.timedelta(days=2 * POLICY.momentum_window_days)
    if baseline_start < momentum_start:
        raise ValueError("R6D wave history exceeds Momentum source window")
    periods = dt.timedelta(days=POLICY.wave_baseline_days) / width
    if periods < 1 or periods != int(periods):
        raise ValueError("R6D baseline must contain whole wave periods")
    periods = int(periods)

    topic_group = {}
    for cluster in clusters:
        if (not isinstance(cluster, list) or not cluster or
            any(not isinstance(t, str) or not t or t.strip() != t
                for t in cluster)):
            raise ValueError("R6D invalid theme cluster")
        group = tuple(sorted(cluster))
        if len(set(group)) != len(group):
            raise ValueError("R6D repeated cluster member")
        for topic in group:
            if topic in topic_group:
                raise ValueError("R6D duplicate theme across clusters")
            topic_group[topic] = group
    groups = sorted(set(topic_group.values()))
    recent = {g: set() for g in groups}
    prior = {g: [set() for _ in range(periods)] for g in groups}

    for item in observations:
        if not isinstance(item, dict) or set(item) != {
                "theme_key", "contributor_key", "observed_at"}:
            raise ValueError("R6D observation fields mismatch")
        topic, person, at = (item[k] for k in (
            "theme_key", "contributor_key", "observed_at"))
        if topic not in topic_group or not isinstance(person, str) or not person.strip():
            raise ValueError("R6D invalid transient observation")
        if (not isinstance(at, dt.datetime) or at.tzinfo is None or
            at.utcoffset() is None):
            raise ValueError("R6D observation time must be aware")
        at = at.astimezone(dt.timezone.utc)
        if not momentum_start <= at < end:
            raise ValueError("R6D observation outside complete source window")
        group = topic_group[topic]
        if at >= wave_start:
            recent[group].add(person)
        elif at >= baseline_start:
            index = int((at - baseline_start) // width)
            if not 0 <= index < periods:
                raise ValueError("R6D invalid baseline period")
            prior[group][index].add(person)

    global_ok = active_seekers_28d >= POLICY.minimum_active_seekers
    waves = []
    for group in groups:
        contributors = len(recent[group])
        if not global_ok or contributors < POLICY.minimum_live_wave_contributors:
            continue
        prior_total = sum(len(p) for p in prior[group])
        if contributors * periods < POLICY.surge_lift_ratio * prior_total:
            continue
        mean = prior_total / periods
        waves.append(dict(theme_keys=list(group),
                          distinct_contributors_wave_window=contributors,
                          baseline_mean_contributors_wave_window=mean,
                          baseline_zero=(prior_total == 0),
                          surge_lift_ratio=None if prior_total == 0 else contributors / mean))
    return dict(result, status="evaluated", global_population_met=global_ok,
                wave_window_hours=POLICY.wave_window_hours,
                baseline_days=POLICY.wave_baseline_days,
                themes_evaluated=len(groups), qualifying_wave_count=len(waves),
                qualifying_waves=waves)
