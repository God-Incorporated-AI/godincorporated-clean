import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

XAI_CLIENT_SECRET_URL = "https://api.x.ai/v1/realtime/client_secrets"


def get_xai_api_key() -> str:
    api_key = (
        os.getenv("XAI_API_KEY")
        or os.getenv("XAI_APIKEY")
        or os.getenv("GROK_API_KEY")
        or ""
    ).strip()

    if not api_key:
        raise RuntimeError("XAI_API_KEY is not configured.")

    return api_key


def get_xai_realtime_model() -> str:
    return os.getenv("XAI_REALTIME_MODEL", "grok-voice-latest").strip() or "grok-voice-latest"


def normalize_xai_realtime_voice(value: Optional[str]) -> Optional[str]:
    normalized = (value or "").strip().lower()
    allowed = {"ara", "eve", "sal", "rex", "leo"}
    if normalized in allowed:
        return normalized
    return None


def get_xai_realtime_voice(deity: str, voice_override: Optional[str] = None) -> str:
    override = normalize_xai_realtime_voice(voice_override)
    if override:
        return override

    normalized = (deity or "Hathor").strip().lower()

    if normalized == "moses":
        return os.getenv("XAI_REALTIME_VOICE_MOSES", "leo").strip() or "leo"

    return os.getenv("XAI_REALTIME_VOICE_HATHOR", "eve").strip() or "eve"


def create_xai_realtime_client_secret(expires_seconds: int = 300) -> Dict[str, Any]:
    api_key = get_xai_api_key()

    payload = json.dumps({"expires_after": {"seconds": int(expires_seconds)}}).encode("utf-8")

    request = urllib.request.Request(
        XAI_CLIENT_SECRET_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"xAI realtime client secret failed with HTTP {error.code}: {body[:1000]}") from error


def create_xai_realtime_session(deity: str, voice_override: Optional[str] = None) -> Dict[str, Any]:
    started = time.perf_counter()
    secret = create_xai_realtime_client_secret()
    total_ms = round((time.perf_counter() - started) * 1000, 2)

    token = secret.get("value")
    if not token:
        raise RuntimeError("xAI realtime client secret response did not include value.")

    voice = get_xai_realtime_voice(deity, voice_override=voice_override)
    model = get_xai_realtime_model()

    return {
        "provider": "xai",
        "model": model,
        "deity": (deity or "Hathor").strip() or "Hathor",
        "realtime_voice": voice,
        "client_secret": token,
        "expires_at": secret.get("expires_at"),
        "transport": "websocket",
        "mode": "text_in_voice_out_lab",
        "total_ms": total_ms,
        "estimated_cost": {
            "source": "client_side_duration_estimate_required",
            "audio_price_per_minute_usd": 0.05,
            "text_input_price_per_message_usd": 0.004,
        },
    }
