import os
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlparse

# Explicit .env loading with absolute path for robustness across execution contexts
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

xai_api_key = os.getenv("XAI_API_KEY")
