import os
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlparse

# Explicit .env loading with absolute path for robustness across execution contexts
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

print("XAI key loaded:", bool(os.getenv("XAI_API_KEY")))
print("OpenAI key loaded:", bool(os.getenv("OPENAI_API_KEY")))

# DB config logging
db_url = os.getenv("DATABASE_URL")
if db_url:
    parsed = urlparse(db_url)
    log_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}/{parsed.path.lstrip('/')}"
    print(f"DB config loaded: {log_url}")
else:
    print("DB config: DATABASE_URL not set")

LLAMA_ENABLED = os.getenv("LLAMA_ENABLED", "false").lower() == "true"

xai_api_key = os.getenv("XAI_API_KEY")
