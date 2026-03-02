#!/usr/bin/env bash
set -e

echo "Starting Postgres..."
brew services start postgresql@18 >/dev/null 2>&1 || true

if ! pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
  echo "Postgres not responding. Removing stale pid..."
  rm -f /opt/homebrew/var/postgresql@18/postmaster.pid || true
  brew services restart postgresql@18
fi

echo "Postgres status:"
pg_isready -h localhost -p 5432

echo "Activating venv..."
source venv/bin/activate

echo "Starting app..."
uvicorn main:app --reload