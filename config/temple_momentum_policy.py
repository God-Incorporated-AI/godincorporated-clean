"""R6D Temple Momentum policy v2; no implicit activation or provider calls.

The 50-row budget belongs to the legacy all-history collector and must be
replaced before population-scale execution. The 2x surge rule is approved.
"""
from dataclasses import dataclass
from typing import Final

@dataclass(frozen=True, slots=True)
class TempleMomentumPolicy:
    version: str
    active_seeker_lookback_days: int
    minimum_active_seekers: int
    wave_window_hours: int
    wave_baseline_days: int
    minimum_live_wave_contributors: int
    momentum_window_days: int
    minimum_shadow_contributors: int
    similarity_threshold: float
    frequency_weight: float
    acceleration_weight: float
    recency_half_life_days: int
    legacy_source_row_budget: int
    surge_lift_ratio: float | None
    live_oracle_enabled: bool
    automatic_scheduling_enabled: bool

TEMPLE_MOMENTUM_POLICY: Final[TempleMomentumPolicy] = TempleMomentumPolicy(
    version="r6d-wave-v2",
    active_seeker_lookback_days=28,
    minimum_active_seekers=500,
    wave_window_hours=48,
    wave_baseline_days=14,
    minimum_live_wave_contributors=50,
    momentum_window_days=14,
    minimum_shadow_contributors=3,
    similarity_threshold=0.85,
    frequency_weight=1.0,
    acceleration_weight=0.25,
    recency_half_life_days=14,
    legacy_source_row_budget=50,
    surge_lift_ratio=2.0,  # Approved; live Oracle gate remains closed.
    live_oracle_enabled=False,
    automatic_scheduling_enabled=False,
)
