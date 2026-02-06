import os
import tempfile
import whisper

whisper_model = None  # Lazy load

def transcribe_audio(file_bytes):
    global whisper_model
    if whisper_model is None:
        whisper_model = whisper.load_model("base")
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".webm")
    temp_file.write(file_bytes)
    temp_file.close()

    result = whisper_model.transcribe(temp_file.name)
    os.unlink(temp_file.name)

    return result["text"].strip()
