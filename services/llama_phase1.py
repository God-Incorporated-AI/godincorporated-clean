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
LLAMA_PASSAGE_CHAR_LIMIT = int(os.getenv("LLAMA_PASSAGE_CHAR_LIMIT", "400"))
LLAMA_MEMORY_CHAR_LIMIT = int(os.getenv("LLAMA_MEMORY_CHAR_LIMIT", "80"))
LLAMA_BRIEF_CHAR_LIMIT = int(os.getenv("LLAMA_BRIEF_CHAR_LIMIT", "1200"))
LLAMA_MAX_PASSAGES = int(os.getenv("LLAMA_MAX_PASSAGES", "3"))
LLAMA_MAX_LONG_TERM_MEMORIES = int(os.getenv("LLAMA_MAX_LONG_TERM_MEMORIES", "0"))

VALID_BUDGET_TIERS = {"low", "medium", "full"}


def _ns_to_ms_text(value) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{(float(value) / 1_000_000):.2f}"
    except Exception:
        return "-"

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
    for item in limited_memories[:LLAMA_MAX_LONG_TERM_MEMORIES]:
        text = (item or "").strip()
        if text:
            trimmed_memories.append(text[:LLAMA_MEMORY_CHAR_LIMIT])

    trimmed_passages = []
    max_passages = 3
    count = 0

    for idx, item in enumerate(passages[:LLAMA_MAX_PASSAGES]):
        if count >= max_passages:
            break

        text = (item or "").strip()
        if text:
            trimmed_passages.append({
                "index": idx,
                "text": text[:600]
            })
            count += 1

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
            "temperature": 0,
            "num_predict": 160
        }
    }

    candidate_passages = packet.get("candidate_passages") or []
    candidate_chars = sum(len((item.get("text") or "")) for item in candidate_passages if isinstance(item, dict))
    long_term_memories = packet.get("long_term_memories") or []
    long_term_memory_chars = sum(len(item or "") for item in long_term_memories)

    logger.info(
        "LLAMA_OLLAMA_REQUEST model=%s timeout_s=%s question_chars=%s recent_memory_chars=%s compressed_memory_chars=%s long_term_memory_count=%s long_term_memory_chars=%s candidate_passages=%s candidate_chars=%s prompt_chars=%s",
        LLAMA_MODEL,
        LLAMA_TIMEOUT_SECONDS,
        len(packet.get("question") or ""),
        len(packet.get("recent_memory") or ""),
        len(packet.get("compressed_memory") or ""),
        len(long_term_memories),
        long_term_memory_chars,
        len(candidate_passages),
        candidate_chars,
        len(user_prompt)
    )

    try:
        async with httpx.AsyncClient(timeout=LLAMA_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{LLAMA_BASE_URL.rstrip('/')}/api/generate",
                json=payload
            )
            response.raise_for_status()
            outer = response.json()

        logger.info(
            "LLAMA_OLLAMA_RESPONSE model=%s done=%s total_ms=%s load_ms=%s prompt_eval_count=%s prompt_eval_ms=%s eval_count=%s eval_ms=%s response_chars=%s",
            LLAMA_MODEL,
            outer.get("done"),
            _ns_to_ms_text(outer.get("total_duration")),
            _ns_to_ms_text(outer.get("load_duration")),
            outer.get("prompt_eval_count", "-"),
            _ns_to_ms_text(outer.get("prompt_eval_duration")),
            outer.get("eval_count", "-"),
            _ns_to_ms_text(outer.get("eval_duration")),
            len(outer.get("response") or "")
        )

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

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        response_text = ""
        try:
            response_text = (exc.response.text or "").strip()
        except Exception:
            response_text = ""
        if len(response_text) > 500:
            response_text = response_text[:497] + "..."
        logger.warning(
            "LLaMA Phase 1 failed open: HTTPStatusError status=%s url=%s body=%s",
            status_code,
            str(exc.request.url) if exc.request is not None else "-",
            response_text or "-"
        )
        return _fallback_result(f"fail_open: HTTPStatusError:{status_code}:{response_text or '-'}")

    except httpx.ReadTimeout as exc:
        logger.warning("LLaMA Phase 1 failed open: ReadTimeout url=%s", str(exc.request.url) if exc.request is not None else "-")
        return _fallback_result("fail_open: ReadTimeout")

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


def summarize_phase1_result(result: Optional[dict], passages_before: int, passages_after: int) -> str:
    if not result:
        return (
            "LLAMA_PHASE1 enabled=false "
            f"passages_before={passages_before} passages_after={passages_after}"
        )

    selected = result.get("selected_passage_indexes") or []
    compact_brief = (result.get("compact_brief") or "").strip()
    reason = (result.get("reason") or "").strip().replace("\n", " ")
    if len(reason) > 180:
        reason = reason[:177] + "..."

    return (
        "LLAMA_PHASE1 "
        f"enabled={str(bool(result.get('enabled'))).lower()} "
        f"shadow_only={str(bool(result.get('shadow_only'))).lower()} "
        f"provider={result.get('provider', '-') } "
        f"budget={result.get('budget_tier', '-') } "
        f"passages_before={passages_before} "
        f"passages_after={passages_after} "
        f"selected_indexes={selected} "
        f"brief_chars={len(compact_brief)} "
        f"reason={reason or '-'}"
    )
