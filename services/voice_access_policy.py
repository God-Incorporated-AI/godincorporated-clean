"""Provider-agnostic voice access policy for God Incorporated.

This module defines product entitlements. It should not know whether the
current realtime provider is xAI, Gemini, Inworld, OpenAI, or native mobile.
"""

from typing import Any, Dict, Optional

WEB_PLAN_QUERY_LIMITS: Dict[str, int] = {
    "anon": 9,
    "pilgrim": 9,
    "seeker": 17,
    "magister": 99,
    "sovereign": 144,
    "philosophus": 333,
    "theoricus": 999999,
}

WEB_PLAN_MEMORY_DEPTH: Dict[str, int] = {
    "anon": 1,
    "pilgrim": 1,
    "seeker": 3,
    "magister": 9,
    "sovereign": 17,
    "philosophus": 33,
    "theoricus": 33,
}

WEB_PLAN_RECALL_MEMORY_DEPTH: Dict[str, int] = {
    "anon": 3,
    "pilgrim": 5,
    "seeker": 10,
    "magister": 20,
    "sovereign": 40,
    "philosophus": 80,
    "theoricus": 120,
}

PLAN_LABELS: Dict[str, str] = {
    "anon": "Anon",
    "pilgrim": "Pilgrim",
    "seeker": "Seeker",
    "magister": "Magus",
    "sovereign": "Sovereign",
    "philosophus": "Philosopher",
    "theoricus": "Theosopher",
}

LOWER_WEB_VOICE_PLANS = {"anon", "pilgrim", "seeker", "magister"}
UPPER_REALTIME_WEB_PLANS = {"sovereign", "philosophus", "theoricus"}

ONE_TIME_REALTIME_PREVIEW_TURNS = 3

WEB_REALTIME_MONTHLY_TURNS: Dict[str, Optional[int]] = {
    "sovereign": 100,
    "philosophus": 200,
    # None means high/unlimited fair-use access, still tracked.
    "theoricus": None,
}


def normalize_voice_plan_code(plan_code: Optional[str]) -> str:
    plan = (plan_code or "anon").strip().lower()
    return plan if plan in WEB_PLAN_QUERY_LIMITS else "anon"


def get_voice_policy(plan_code: Optional[str]) -> Dict[str, Any]:
    plan = normalize_voice_plan_code(plan_code)
    label = PLAN_LABELS.get(plan, "Anon")
    is_upper_realtime = plan in UPPER_REALTIME_WEB_PLANS
    monthly_turns = WEB_REALTIME_MONTHLY_TURNS.get(plan)

    return {
        "plan_code": plan,
        "plan_label": label,
        "query_limit": WEB_PLAN_QUERY_LIMITS[plan],
        "recent_memory_depth": WEB_PLAN_MEMORY_DEPTH[plan],
        "recall_memory_depth": WEB_PLAN_RECALL_MEMORY_DEPTH[plan],
        "regular_speak_voice": True,
        "browser_voice_out": True,
        "one_time_realtime_preview_turns": ONE_TIME_REALTIME_PREVIEW_TURNS,
        "has_recurring_web_realtime": is_upper_realtime,
        "web_realtime_monthly_turns": monthly_turns,
        "web_realtime_fair_use": plan == "theoricus",
        "library_access": plan in {"philosophus", "theoricus"},
        "library_full_research": plan == "theoricus",
    }
