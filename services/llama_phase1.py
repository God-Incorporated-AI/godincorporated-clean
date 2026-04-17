import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

LLAMA_PHASE1_ENABLED = _env_bool("LLAMA_PHASE1_ENABLED", False)
LLAMA_SHADOW_ONLY = _env_bool("LLAMA_SHADOW_ONLY", True)
LLAMA_BASE_URL = os.getenv("LLAMA_BASE_URL", "http://127.0.0.1:11434").strip()
LLAMA_MODEL = os.getenv("LLAMA_MODEL", "llama3.1:8b-instruct").strip()
LLAMA_TIMEOUT_SECONDS = float(os.getenv("LLAMA_TIMEOUT_SECONDS", "8"))
LLAMA_PASSAGE_CHAR_LIMIT = int(os.getenv("LLAMA_PASSAGE_CHAR_LIMIT", "700"))
LLAMA_MEMORY_CHAR_LIMIT = int(os.getenv("LLAMA_MEMORY_CHAR_LIMIT", "500"))
LLAMA_BRIEF_CHAR_LIMIT = int(os.getenv("LLAMA_BRIEF_CHAR_LIMIT", "1200"))

VALID_BUDGET_TIERS = {"low", "medium", "full"}

def build_support_packet(
    question: str,
    deity: str,
    memory_intent: str,
    plan_code: str,
    recent_memory: Optional[str],
    compressed_memory: Optional[str],
    limited_memories: list[str],
    passages: list[str]
) -> dict:
    trimmed_memories = []
    for item in limited_memories[:5]:
        text = (item or "").strip()
        if text:
            trimmed_memories.append(text[:LLAMA_MEMORY_CHAR_LIMIT])

    trimmed_passages = []
    for idx, item in enumerate(passages[:8]):
        text = (item or "").strip()
        if text:
            trimmed_passages.append({
                "index": idx,
                "text": text[:LLAMA_PASSAGE_CHAR_LIMIT]
            })

    return {
        "question": (question or "").strip(),
        "oracle_requested": deity,
        "memory_intent": memory_intent,
        "plan_code": plan_code,
        "recent_memory": (recent_memory or "").strip()[:LLAMA_MEMORY_CHAR_LIMIT],
        "compressed_memory": (compressed_memory or "").strip()[:LLAMA_MEMORY_CHAR_LIMIT],
        "long_term_memories": trimmed_memories,
        "candidate_passages": trimmed_passages
    }

def _fallback_result(reason: str) -> dict:
    return {
        "enabled": LLAMA_PHASE1_ENABLED,
        "shadow_only": LLAMA_SHADOW_ONLY,
        "provider": "ollama",
        "budget_tier": "full",
        "selected_passage_indexes": [],
        "compact_brief": "",
        "reason": reason
    }

async def run_llama_phase1(packet: dict) -> Optional[dict]:
    if not LLAMA_PHASE1_ENABLED:
        return None

    if not LLAMA_BASE_URL:
        return _fallback_result("LLAMA_BASE_URL is empty")

    schema = {
        "type": "object",
        "properties": {
            "selected_passage_indexes": {
                "type": "array",
                "items": {"type": "integer"}
            },
            "compact_brief": {"type": "string"},
            "budget_tier": {
                "type": "string",
                "enum": ["low", "medium", "full"]
            },
            "reason": {"type": "string"}
        },
        "required": ["selected_passage_indexes", "compact_brief", "budget_tier", "reason"]
    }

    system_prompt = """
You are a neutral retrieval-and-budget assistant for an oracle system.

You do NOT answer the seeker.
You do NOT write in Hathor or Moses voice.
You do NOT change the selected oracle.

Your job:
1. Pick the most relevant candidate passages.
2. Write a short factual context brief.
3. Recommend a context budget tier: low, medium, or full.
4. Return JSON only.
""".strip()

    user_prompt = json.dumps(packet, ensure_ascii=False)

    payload = {
        "model": LLAMA_MODEL,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
        "format": schema,
        "options": {
            "temperature": 0
        }
    }

    try:
        async with httpx.AsyncClient(timeout=LLAMA_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{LLAMA_BASE_URL.rstrip('/')}/api/generate",
                json=payload
            )
            response.raise_for_status()
            outer = response.json()

        inner_raw = outer.get("response", "{}")
        inner = json.loads(inner_raw) if isinstance(inner_raw, str) else inner_raw

        selected = []
        candidate_count = len(packet.get("candidate_passages") or [])
        for item in inner.get("selected_passage_indexes", []):
            try:
                idx = int(item)
            except Exception:
                continue
            if 0 <= idx < candidate_count and idx not in selected:
                selected.append(idx)

        budget_tier = (inner.get("budget_tier") or "full").strip().lower()
        if budget_tier not in VALID_BUDGET_TIERS:
            budget_tier = "full"

        compact_brief = (inner.get("compact_brief") or "").strip()[:LLAMA_BRIEF_CHAR_LIMIT]
        reason = (inner.get("reason") or "No reason supplied").strip()

        return {
            "enabled": True,
            "shadow_only": LLAMA_SHADOW_ONLY,
            "provider": "ollama",
            "budget_tier": budget_tier,
            "selected_passage_indexes": selected,
            "compact_brief": compact_brief,
            "reason": reason
        }

    except Exception as exc:
        logger.warning("LLaMA Phase 1 failed open: %s", exc)
        return _fallback_result(f"fail_open: {type(exc).__name__}")

def apply_phase1_result(original_passages: list[str], result: Optional[dict]) -> tuple[list[str], str]:
    passages = list(original_passages or [])
    if not passages or not result:
        return passages, ""

    compact_brief = (result.get("compact_brief") or "").strip()

    if result.get("shadow_only", True):
        return passages, compact_brief

    selected_indexes = result.get("selected_passage_indexes") or []
    if selected_indexes:
        selected_passages = [passages[idx] for idx in selected_indexes if 0 <= idx < len(passages)]
        if selected_passages:
            passages = selected_passages

    cap = {
        "low": 2,
        "medium": 4,
        "full": 6
    }.get(result.get("budget_tier"), 6)

    return passages[:cap], compact_brief
