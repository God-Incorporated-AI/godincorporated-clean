import os
import uuid
from openai import OpenAI
from ..storage.json_store import AUDIO_DIR

openai_client = None  # Lazy load

def get_openai_client():
    global openai_client
    if openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")
        openai_client = OpenAI(api_key=api_key)
    return openai_client

def generate_tts_audio(answer: str, voice: str) -> str:
    voice_map = {
        "Hathor": "shimmer",
        "Moses": "onyx",
        "Llama": "alloy"
    }
    selected_voice = voice_map.get(voice, "onyx")

    client = get_openai_client()
    tts_response = client.audio.speech.create(
        model="tts-1",
        voice=selected_voice,
        input=answer
    )

    audio_id = str(uuid.uuid4())
    audio_path = os.path.join(AUDIO_DIR, f"{audio_id}.mp3")
    with open(audio_path, "wb") as f:
        f.write(tts_response.content)

    return f"/audio/{audio_id}.mp3"
