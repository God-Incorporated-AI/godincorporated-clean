#!/usr/bin/env bash

if [ -z "$VIRTUAL_ENV" ]; then
  echo "❌ Virtual environment not active."
  echo "Run: source venv/bin/activate"
  exit 1
fi

echo "🚀 Starting GodIncorporated (dev mode)..."
uvicorn main:app --reload --host 127.0.0.1 --port 8000

