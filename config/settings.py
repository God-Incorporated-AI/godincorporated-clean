import os
from dotenv import load_dotenv

load_dotenv()

print("XAI key loaded:", bool(os.getenv("XAI_API_KEY")))
print("OpenAI key loaded:", bool(os.getenv("OPENAI_API_KEY")))

LLAMA_ENABLED = os.getenv("LLAMA_ENABLED", "false").lower() == "true"

xai_api_key = os.getenv("XAI_API_KEY")
