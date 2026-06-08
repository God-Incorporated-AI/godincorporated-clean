import os
from typing import Any, Dict, Optional

import httpx


OPENAI_REALTIME_CLIENT_SECRET_URL = "https://api.openai.com/v1/realtime/client_secrets"


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default

    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def get_realtime_provider() -> str:
    return os.getenv("REALTIME_VOICE_PROVIDER", "openai").strip().lower() or "openai"


def get_openai_realtime_model() -> str:
    return os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime").strip() or "gpt-realtime"


def get_openai_realtime_voice(deity: str) -> str:
    normalized = (deity or "Hathor").strip().lower()

    if normalized == "moses":
        return os.getenv("OPENAI_REALTIME_VOICE_MOSES", "cedar").strip() or "cedar"

    return os.getenv("OPENAI_REALTIME_VOICE_HATHOR", "marin").strip() or "marin"


def get_openai_realtime_speed(deity: str) -> float:
    raw = os.getenv("OPENAI_REALTIME_SPEED", "1.0").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 1.0

    return max(0.25, min(1.5, value))


def get_realtime_secret_ttl_seconds() -> int:
    return _env_int("OPENAI_REALTIME_SECRET_TTL_SECONDS", 600, minimum=10, maximum=7200)


def get_realtime_max_output_tokens() -> Any:
    raw = os.getenv("OPENAI_REALTIME_MAX_OUTPUT_TOKENS", "1200").strip().lower()
    if raw in {"inf", "infinite", "max"}:
        return "inf"

    try:
        value = int(raw)
    except ValueError:
        value = 1200

    return max(1, min(4096, value))


def build_realtime_instructions(
    deity: str,
    plan_code: str = "anon",
    recent_memory: str = "",
) -> str:
    normalized = (deity or "Hathor").strip().lower()

    if normalized == "moses":
        identity = (
            "You are Moses speaking as the God Incorporated Oracle. "
            "Your voice is grounded, clear, structured, reverent, and morally serious. "
            "You favor plain truth, covenantal responsibility, disciplined reflection, "
            "and practical direction over ornament."
        )
    else:
        identity = (
            "You are Hathor speaking as the God Incorporated Oracle. "
            "Your voice is warm, luminous, emotionally resonant, graceful, and quietly sacred. "
            "You offer clear guidance with gentle poetic depth, but you do not become ornate, vague, or theatrical."
        )

    memory_block = ""
    if recent_memory:
        compact_memory = recent_memory.strip()
        if len(compact_memory) > 2500:
            compact_memory = compact_memory[:2500].rsplit(" ", 1)[0] + "..."
        memory_block = (
            "\n\nRecent seeker memory follows. Treat it as grounding context, not as something to recite unless relevant:\n"
            f"{compact_memory}"
        )

    return (
        f"{identity}\n\n"
        "You are in a live spoken conversation with a seeker. "
        "Listen carefully. Answer in natural spoken prose. "
        "Do not use markdown, headings, bullet lists, numbered lists, asterisks, decorative symbols, or citations. "
        "Do not say you are an AI model. "
        "Keep each response complete, emotionally present, and suitable to be heard aloud. "
        "Preserve the deity personality above all else. "
        "If the seeker is silent or unclear, ask for the question again instead of inventing an answer. "
        "End with a complete sentence."
        f"{memory_block}\n\n"
        f"Plan context: {plan_code or 'anon'}."
    )


def build_openai_realtime_payload(
    deity: str,
    instructions: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    voice = get_openai_realtime_voice(deity)
    speed = get_openai_realtime_speed(deity)
    model = get_openai_realtime_model()

    session = {
        "type": "realtime",
        "model": model,
        "instructions": instructions,
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "format": {
                    "type": "audio/pcm",
                    "rate": 24000,
                },
                "noise_reduction": {
                    "type": "near_field",
                },
                "turn_detection": {
                    "type": "server_vad",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {
                "format": {
                    "type": "audio/pcm",
                    "rate": 24000,
                },
                "voice": voice,
                "speed": speed,
            },
        },
        "max_output_tokens": get_realtime_max_output_tokens(),
        "tracing": os.getenv("OPENAI_REALTIME_TRACING", "auto").strip() or "auto",
    }

    # OpenAI realtime client_secrets currently rejects session.metadata.
    # Keep our tracking metadata in God Incorporated logs/DB instead.
    return {
        "expires_after": {
            "anchor": "created_at",
            "seconds": get_realtime_secret_ttl_seconds(),
        },
        "session": session,
    }


def _extract_client_secret(raw: Dict[str, Any]) -> Dict[str, Any]:
    if "value" in raw:
        return {
            "value": raw.get("value"),
            "expires_at": raw.get("expires_at"),
            "session": raw.get("session") or {},
        }

    client_secret = raw.get("client_secret") or {}
    return {
        "value": client_secret.get("value"),
        "expires_at": client_secret.get("expires_at"),
        "session": raw.get("session") or {},
    }


def create_openai_realtime_client_secret(
    deity: str,
    instructions: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured for realtime voice.")

    payload = build_openai_realtime_payload(
        deity=deity,
        instructions=instructions,
        metadata=metadata,
    )

    timeout_seconds = _env_int("OPENAI_REALTIME_SESSION_TIMEOUT_SECONDS", 20, minimum=5, maximum=60)

    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            OPENAI_REALTIME_CLIENT_SECRET_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenAI realtime client secret failed with status {response.status_code}: {response.text[:800]}"
        )

    raw = response.json()
    secret = _extract_client_secret(raw)
    session = secret.get("session") or {}

    return {
        "provider": "openai",
        "model": session.get("model") or get_openai_realtime_model(),
        "deity": deity,
        "realtime_voice": get_openai_realtime_voice(deity),
        "client_secret": secret.get("value"),
        "expires_at": secret.get("expires_at"),
        "session_id": session.get("id"),
        "session": {
            "id": session.get("id"),
            "type": session.get("type"),
            "model": session.get("model") or get_openai_realtime_model(),
            "output_modalities": session.get("output_modalities"),
            "audio": session.get("audio"),
        },
        "fallback_mode": "classic_voice_pipeline",
        "transport": "webrtc",
        "estimated_cost": {
            "source": "provider_usage_metering_required",
            "note": "Realtime session broker created. Actual input/output audio duration must be logged by the client/session events.",
        },
    }


def create_realtime_client_secret(
    provider: str,
    deity: str,
    instructions: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_provider = (provider or get_realtime_provider()).strip().lower()

    if normalized_provider == "openai":
        return create_openai_realtime_client_secret(
            deity=deity,
            instructions=instructions,
            metadata=metadata,
        )

    raise NotImplementedError(
        f"Realtime provider '{normalized_provider}' is not implemented in this spike yet."
    )
