import io
import os
import time
from typing import Any, Dict, List, Optional

import requests
from openai import OpenAI

from services.whisper import transcribe_audio as transcribe_audio_local_whisper


def _ms_since(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 2)


def _first_text_value(payload: Any) -> str:
    if payload is None:
        return ""

    if isinstance(payload, str):
        return payload.strip()

    if isinstance(payload, dict):
        for key in ("text", "transcript", "question"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for key in ("data", "result", "results"):
            value = payload.get(key)
            text = _first_text_value(value)
            if text:
                return text

        for value in payload.values():
            text = _first_text_value(value)
            if text:
                return text

    if isinstance(payload, list):
        for item in payload:
            text = _first_text_value(item)
            if text:
                return text

    return ""


def _provider_order() -> List[str]:
    primary = os.getenv("VOICE_TRANSCRIBE_PROVIDER", "local_whisper").strip().lower()
    fallback_raw = os.getenv("VOICE_TRANSCRIBE_FALLBACK_PROVIDER", "openai_rest,local_whisper")
    providers = [primary]
    providers.extend(
        item.strip().lower()
        for item in fallback_raw.split(",")
        if item.strip()
    )

    clean = []
    for provider in providers:
        if provider and provider not in clean:
            clean.append(provider)
    return clean


def _xai_rest_stt(
    file_bytes: bytes,
    filename: str,
    content_type: str,
) -> Dict[str, Any]:
    api_key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY/GROK_API_KEY is not configured.")

    started = time.monotonic()
    url = os.getenv("XAI_STT_URL", "https://api.x.ai/v1/stt")
    language = os.getenv("XAI_STT_LANGUAGE", "").strip()
    keyterms_raw = os.getenv(
        "XAI_STT_KEYTERMS",
        "God Incorporated,Hathor,Moses,Oracle,Temple,Theosopher,Magister,Seeker",
    )

    data = {
        "format": os.getenv("XAI_STT_FORMAT", "true"),
    }
    if language:
        data["language"] = language

    # requests supports repeated form fields when data is a list of tuples.
    form_items = list(data.items())
    for term in [item.strip() for item in keyterms_raw.split(",") if item.strip()]:
        form_items.append(("keyterm", term))

    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        data=form_items,
        files={
            "file": (
                filename or "voice_input.m4a",
                io.BytesIO(file_bytes),
                content_type or "audio/mp4",
            )
        },
        timeout=float(os.getenv("XAI_STT_TIMEOUT_SECONDS", "30")),
    )
    api_ms = _ms_since(started)

    if not response.ok:
        raise RuntimeError(f"xAI STT failed {response.status_code}: {response.text[:500]}")

    try:
        payload = response.json()
    except Exception:
        payload = response.text

    transcript = _first_text_value(payload)
    return {
        "provider": "xai",
        "model": os.getenv("XAI_STT_MODEL", "xai-stt-rest"),
        "transcript": transcript,
        "api_ms": api_ms,
        "raw_response_type": type(payload).__name__,
    }


def _openai_rest_stt(
    file_bytes: bytes,
    filename: str,
    content_type: str,
) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
    started = time.monotonic()
    client = OpenAI(api_key=api_key)

    audio_file = (
        filename or "voice_input.m4a",
        io.BytesIO(file_bytes),
        content_type or "audio/mp4",
    )

    result = client.audio.transcriptions.create(
        model=model,
        file=audio_file,
    )
    api_ms = _ms_since(started)

    transcript = getattr(result, "text", "") or _first_text_value(
        result.model_dump() if hasattr(result, "model_dump") else result
    )

    return {
        "provider": "openai",
        "model": model,
        "transcript": transcript.strip(),
        "api_ms": api_ms,
        "raw_response_type": type(result).__name__,
    }


def _local_whisper_stt(file_bytes: bytes) -> Dict[str, Any]:
    started = time.monotonic()
    transcript = transcribe_audio_local_whisper(file_bytes)
    return {
        "provider": "local_whisper",
        "model": "whisper-base",
        "transcript": (transcript or "").strip(),
        "api_ms": _ms_since(started),
        "raw_response_type": "local",
    }


def transcribe_audio_with_metrics(
    file_bytes: bytes,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    voice: Optional[str] = None,
) -> Dict[str, Any]:
    attempts = []
    audio_bytes = len(file_bytes or b"")

    for provider in _provider_order():
        started = time.monotonic()
        try:
            if provider in ("xai", "xai_rest", "grok", "grok_stt"):
                result = _xai_rest_stt(file_bytes, filename or "voice_input.m4a", content_type or "audio/mp4")
            elif provider in ("openai", "openai_rest", "openai_transcribe"):
                result = _openai_rest_stt(file_bytes, filename or "voice_input.m4a", content_type or "audio/mp4")
            elif provider in ("local", "local_whisper", "whisper"):
                result = _local_whisper_stt(file_bytes)
            else:
                raise RuntimeError(f"Unknown transcription provider: {provider}")

            transcript = (result.get("transcript") or "").strip()
            attempt = {
                "provider": result.get("provider", provider),
                "model": result.get("model", provider),
                "status": "ok" if transcript else "empty",
                "api_ms": result.get("api_ms", _ms_since(started)),
                "transcript_chars": len(transcript),
            }
            attempts.append(attempt)

            if transcript:
                result["attempts"] = attempts
                result["audio_bytes"] = audio_bytes
                result["voice"] = voice
                return result

        except Exception as exc:
            attempts.append({
                "provider": provider,
                "model": provider,
                "status": "error",
                "api_ms": _ms_since(started),
                "error": str(exc)[:500],
            })

    raise RuntimeError(f"All transcription providers failed: {attempts}")
