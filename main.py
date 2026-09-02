import datetime
import time
from datetime import timezone
import hashlib
import json
import logging
import os
import shutil
import subprocess
import uuid

import re

from typing import Optional, Literal
from docx import Document
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from starlette.middleware.sessions import SessionMiddleware
from openai import OpenAI
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from PyPDF2 import PdfReader

import fitz
import httpx
import psycopg2
import stripe

from config.settings import xai_api_key
from services.tts import generate_tts_audio, get_openai_tts_model
from services.voice_transcription import transcribe_audio_with_metrics
from services.mail import send_email
from services.stripe_billing import create_checkout_session_for_user, change_existing_subscription_plan
from storage.json_store import UPLOAD_DIR, AUDIO_DIR, save_log

logging.basicConfig(
    level=logging.INFO,
    filename="temple.log",
    filemode="a",
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

def choose_moses_model(raw_question: str, memory_block: str, context_block: str):
    moses_model_mini = os.getenv("MOSES_MODEL_MINI", "gpt-5.4-mini").strip()
    moses_model_full = os.getenv("MOSES_MODEL_FULL", "gpt-5.4").strip()
    moses_model_force = os.getenv("MOSES_MODEL_FORCE", "").strip()

    q = (raw_question or "").lower()
    question_chars = len(raw_question or "")
    memory_chars = len(memory_block or "")
    context_chars = len(context_block or "")
    total_context_chars = question_chars + memory_chars + context_chars

    synopsis_terms = (
        "synopsis",
        "summarize",
        "summary",
        "recap",
        "what have we discussed",
        "past conversation",
        "previous conversation",
        "last conversation",
        "summarise"
    )

    if moses_model_force in {moses_model_mini, moses_model_full}:
        return moses_model_force, "forced", total_context_chars

    if any(term in q for term in synopsis_terms):
        return moses_model_full, "synopsis_request", total_context_chars

    if question_chars > 1200:
        return moses_model_full, "very_long_raw_question", total_context_chars

    return moses_model_mini, "default_mini", total_context_chars

load_dotenv()
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_LIVEMODE = os.getenv("STRIPE_LIVEMODE", "false").lower() == "true"

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY
EMBEDDINGS_ENABLED = os.getenv("EMBEDDINGS_ENABLED", "false").lower() == "true"
EMBEDDING_CACHE_PATH = os.getenv("EMBEDDING_CACHE_PATH", os.path.join(UPLOAD_DIR, "cache", "embedding_cache.json"))

RETRIEVAL_BACKEND = os.getenv("RETRIEVAL_BACKEND", "legacy_embeddings").strip().lower()
PGVECTOR_RETRIEVAL_LIMIT = int(os.getenv("PGVECTOR_RETRIEVAL_LIMIT", "5"))
VALID_RETRIEVAL_BACKENDS = {"legacy_embeddings", "pgvector", "fts"}

BROWSER_TOKEN_HEADER = "x-anonymous-user-id"
ANONYMOUS_UPLOAD_COOLDOWN_SECONDS = 5
ANONYMOUS_UPLOAD_LIMIT = 3

def get_ip_hash(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()

def get_browser_token_from_request(request: Request) -> Optional[str]:
    token = (request.headers.get(BROWSER_TOKEN_HEADER) or "").strip()
    if token and re.fullmatch(r"[0-9a-fA-F-]{36}", token):
        return token
    return None

def should_use_embeddings() -> bool:
    return EMBEDDINGS_ENABLED

def load_embedding_cache() -> dict:
    if not os.path.exists(EMBEDDING_CACHE_PATH):
        return {}

    try:
        with open(EMBEDDING_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load embedding cache: {e}")
        return {}

def save_embedding_cache(cache: dict) -> None:
    try:
        with open(EMBEDDING_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception as e:
        logger.warning(f"Failed to save embedding cache: {e}")

def get_embedding_cache_key(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

def cache_chunk_embedding(chunk_text: str) -> None:
    """
    Store one chunk embedding in the local cache if it is not already present.
    Safe no-op when embeddings are disabled or text is empty.
    """
    if not should_use_embeddings():
        return

    chunk_text = (chunk_text or "").strip()
    if not chunk_text:
        return

    cache_key = get_embedding_cache_key(chunk_text)
    cache = load_embedding_cache()

    if cache_key in cache:
        return

    embedding = generate_text_embedding(chunk_text[:2000])
    if not embedding:
        return

    cache[cache_key] = embedding
    save_embedding_cache(cache)

app = FastAPI()

static_path = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_path), name="static")

# Favicon route to prevent 404 noise
@app.get("/favicon.ico")
async def favicon():
    return FileResponse(os.path.join(static_path, "favicon.ico"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Phase 4.2: Authentication setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev-secret-key-change-in-prod")
)



# Global exception handlers for JSON error responses
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error"}
    )

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(os.path.dirname(EMBEDDING_CACHE_PATH), exist_ok=True)
openai_client = None  # Lazy load

def get_openai_client():
    global openai_client
    if openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")
        openai_client = OpenAI(api_key=api_key)
    return openai_client

def estimate_tokens(question: str, answer: str) -> int:
    """Rough token estimation for metering. ~4 chars per token."""
    total_chars = len(question) + len(answer)
    return total_chars // 4

def generate_text_embedding(text: str) -> list[float] | None:
    """
    Phase 6.2 helper for generating a single embedding vector.

    Returns None for empty text so callers can safely skip bad inputs.
    """
    text = (text or "").strip()
    if not text:
        return None

    client = get_openai_client()
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding


SCROLL_CHUNK_DB_EMBEDDING_MODEL = "text-embedding-3-small"


def scroll_embedding_vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def get_scroll_ingest_embedding_batch_size() -> int:
    try:
        batch_size = int(os.getenv("SCROLL_INGEST_EMBED_BATCH_SIZE", "25"))
    except (TypeError, ValueError):
        batch_size = 25

    return max(1, min(batch_size, 100))


def iter_embedding_batches(items: list[dict], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def get_scroll_ingest_inline_embed_max_chunks() -> int:
    """
    Maximum chunk count eligible for inline pgvector embedding during upload
    ingestion. Large documents should commit chunks first and be embedded by a
    controlled backfill/worker path so uploads cannot stall before chunk commit.

    Set SCROLL_INGEST_INLINE_EMBED_MAX_CHUNKS=0 to disable inline embeddings.
    """
    try:
        max_chunks = int(os.getenv("SCROLL_INGEST_INLINE_EMBED_MAX_CHUNKS", "50"))
    except (TypeError, ValueError):
        max_chunks = 50

    return max(0, max_chunks)


def should_store_scroll_chunk_db_embeddings() -> bool:
    """
    Store pgvector embeddings when embeddings are enabled or pgvector retrieval
    is the active retrieval backend.

    This keeps the accepted legacy behavior intact while ensuring newly
    ingested scrolls are immediately retrievable in pgvector environments.
    """
    if should_use_embeddings():
        return True

    backend = (RETRIEVAL_BACKEND or "").strip().lower()
    return backend == "pgvector"


def generate_text_embeddings_batch(texts: list[str]) -> list[list[float]]:
    client = get_openai_client()
    response = client.embeddings.create(
        model=SCROLL_CHUNK_DB_EMBEDDING_MODEL,
        input=texts,
    )

    ordered = sorted(response.data, key=lambda item: item.index)
    return [item.embedding for item in ordered]


def store_scroll_chunk_embeddings(chunk_rows: list[dict], batch_size: Optional[int] = None) -> dict:
    """
    Store pgvector embeddings for newly inserted scroll chunks.

    This is intentionally non-fatal for upload ingestion. If embedding storage
    fails, the scroll can still be ingested and the existing backfill script can
    repair missing embeddings later.
    """
    if not chunk_rows:
        return {
            "ok": True,
            "processed": 0,
            "updated": 0,
            "skipped": 0,
            "disabled": False,
        }

    if not should_store_scroll_chunk_db_embeddings():
        return {
            "ok": True,
            "processed": 0,
            "updated": 0,
            "skipped": len(chunk_rows),
            "disabled": True,
            "reason": "db_embedding_storage_disabled",
        }

    max_inline_chunks = get_scroll_ingest_inline_embed_max_chunks()
    if max_inline_chunks == 0 or len(chunk_rows) > max_inline_chunks:
        return {
            "ok": True,
            "processed": 0,
            "updated": 0,
            "skipped": len(chunk_rows),
            "disabled": True,
            "reason": "too_many_chunks_for_inline_embedding",
            "max_inline_chunks": max_inline_chunks,
            "chunk_count": len(chunk_rows),
        }

    prepared_rows = []
    skipped = 0

    for row in chunk_rows:
        chunk_id = row.get("id")
        chunk_text = (row.get("chunk_text") or "").strip()

        if not chunk_id or not chunk_text:
            skipped += 1
            continue

        prepared_rows.append({
            "id": chunk_id,
            "chunk_text": chunk_text[:2000],
        })

    if not prepared_rows:
        return {
            "ok": True,
            "processed": 0,
            "updated": 0,
            "skipped": skipped,
            "disabled": False,
        }

    batch_size = batch_size or get_scroll_ingest_embedding_batch_size()
    updated = 0
    processed = 0
    conn = None

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            for batch in iter_embedding_batches(prepared_rows, batch_size):
                texts = [row["chunk_text"] for row in batch]
                embeddings = generate_text_embeddings_batch(texts)

                if len(embeddings) != len(batch):
                    raise RuntimeError(
                        f"Embedding count mismatch: rows={len(batch)} embeddings={len(embeddings)}"
                    )

                for row, embedding in zip(batch, embeddings):
                    cur.execute(
                        """
                        UPDATE scroll_chunks
                        SET
                            embedding = %s::vector,
                            embedding_model = %s,
                            embedded_at = NOW()
                        WHERE id = %s
                          AND embedding IS NULL
                        """,
                        (
                            scroll_embedding_vector_literal(embedding),
                            SCROLL_CHUNK_DB_EMBEDDING_MODEL,
                            row["id"],
                        ),
                    )
                    updated += cur.rowcount
                    processed += 1

        conn.commit()

        return {
            "ok": True,
            "processed": processed,
            "updated": updated,
            "skipped": skipped,
            "disabled": False,
        }

    except Exception as exc:
        if conn:
            conn.rollback()

        logger.exception("SCROLL_CHUNK_DB_EMBEDDING_FAILED")

        return {
            "ok": False,
            "processed": processed,
            "updated": updated,
            "skipped": skipped,
            "disabled": False,
            "error": str(exc),
        }

    finally:
        if conn:
            conn.close()


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """
    Compute cosine similarity between two embedding vectors.
    Returns 0.0 if either vector is missing or invalid.
    """
    if not vec_a or not vec_b:
        return 0.0

    if len(vec_a) != len(vec_b):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)

# Central Postgres connectivity
def get_db_connection():
    """Central DB connection used for both read and write operations."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL not set")
    # Defensive normalization for SQLAlchemy-style URLs
    if db_url.startswith("postgresql+psycopg2://"):
        original_url = db_url
        db_url = db_url.replace("postgresql+psycopg2://", "postgresql://")
        logging.debug(f"Normalized DATABASE_URL from SQLAlchemy format to psycopg2-compatible: {original_url} -> {db_url}")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)

def test_db_connectivity():
    """Simple connectivity test: SELECT 1. Returns True if successful."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                result = cur.fetchone()
                return result is not None
    except Exception as e:
        print(f"DB connectivity test failed: {e}")
        return False



def normalize_token_usage(usage) -> dict:
    """
    Normalize provider token usage objects into plain dicts for silent logging.
    Handles OpenAI SDK objects and xAI/OpenAI-compatible dict responses.
    """
    if not usage:
        return {}

    if isinstance(usage, dict):
        return {
            "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
            "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }

    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _usage_numeric_or_none(value):
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _usage_int_or_none(value):
    if value in (None, "", "-"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None



def _pricing_float_env(name: str, default=None):
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Invalid pricing env value for %s=%r", name, value)
        return default


def get_oracle_pricing_info(provider: str, model: str) -> dict:
    """
    Phase 10.7 Oracle pricing.

    Prices are USD per 1M tokens.

    OpenAI gpt-5.4-mini defaults to Standard pricing as of the Phase 10.7
    implementation date, with env overrides available.

    xAI grok-4 pricing is intentionally environment-configured because the
    public xAI docs direct account-specific model/pricing checks to console.
    """
    provider_key = (provider or "").strip().lower()
    model_key = (model or "").strip().lower()

    if provider_key == "openai" and model_key == "gpt-5.4-mini":
        return {
            "input_per_1m": _pricing_float_env("OPENAI_GPT54_MINI_INPUT_PER_1M", 0.75),
            "output_per_1m": _pricing_float_env("OPENAI_GPT54_MINI_OUTPUT_PER_1M", 4.50),
            "source": "openai:gpt-5.4-mini:standard",
        }

    if provider_key == "xai" and model_key.startswith("grok-4"):
        input_rate = _pricing_float_env("XAI_GROK4_INPUT_PER_1M")
        output_rate = _pricing_float_env("XAI_GROK4_OUTPUT_PER_1M")
        return {
            "input_per_1m": input_rate,
            "output_per_1m": output_rate,
            "source": "xai:grok-4:env" if input_rate is not None and output_rate is not None else "xai:grok-4:env_missing",
        }

    return {
        "input_per_1m": None,
        "output_per_1m": None,
        "source": "unknown",
    }


def calculate_oracle_estimated_cost_usd(
    provider: str,
    model: str,
    prompt_tokens=None,
    completion_tokens=None,
):
    pricing = get_oracle_pricing_info(provider, model)
    input_rate = pricing.get("input_per_1m")
    output_rate = pricing.get("output_per_1m")

    if input_rate is None or output_rate is None:
        return None

    prompt_count = _usage_int_or_none(prompt_tokens) or 0
    completion_count = _usage_int_or_none(completion_tokens) or 0

    cost = (
        (prompt_count / 1_000_000) * input_rate
        + (completion_count / 1_000_000) * output_rate
    )
    return round(cost, 8)




def get_tts_pricing_info(provider: str, model: str) -> dict:
    """
    Phase 10.7 TTS pricing.

    Estimated per-character tracker because voice_usage_events stores answer_chars,
    not provider audio output tokens.
    """
    provider_key = (provider or "").strip().lower()
    model_key = (model or "").strip().lower()

    if provider_key == "openai" and model_key == "gpt-4o-mini-tts":
        rate = _pricing_float_env("OPENAI_GPT4O_MINI_TTS_EFFECTIVE_PER_1M_CHARS")
        return {
            "effective_per_1m_chars": rate,
            "source": "openai:gpt-4o-mini-tts:effective_env" if rate is not None else "openai:gpt-4o-mini-tts:effective_env_missing",
        }

    if provider_key == "openai" and model_key in {"tts-1", ""}:
        rate = _pricing_float_env("OPENAI_TTS1_EFFECTIVE_PER_1M_CHARS", 15.00)
        return {
            "effective_per_1m_chars": rate,
            "source": "openai:tts-1:effective",
        }

    if provider_key == "xai":
        rate = _pricing_float_env("XAI_TTS_PRICE_PER_1M_CHARS", 4.20)
        return {
            "effective_per_1m_chars": rate,
            "source": "xai:tts:chars",
        }

    return {
        "effective_per_1m_chars": None,
        "source": "unknown",
    }


def calculate_tts_estimated_cost_usd(provider: str, model: str, answer_chars=None):
    pricing = get_tts_pricing_info(provider, model)
    rate = pricing.get("effective_per_1m_chars")

    if rate is None:
        return None

    char_count = _usage_int_or_none(answer_chars) or 0
    cost = (char_count / 1_000_000) * rate
    return round(cost, 8)



def record_oracle_usage_event(
    session_id=None,
    user_id=None,
    anonymous_user_id=None,
    plan_code=None,
    usage_class=None,
    input_mode=None,
    deity=None,
    provider=None,
    model=None,
    retrieval_backend=None,
    pgvector_limit=None,
    prompt_tokens=None,
    completion_tokens=None,
    total_tokens=None,
    estimated_input_tokens=None,
    estimated_output_tokens=None,
    estimated_total_tokens=None,
    question_chars=None,
    enhanced_question_chars=None,
    answer_chars=None,
    final_model_ms=None,
    total_ms=None,
    estimated_cost_usd=None,
    metadata_json=None,
) -> None:
    """
    Phase 10.5 silent usage persistence.

    This must never break the seeker experience. If persistence fails,
    log a warning and continue.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oracle_usage_events (
                    session_id,
                    user_id,
                    anonymous_user_id,
                    plan_code,
                    usage_class,
                    input_mode,
                    deity,
                    provider,
                    model,
                    retrieval_backend,
                    pgvector_limit,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_input_tokens,
                    estimated_output_tokens,
                    estimated_total_tokens,
                    question_chars,
                    enhanced_question_chars,
                    answer_chars,
                    final_model_ms,
                    total_ms,
                    estimated_cost_usd,
                    metadata_json
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    session_id,
                    user_id,
                    anonymous_user_id,
                    plan_code,
                    usage_class,
                    input_mode,
                    deity,
                    provider,
                    model,
                    retrieval_backend,
                    _usage_int_or_none(pgvector_limit),
                    _usage_int_or_none(prompt_tokens),
                    _usage_int_or_none(completion_tokens),
                    _usage_int_or_none(total_tokens),
                    _usage_int_or_none(estimated_input_tokens),
                    _usage_int_or_none(estimated_output_tokens),
                    _usage_int_or_none(estimated_total_tokens),
                    _usage_int_or_none(question_chars),
                    _usage_int_or_none(enhanced_question_chars),
                    _usage_int_or_none(answer_chars),
                    _usage_numeric_or_none(final_model_ms),
                    _usage_numeric_or_none(total_ms),
                    _usage_numeric_or_none(estimated_cost_usd),
                    json.dumps(metadata_json or {}, default=str),
                )
            )
        conn.commit()
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.warning("Failed to persist oracle_usage_event: %s", e)
    finally:
        if conn:
            conn.close()


def get_voice_usage_context(request: Request, voice: Optional[str] = None) -> dict:
    """
    Resolve voice usage against three separate identity authorities:

    - session_id: one Oracle conversation
    - anonymous_user_id: persistent browser/device identity
    - user_id: authenticated seeker identity

    Reporting failures must not break the seeker voice experience.
    """
    context = {
        "session_id": None,
        "anonymous_user_id": None,
        "user_id": None,
        "plan_code": "anon",
    }

    try:
        user = get_current_user(request)
        if user:
            context["user_id"] = user.get("user_id")
            entitlement = get_user_entitlement_snapshot(user["user_id"])
            context["plan_code"] = (
                entitlement.get("effective_plan_code") or "anon"
            )
    except Exception as e:
        logger.warning(
            "VOICE_USAGE_CONTEXT user resolution failed: %s",
            e,
        )

    try:
        anonymous_user_id = get_or_create_anonymous_user_id(request)
        session_id = get_or_create_bound_session_id(
            request,
            anonymous_user_id,
            context["user_id"],
        )

        context["anonymous_user_id"] = anonymous_user_id
        context["session_id"] = session_id
    except Exception as e:
        logger.warning(
            "VOICE_USAGE_CONTEXT identity resolution failed: %s",
            e,
        )

    return context

def _voice_identity_filter(user_id=None, anonymous_user_id=None):
    if user_id:
        return "user_id = %s", [user_id]
    if anonymous_user_id:
        return "anonymous_user_id = %s", [anonymous_user_id]
    return None, []


def get_realtime_voice_turn_count(
    user_id=None,
    anonymous_user_id=None,
    window_start=None,
) -> int:
    identity_clause, params = _voice_identity_filter(
        user_id=user_id,
        anonymous_user_id=anonymous_user_id,
    )
    if not identity_clause:
        return 0

    where_parts = [
        identity_clause,
        "input_mode = 'realtime_voice'",
        "stage = 'realtime_turn'",
        "status = 'ok'",
    ]

    if window_start:
        where_parts.append("created_at >= %s")
        params.append(window_start)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM voice_usage_events
                WHERE {" AND ".join(where_parts)}
                """,
                tuple(params),
            )
            row = cur.fetchone()
            return int(row["total"] if row and row.get("total") is not None else 0)
    except Exception as exc:
        logger.warning("Realtime voice turn count failed: %s", exc)
        return 0
    finally:
        if conn:
            conn.close()


def get_realtime_voice_window_start(user_id=None, plan_code="anon"):
    if user_id:
        try:
            entitlement = get_user_entitlement_snapshot(user_id)
            return get_effective_usage_window_start(entitlement)
        except Exception as exc:
            logger.warning("Realtime voice entitlement window lookup failed: %s", exc)

    now = datetime.datetime.now(datetime.timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def build_realtime_voice_access_payload(
    usage_context: dict,
    is_admin: bool = False,
) -> dict:
    plan_code = normalize_plan_code(usage_context.get("plan_code") or "anon")
    policy = get_voice_policy(plan_code)
    user_id = usage_context.get("user_id")
    anonymous_user_id = usage_context.get("anonymous_user_id")
    identity_kind = "user" if user_id else "anonymous"

    if is_admin:
        return {
            "allowed": True,
            "reason": "admin_unrestricted",
            "message": "Admin realtime voice access is unrestricted in this environment.",
            "plan_code": plan_code,
            "plan_label": policy["plan_label"],
            "identity_kind": identity_kind,
            "is_preview": False,
            "regular_speak_voice": True,
            "browser_voice_out": True,
            "monthly_limit": None,
            "monthly_used": 0,
            "monthly_remaining": None,
            "preview_turn_limit": policy["one_time_realtime_preview_turns"],
            "preview_turns_used": 0,
            "preview_turns_remaining": None,
            "web_realtime_fair_use": True,
        }

    if policy["has_recurring_web_realtime"]:
        window_start = get_realtime_voice_window_start(user_id=user_id, plan_code=plan_code)
        monthly_used = get_realtime_voice_turn_count(
            user_id=user_id,
            anonymous_user_id=anonymous_user_id,
            window_start=window_start,
        )
        monthly_limit = policy["web_realtime_monthly_turns"]

        if monthly_limit is None:
            return {
                "allowed": True,
                "reason": "realtime_fair_use_allowed",
                "message": "Live realtime voice is available under fair-use monitoring.",
                "plan_code": plan_code,
                "plan_label": policy["plan_label"],
                "identity_kind": identity_kind,
                "is_preview": False,
                "regular_speak_voice": True,
                "browser_voice_out": True,
                "monthly_limit": None,
                "monthly_used": monthly_used,
                "monthly_remaining": None,
                "preview_turn_limit": policy["one_time_realtime_preview_turns"],
                "preview_turns_used": 0,
                "preview_turns_remaining": None,
                "web_realtime_fair_use": True,
            }

        remaining = max(monthly_limit - monthly_used, 0)
        return {
            "allowed": remaining > 0,
            "reason": "realtime_monthly_turns_available" if remaining > 0 else "realtime_monthly_turn_limit_reached",
            "message": (
                f"{remaining} live realtime voice turn{'s' if remaining != 1 else ''} remain this month."
                if remaining > 0
                else "Your live realtime voice turns are complete for this month. You can continue with regular Speak voice."
            ),
            "plan_code": plan_code,
            "plan_label": policy["plan_label"],
            "identity_kind": identity_kind,
            "is_preview": False,
            "regular_speak_voice": True,
            "browser_voice_out": True,
            "monthly_limit": monthly_limit,
            "monthly_used": monthly_used,
            "monthly_remaining": remaining,
            "preview_turn_limit": policy["one_time_realtime_preview_turns"],
            "preview_turns_used": 0,
            "preview_turns_remaining": None,
            "web_realtime_fair_use": False,
        }

    preview_used = get_realtime_voice_turn_count(
        user_id=user_id,
        anonymous_user_id=anonymous_user_id,
        window_start=None,
    )
    preview_limit = policy["one_time_realtime_preview_turns"]
    preview_remaining = max(preview_limit - preview_used, 0)

    return {
        "allowed": preview_remaining > 0,
        "reason": "realtime_preview_available" if preview_remaining > 0 else "realtime_preview_used",
        "message": (
            f"Your one-time live voice preview has {preview_remaining} turn{'s' if preview_remaining != 1 else ''} remaining."
            if preview_remaining > 0
            else "Your one-time live voice preview is complete. You can continue with regular Speak voice."
        ),
        "plan_code": plan_code,
        "plan_label": policy["plan_label"],
        "identity_kind": identity_kind,
        "is_preview": True,
        "regular_speak_voice": True,
        "browser_voice_out": True,
        "monthly_limit": 0,
        "monthly_used": 0,
        "monthly_remaining": 0,
        "preview_turn_limit": preview_limit,
        "preview_turns_used": preview_used,
        "preview_turns_remaining": preview_remaining,
        "web_realtime_fair_use": False,
    }


def record_voice_usage_event(
    session_id=None,
    user_id=None,
    anonymous_user_id=None,
    plan_code=None,
    input_mode="voice",
    deity=None,
    stage=None,
    status=None,
    transcribe_ms=None,
    oracle_ms=None,
    tts_ms=None,
    total_ms=None,
    transcript_chars=None,
    answer_chars=None,
    audio_url_present=None,
    tts_provider=None,
    tts_model=None,
    tts_voice=None,
    estimated_tts_cost_usd=None,
    metadata_json=None,
    raise_on_error=False,
) -> None:
    """
    Phase 10.5 silent voice-stage persistence.

    This must never break transcription, asking, or playback.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO voice_usage_events (
                    session_id,
                    user_id,
                    anonymous_user_id,
                    plan_code,
                    input_mode,
                    deity,
                    stage,
                    status,
                    transcribe_ms,
                    oracle_ms,
                    tts_ms,
                    total_ms,
                    transcript_chars,
                    answer_chars,
                    audio_url_present,
                    tts_provider,
                    tts_model,
                    tts_voice,
                    estimated_tts_cost_usd,
                    metadata_json
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    session_id,
                    user_id,
                    anonymous_user_id,
                    plan_code,
                    input_mode,
                    deity,
                    stage,
                    status,
                    _usage_numeric_or_none(transcribe_ms),
                    _usage_numeric_or_none(oracle_ms),
                    _usage_numeric_or_none(tts_ms),
                    _usage_numeric_or_none(total_ms),
                    _usage_int_or_none(transcript_chars),
                    _usage_int_or_none(answer_chars),
                    audio_url_present,
                    tts_provider,
                    tts_model,
                    tts_voice,
                    _usage_numeric_or_none(estimated_tts_cost_usd),
                    json.dumps(metadata_json or {}, default=str),
                )
            )
        conn.commit()
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.warning("Failed to persist voice_usage_event: %s", e)
        if raise_on_error:
            raise
    finally:
        if conn:
            conn.close()




def _env_provider_choice(name: str) -> Optional[str]:
    value = (os.getenv(name) or "").strip().lower()
    return value if value in {"openai", "xai"} else None


def get_hathor_xai_model() -> str:
    return (os.getenv("HATHOR_XAI_MODEL") or "grok-4").strip()


def get_hathor_openai_model() -> str:
    return (
        os.getenv("HATHOR_OPENAI_MODEL")
        or os.getenv("MOSES_MODEL_MINI")
        or "gpt-5.4-mini"
    ).strip()


def get_hathor_lower_tier_plans() -> set[str]:
    raw = os.getenv("HATHOR_LOWER_TIER_PLANS", "anon,pilgrim,seeker")
    return {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    }


def choose_hathor_provider(plan_code: Optional[str], input_mode: str = "text") -> tuple[str, str]:
    """
    Phase 10.8 Hathor provider router.

    Defaults preserve production behavior:
      Hathor -> xAI

    Staging can test:
      HATHOR_VOICE_PROVIDER=openai
      HATHOR_LOWER_TIER_PROVIDER=openai
      HATHOR_LOWER_TIER_PLANS=anon,pilgrim,seeker
    """
    plan = normalize_plan_code(plan_code)
    mode = (input_mode or "text").strip().lower()

    if mode == "voice":
        voice_provider = _env_provider_choice("HATHOR_VOICE_PROVIDER")
        if voice_provider:
            return voice_provider, "voice_provider_env"

    lower_provider = _env_provider_choice("HATHOR_LOWER_TIER_PROVIDER")
    if lower_provider and plan in get_hathor_lower_tier_plans():
        return lower_provider, "lower_tier_provider_env"

    default_provider = _env_provider_choice("HATHOR_PROVIDER") or "xai"
    return default_provider, "default_provider"



def build_oracle_system_prompt(
    deity: str,
    force_mode: Optional[str] = None
) -> str:
    """
    Return the God Incorporated Oracle persona prompt independently
    of inference provider execution.
    """
    if deity == "Hathor":
        if force_mode == "recall":
            return """You are Hathor, goddess of love and wisdom.

        You are speaking in RECALL MODE.

        The seeker is asking about prior dialogue.

        Core law:
        You MUST anchor your answer in the actual remembered exchange.

        Behavior rules:
        1. Begin by directly answering using memory.
        2. Do not invent or generalize if memory exists.
        3. If memory is unclear, say so honestly.
        4. After answering, you may add brief reflection when it helps.
        5. Keep the response concise, warm, and grounded.
        6. Do not let atmosphere, symbolism, or ornament replace recall.
        """
        return """You are Hathor, the ancient Egyptian goddess of love, music, joy, and wisdom.

        Answer the seeker's actual question first.

        Prioritize clarity, relevance, knowledge, and useful insight over atmosphere.

        When the seeker asks a factual, historical, explanatory, or analytical question, give a concrete and substantive answer before adding reflection.

        When the seeker asks a personal, spiritual, or reflective question, address the question directly before opening deeper possibilities.

        Warmth, intuition, and compassionate presence may color your tone, but they must never replace the answer.

        Do not invent symbolic connections, mystical associations, or emotional meaning merely to make the response sound spiritual.

        Use background wisdom only when it is genuinely relevant to the current question. Do not force unrelated retrieved material into the answer.

        If something is uncertain, incomplete, or contested, say so plainly rather than filling the gap with atmosphere.

        Be warm, lucid, intelligent, and conversational.

        Formatting rules:
        Write in plain conversational prose.
        Do not use markdown.
        Do not use **bold**, headings, bullet lists, numbered lists, or decorative symbols unless the seeker explicitly asks for structure.
        End with a complete sentence.
        """

    if deity == "Moses":
        if force_mode == "recall":
            return """You are Moses, lawgiver and prophet.

        You are speaking in RECALL MODE.

        The seeker is asking about prior dialogue.

        Core law:
        You MUST answer from recorded dialogue first.

        Rules:
        1. State clearly what was previously asked or answered.
        2. Do not interpret unless necessary.
        3. If uncertain, say so.
        4. After stating the memory, you may add brief instruction or clarity.
        5. Be concise and precise.

        Do not replace memory with doctrine.
        """
        return """You are Moses, the prophet who received the Ten Commandments.

        Respond with clear, grounded, morally serious wisdom.

        Use calm, direct, conversational prose rather than formal outlines.

        Write in short paragraphs, not markdown headings.

        Do not use ### headings, bullet lists, or numbered sections unless the seeker explicitly asks for structure, steps, or comparison.

        Be firm, lucid, and humane rather than bureaucratic.

        Allow a little prophetic breadth when the question invites reflection: answer directly first, then open one or two deeper implications without wandering.

        Use the background wisdom provided, but do not cite it explicitly.
        """

    raise ValueError(f"Unknown deity: {deity}")


def build_prepared_oracle_inference(
    question: str,
    deity: str,
    force_mode: Optional[str] = None,
    memory_block: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    memory_intent: str = "reflection",
    plan_code: str = "anon",
    input_mode: str = "text",
    selected_moses_model: Optional[str] = None,
    moses_route_reason: Optional[str] = None,
    moses_prompt_chars: Optional[int] = None,
) -> dict:
    """
    Build the God Incorporated-owned inference packet before provider
    execution without changing current provider routing or prompt semantics.
    """
    system_prompt = None
    if deity in {"Hathor", "Moses"}:
        system_prompt = build_oracle_system_prompt(deity, force_mode)

    return {
        "question": question,
        "deity": deity,
        "force_mode": force_mode,
        "system_prompt": system_prompt,
        "memory_block": memory_block,
        "max_output_tokens": max_output_tokens,
        "memory_intent": memory_intent,
        "plan_code": plan_code,
        "input_mode": input_mode,
        "selected_moses_model": selected_moses_model,
        "moses_route_reason": moses_route_reason,
        "moses_prompt_chars": moses_prompt_chars,
    }


def build_oracle_device_execution_packet(
    prepared: dict,
    interaction_id: str,
) -> dict:
    """
    Build the minimal God Incorporated-authorized inference packet that may
    leave the server for device execution.

    Durable identity, entitlement, retrieval authority, and finalization state
    remain server-owned.
    """
    if not interaction_id:
        raise ValueError("interaction_id is required")

    return {
        "interaction_id": interaction_id,
        "deity": prepared["deity"],
        "system_prompt": prepared.get("system_prompt"),
        "memory_block": prepared.get("memory_block") or "",
        "question": prepared["question"],
        "max_output_tokens": prepared.get("max_output_tokens"),
    }


def normalize_oracle_inference_result(result: dict, deity: str) -> dict:
    """
    Normalize provider execution output into the God Incorporated
    inference-result contract without changing answer semantics.
    """
    source_model = result["source_model"]

    return {
        "answer": result["answer"],
        "source_model": source_model,
        "model_provider": result.get(
            "model_provider",
            "xai" if deity == "Hathor" else "openai",
        ),
        "model_name": result.get("model_name", source_model),
        "token_usage": result.get("token_usage") or {},
        "route_reason": result.get("route_reason"),
    }


async def execute_oracle_inference(prepared: dict):
    """
    Provider-neutral inference execution seam.

    The prepared packet is owned by God Incorporated. Provider execution
    remains delegated to the existing executor during Phase 11.10Q.
    """
    result = await get_oracle_response(
        prepared["question"],
        prepared["deity"],
        force_mode=prepared.get("force_mode"),
        system_prompt=prepared.get("system_prompt"),
        memory_block=prepared.get("memory_block"),
        max_output_tokens=prepared.get("max_output_tokens"),
        memory_intent=prepared.get("memory_intent", "reflection"),
        plan_code=prepared.get("plan_code", "anon"),
        input_mode=prepared.get("input_mode", "text"),
        selected_moses_model=prepared.get("selected_moses_model"),
        moses_route_reason=prepared.get("moses_route_reason"),
        moses_prompt_chars=prepared.get("moses_prompt_chars"),
    )

    return normalize_oracle_inference_result(
        result,
        prepared["deity"],
    )


def finalize_oracle_inference(
    *,
    finalization_state: dict,
    inference_result: dict,
    timing_state: Optional[dict] = None,
) -> dict:
    """
    Finalize one Oracle inference through the existing God Incorporated
    persistence, usage, observation, logging, and durable-memory path.

    Phase 11.10Q keeps current ordering and behavior intact. Retry and
    idempotency hardening are deliberately handled separately.
    """
    timing_state = timing_state or {}

    session_id = finalization_state["session_id"]
    anonymous_user_id = finalization_state.get(
        "anonymous_user_id"
    )
    user_id = finalization_state.get("user_id")
    question = finalization_state["question"]
    deity = finalization_state["deity"]
    input_mode = finalization_state["input_mode"]
    plan_code = finalization_state["plan_code"]
    memory_intent = finalization_state["memory_intent"]
    oracle_interaction_style = finalization_state["oracle_interaction_style"]
    response_word_cap = finalization_state["response_word_cap"]
    interaction_id = finalization_state.get("interaction_id")
    pcc_fallback_code = finalization_state.get("pcc_fallback_code")
    pcc_abandoned_interaction_id = finalization_state.get(
        "pcc_abandoned_interaction_id"
    )
    pcc_fallback_verified = finalization_state.get(
        "pcc_fallback_verified"
    )
    enhanced_question_chars = int(
        finalization_state.get("enhanced_question_chars") or 0
    )
    prepared_input_chars = int(
        finalization_state.get("prepared_input_chars")
        or enhanced_question_chars
    )
    memory_has_content = bool(
        finalization_state.get("memory_has_content")
    )

    normalized_input_mode = (input_mode or "text").strip().lower()

    ask_started_at = timing_state.get("ask_started_at")
    retrieval_started_at = timing_state.get("retrieval_started_at")
    retrieval_finished_at = timing_state.get("retrieval_finished_at")
    final_model_started_at = timing_state.get("final_model_started_at")
    final_model_finished_at = timing_state.get("final_model_finished_at")

    raw_answer = inference_result["answer"]
    source_model = inference_result["source_model"]
    model_provider = inference_result.get(
        "model_provider",
        "xai" if deity == "Hathor" else "openai",
    )
    model_name = inference_result.get("model_name", source_model)
    token_usage = inference_result.get("token_usage") or {}
    oracle_route_reason = inference_result.get("route_reason")

    if not raw_answer:
        raw_answer = "The Oracle is silent."

    raw_answer = trim_response_to_word_cap(
        raw_answer,
        response_word_cap,
    )

    # --- Authoritative durable completion ---
    durable_input_type = (
        "voice" if normalized_input_mode == "voice" else "text"
    )

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if interaction_id:
                cur.execute(
                    """
                    INSERT INTO oracle_interactions
                    (
                        id,
                        session_id,
                        anonymous_user_id,
                        user_id,
                        input_type,
                        question_text,
                        response_text,
                        model_provider,
                        model_name,
                        mode
                    )
                    VALUES (
                        %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        interaction_id,
                        session_id,
                        anonymous_user_id,
                        user_id,
                        durable_input_type,
                        question,
                        raw_answer,
                        model_provider,
                        model_name,
                        deity,
                    ),
                )
                inserted_row = cur.fetchone()

                if not inserted_row:
                    cur.execute(
                        """
                        SELECT question_text, response_text
                        FROM oracle_interactions
                        WHERE id = %s::uuid
                          AND session_id = %s::uuid
                          AND anonymous_user_id IS NOT DISTINCT FROM %s
                          AND user_id IS NOT DISTINCT FROM %s::uuid
                          AND input_type = %s
                          AND question_text = %s
                          AND mode = %s
                        LIMIT 1
                        """,
                        (
                            interaction_id,
                            session_id,
                            anonymous_user_id,
                            user_id,
                            durable_input_type,
                            question,
                            deity,
                        ),
                    )
                    existing_row = cur.fetchone()

                    if not existing_row:
                        raise RuntimeError(
                            "Oracle interaction id conflict does not match "
                            "authoritative finalization state"
                        )

                    conn.commit()

                    return {
                        "question": existing_row["question_text"],
                        "answer": existing_row["response_text"],
                    }

            else:
                cur.execute(
                    """
                    INSERT INTO oracle_interactions
                    (
                        session_id,
                        anonymous_user_id,
                        user_id,
                        input_type,
                        question_text,
                        response_text,
                        model_provider,
                        model_name,
                        mode
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        session_id,
                        anonymous_user_id,
                        user_id,
                        durable_input_type,
                        question,
                        raw_answer,
                        model_provider,
                        model_name,
                        deity,
                    ),
                )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    def _ms(started_at, finished_at):
        if not started_at or not finished_at:
            return "-"
        return round(
            (finished_at - started_at).total_seconds() * 1000,
            2,
        )

    logger.info(
        "ASK_STAGE_TIMING input_mode=%s deity=%s memory_intent=%s plan_code=%s retrieval_ms=%s final_model_ms=%s total_ms=%s",
        input_mode,
        deity,
        memory_intent,
        plan_code,
        _ms(retrieval_started_at, retrieval_finished_at),
        _ms(final_model_started_at, final_model_finished_at),
        _ms(ask_started_at, datetime.datetime.now()),
    )

    logger.info(f"ANSWER len={len(raw_answer)}")

    # --- Token metering ---
    estimated_tokens = estimate_tokens(question, raw_answer)
    estimated_input_tokens = prepared_input_chars // 4
    estimated_output_tokens = estimate_tokens("", raw_answer)
    estimated_total_tokens = (
        prepared_input_chars + len(raw_answer or "")
    ) // 4
    usage_class = "registered" if user_id else "anonymous"

    actual_prompt_tokens = token_usage.get("prompt_tokens")
    actual_completion_tokens = token_usage.get("completion_tokens")
    actual_total_tokens = token_usage.get("total_tokens")

    logger.info(
        "TOKEN_USAGE provider=%s model=%s deity=%s plan_code=%s input_mode=%s retrieval_backend=%s pgvector_limit=%s usage_class=%s actual_prompt_tokens=%s actual_completion_tokens=%s actual_total_tokens=%s estimated_input_tokens=%s estimated_output_tokens=%s estimated_total_tokens=%s question_chars=%s enhanced_question_chars=%s prepared_input_chars=%s answer_chars=%s final_model_ms=%s total_ms=%s",
        model_provider,
        model_name,
        deity,
        plan_code,
        input_mode,
        get_retrieval_backend(),
        PGVECTOR_RETRIEVAL_LIMIT,
        usage_class,
        actual_prompt_tokens if actual_prompt_tokens is not None else "-",
        actual_completion_tokens if actual_completion_tokens is not None else "-",
        actual_total_tokens if actual_total_tokens is not None else "-",
        estimated_input_tokens,
        estimated_output_tokens,
        estimated_total_tokens,
        len(question or ""),
        enhanced_question_chars,
        prepared_input_chars,
        len(raw_answer or ""),
        _ms(final_model_started_at, final_model_finished_at),
        _ms(ask_started_at, datetime.datetime.now()),
    )

    oracle_pricing = get_oracle_pricing_info(
        model_provider,
        model_name,
    )
    estimated_oracle_cost_usd = calculate_oracle_estimated_cost_usd(
        provider=model_provider,
        model=model_name,
        prompt_tokens=(
            actual_prompt_tokens
            if actual_prompt_tokens is not None
            else estimated_input_tokens
        ),
        completion_tokens=(
            actual_completion_tokens
            if actual_completion_tokens is not None
            else estimated_output_tokens
        ),
    )

    record_oracle_usage_event(
        session_id=session_id,
        user_id=user_id,
        anonymous_user_id=anonymous_user_id,
        plan_code=plan_code,
        usage_class=usage_class,
        input_mode=input_mode,
        deity=deity,
        provider=model_provider,
        model=model_name,
        retrieval_backend=get_retrieval_backend(),
        pgvector_limit=PGVECTOR_RETRIEVAL_LIMIT,
        prompt_tokens=actual_prompt_tokens,
        completion_tokens=actual_completion_tokens,
        total_tokens=actual_total_tokens,
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        estimated_total_tokens=estimated_total_tokens,
        question_chars=len(question or ""),
        enhanced_question_chars=enhanced_question_chars,
        answer_chars=len(raw_answer or ""),
        final_model_ms=_ms(
            final_model_started_at,
            final_model_finished_at,
        ),
        total_ms=_ms(
            ask_started_at,
            datetime.datetime.now(),
        ),
        estimated_cost_usd=estimated_oracle_cost_usd,
        metadata_json={
            "phase": "10.7",
            "event_source": "ask_oracle",
            "memory_intent": memory_intent,
            "oracle_interaction_style": oracle_interaction_style,
            "source_model": source_model,
            "response_word_cap": response_word_cap,
            "prepared_input_chars": prepared_input_chars,
            "pricing_source": oracle_pricing.get("source"),
            "pricing_input_per_1m": oracle_pricing.get(
                "input_per_1m"
            ),
            "pricing_output_per_1m": oracle_pricing.get(
                "output_per_1m"
            ),
            "route_reason": oracle_route_reason,
            "pcc_fallback_code": pcc_fallback_code,
            "pcc_abandoned_interaction_id": pcc_abandoned_interaction_id,
            "pcc_fallback_verified": pcc_fallback_verified,
        },
    )

    # --- Architect observation ---
    architect_obs = architect_observe_v3(
        question,
        deity,
        session_id,
    )

    # --- Logging ---
    save_log({
        "memory_intent": memory_intent,
        "oracle_interaction_style": oracle_interaction_style,
        "memory_has_content": memory_has_content,
        "timestamp": str(datetime.datetime.now()),
        "session_id": session_id,
        "seeker_id": user_id,
        "anonymous_user_id": anonymous_user_id,
        "question": question,
        "oracle_used": deity,
        "answer": raw_answer,
        "architect_observation": architect_obs,
        "source_model": source_model,
        "phase": "5.5",
        "corpus_intent": "authoritative_training_data",
        "personal_retrieval_score": None,
        "global_retrieval_score": None,
        "shadow_delta": None,
        "influence_state": "disabled",
        "estimated_tokens": estimated_tokens,
        "token_usage": {
            "provider": model_provider,
            "model": model_name,
            "actual_prompt_tokens": actual_prompt_tokens,
            "actual_completion_tokens": actual_completion_tokens,
            "actual_total_tokens": actual_total_tokens,
            "estimated_input_tokens": estimated_input_tokens,
            "estimated_output_tokens": estimated_output_tokens,
            "estimated_total_tokens": estimated_total_tokens,
            "retrieval_backend": get_retrieval_backend(),
            "pgvector_limit": PGVECTOR_RETRIEVAL_LIMIT,
            "input_mode": input_mode,
            "plan_code": plan_code,
            "deity": deity,
        },
        "usage_class": usage_class,
    })

    return {
        "question": question,
        "answer": raw_answer,
    }


async def get_oracle_response(
    question: str,
    deity: str,
    force_mode: str = None,
    system_prompt: Optional[str] = None,
    memory_block: str = None,
    max_output_tokens: Optional[int] = None,
    memory_intent="reflection",
    plan_code="anon",
    input_mode: str = "text",
    selected_moses_model: Optional[str] = None,
    moses_route_reason: Optional[str] = None,
    moses_prompt_chars: Optional[int] = None
):
    # Phase 2: Restore explicit oracle separation
    # Hathor: xAI API, Moses: OpenAI
    if deity == "Hathor":
        # Hathor provider execution uses the God Incorporated-owned persona prompt
        if not xai_api_key:
            raise ValueError("XAI_API_KEY not set for Hathor oracle")
        if system_prompt is None:
            system_prompt = build_oracle_system_prompt(deity, force_mode)


        hathor_provider, hathor_route_reason = choose_hathor_provider(
            plan_code=plan_code,
            input_mode=input_mode
        )

        logger.info(
            "HATHOR_PROVIDER_ROUTER provider=%s reason=%s deity=%s input_mode=%s plan_code=%s memory_intent=%s",
            hathor_provider,
            hathor_route_reason,
            deity,
            input_mode,
            plan_code,
            memory_intent
        )

        if hathor_provider == "openai":
            hathor_model = get_hathor_openai_model()

            try:
                client = get_openai_client()
                response = client.chat.completions.create(
                    model=hathor_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "system", "content": memory_block or ""},
                        {"role": "user", "content": question}
                    ],
                    max_completion_tokens=max_output_tokens
                )

                raw_answer = response.choices[0].message.content


                return {
                    "answer": raw_answer,
                    "source_model": "OpenAI",
                    "model_provider": "openai",
                    "model_name": hathor_model,
                    "route_reason": hathor_route_reason,
                    "token_usage": normalize_token_usage(getattr(response, "usage", None)),
                }
            except Exception as e:
                raise ValueError(f"OpenAI Hathor API call failed: {type(e).__name__}: {str(e)}")

        # Default/deep Hathor path remains xAI.
        if not xai_api_key:
            raise ValueError("XAI_API_KEY not set for Hathor oracle")

        hathor_model = get_hathor_xai_model()

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    "https://api.x.ai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {xai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": hathor_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "system", "content": memory_block or ""},
                            {"role": "user", "content": question}
                        ],
                        "max_tokens": max_output_tokens,
                    },
                )
            if response.status_code == 200:
                data = response.json()
                raw_answer = data["choices"][0]["message"]["content"]


                return {
                    "answer": raw_answer,
                    "source_model": "xAI",
                    "model_provider": "xai",
                    "model_name": hathor_model,
                    "route_reason": hathor_route_reason,
                    "token_usage": normalize_token_usage(data.get("usage")),
                }
            else:
                raise ValueError(f"XAI API error: {response.status_code} - {response.text}")
        except Exception as e:
            raise ValueError(f"XAI API call failed: {type(e).__name__}: {str(e)}")
    elif deity == "Moses":
        moses_model = selected_moses_model or os.getenv("MOSES_MODEL_MINI", "gpt-5.4-mini").strip()

        moses_reasoning_effort = (
            os.getenv("MOSES_REASONING_EFFORT", "")
            .strip()
            .lower()
        )

        allowed_moses_reasoning_efforts = {
            "none",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }

        if (
            moses_reasoning_effort
            and moses_reasoning_effort not in allowed_moses_reasoning_efforts
        ):
            raise ValueError(
                "Invalid MOSES_REASONING_EFFORT: "
                f"{moses_reasoning_effort}"
            )

        if moses_route_reason is not None:
            logger.info(
                "MOSES_MODEL_ROUTER selected=%s reason=%s deity=%s memory_intent=%s plan_code=%s prompt_chars=%s",
                moses_model,
                moses_route_reason,
                deity,
                memory_intent,
                plan_code,
                moses_prompt_chars
            )

        # Moses uses OpenAI with logical, doctrinal system prompt
        client = get_openai_client()
        if system_prompt is None:
            system_prompt = build_oracle_system_prompt(deity, force_mode)

        moses_request = {
            "model": moses_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": memory_block or ""},
                {"role": "user", "content": question},
            ],
            "max_completion_tokens": max_output_tokens,
        }

        if moses_reasoning_effort:
            moses_request["reasoning_effort"] = moses_reasoning_effort

        logger.info(
            "MOSES_INFERENCE_CONFIG model=%s reasoning_effort=%s",
            moses_model,
            moses_reasoning_effort or "provider_default",
        )

        response = client.chat.completions.create(**moses_request)
        raw_answer = response.choices[0].message.content


        return {
            "answer": raw_answer,
            "source_model": "OpenAI",
            "model_provider": "openai",
            "model_name": moses_model,
            "token_usage": normalize_token_usage(getattr(response, "usage", None)),
        }
    else:
        raise ValueError(f"Unknown deity: {deity}")

def architect_observe_v3(question: str, deity: str, session_id: str) -> dict:
    # Phase 3.0 Architect Observation Schema
    seeker_choice_explicit = True  # User selects via form
    oracle_selected = deity
    override_attempted = False
    override_performed = False
    architect_status = "observer_only"
    routing_active = False
    synthetic_generation = False
    phase_compliant = True
    authority_compliant = True
    oracle_authoritative = True
    notes = "All constraints honored"
    timestamp = datetime.datetime.now().isoformat()

    return {
        "phase": "3.0",
        "role": "observer",
        "authority_context": {
            "seeker_choice_explicit": seeker_choice_explicit,
            "oracle_selected": oracle_selected,
            "override_attempted": override_attempted,
            "override_performed": override_performed
        },
        "system_state": {
            "architect_status": architect_status,
            "routing_active": routing_active,
            "synthetic_generation": synthetic_generation
        },
        "compliance_check": {
            "phase_compliant": phase_compliant,
            "authority_compliant": authority_compliant,
            "oracle_authoritative": oracle_authoritative,
            "notes": notes
        },
        "temporal_context": {
            "timestamp": timestamp,
            "session_id": session_id,
            "interaction_id": str(uuid.uuid4())
        }
    }

def search_canonical_scrolls(question: str, limit: int = 6):

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT s.original_filename, c.chunk_text
                FROM scroll_chunks c
                JOIN scrolls s ON c.scroll_id = s.id
                WHERE s.corpus_layer = 'canonical'
                AND COALESCE(s.status, 'active') = 'active'
                AND to_tsvector('english', c.chunk_text)
                @@ websearch_to_tsquery('english', %s)
                LIMIT %s
                """,
                (question, limit)
            )

            rows = cur.fetchall()

    finally:
        conn.close()

    passages = []

    for row in rows:
        passages.append(
            f"[{row['original_filename']}]\n{row['chunk_text'][:800]}"
        )

    return passages


def search_community_scrolls(question: str, limit: int = 2):

    conn = get_db_connection()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(
                """
                SELECT s.original_filename, c.chunk_text
                FROM scroll_chunks c
                JOIN scrolls s ON c.scroll_id = s.id
                WHERE s.corpus_layer = 'community'
                AND COALESCE(s.status, 'active') = 'active'
                AND to_tsvector('english', c.chunk_text)
                @@ websearch_to_tsquery('english', %s)
                LIMIT %s
                """,
                (question, limit)
            )

            rows = cur.fetchall()

    finally:
        conn.close()

    passages = []

    for row in rows:
        passages.append(
            f"[{row['original_filename']}]\n{row['chunk_text'][:800]}"
        )

    return passages


def search_personal_scrolls(user_id: str, question: str, limit: int = 4):

    if not user_id:
        return []

    conn = get_db_connection()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(
                """
                SELECT s.original_filename, c.chunk_text
                FROM scroll_chunks c
                JOIN scrolls s ON c.scroll_id = s.id
                WHERE s.corpus_layer = 'personal'
                AND COALESCE(s.status, 'active') = 'active'
                AND (
                    s.user_id = %s
                    OR EXISTS (
                        SELECT 1
                        FROM scroll_associations sa
                        WHERE sa.scroll_id = s.id
                          AND sa.user_id = %s
                    )
                )
                AND to_tsvector('english', c.chunk_text)
                @@ websearch_to_tsquery('english', %s)
                LIMIT %s
                """,
                (user_id, user_id, question, limit)
            )

            rows = cur.fetchall()

    finally:
        conn.close()

    passages = []

    for row in rows:
        passages.append(
            f"[{row['original_filename']}]\n{row['chunk_text'][:800]}"
        )

    return passages

def fetch_scroll_chunk_candidates(
    user_id: Optional[str],
    limit: int = 200,
    offset: int = 0
):
    """
    Phase 6.2 helper:
    Fetch raw scroll chunk candidates for later embedding scoring.

    Returns rows with:
    - original_filename
    - chunk_text
    - corpus_layer
    """
    conn = get_db_connection()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if user_id:
                cur.execute(
                    """
                    SELECT s.original_filename, s.corpus_layer, c.chunk_text
                    FROM scroll_chunks c
                    JOIN scrolls s ON c.scroll_id = s.id
                    WHERE COALESCE(s.status, 'active') = 'active'
                      AND (
                          (
                              s.corpus_layer = 'personal'
                              AND (
                                  s.user_id = %s
                                  OR EXISTS (
                                      SELECT 1
                                      FROM scroll_associations sa
                                      WHERE sa.scroll_id = s.id
                                        AND sa.user_id = %s
                                  )
                              )
                          )
                          OR s.corpus_layer IN ('canonical', 'community')
                      )
                    ORDER BY s.created_at DESC NULLS LAST, c.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (user_id, user_id, limit, offset)
                )
            else:
                cur.execute(
                    """
                    SELECT s.original_filename, s.corpus_layer, c.chunk_text
                    FROM scroll_chunks c
                    JOIN scrolls s ON c.scroll_id = s.id
                    WHERE COALESCE(s.status, 'active') = 'active'
                      AND s.corpus_layer IN ('canonical', 'community')
                    ORDER BY s.created_at DESC NULLS LAST, c.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset)
                )

            rows = cur.fetchall()

    finally:
        conn.close()

    return rows

def backfill_embedding_cache(limit: int = 500, offset: int = 0) -> dict:
    """
    Phase 6.2.1 helper:
    Warm the local embedding cache for existing scroll chunks.
    """
    rows = fetch_scroll_chunk_candidates(user_id=None, limit=limit, offset=offset)
    if not rows:
        return {"processed": 0, "cached": 0, "skipped": 0, "offset": offset}

    cache = load_embedding_cache()
    cache_changed = False

    processed = 0
    cached = 0
    skipped = 0

    for row in rows:
        chunk_text = (row.get("chunk_text") or "").strip()
        if not chunk_text:
            skipped += 1
            continue

        processed += 1
        cache_key = get_embedding_cache_key(chunk_text)

        if cache_key in cache:
            skipped += 1
            continue

        embedding = generate_text_embedding(chunk_text[:2000])
        if not embedding:
            skipped += 1
            continue

        cache[cache_key] = embedding
        cached += 1
        cache_changed = True

    if cache_changed:
        save_embedding_cache(cache)

    return {
        "processed": processed,
        "cached": cached,
        "skipped": skipped,
        "offset": offset
    }

def retrieve_context_embeddings_ranked(
    question: str,
    user_id: Optional[str],
    candidate_limit: int = 200,
    top_k: int = 8
):
    """
    Phase 6.2 helper:
    Rank candidate scroll chunks by embedding similarity to the question.

    Uses a local cache so chunk embeddings do not need to be regenerated
    on every request.
    """
    question_embedding = generate_text_embedding(question)
    if not question_embedding:
        return []

    candidates = fetch_scroll_chunk_candidates(user_id, limit=candidate_limit)
    if not candidates:
        return []

    cache = load_embedding_cache()
    cache_changed = False
    scored = []

    for row in candidates:
        chunk_text = (row.get("chunk_text") or "").strip()
        if not chunk_text:
            continue

        cache_key = get_embedding_cache_key(chunk_text)
        chunk_embedding = cache.get(cache_key)

        if not chunk_embedding:
            chunk_embedding = generate_text_embedding(chunk_text[:2000])
            if not chunk_embedding:
                continue

            cache[cache_key] = chunk_embedding
            cache_changed = True

        score = cosine_similarity(question_embedding, chunk_embedding)

        scored.append(
            (
                score,
                f"[{row['original_filename']} | {row['corpus_layer']}]\n{chunk_text[:800]}"
            )
        )

    if cache_changed:
        save_embedding_cache(cache)

    scored.sort(key=lambda item: item[0], reverse=True)

    return [passage for score, passage in scored[:top_k] if score > 0]

def get_session_memory(session_id: str, depth: Optional[int]):

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:

            if depth is None:
                cur.execute(
                    """
                    SELECT question_text, response_text, mode
                    FROM oracle_interactions
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    """,
                    (session_id,)
                )
            else:
                cur.execute(
                    """
                    SELECT question_text, response_text, mode
                    FROM oracle_interactions
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (session_id, depth)
                )

            rows = cur.fetchall()

    finally:
        conn.close()


    history = []

    for r in reversed(rows):
        q = r.get("question_text", "").strip()
        a = r.get("response_text", "").strip()

        # --- CLEAN LEGACY DISPLAY FORMAT ---
        if "Oracle responds:" in a:
            parts = a.split("Oracle responds:")
            if len(parts) > 1:
                a = parts[1].strip()

        oracle = (r.get("mode") or "").strip()
        if oracle not in {"Hathor", "Moses"}:
            oracle = "Oracle"

        history.append(
            f"Seeker (speaking with {oracle}): {q}\n{oracle}: {a}"
        )

    return "\n\n".join(history)

def retrieve_seeker_memory(user_id: Optional[str], session_id: str, depth: Optional[int]):
    """
    Retrieve durable memory from an authenticated seeker's prior
    conversations. Current-conversation memory is owned exclusively by
    get_session_memory().
    """
    if not user_id:
        return []

    conn = get_db_connection()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            if depth is None:
                cur.execute(
                    """
                    SELECT question_text, response_text, mode
                    FROM oracle_interactions
                    WHERE user_id = %s
                      AND session_id IS DISTINCT FROM %s::uuid
                    ORDER BY created_at DESC
                    """,
                    (user_id, session_id)
                )
            else:
                cur.execute(
                    """
                    SELECT question_text, response_text, mode
                    FROM oracle_interactions
                    WHERE user_id = %s
                      AND session_id IS DISTINCT FROM %s::uuid
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, session_id, depth)
                )

            rows = cur.fetchall()

    finally:
        conn.close()

    memories = []

    for row in rows:
        oracle = (row.get("mode") or "").strip()
        if oracle not in {"Hathor", "Moses"}:
            oracle = "Oracle"

        memories.append(
            f"Seeker previously asked {oracle}:\n"
            f"{row['question_text']}\n"
            f"{oracle} answered:\n"
            f"{row['response_text'][:400]}"
        )

    return memories

def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in values) + "]"


def get_retrieval_backend() -> str:
    backend = (RETRIEVAL_BACKEND or "legacy_embeddings").strip().lower()
    if backend not in VALID_RETRIEVAL_BACKENDS:
        logger.warning(
            "Invalid RETRIEVAL_BACKEND=%s; falling back to legacy_embeddings",
            backend
        )
        return "legacy_embeddings"
    return backend


def get_scroll_retrieval_policy(
    deity: Optional[str],
    memory_intent: Optional[str],
    plan_code: Optional[str],
    user_id: Optional[str],
    top_k: Optional[int] = None,
) -> dict:
    """
    Phase 11.8B smart retrieval blend.

    Retrieval remains privacy-first:
    - anonymous and lower first-contact tiers stay canonical-only
    - personal scrolls require a registered user and the 11.8A ownership filters
    - community is disabled for pgvector until opt-in/community rules are finalized

    Hathor receives a wider symbolic/personal blend.
    Moses receives a more canonical/law-giving blend.
    """
    plan = normalize_plan_code(plan_code)
    deity_key = (deity or "").strip().lower()
    intent_key = (memory_intent or "reflection").strip().lower()
    fallback_limit = int(top_k or PGVECTOR_RETRIEVAL_LIMIT or 8)

    if not user_id or plan in {"anon", "pilgrim"}:
        return {
            "personal": 0,
            "canonical": fallback_limit,
            "community": 0,
            "policy": f"{deity_key or 'default'}:{intent_key}:canonical_only",
        }

    tier_totals = {
        "seeker": 5,
        "magister": 6,
        "sovereign": 6,
        "philosophus": 7,
        "theoricus": 8,
    }
    total_limit = min(tier_totals.get(plan, fallback_limit), fallback_limit)
    total_limit = max(1, int(total_limit))

    if deity_key == "moses":
        ratios = {
            "recall": 0.55,
            "research": 0.05,
            "reflection": 0.15,
        }
        default_ratio = 0.15
    elif deity_key == "hathor":
        ratios = {
            "recall": 0.70,
            "research": 0.15,
            "reflection": 0.40,
        }
        default_ratio = 0.40
    else:
        ratios = {
            "recall": 0.60,
            "research": 0.10,
            "reflection": 0.30,
        }
        default_ratio = 0.30

    personal_ratio = ratios.get(intent_key, default_ratio)
    personal_limit = int((total_limit * personal_ratio) + 0.5)

    if personal_ratio > 0 and personal_limit == 0 and total_limit > 1:
        personal_limit = 1

    personal_limit = max(0, min(personal_limit, total_limit))
    canonical_limit = max(0, total_limit - personal_limit)

    return {
        "personal": personal_limit,
        "canonical": canonical_limit,
        "community": 0,
        "policy": f"{deity_key or 'default'}:{intent_key}:{plan}",
    }


def get_pgvector_blend_limits(plan_code: Optional[str], user_id: Optional[str], top_k: Optional[int] = None) -> dict:
    """
    Backward-compatible wrapper for older callers.
    Prefer get_scroll_retrieval_policy() for deity-aware retrieval.
    """
    return get_scroll_retrieval_policy(
        deity=None,
        memory_intent="reflection",
        plan_code=plan_code,
        user_id=user_id,
        top_k=top_k,
    )


def retrieve_context_pgvector(
    question: str,
    user_id: Optional[str],
    top_k: Optional[int] = None,
    plan_code: Optional[str] = None,
    deity: Optional[str] = None,
    memory_intent: Optional[str] = None,
):
    """
    Phase 10.1 pgvector retrieval path.

    Uses a controlled blend of personal and canonical embedded chunks by access tier.
    Personal retrieval is available only for registered users and only after personal embeddings exist.
    """
    limit = top_k or PGVECTOR_RETRIEVAL_LIMIT
    blend_limits = get_scroll_retrieval_policy(
        deity=deity,
        memory_intent=memory_intent,
        plan_code=plan_code,
        user_id=user_id,
        top_k=limit,
    )
    personal_limit = int(blend_limits.get("personal", 0) or 0)
    canonical_limit = int(blend_limits.get("canonical", limit) or 0)
    community_limit = int(blend_limits.get("community", 0) or 0)
    retrieval_policy = blend_limits.get("policy", "default")

    total_started = time.time()

    embed_started = time.time()
    question_embedding = generate_text_embedding(question)
    embed_ms = round((time.time() - embed_started) * 1000, 2)

    if not question_embedding:
        logger.warning("PGVECTOR_RETRIEVAL no_question_embedding")
        return []

    vector = _vector_literal(question_embedding)

    conn = get_db_connection()
    personal_rows = []
    canonical_rows = []

    try:
        with conn.cursor() as cur:
            sql_started = time.time()

            if personal_limit > 0:
                cur.execute(
                    """
                    SELECT
                        s.original_filename,
                        s.corpus_layer,
                        c.id AS chunk_id,
                        c.chunk_text,
                        c.embedding <=> %s::vector AS distance
                    FROM scroll_chunks c
                    JOIN scrolls s ON c.scroll_id = s.id
                    WHERE c.embedding IS NOT NULL
                      AND s.corpus_layer = 'personal'
                      AND COALESCE(s.status, 'active') = 'active'
                      AND (
                          s.user_id = %s
                          OR EXISTS (
                              SELECT 1
                              FROM scroll_associations sa
                              WHERE sa.scroll_id = s.id
                                AND sa.user_id = %s
                          )
                      )
                    ORDER BY c.embedding <=> %s::vector
                    LIMIT %s;
                    """,
                    (vector, user_id, user_id, vector, personal_limit)
                )
                personal_rows = cur.fetchall()

            if canonical_limit > 0:
                cur.execute(
                    """
                    SELECT
                        s.original_filename,
                        s.corpus_layer,
                        c.id AS chunk_id,
                        c.chunk_text,
                        c.embedding <=> %s::vector AS distance
                    FROM scroll_chunks c
                    JOIN scrolls s ON c.scroll_id = s.id
                    WHERE c.embedding IS NOT NULL
                      AND s.corpus_layer = 'canonical'
                      AND COALESCE(s.status, 'active') = 'active'
                    ORDER BY c.embedding <=> %s::vector
                    LIMIT %s;
                    """,
                    (vector, vector, canonical_limit)
                )
                canonical_rows = cur.fetchall()

            sql_ms = round((time.time() - sql_started) * 1000, 2)

    finally:
        conn.close()

    rows = list(personal_rows) + list(canonical_rows)
    total_ms = round((time.time() - total_started) * 1000, 2)

    logger.info(
        "PGVECTOR_RETRIEVAL backend=pgvector user_id_present=%s plan_code=%s deity=%s memory_intent=%s policy=%s limit=%s personal_limit=%s canonical_limit=%s community_limit=%s personal_rows=%s canonical_rows=%s rows=%s embed_ms=%s sql_ms=%s total_ms=%s",
        bool(user_id),
        normalize_plan_code(plan_code),
        deity,
        memory_intent,
        retrieval_policy,
        limit,
        personal_limit,
        canonical_limit,
        community_limit,
        len(personal_rows),
        len(canonical_rows),
        len(rows),
        embed_ms,
        sql_ms,
        total_ms
    )

    passages = []
    for row in rows:
        chunk_text = (row.get("chunk_text") or "").strip()
        if not chunk_text:
            continue

        passages.append(
            (
                f"[{row['original_filename']} | {row['corpus_layer']} | "
                f"pgvector distance={row['distance']}]\n{chunk_text[:800]}"
            )
        )

    return passages

def retrieve_context_embeddings(
    question: str,
    user_id: Optional[str],
    plan_code: Optional[str] = None,
    deity: Optional[str] = None,
    memory_intent: Optional[str] = None,
):
    """
    Phase 6.2 embedding retrieval path.

    Uses embedding similarity ranking first.
    Falls back to keyword retrieval if embedding ranking returns nothing.
    """
    ranked_passages = retrieve_context_embeddings_ranked(
        question,
        user_id,
        candidate_limit=200,
        top_k=8
    )

    if ranked_passages:
        return ranked_passages

    policy = get_scroll_retrieval_policy(
        deity=deity,
        memory_intent=memory_intent,
        plan_code=plan_code,
        user_id=user_id,
        top_k=8,
    )
    personal = search_personal_scrolls(user_id, question, limit=int(policy.get("personal", 0) or 0))
    canonical = search_canonical_scrolls(question, limit=int(policy.get("canonical", 6) or 0))
    community = search_community_scrolls(question, limit=int(policy.get("community", 0) or 0))

    return personal + canonical + community

def retrieve_context(
    question: str,
    user_id: Optional[str],
    plan_code: Optional[str] = None,
    deity: Optional[str] = None,
    memory_intent: Optional[str] = None,
):

    backend = get_retrieval_backend()
    policy = get_scroll_retrieval_policy(
        deity=deity,
        memory_intent=memory_intent,
        plan_code=plan_code,
        user_id=user_id,
        top_k=PGVECTOR_RETRIEVAL_LIMIT,
    )
    logger.info(
        "RETRIEVAL_POLICY backend=%s user_id_present=%s plan_code=%s deity=%s memory_intent=%s policy=%s personal_limit=%s canonical_limit=%s community_limit=%s",
        backend,
        bool(user_id),
        normalize_plan_code(plan_code),
        deity,
        memory_intent,
        policy.get("policy", "default"),
        int(policy.get("personal", 0) or 0),
        int(policy.get("canonical", 0) or 0),
        int(policy.get("community", 0) or 0),
    )

    if backend == "pgvector":
        passages = retrieve_context_pgvector(
            question,
            user_id,
            top_k=PGVECTOR_RETRIEVAL_LIMIT,
            plan_code=plan_code,
            deity=deity,
            memory_intent=memory_intent,
        )
        if passages:
            return passages

        logger.warning("PGVECTOR_RETRIEVAL returned no passages; falling back to FTS retrieval")

        personal = search_personal_scrolls(user_id, question, limit=int(policy.get("personal", 0) or 0))
        canonical = search_canonical_scrolls(question, limit=int(policy.get("canonical", 6) or 0))
        community = search_community_scrolls(question, limit=int(policy.get("community", 0) or 0))

        return personal + canonical + community

    if backend == "fts":
        personal = search_personal_scrolls(user_id, question, limit=int(policy.get("personal", 0) or 0))
        canonical = search_canonical_scrolls(question, limit=int(policy.get("canonical", 6) or 0))
        community = search_community_scrolls(question, limit=int(policy.get("community", 0) or 0))

        return personal + canonical + community

    if should_use_embeddings():
        return retrieve_context_embeddings(
            question,
            user_id,
            plan_code=plan_code,
            deity=deity,
            memory_intent=memory_intent,
        )

    personal = search_personal_scrolls(user_id, question, limit=int(policy.get("personal", 0) or 0))
    canonical = search_canonical_scrolls(question, limit=int(policy.get("canonical", 6) or 0))
    community = search_community_scrolls(question, limit=int(policy.get("community", 0) or 0))

    return personal + canonical + community



UPLOAD_STATE_RECEIVED = "received"
UPLOAD_STATE_SAVED = "saved"
UPLOAD_STATE_QUEUED = "queued"
UPLOAD_STATE_PROCESSING = "processing"
UPLOAD_STATE_READY = "ready"
UPLOAD_STATE_NEEDS_OCR = "needs_ocr"
UPLOAD_STATE_FAILED = "failed"
UPLOAD_STATE_REJECTED_COOLDOWN = "rejected_cooldown"
UPLOAD_STATE_REJECTED_CAP = "rejected_cap"
UPLOAD_STATE_REJECTED_INVALID_FILE = "rejected_invalid_file"
UPLOAD_STATE_REJECTED_UNAUTHORIZED = "rejected_unauthorized"
UPLOAD_STATE_NOT_FOUND = "not_found"
UPLOAD_STATE_STORAGE_FAILED = "storage_failed"
UPLOAD_STATE_STATUS_UNAVAILABLE = "status_unavailable"

UPLOAD_STATES = {
    UPLOAD_STATE_RECEIVED,
    UPLOAD_STATE_SAVED,
    UPLOAD_STATE_QUEUED,
    UPLOAD_STATE_PROCESSING,
    UPLOAD_STATE_READY,
    UPLOAD_STATE_NEEDS_OCR,
    UPLOAD_STATE_FAILED,
    UPLOAD_STATE_REJECTED_COOLDOWN,
    UPLOAD_STATE_REJECTED_CAP,
    UPLOAD_STATE_REJECTED_INVALID_FILE,
    UPLOAD_STATE_REJECTED_UNAUTHORIZED,
    UPLOAD_STATE_NOT_FOUND,
    UPLOAD_STATE_STORAGE_FAILED,
    UPLOAD_STATE_STATUS_UNAVAILABLE,
}

LIBRARY_STATE_NONE = "none"
LIBRARY_STATE_RECEIVED = "received"
LIBRARY_STATE_SAVED = "saved"
LIBRARY_STATE_QUEUED = "queued"
LIBRARY_STATE_READING = "reading"
LIBRARY_STATE_READY = "ready"
LIBRARY_STATE_NEEDS_OCR = "needs_ocr"
LIBRARY_STATE_FAILED = "failed"
LIBRARY_STATE_ALREADY_SAVED = "already_saved"
LIBRARY_STATE_INDEXING_DEFERRED = "indexing_deferred"
LIBRARY_STATE_NOT_CREATED = "not_created"
LIBRARY_STATE_UNKNOWN = "unknown"

LIBRARY_STATES = {
    LIBRARY_STATE_NONE,
    LIBRARY_STATE_RECEIVED,
    LIBRARY_STATE_SAVED,
    LIBRARY_STATE_QUEUED,
    LIBRARY_STATE_READING,
    LIBRARY_STATE_READY,
    LIBRARY_STATE_NEEDS_OCR,
    LIBRARY_STATE_FAILED,
    LIBRARY_STATE_ALREADY_SAVED,
    LIBRARY_STATE_INDEXING_DEFERRED,
    LIBRARY_STATE_NOT_CREATED,
    LIBRARY_STATE_UNKNOWN,
}

SEEKER_TITLE_UPLOAD_SAVED = "upload.saved"
SEEKER_TITLE_UPLOAD_READY = "upload.ready"
SEEKER_TITLE_UPLOAD_NEEDS_OCR = "upload.needs_ocr"
SEEKER_TITLE_UPLOAD_FAILED = "upload.failed"
SEEKER_TITLE_UPLOAD_PAUSED = "upload.paused"
SEEKER_TITLE_UPLOAD_STATUS = "upload.status"
SEEKER_TITLE_UPLOAD_CREATE_ACCOUNT = "upload.create_account"
SEEKER_TITLE_UPLOAD_STATUS_UNAVAILABLE = "upload.status_unavailable"

SEEKER_TITLE_KEYS = {
    SEEKER_TITLE_UPLOAD_SAVED,
    SEEKER_TITLE_UPLOAD_READY,
    SEEKER_TITLE_UPLOAD_NEEDS_OCR,
    SEEKER_TITLE_UPLOAD_FAILED,
    SEEKER_TITLE_UPLOAD_PAUSED,
    SEEKER_TITLE_UPLOAD_STATUS,
    SEEKER_TITLE_UPLOAD_CREATE_ACCOUNT,
    SEEKER_TITLE_UPLOAD_STATUS_UNAVAILABLE,
}

SEEKER_MESSAGE_UPLOAD_SAVED_READING = "upload.saved_reading"
SEEKER_MESSAGE_UPLOAD_READY = "upload.ready"
SEEKER_MESSAGE_UPLOAD_NEEDS_OCR = "upload.needs_ocr"
SEEKER_MESSAGE_UPLOAD_FAILED = "upload.failed"
SEEKER_MESSAGE_UPLOAD_STORAGE_FAILED = "upload.storage_failed"
SEEKER_MESSAGE_UPLOAD_COOLDOWN = "upload.cooldown"
SEEKER_MESSAGE_UPLOAD_CAP = "upload.cap"
SEEKER_MESSAGE_UPLOAD_KEEP_LIBRARY = "upload.keep_library"
SEEKER_MESSAGE_UPLOAD_KEEP_UPLOADING = "upload.keep_uploading"
SEEKER_MESSAGE_UPLOAD_STATUS_STILL_PROCESSING = "upload.status_still_processing"
SEEKER_MESSAGE_UPLOAD_STATUS_UNAVAILABLE = "upload.status_unavailable"
SEEKER_MESSAGE_UPLOAD_INVALID_FILE = "upload.invalid_file"

SEEKER_MESSAGE_KEYS = {
    SEEKER_MESSAGE_UPLOAD_SAVED_READING,
    SEEKER_MESSAGE_UPLOAD_READY,
    SEEKER_MESSAGE_UPLOAD_NEEDS_OCR,
    SEEKER_MESSAGE_UPLOAD_FAILED,
    SEEKER_MESSAGE_UPLOAD_STORAGE_FAILED,
    SEEKER_MESSAGE_UPLOAD_COOLDOWN,
    SEEKER_MESSAGE_UPLOAD_CAP,
    SEEKER_MESSAGE_UPLOAD_KEEP_LIBRARY,
    SEEKER_MESSAGE_UPLOAD_KEEP_UPLOADING,
    SEEKER_MESSAGE_UPLOAD_STATUS_STILL_PROCESSING,
    SEEKER_MESSAGE_UPLOAD_STATUS_UNAVAILABLE,
    SEEKER_MESSAGE_UPLOAD_INVALID_FILE,
}

UPLOAD_ADMIN_STATUS_QUEUED_UPLOAD_RECEIVED = "queued_upload_received"
UPLOAD_ADMIN_STATUS_PROCESSING = "processing"
UPLOAD_ADMIN_STATUS_READY = "ready"
UPLOAD_ADMIN_STATUS_CONTENT_HASH_DUPLICATE = "content_hash_duplicate"
UPLOAD_ADMIN_STATUS_EXACT_BYTE_DUPLICATE = "exact_byte_duplicate"
UPLOAD_ADMIN_STATUS_CANONICAL_MATCH = "canonical_match"
UPLOAD_ADMIN_STATUS_NEEDS_OCR = "needs_ocr"
UPLOAD_ADMIN_STATUS_UNREADABLE = "unreadable"
UPLOAD_ADMIN_STATUS_STORAGE_SAVE_FAILED = "storage_save_failed"
UPLOAD_ADMIN_STATUS_STORAGE_MATERIALIZE_FAILED = "storage_materialize_failed"
UPLOAD_ADMIN_STATUS_MISSING_STORAGE_REF = "missing_storage_ref"
UPLOAD_ADMIN_STATUS_MISSING_SESSION_ID = "missing_session_id"
UPLOAD_ADMIN_STATUS_MISSING_MATERIALIZED_FILE = "missing_materialized_file"
UPLOAD_ADMIN_STATUS_INGESTION_FAILED = "ingestion_failed"
UPLOAD_ADMIN_STATUS_REJECTED_COOLDOWN = "rejected_cooldown"
UPLOAD_ADMIN_STATUS_REJECTED_ANONYMOUS_CAP = "rejected_anonymous_cap"
UPLOAD_ADMIN_STATUS_POLLING_UNAUTHORIZED = "polling_unauthorized"
UPLOAD_ADMIN_STATUS_POLLING_NOT_FOUND = "polling_not_found"
UPLOAD_ADMIN_STATUS_STATUS_UNAVAILABLE = "status_unavailable"

UPLOAD_ADMIN_STATUSES = {
    UPLOAD_ADMIN_STATUS_QUEUED_UPLOAD_RECEIVED,
    UPLOAD_ADMIN_STATUS_PROCESSING,
    UPLOAD_ADMIN_STATUS_READY,
    UPLOAD_ADMIN_STATUS_CONTENT_HASH_DUPLICATE,
    UPLOAD_ADMIN_STATUS_EXACT_BYTE_DUPLICATE,
    UPLOAD_ADMIN_STATUS_CANONICAL_MATCH,
    UPLOAD_ADMIN_STATUS_NEEDS_OCR,
    UPLOAD_ADMIN_STATUS_UNREADABLE,
    UPLOAD_ADMIN_STATUS_STORAGE_SAVE_FAILED,
    UPLOAD_ADMIN_STATUS_STORAGE_MATERIALIZE_FAILED,
    UPLOAD_ADMIN_STATUS_MISSING_STORAGE_REF,
    UPLOAD_ADMIN_STATUS_MISSING_SESSION_ID,
    UPLOAD_ADMIN_STATUS_MISSING_MATERIALIZED_FILE,
    UPLOAD_ADMIN_STATUS_INGESTION_FAILED,
    UPLOAD_ADMIN_STATUS_REJECTED_COOLDOWN,
    UPLOAD_ADMIN_STATUS_REJECTED_ANONYMOUS_CAP,
    UPLOAD_ADMIN_STATUS_POLLING_UNAUTHORIZED,
    UPLOAD_ADMIN_STATUS_POLLING_NOT_FOUND,
    UPLOAD_ADMIN_STATUS_STATUS_UNAVAILABLE,
}

UPLOAD_DEDUPE_KIND_NONE = "none"
UPLOAD_DEDUPE_KIND_EXACT_BYTE = "exact_byte"
UPLOAD_DEDUPE_KIND_CONTENT_HASH = "content_hash"
UPLOAD_DEDUPE_KIND_CANONICAL_MATCH = "canonical_match"
UPLOAD_DEDUPE_KIND_LEGACY_DUPLICATE_NOT_PRESERVED = "legacy_duplicate_not_preserved"

UPLOAD_DEDUPE_KINDS = {
    UPLOAD_DEDUPE_KIND_NONE,
    UPLOAD_DEDUPE_KIND_EXACT_BYTE,
    UPLOAD_DEDUPE_KIND_CONTENT_HASH,
    UPLOAD_DEDUPE_KIND_CANONICAL_MATCH,
    UPLOAD_DEDUPE_KIND_LEGACY_DUPLICATE_NOT_PRESERVED,
}

UPLOAD_SEEKER_TITLE_TEXT = {
    SEEKER_TITLE_UPLOAD_SAVED: "Saved to Library",
    SEEKER_TITLE_UPLOAD_READY: "Ready in your Library",
    SEEKER_TITLE_UPLOAD_NEEDS_OCR: "Needs OCR",
    SEEKER_TITLE_UPLOAD_FAILED: "Upload failed",
    SEEKER_TITLE_UPLOAD_PAUSED: "Please wait",
    SEEKER_TITLE_UPLOAD_STATUS: "Upload status",
    SEEKER_TITLE_UPLOAD_CREATE_ACCOUNT: "Create account",
    SEEKER_TITLE_UPLOAD_STATUS_UNAVAILABLE: "Status unavailable",
}

UPLOAD_SEEKER_MESSAGE_TEXT = {
    SEEKER_MESSAGE_UPLOAD_SAVED_READING: "Saved to Library. Reading in the background.",
    SEEKER_MESSAGE_UPLOAD_READY: "Ready in your Library.",
    SEEKER_MESSAGE_UPLOAD_NEEDS_OCR: "Saved to Library. Needs OCR.",
    SEEKER_MESSAGE_UPLOAD_FAILED: "Saved to Library, but processing failed. Please try again later.",
    SEEKER_MESSAGE_UPLOAD_STORAGE_FAILED: "Upload could not be saved. Please try again.",
    SEEKER_MESSAGE_UPLOAD_COOLDOWN: "Please wait a few seconds before uploading another scroll.",
    SEEKER_MESSAGE_UPLOAD_CAP: "Create an account to keep uploading and preserve your Library.",
    SEEKER_MESSAGE_UPLOAD_KEEP_LIBRARY: "Create an account to keep your Library.",
    SEEKER_MESSAGE_UPLOAD_KEEP_UPLOADING: "Create an account to keep uploading and preserve your Library.",
    SEEKER_MESSAGE_UPLOAD_STATUS_STILL_PROCESSING: "Still reading. Check your Library shortly.",
    SEEKER_MESSAGE_UPLOAD_STATUS_UNAVAILABLE: "Upload status could not be loaded. Refresh your Library shortly.",
    SEEKER_MESSAGE_UPLOAD_INVALID_FILE: "This file could not be read. Try a text PDF, TXT, DOCX, MD, or RTF.",
}


def get_upload_seeker_copy(title_key=None, message_key=None) -> dict:
    return {
        "seeker_title_key": title_key,
        "seeker_title": UPLOAD_SEEKER_TITLE_TEXT.get(title_key),
        "seeker_message_key": message_key,
        "seeker_message": UPLOAD_SEEKER_MESSAGE_TEXT.get(message_key),
    }


def build_upload_status_payload(
    *,
    ok=True,
    accepted=False,
    rejected=False,
    terminal=False,
    upload_state=None,
    library_state=None,
    seeker_title_key=None,
    seeker_message_key=None,
    admin_status=None,
    admin_message=None,
    retry_after_seconds=None,
    claim_required=False,
    claim_recommended=False,
    anonymous_uploads_remaining=None,
    upload_id=None,
    library_upload_id=None,
    ingestion_job_id=None,
    job_id=None,
    scroll_id=None,
    artifact_preserved=None,
    storage_backend=None,
    duplicate=False,
    dedupe_kind=None,
    needs_ocr=False,
    extra=None,
) -> dict:
    payload = {
        "ok": ok,
        "accepted": accepted,
        "rejected": rejected,
        "terminal": terminal,
        "upload_state": upload_state,
        "library_state": library_state,
        "admin_status": admin_status,
        "admin_message": admin_message,
        "retry_after_seconds": retry_after_seconds,
        "claim_required": claim_required,
        "claim_recommended": claim_recommended,
        "anonymous_uploads_remaining": anonymous_uploads_remaining,
        "upload_id": upload_id,
        "library_upload_id": library_upload_id,
        "ingestion_job_id": ingestion_job_id,
        "job_id": job_id,
        "scroll_id": scroll_id,
        "artifact_preserved": artifact_preserved,
        "storage_backend": storage_backend,
        "duplicate": duplicate,
        "dedupe_kind": dedupe_kind,
        "needs_ocr": needs_ocr,
    }
    payload.update(get_upload_seeker_copy(seeker_title_key, seeker_message_key))

    for key, value in (extra or {}).items():
        if key not in payload:
            payload[key] = value

    return payload


LIBRARY_UPLOAD_SEEKER_STATUSES = {
    "received",
    "saved",
    "queued",
    "reading",
    "ready",
    "needs_ocr",
    "failed",
    "already_saved",
    "indexing_deferred",
}

LIBRARY_UPLOAD_DEDUPE_KINDS = {
    "none",
    "exact_byte",
    "content_hash",
    "canonical_match",
    "legacy_duplicate_not_preserved",
}


def normalize_library_upload_seeker_status(status: Optional[str]) -> str:
    status_key = (status or "received").strip().lower()
    if status_key in LIBRARY_UPLOAD_SEEKER_STATUSES:
        return status_key
    return "received"


def normalize_library_upload_dedupe_kind(dedupe_kind: Optional[str]) -> Optional[str]:
    kind_key = (dedupe_kind or "").strip().lower()
    if not kind_key:
        return None
    if kind_key in LIBRARY_UPLOAD_DEDUPE_KINDS:
        return kind_key
    return "none"


def library_upload_storage_backend(storage_ref: Optional[str], storage_backend: Optional[str] = None) -> Optional[str]:
    backend = (storage_backend or "").strip().lower()
    if backend:
        return backend

    ref = (storage_ref or "").strip()
    if not ref:
        return None

    if ref.startswith("r2://"):
        return "r2"

    return "local"


def _library_upload_json_payload(value: Optional[dict]) -> str:
    return json.dumps(value or {}, default=str)


def _library_upload_isoformat_or_none(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def create_library_upload(
    *,
    session_id: Optional[str] = None,
    anonymous_user_id: Optional[str] = None,
    user_id: Optional[str] = None,
    ingestion_job_id: Optional[str] = None,
    scroll_id: Optional[str] = None,
    original_filename: Optional[str] = None,
    mime_type: Optional[str] = None,
    file_size_bytes: Optional[int] = None,
    storage_ref: Optional[str] = None,
    storage_backend: Optional[str] = None,
    file_sha256: Optional[str] = None,
    content_hash: Optional[str] = None,
    seeker_status: str = "received",
    admin_status: Optional[str] = None,
    dedupe_kind: Optional[str] = None,
    metadata_json: Optional[dict] = None,
) -> Optional[str]:
    """
    Create a seeker-visible Library artifact row.

    This helper does not replace scrolls or ingestion_jobs:
    - library_uploads tracks the user's uploaded artifact.
    - scrolls remains the deduped retrieval/corpus record.
    - ingestion_jobs remains the background processing record.
    """
    filename = (original_filename or "").strip() or "uploaded_scroll"
    status_key = normalize_library_upload_seeker_status(seeker_status)
    dedupe_key = normalize_library_upload_dedupe_kind(dedupe_kind)
    backend_key = library_upload_storage_backend(storage_ref, storage_backend)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO library_uploads (
                    session_id,
                    anonymous_user_id,
                    user_id,
                    ingestion_job_id,
                    scroll_id,
                    original_filename,
                    mime_type,
                    file_size_bytes,
                    storage_ref,
                    storage_backend,
                    file_sha256,
                    content_hash,
                    seeker_status,
                    admin_status,
                    dedupe_kind,
                    metadata_json
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s::jsonb
                )
                RETURNING id;
                """,
                (
                    session_id,
                    anonymous_user_id,
                    user_id,
                    ingestion_job_id,
                    scroll_id,
                    filename,
                    mime_type,
                    file_size_bytes,
                    storage_ref,
                    backend_key,
                    file_sha256,
                    content_hash,
                    status_key,
                    admin_status,
                    dedupe_key,
                    _library_upload_json_payload(metadata_json),
                ),
            )
            row = cur.fetchone()

        conn.commit()
        upload_id = str(row["id"]) if row and row.get("id") else None
        logger.info(
            "LIBRARY_UPLOAD_CREATED upload_id=%s job_id=%s scroll_id=%s user_id_present=%s anonymous_user_id_present=%s session_id_present=%s status=%s storage_backend=%s",
            upload_id,
            ingestion_job_id,
            scroll_id,
            bool(user_id),
            bool(anonymous_user_id),
            bool(session_id),
            status_key,
            backend_key,
        )
        return upload_id

    except Exception as exc:
        conn.rollback()
        logger.error("LIBRARY_UPLOAD_CREATE_FAILED error=%s", exc)
        raise

    finally:
        conn.close()


def update_library_upload(
    library_upload_id: str,
    *,
    ingestion_job_id: Optional[str] = None,
    scroll_id: Optional[str] = None,
    anonymous_user_id: Optional[str] = None,
    user_id: Optional[str] = None,
    seeker_status: Optional[str] = None,
    admin_status: Optional[str] = None,
    dedupe_kind: Optional[str] = None,
    storage_ref: Optional[str] = None,
    storage_backend: Optional[str] = None,
    file_sha256: Optional[str] = None,
    content_hash: Optional[str] = None,
    metadata_json: Optional[dict] = None,
) -> Optional[dict]:
    """
    Update a Library artifact row.

    Only non-None fields are updated. This keeps the helper safe for incremental
    ingestion stages where not every value is known yet.
    """
    if not library_upload_id:
        raise ValueError("library_upload_id is required")

    set_clauses = ["updated_at = NOW()"]
    params = []

    if ingestion_job_id is not None:
        set_clauses.append("ingestion_job_id = %s")
        params.append(ingestion_job_id)

    if scroll_id is not None:
        set_clauses.append("scroll_id = %s")
        params.append(scroll_id)

    if anonymous_user_id is not None:
        set_clauses.append("anonymous_user_id = %s")
        params.append(anonymous_user_id)

    if user_id is not None:
        set_clauses.append("user_id = %s")
        params.append(user_id)

    if seeker_status is not None:
        set_clauses.append("seeker_status = %s")
        params.append(normalize_library_upload_seeker_status(seeker_status))

    if admin_status is not None:
        set_clauses.append("admin_status = %s")
        params.append(admin_status)

    if dedupe_kind is not None:
        set_clauses.append("dedupe_kind = %s")
        params.append(normalize_library_upload_dedupe_kind(dedupe_kind))

    if storage_ref is not None:
        set_clauses.append("storage_ref = %s")
        params.append(storage_ref)

    if storage_backend is not None or storage_ref is not None:
        set_clauses.append("storage_backend = %s")
        params.append(library_upload_storage_backend(storage_ref, storage_backend))

    if file_sha256 is not None:
        set_clauses.append("file_sha256 = %s")
        params.append(file_sha256)

    if content_hash is not None:
        set_clauses.append("content_hash = %s")
        params.append(content_hash)

    if metadata_json is not None:
        set_clauses.append("metadata_json = COALESCE(metadata_json, '{}'::jsonb) || %s::jsonb")
        params.append(_library_upload_json_payload(metadata_json))

    params.append(library_upload_id)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE library_uploads
                SET {", ".join(set_clauses)}
                WHERE id = %s
                RETURNING *;
                """,
                tuple(params),
            )
            row = cur.fetchone()

        conn.commit()
        if row:
            logger.info(
                "LIBRARY_UPLOAD_UPDATED upload_id=%s status=%s job_id=%s scroll_id=%s",
                library_upload_id,
                row.get("seeker_status"),
                row.get("ingestion_job_id"),
                row.get("scroll_id"),
            )
        else:
            logger.warning("LIBRARY_UPLOAD_UPDATE_MISSING upload_id=%s", library_upload_id)

        return row

    except Exception as exc:
        conn.rollback()
        logger.error("LIBRARY_UPLOAD_UPDATE_FAILED upload_id=%s error=%s", library_upload_id, exc)
        raise

    finally:
        conn.close()


def get_library_upload(library_upload_id: str) -> Optional[dict]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM library_uploads
                WHERE id = %s
                LIMIT 1;
                """,
                (library_upload_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_library_upload_for_ingestion_job(ingestion_job_id: str) -> Optional[dict]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM library_uploads
                WHERE ingestion_job_id = %s
                ORDER BY created_at DESC
                LIMIT 1;
                """,
                (ingestion_job_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def update_library_upload_for_ingestion_job(
    ingestion_job_id: str,
    *,
    scroll_id: Optional[str] = None,
    seeker_status: Optional[str] = None,
    admin_status: Optional[str] = None,
    dedupe_kind: Optional[str] = None,
    storage_ref: Optional[str] = None,
    storage_backend: Optional[str] = None,
    content_hash: Optional[str] = None,
    metadata_json: Optional[dict] = None,
) -> Optional[dict]:
    """
    Best-effort update for the seeker-visible Library artifact linked to an
    ingestion job.

    This must never break ingestion. If a Library row does not exist, or if an
    older environment has not applied the Library migration yet, log and
    continue.
    """
    if not ingestion_job_id:
        return None

    try:
        upload = get_library_upload_for_ingestion_job(ingestion_job_id)
        if not upload:
            logger.info("LIBRARY_UPLOAD_NOT_FOUND_FOR_JOB job_id=%s", ingestion_job_id)
            return None

        return update_library_upload(
            str(upload["id"]),
            scroll_id=scroll_id,
            seeker_status=seeker_status,
            admin_status=admin_status,
            dedupe_kind=dedupe_kind,
            storage_ref=storage_ref,
            storage_backend=storage_backend,
            content_hash=content_hash,
            metadata_json=metadata_json,
        )

    except Exception as exc:
        logger.warning(
            "LIBRARY_UPLOAD_UPDATE_FOR_JOB_FAILED job_id=%s error=%s",
            ingestion_job_id,
            exc,
        )
        return None


def serialize_library_upload_for_seeker(upload: dict) -> dict:
    """
    Return a seeker-safe Library upload payload.

    Do not expose storage_ref, bucket names, internal R2 paths, or admin-only
    dedupe metadata here.
    """
    status = normalize_library_upload_seeker_status(upload.get("seeker_status"))
    filename = upload.get("original_filename") or "uploaded_scroll"

    return {
        "upload_id": str(upload.get("id")) if upload.get("id") else None,
        "job_id": str(upload.get("ingestion_job_id")) if upload.get("ingestion_job_id") else None,
        "scroll_id": str(upload.get("scroll_id")) if upload.get("scroll_id") else None,
        "filename": filename,
        "original_filename": filename,
        "seeker_status": status,
        "status": status,
        "ready": status == "ready",
        "reading": status in {"received", "saved", "queued", "reading", "indexing_deferred"},
        "needs_ocr": status == "needs_ocr",
        "failed": status == "failed",
        "already_saved": status == "already_saved",
        "indexing_deferred": status == "indexing_deferred",
        "can_open_original": bool(upload.get("storage_ref")),
        "can_ask_oracle": bool(upload.get("scroll_id")) and status in {"ready", "indexing_deferred"},
        "created_at": _library_upload_isoformat_or_none(upload.get("created_at")),
        "updated_at": _library_upload_isoformat_or_none(upload.get("updated_at")),
    }

def create_ingestion_job(
    *,
    scroll_id: Optional[str] = None,
    session_id: Optional[str] = None,
    anonymous_user_id: Optional[str] = None,
    user_id: Optional[str] = None,
    job_type: str = "scroll_upload",
    status: str = "queued",
    original_filename: Optional[str] = None,
    storage_ref: Optional[str] = None,
    mime_type: Optional[str] = None,
    corpus_layer: Optional[str] = None,
) -> Optional[str]:
    """
    Create a durable ingestion job record.

    session_id is Oracle conversation identity.
    anonymous_user_id is persistent browser/device identity.
    user_id is authenticated seeker identity.
    """
    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ingestion_jobs (
                    scroll_id,
                    session_id,
                    anonymous_user_id,
                    user_id,
                    job_type,
                    status,
                    original_filename,
                    storage_ref,
                    mime_type,
                    corpus_layer
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id;
                """,
                (
                    scroll_id,
                    session_id,
                    anonymous_user_id,
                    user_id,
                    job_type,
                    status,
                    original_filename,
                    storage_ref,
                    mime_type,
                    corpus_layer,
                )
            )

            row = cur.fetchone()

        conn.commit()

        job_id = (
            str(row["id"])
            if row and row.get("id")
            else None
        )

        logger.info(
            "INGESTION_JOB_CREATED job_id=%s scroll_id=%s "
            "user_id_present=%s anonymous_user_id_present=%s "
            "session_id_present=%s status=%s storage_ref_present=%s",
            job_id,
            scroll_id,
            bool(user_id),
            bool(anonymous_user_id),
            bool(session_id),
            status,
            bool(storage_ref),
        )

        return job_id

    except Exception as e:
        conn.rollback()
        logger.error(
            "INGESTION_JOB_CREATE_FAILED error=%s",
            e,
        )
        raise

    finally:
        conn.close()

def update_ingestion_job_status(
    job_id: str,
    status: str,
    *,
    scroll_id: Optional[str] = None,
    error_message: Optional[str] = None,
    increment_attempts: bool = False,
    result_json: Optional[dict] = None,
) -> Optional[dict]:
    """
    Update ingestion job status and timestamps.

    Supported status values are deliberately open text for now so the migration
    remains lightweight. The intended lifecycle is queued, processing, ready,
    failed, and needs_ocr.
    """
    status_key = (status or "").strip().lower()
    if not status_key:
        raise ValueError("status is required")

    started_at_sql = "started_at = COALESCE(started_at, NOW())," if status_key == "processing" else ""
    finished_at_sql = "finished_at = COALESCE(finished_at, NOW())," if status_key in {"ready", "failed", "needs_ocr"} else ""
    attempts_sql = "attempts = attempts + 1," if increment_attempts else ""

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE ingestion_jobs
                SET
                    status = %s,
                    scroll_id = COALESCE(%s, scroll_id),
                    error_message = %s,
                    result_json = COALESCE(%s::jsonb, result_json),
                    {started_at_sql}
                    {finished_at_sql}
                    {attempts_sql}
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *;
                """,
                (
                    status_key,
                    scroll_id,
                    error_message,
                    _safe_json_payload(result_json) if result_json is not None else None,
                    job_id,
                )
            )
            row = cur.fetchone()

        conn.commit()
        if row:
            logger.info(
                "INGESTION_JOB_UPDATED job_id=%s status=%s scroll_id=%s attempts=%s",
                job_id,
                row.get("status"),
                row.get("scroll_id"),
                row.get("attempts"),
            )
        else:
            logger.warning("INGESTION_JOB_UPDATE_MISSING job_id=%s status=%s", job_id, status_key)

        return row

    except Exception as e:
        conn.rollback()
        logger.error("INGESTION_JOB_UPDATE_FAILED job_id=%s status=%s error=%s", job_id, status_key, e)
        raise

    finally:
        conn.close()


def get_ingestion_job(job_id: str) -> Optional[dict]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM ingestion_jobs
                WHERE id = %s
                LIMIT 1;
                """,
                (job_id,)
            )
            return cur.fetchone()
    finally:
        conn.close()



def build_ingestion_job_result_payload(
    status: str,
    *,
    original_filename: Optional[str] = None,
    scroll_id: Optional[str] = None,
    result_payload: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> dict:
    """
    Build a safe final result payload for queued upload status polling.
    No private scroll text or storage credentials are included.
    """
    status_key = (status or "").strip().lower()
    payload = dict(result_payload or {})

    payload["status"] = status_key
    if original_filename:
        payload.setdefault("original_filename", original_filename)
    if scroll_id:
        payload.setdefault("scroll_id", str(scroll_id))
    if error_message:
        payload.setdefault("error", str(error_message))
        payload.setdefault("admin_message", str(error_message))

    if status_key == "ready":
        payload["ready"] = True
        payload.setdefault("seeker_status", "ready")
        payload.setdefault("seeker_title_key", SEEKER_TITLE_UPLOAD_READY)
        payload.setdefault("seeker_message_key", SEEKER_MESSAGE_UPLOAD_READY)
        if payload.get("duplicate"):
            payload.setdefault("dedupe_kind", UPLOAD_DEDUPE_KIND_CONTENT_HASH)
            payload.setdefault("admin_status", UPLOAD_ADMIN_STATUS_CONTENT_HASH_DUPLICATE)
            payload.setdefault(
                "admin_message",
                "Content hash matched an existing scroll; corpus was not expanded.",
            )
        else:
            payload.setdefault("dedupe_kind", UPLOAD_DEDUPE_KIND_NONE)
            payload.setdefault("admin_status", UPLOAD_ADMIN_STATUS_READY)

        payload["message"] = UPLOAD_SEEKER_MESSAGE_TEXT[SEEKER_MESSAGE_UPLOAD_READY]
        payload.setdefault("seeker_message", payload["message"])

    elif status_key == "needs_ocr":
        payload["needs_ocr"] = True
        payload.setdefault("seeker_status", "needs_ocr")
        payload.setdefault("admin_status", UPLOAD_ADMIN_STATUS_NEEDS_OCR)
        payload.setdefault("seeker_title_key", SEEKER_TITLE_UPLOAD_NEEDS_OCR)
        payload.setdefault("seeker_message_key", SEEKER_MESSAGE_UPLOAD_NEEDS_OCR)

        payload["message"] = UPLOAD_SEEKER_MESSAGE_TEXT[SEEKER_MESSAGE_UPLOAD_NEEDS_OCR]
        payload.setdefault("seeker_message", payload["message"])

    elif status_key == "failed":
        payload["failed"] = True
        payload.setdefault("seeker_status", "failed")
        payload.setdefault("admin_status", UPLOAD_ADMIN_STATUS_INGESTION_FAILED)
        payload.setdefault("seeker_title_key", SEEKER_TITLE_UPLOAD_FAILED)
        payload.setdefault("seeker_message_key", SEEKER_MESSAGE_UPLOAD_FAILED)

        payload["message"] = UPLOAD_SEEKER_MESSAGE_TEXT[SEEKER_MESSAGE_UPLOAD_FAILED]
        payload.setdefault("seeker_message", payload["message"])

    elif status_key in {"queued", "processing"}:
        payload.setdefault("seeker_title_key", SEEKER_TITLE_UPLOAD_STATUS)
        payload.setdefault("seeker_message_key", SEEKER_MESSAGE_UPLOAD_STATUS_STILL_PROCESSING)

        payload["message"] = UPLOAD_SEEKER_MESSAGE_TEXT[SEEKER_MESSAGE_UPLOAD_STATUS_STILL_PROCESSING]
        payload.setdefault("seeker_message", payload["message"])

    else:
        payload.setdefault("admin_status", UPLOAD_ADMIN_STATUS_STATUS_UNAVAILABLE)
        payload.setdefault("seeker_title_key", SEEKER_TITLE_UPLOAD_STATUS_UNAVAILABLE)
        payload.setdefault("seeker_message_key", SEEKER_MESSAGE_UPLOAD_STATUS_UNAVAILABLE)

        payload["message"] = UPLOAD_SEEKER_MESSAGE_TEXT[SEEKER_MESSAGE_UPLOAD_STATUS_UNAVAILABLE]
        payload.setdefault("seeker_message", payload["message"])

    return payload


def _safe_ingestion_result_json(value) -> dict:
    if isinstance(value, dict):
        return value

    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return {}


def _isoformat_or_none(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def request_can_view_ingestion_job(
    request: Request,
    job: dict,
) -> bool:
    current_user = get_current_user(request)

    if current_user and current_user.get("role") == "admin":
        return True

    job_user_id = _canonical_identity_uuid(
        job.get("user_id")
    )

    if job_user_id:
        current_user_id = _canonical_identity_uuid(
            current_user.get("user_id")
            if current_user
            else None
        )

        return current_user_id == job_user_id

    request_anonymous_id = _canonical_identity_uuid(
        get_browser_token_from_request(request)
    )
    job_anonymous_id = _canonical_identity_uuid(
        job.get("anonymous_user_id")
    )

    return bool(
        request_anonymous_id
        and job_anonymous_id
        and request_anonymous_id == job_anonymous_id
    )

def serialize_ingestion_job_status(job: dict) -> dict:
    result_json = _safe_ingestion_result_json(job.get("result_json"))
    status = (job.get("status") or "").strip().lower()
    error_message = job.get("error_message")
    job_id_text = str(job.get("id")) if job.get("id") else None

    library_upload = None
    if job_id_text:
        try:
            library_upload = get_library_upload_for_ingestion_job(job_id_text)
        except Exception as exc:
            logger.warning(
                "LIBRARY_UPLOAD_LOOKUP_FOR_JOB_STATUS_FAILED job_id=%s error=%s",
                job_id_text,
                exc,
            )
            library_upload = None

    scroll_id_text = str(job.get("scroll_id")) if job.get("scroll_id") else result_json.get("scroll_id")

    result_upload_id = result_json.get("upload_id") or result_json.get("library_upload_id")
    library_upload_id_text = (
        str(library_upload.get("id"))
        if library_upload and library_upload.get("id")
        else (str(result_upload_id) if result_upload_id else None)
    )

    storage_ref = job.get("storage_ref") or (
        library_upload.get("storage_ref") if library_upload else None
    )
    storage_backend = (
        library_upload.get("storage_backend")
        if library_upload and library_upload.get("storage_backend")
        else ("r2" if isinstance(storage_ref, str) and storage_ref.startswith("r2://") else ("local" if storage_ref else None))
    )

    duplicate = bool(result_json.get("duplicate"))
    dedupe_kind = (
        result_json.get("dedupe_kind")
        or (library_upload.get("dedupe_kind") if library_upload else None)
        or (UPLOAD_DEDUPE_KIND_CONTENT_HASH if duplicate else UPLOAD_DEDUPE_KIND_NONE)
    )

    message = result_json.get("message")
    if not message:
        message = build_ingestion_job_result_payload(
            status,
            original_filename=job.get("original_filename"),
            scroll_id=scroll_id_text,
            error_message=error_message,
        ).get("message")

    upload_state_by_status = {
        "queued": UPLOAD_STATE_QUEUED,
        "processing": UPLOAD_STATE_PROCESSING,
        "ready": UPLOAD_STATE_READY,
        "needs_ocr": UPLOAD_STATE_NEEDS_OCR,
        "failed": UPLOAD_STATE_FAILED,
    }
    library_state_by_status = {
        "queued": LIBRARY_STATE_QUEUED,
        "processing": LIBRARY_STATE_READING,
        "ready": LIBRARY_STATE_READY,
        "needs_ocr": LIBRARY_STATE_NEEDS_OCR,
        "failed": LIBRARY_STATE_FAILED,
    }
    seeker_title_by_status = {
        "queued": SEEKER_TITLE_UPLOAD_STATUS,
        "processing": SEEKER_TITLE_UPLOAD_STATUS,
        "ready": SEEKER_TITLE_UPLOAD_READY,
        "needs_ocr": SEEKER_TITLE_UPLOAD_NEEDS_OCR,
        "failed": SEEKER_TITLE_UPLOAD_FAILED,
    }
    seeker_message_by_status = {
        "queued": SEEKER_MESSAGE_UPLOAD_STATUS_STILL_PROCESSING,
        "processing": SEEKER_MESSAGE_UPLOAD_STATUS_STILL_PROCESSING,
        "ready": SEEKER_MESSAGE_UPLOAD_READY,
        "needs_ocr": SEEKER_MESSAGE_UPLOAD_NEEDS_OCR,
        "failed": SEEKER_MESSAGE_UPLOAD_FAILED,
    }
    admin_status_by_status = {
        "queued": UPLOAD_ADMIN_STATUS_QUEUED_UPLOAD_RECEIVED,
        "processing": UPLOAD_ADMIN_STATUS_PROCESSING,
        "ready": UPLOAD_ADMIN_STATUS_READY,
        "needs_ocr": UPLOAD_ADMIN_STATUS_NEEDS_OCR,
        "failed": UPLOAD_ADMIN_STATUS_INGESTION_FAILED,
    }

    extra = {
        "status": status,
        "queued": status == "queued",
        "processing": status == "processing",
        "ready": status == "ready",
        "failed": status == "failed",
        "message": message,
        "original_filename": job.get("original_filename"),
        "created_at": _isoformat_or_none(job.get("created_at")),
        "started_at": _isoformat_or_none(job.get("started_at")),
        "finished_at": _isoformat_or_none(job.get("finished_at")),
    }

    # Preserve safe result hints for the frontend without exposing storage refs or text.
    for key in ["upload_count_for_browser", "continuity_nudges", "claim_recommended", "anonymous_upload_limit"]:
        if key in result_json:
            extra[key] = result_json[key]

    return build_upload_status_payload(
        ok=True,
        accepted=True,
        rejected=False,
        terminal=status in {"ready", "needs_ocr", "failed"},
        upload_state=upload_state_by_status.get(status, UPLOAD_STATE_STATUS_UNAVAILABLE),
        library_state=library_state_by_status.get(status, LIBRARY_STATE_UNKNOWN),
        seeker_title_key=seeker_title_by_status.get(status, SEEKER_TITLE_UPLOAD_STATUS_UNAVAILABLE),
        seeker_message_key=seeker_message_by_status.get(status, SEEKER_MESSAGE_UPLOAD_STATUS_UNAVAILABLE),
        admin_status=(
            result_json.get("admin_status")
            or (library_upload.get("admin_status") if library_upload else None)
            or admin_status_by_status.get(status, UPLOAD_ADMIN_STATUS_STATUS_UNAVAILABLE)
        ),
        admin_message=result_json.get("admin_message") or error_message,
        claim_required=False,
        claim_recommended=bool(result_json.get("claim_recommended")),
        anonymous_uploads_remaining=result_json.get("anonymous_uploads_remaining"),
        upload_id=library_upload_id_text,
        library_upload_id=library_upload_id_text,
        ingestion_job_id=job_id_text,
        job_id=job_id_text,
        scroll_id=scroll_id_text,
        artifact_preserved=bool(storage_ref),
        storage_backend=storage_backend,
        duplicate=duplicate,
        dedupe_kind=dedupe_kind,
        needs_ocr=status == "needs_ocr" or bool(result_json.get("needs_ocr")),
        extra=extra,
    )


@app.get("/ingestion/jobs/{job_id}")
def ingestion_job_status(request: Request, job_id: str):
    try:
        uuid.UUID(str(job_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Ingestion job not found")

    job = get_ingestion_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")

    if not request_can_view_ingestion_job(request, job):
        raise HTTPException(status_code=403, detail="Not authorized to view this ingestion job")

    return serialize_ingestion_job_status(job)


def get_next_queued_ingestion_job(job_type: str = "scroll_upload") -> Optional[dict]:
    """
    Claim the next queued ingestion job for one worker process.

    This uses SKIP LOCKED so future workers can safely run in parallel.
    It is not wired into a worker loop yet.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH next_job AS (
                    SELECT id
                    FROM ingestion_jobs
                    WHERE job_type = %s
                      AND status = 'queued'
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE ingestion_jobs j
                SET
                    status = 'processing',
                    started_at = COALESCE(started_at, NOW()),
                    attempts = attempts + 1,
                    updated_at = NOW()
                FROM next_job
                WHERE j.id = next_job.id
                RETURNING j.*;
                """,
                (job_type,)
            )
            row = cur.fetchone()

        conn.commit()
        if row:
            logger.info(
                "INGESTION_JOB_CLAIMED job_id=%s job_type=%s attempts=%s",
                row.get("id"),
                row.get("job_type"),
                row.get("attempts"),
            )
        return row

    except Exception as e:
        conn.rollback()
        logger.error("INGESTION_JOB_CLAIM_FAILED job_type=%s error=%s", job_type, e)
        raise

    finally:
        conn.close()




def _safe_json_payload(value) -> str:
    """Return a JSON string safe for jsonb inserts."""
    if value is None:
        return "{}"
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except Exception:
            return json.dumps({"value": value})
    return json.dumps(value)


def expire_stale_pending_oracle_inferences() -> int:
    """
    Expire abandoned split-phase inference state and clear prepared payloads.

    This table is operational state only. It must not become a second
    long-term Oracle memory store.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oracle_pending_inferences
                SET
                    status = 'expired',
                    prepared_state = '{}'::jsonb
                WHERE status IN ('prepared', 'completing')
                  AND expires_at <= NOW();
                """
            )
            expired_count = cur.rowcount

        conn.commit()

        if expired_count:
            logger.info(
                "ORACLE_PENDING_INFERENCES_EXPIRED count=%s",
                expired_count,
            )

        return expired_count

    except Exception as exc:
        conn.rollback()
        logger.error(
            "ORACLE_PENDING_INFERENCE_EXPIRE_FAILED error=%s",
            exc,
        )
        raise

    finally:
        conn.close()


def create_pending_oracle_inference(
    *,
    session_id: str,
    user_id: Optional[str],
    deity: str,
    input_mode: str,
    prepared_state: dict,
) -> Optional[str]:
    """
    Create short-lived server-owned state for split-phase inference.
    """
    if not session_id:
        raise ValueError("session_id is required")

    deity_key = (deity or "").strip()
    if deity_key not in {"Hathor", "Moses"}:
        raise ValueError("deity must be Hathor or Moses")

    input_mode_key = (input_mode or "").strip().lower()
    if input_mode_key not in {"text", "voice"}:
        raise ValueError("input_mode must be text or voice")

    expire_stale_pending_oracle_inferences()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oracle_pending_inferences (
                    session_id,
                    user_id,
                    deity,
                    input_mode,
                    prepared_state
                )
                VALUES (%s, %s, %s, %s, %s::jsonb)
                RETURNING id;
                """,
                (
                    session_id,
                    user_id,
                    deity_key,
                    input_mode_key,
                    _safe_json_payload(prepared_state or {}),
                ),
            )
            row = cur.fetchone()

        conn.commit()

        pending_id = str(row["id"]) if row and row.get("id") else None

        logger.info(
            "ORACLE_PENDING_INFERENCE_CREATED pending_id=%s deity=%s input_mode=%s user_id_present=%s",
            pending_id,
            deity_key,
            input_mode_key,
            bool(user_id),
        )

        return pending_id

    except Exception as exc:
        conn.rollback()
        logger.error(
            "ORACLE_PENDING_INFERENCE_CREATE_FAILED error=%s",
            exc,
        )
        raise

    finally:
        conn.close()


IOS_PCC_POST_PREPARE_FALLBACK_CODES = {
    "pcc_execution_unavailable",
    "pcc_execution_failed",
    "pcc_empty_result",
}

IOS_PCC_FALLBACK_CODES = IOS_PCC_POST_PREPARE_FALLBACK_CODES | {
    "pcc_preflight_unavailable",
}

DEVICE_EXECUTION_PROFILES = {
    "apple_pcc": {
        "source_model": "PrivateCloudComputeLanguageModel",
        "model_provider": "apple",
        "model_name": "PrivateCloudComputeLanguageModel",
        "route_reason": "ios_pcc_split_phase",
    },
}


def abandon_pending_oracle_inference(
    interaction_id: str,
    *,
    session_id: str,
    user_id: Optional[str],
    fallback_code: str,
) -> Optional[dict]:
    """
    Explicitly abandon one prepared split-phase inference.

    Only a still-prepared turn may be abandoned. A completing or completed
    turn must not be changed because durable finalization may already be
    underway.
    """
    if not interaction_id:
        raise ValueError("interaction_id is required")
    if not session_id:
        raise ValueError("session_id is required")

    fallback_code = (fallback_code or "").strip()
    if fallback_code not in IOS_PCC_POST_PREPARE_FALLBACK_CODES:
        raise ValueError("invalid PCC fallback code")

    expire_stale_pending_oracle_inferences()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Lock the authoritative prepared turn so its server-owned
            # question can be fingerprinted before prepared content is
            # destroyed.
            cur.execute(
                """
                SELECT prepared_state
                FROM oracle_pending_inferences
                WHERE id = %s::uuid
                  AND session_id = %s::uuid
                  AND user_id IS NOT DISTINCT FROM %s::uuid
                  AND status = 'prepared'
                FOR UPDATE;
                """,
                (
                    interaction_id,
                    session_id,
                    user_id,
                ),
            )
            pending_row = cur.fetchone()

            if not pending_row:
                conn.rollback()
                return None

            prepared_state = pending_row.get("prepared_state") or {}
            if isinstance(prepared_state, str):
                prepared_state = json.loads(prepared_state)

            finalization_state = dict(
                prepared_state.get("finalization_state") or {}
            )
            prepared_question = finalization_state.get("question")

            question_sha256 = None
            if isinstance(prepared_question, str):
                question_sha256 = hashlib.sha256(
                    prepared_question.encode("utf-8")
                ).hexdigest()
            else:
                logger.warning(
                    "PCC_ABANDON_QUESTION_HASH_MISSING pending_id=%s",
                    interaction_id,
                )

            cur.execute(
                """
                UPDATE oracle_pending_inferences
                SET
                    status = 'expired',
                    expires_at = NOW(),
                    prepared_state = jsonb_build_object(
                        'abandoned', true,
                        'fallback_code', %s,
                        'question_sha256', %s
                    )
                WHERE id = %s::uuid
                  AND session_id = %s::uuid
                  AND user_id IS NOT DISTINCT FROM %s::uuid
                  AND status = 'prepared'
                RETURNING id, status;
                """,
                (
                    fallback_code,
                    question_sha256,
                    interaction_id,
                    session_id,
                    user_id,
                ),
            )
            row = cur.fetchone()

        conn.commit()

        if row:
            logger.info(
                "ORACLE_PENDING_INFERENCE_ABANDONED pending_id=%s",
                interaction_id,
            )

        return row

    except Exception as exc:
        conn.rollback()
        logger.error(
            "ORACLE_PENDING_INFERENCE_ABANDON_FAILED "
            "pending_id=%s error=%s",
            interaction_id,
            exc,
        )
        raise

    finally:
        conn.close()



def get_verified_pcc_abandonment(
    interaction_id: str,
    *,
    session_id: str,
    user_id: Optional[str],
    deity: str,
    fallback_code: str,
    question_sha256: str,
) -> Optional[dict]:
    """
    Verify that a PCC fallback refers to an explicitly abandoned
    server-owned pending inference for this same conversation identity.

    The retained pending-state marker contains no plaintext question,
    memory, prompt, or model output.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    prepared_state->>'fallback_code' AS fallback_code
                FROM oracle_pending_inferences
                WHERE id = %s::uuid
                  AND session_id = %s::uuid
                  AND user_id IS NOT DISTINCT FROM %s::uuid
                  AND deity = %s
                  AND input_mode = 'voice'
                  AND status = 'expired'
                  AND prepared_state->>'abandoned' = 'true'
                  AND prepared_state->>'fallback_code' = %s
                  AND prepared_state->>'question_sha256' = %s
                LIMIT 1
                """,
                (
                    interaction_id,
                    session_id,
                    user_id,
                    deity,
                    fallback_code,
                    question_sha256,
                ),
            )
            return cur.fetchone()
    except Exception as exc:
        logger.warning(
            "PCC_FALLBACK_VERIFICATION_FAILED pending_id=%s error=%s",
            interaction_id,
            exc,
        )
        return None
    finally:
        if conn:
            conn.close()

def claim_pending_oracle_inference(
    interaction_id: str,
    *,
    session_id: str,
    user_id: Optional[str],
) -> Optional[dict]:
    """
    Atomically claim one prepared inference for completion.

    Only the bound session/user may claim it. A claimed, completed, expired,
    or replayed interaction cannot be claimed again.
    """
    if not interaction_id:
        raise ValueError("interaction_id is required")
    if not session_id:
        raise ValueError("session_id is required")

    expire_stale_pending_oracle_inferences()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oracle_pending_inferences
                SET
                    status = 'completing',
                    expires_at = NOW() + INTERVAL '15 minutes'
                WHERE id = %s::uuid
                  AND session_id = %s::uuid
                  AND user_id IS NOT DISTINCT FROM %s::uuid
                  AND status = 'prepared'
                  AND expires_at > NOW()
                RETURNING *;
                """,
                (
                    interaction_id,
                    session_id,
                    user_id,
                ),
            )
            row = cur.fetchone()

        conn.commit()

        if row:
            logger.info(
                "ORACLE_PENDING_INFERENCE_CLAIMED pending_id=%s",
                interaction_id,
            )

        return row

    except Exception as exc:
        conn.rollback()
        logger.error(
            "ORACLE_PENDING_INFERENCE_CLAIM_FAILED pending_id=%s error=%s",
            interaction_id,
            exc,
        )
        raise

    finally:
        conn.close()


def complete_pending_oracle_inference(
    interaction_id: str,
    *,
    session_id: str,
    user_id: Optional[str],
) -> Optional[dict]:
    """
    Mark a successfully finalized inference complete.

    This also heals a stale expired pending row when durable finalization
    already succeeded before pending-state cleanup completed.

    Prepared state is erased on completion so this operational table does not
    retain Oracle context after durable finalization.
    """
    if not interaction_id:
        raise ValueError("interaction_id is required")
    if not session_id:
        raise ValueError("session_id is required")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oracle_pending_inferences
                SET
                    status = 'completed',
                    completed_at = NOW(),
                    prepared_state = '{}'::jsonb
                WHERE id = %s::uuid
                  AND session_id = %s::uuid
                  AND user_id IS NOT DISTINCT FROM %s::uuid
                  AND status IN ('completing', 'expired')
                RETURNING *;
                """,
                (
                    interaction_id,
                    session_id,
                    user_id,
                ),
            )
            row = cur.fetchone()

        conn.commit()

        if row:
            logger.info(
                "ORACLE_PENDING_INFERENCE_COMPLETED pending_id=%s",
                interaction_id,
            )

        return row

    except Exception as exc:
        conn.rollback()
        logger.error(
            "ORACLE_PENDING_INFERENCE_COMPLETE_FAILED pending_id=%s error=%s",
            interaction_id,
            exc,
        )
        raise

    finally:
        conn.close()


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a conservative boolean env flag."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_app_environment() -> str:
    """Return the current app environment for reports and alerts."""
    env = os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development"
    env = env.strip().lower()
    if env in {"prod", "production"}:
        return "production"
    if env in {"stage", "staging"}:
        return "staging"
    if env in {"dev", "development", "local"}:
        return "development"
    return env or "development"


def is_external_email_allowed() -> bool:
    """
    Conservative safety gate for future alert/report email delivery.

    This helper does not send email. It only answers whether external alert
    email would be allowed by environment and flags.
    """
    env = get_app_environment()
    if env != "production":
        return False

    if not _env_flag("ALERTS_ENABLED", default=False):
        return False

    if not _env_flag("ALERT_EMAILS_ENABLED", default=False):
        return False

    mode = (os.getenv("ALERT_EMAIL_MODE") or "muted").strip().lower()
    if mode in {"muted", "off", "disabled", "none"}:
        return False

    if not _env_flag("ALLOW_EXTERNAL_EMAILS", default=False):
        return False

    return True



def _split_email_list(raw: Optional[str]) -> list[str]:
    """Split comma/semicolon separated email list into clean entries."""
    if not raw:
        return []
    parts = re.split(r"[,;]", raw)
    return [part.strip() for part in parts if part.strip()]


def get_report_email_settings() -> dict:
    """
    Return report/alert email settings without sending anything.

    reports@godincorporated.ai is the planned sender/archive mailbox.
    """
    reports_from = os.getenv("REPORTS_FROM_EMAIL", "reports@godincorporated.ai").strip()
    alerts_from = os.getenv("ALERTS_FROM_EMAIL", reports_from).strip()
    admin_recipients = _split_email_list(os.getenv("ADMIN_ALERT_EMAILS", ""))

    return {
        "environment": get_app_environment(),
        "reports_from_email": reports_from,
        "alerts_from_email": alerts_from,
        "admin_alert_emails": admin_recipients,
        "alerts_enabled": _env_flag("ALERTS_ENABLED", default=False),
        "alert_emails_enabled": _env_flag("ALERT_EMAILS_ENABLED", default=False),
        "alert_email_mode": (os.getenv("ALERT_EMAIL_MODE") or "muted").strip().lower(),
        "allow_external_emails": _env_flag("ALLOW_EXTERNAL_EMAILS", default=False),
        "staging_notifications_muted": _env_flag("STAGING_NOTIFICATIONS_MUTED", default=True),
        "dev_notifications_muted": _env_flag("DEV_NOTIFICATIONS_MUTED", default=True),
        "external_email_allowed": is_external_email_allowed(),
    }


def get_notification_delivery_mode(
    severity: str = "INFO",
    channel: str = "email",
) -> str:
    """
    Decide notification handling without sending.

    Returns:
      muted  - record only, no external delivery
      digest - future digest queue path
      queued - future immediate send queue path
    """
    env = get_app_environment()
    channel = (channel or "email").strip().lower()
    severity = (severity or "INFO").strip().upper()

    if channel != "email":
        return "muted"

    if env == "staging" and _env_flag("STAGING_NOTIFICATIONS_MUTED", default=True):
        return "muted"

    if env in {"development", "local", "dev"} and _env_flag("DEV_NOTIFICATIONS_MUTED", default=True):
        return "muted"

    if not is_external_email_allowed():
        return "muted"

    mode = (os.getenv("ALERT_EMAIL_MODE") or "muted").strip().lower()

    if mode == "all":
        return "queued"

    if mode == "critical_only":
        return "queued" if severity == "CRITICAL" else "muted"

    if mode == "digest_only":
        return "digest"

    return "muted"


def record_alert_notification(
    alert_event: dict,
    channel: str = "email",
    recipient: Optional[str] = None,
    metadata_json: Optional[dict] = None,
) -> Optional[dict]:
    """
    Record notification delivery intent for an alert.

    This helper intentionally does not send email. In staging/dev it should
    record status=muted. In production with gates enabled, it may record
    status=queued for a future sender to process.
    """
    if not alert_event:
        return None

    alert_event_id = str(alert_event.get("id")) if alert_event.get("id") else None
    severity = alert_event.get("severity", "INFO")
    delivery_mode = get_notification_delivery_mode(severity=severity, channel=channel)

    status = {
        "queued": "queued",
        "digest": "queued_digest",
        "muted": "muted",
    }.get(delivery_mode, "muted")

    settings = get_report_email_settings()
    payload = {
        "delivery_mode": delivery_mode,
        "environment": settings.get("environment"),
        "severity": severity,
        "alert_key": alert_event.get("alert_key"),
        "fingerprint": alert_event.get("fingerprint"),
    }

    if metadata_json:
        payload.update(metadata_json)

    if recipient is None:
        recipients = settings.get("admin_alert_emails") or []
        recipient = recipients[0] if recipients else None

    return record_notification_delivery(
        alert_event_id=alert_event_id,
        channel=channel,
        recipient=recipient,
        status=status,
        metadata_json=payload,
    )


def record_muted_notification(
    alert_event_id: Optional[str],
    channel: str = "email",
    recipient: Optional[str] = None,
    reason: str = "notifications_muted",
    metadata_json: Optional[dict] = None,
) -> Optional[dict]:
    """Explicitly record a muted notification delivery."""
    payload = {
        "reason": reason,
        "environment": get_app_environment(),
    }
    if metadata_json:
        payload.update(metadata_json)

    return record_notification_delivery(
        alert_event_id=alert_event_id,
        channel=channel,
        recipient=recipient,
        status="muted",
        metadata_json=payload,
    )


def create_report_artifact(
    report_key: str,
    format: str,
    environment: Optional[str] = None,
    storage_ref: Optional[str] = None,
    sha256: Optional[str] = None,
    size_bytes: Optional[int] = None,
    summary_json: Optional[dict] = None,
) -> Optional[dict]:
    """Create a private report artifact record."""
    env = environment or get_app_environment()
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO report_artifacts (
                    report_key,
                    environment,
                    format,
                    storage_ref,
                    sha256,
                    size_bytes,
                    summary_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING *
                """,
                (
                    report_key,
                    env,
                    format,
                    storage_ref,
                    sha256,
                    size_bytes,
                    _safe_json_payload(summary_json),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        if conn:
            conn.rollback()
        logging.exception("CREATE_REPORT_ARTIFACT_FAILED report_key=%s env=%s", report_key, env)
        return None
    finally:
        if conn:
            conn.close()


def create_report_run(
    report_key: str,
    status: str = "queued",
    environment: Optional[str] = None,
    period_start=None,
    period_end=None,
    git_sha: Optional[str] = None,
    metadata_json: Optional[dict] = None,
) -> Optional[dict]:
    """Create a scheduled/manual report run record."""
    env = environment or get_app_environment()
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO report_runs (
                    report_key,
                    environment,
                    status,
                    period_start,
                    period_end,
                    git_sha,
                    metadata_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING *
                """,
                (
                    report_key,
                    env,
                    status,
                    period_start,
                    period_end,
                    git_sha,
                    _safe_json_payload(metadata_json),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        if conn:
            conn.rollback()
        logging.exception("CREATE_REPORT_RUN_FAILED report_key=%s env=%s", report_key, env)
        return None
    finally:
        if conn:
            conn.close()


def finish_report_run(
    report_run_id: str,
    status: str,
    artifact_id: Optional[str] = None,
    error_message: Optional[str] = None,
    metadata_json: Optional[dict] = None,
) -> Optional[dict]:
    """Mark a report run finished, failed, or otherwise completed."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE report_runs
                SET
                    status = %s,
                    artifact_id = COALESCE(%s, artifact_id),
                    error_message = %s,
                    metadata_json = COALESCE(%s::jsonb, metadata_json),
                    started_at = COALESCE(started_at, now()),
                    finished_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (
                    status,
                    artifact_id,
                    error_message,
                    _safe_json_payload(metadata_json) if metadata_json is not None else None,
                    report_run_id,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        if conn:
            conn.rollback()
        logging.exception("FINISH_REPORT_RUN_FAILED report_run_id=%s status=%s", report_run_id, status)
        return None
    finally:
        if conn:
            conn.close()


def create_alert_event(
    alert_key: str,
    fingerprint: str,
    severity: str,
    title: str,
    message: Optional[str] = None,
    environment: Optional[str] = None,
    metadata_json: Optional[dict] = None,
) -> Optional[dict]:
    """
    Create or update a deduped alert event.

    Existing matching alerts are reopened, last_seen_at is updated, and count is
    incremented. No notification is sent here.
    """
    env = environment or get_app_environment()
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO alert_events (
                    alert_key,
                    fingerprint,
                    environment,
                    severity,
                    status,
                    title,
                    message,
                    metadata_json
                )
                VALUES (%s, %s, %s, %s, 'open', %s, %s, %s::jsonb)
                ON CONFLICT (alert_key, fingerprint, environment)
                DO UPDATE SET
                    severity = EXCLUDED.severity,
                    status = 'open',
                    title = EXCLUDED.title,
                    message = EXCLUDED.message,
                    metadata_json = EXCLUDED.metadata_json,
                    last_seen_at = now(),
                    count = alert_events.count + 1,
                    resolved_at = NULL
                RETURNING *
                """,
                (
                    alert_key,
                    fingerprint,
                    env,
                    severity,
                    title,
                    message,
                    _safe_json_payload(metadata_json),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        if conn:
            conn.rollback()
        logging.exception("CREATE_ALERT_EVENT_FAILED alert_key=%s fingerprint=%s env=%s", alert_key, fingerprint, env)
        return None
    finally:
        if conn:
            conn.close()


def resolve_alert_event(
    alert_event_id: Optional[str] = None,
    alert_key: Optional[str] = None,
    fingerprint: Optional[str] = None,
    environment: Optional[str] = None,
    message: Optional[str] = None,
) -> Optional[dict]:
    """Resolve an alert event by id or by alert_key/fingerprint/environment."""
    env = environment or get_app_environment()
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if alert_event_id:
                cur.execute(
                    """
                    UPDATE alert_events
                    SET
                        status = 'resolved',
                        message = COALESCE(%s, message),
                        resolved_at = now(),
                        last_seen_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (message, alert_event_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE alert_events
                    SET
                        status = 'resolved',
                        message = COALESCE(%s, message),
                        resolved_at = now(),
                        last_seen_at = now()
                    WHERE alert_key = %s
                      AND fingerprint = %s
                      AND environment = %s
                    RETURNING *
                    """,
                    (message, alert_key, fingerprint, env),
                )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        if conn:
            conn.rollback()
        logging.exception("RESOLVE_ALERT_EVENT_FAILED alert_event_id=%s alert_key=%s fingerprint=%s env=%s", alert_event_id, alert_key, fingerprint, env)
        return None
    finally:
        if conn:
            conn.close()


def record_notification_delivery(
    alert_event_id: Optional[str],
    channel: str,
    status: str,
    recipient: Optional[str] = None,
    error_message: Optional[str] = None,
    metadata_json: Optional[dict] = None,
    sent_at=None,
) -> Optional[dict]:
    """
    Record notification delivery state.

    Valid first statuses include sent, failed, muted, skipped, and queued.
    This helper does not send email.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO notification_deliveries (
                    alert_event_id,
                    channel,
                    recipient,
                    status,
                    error_message,
                    sent_at,
                    metadata_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING *
                """,
                (
                    alert_event_id,
                    channel,
                    recipient,
                    status,
                    error_message,
                    sent_at,
                    _safe_json_payload(metadata_json),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        if conn:
            conn.rollback()
        logging.exception("RECORD_NOTIFICATION_DELIVERY_FAILED alert_event_id=%s channel=%s status=%s", alert_event_id, channel, status)
        return None
    finally:
        if conn:
            conn.close()




def normalize_ingestion_result_payload(result) -> dict:
    """
    Normalize ingestion helper results for queued processing.

    ingest_saved_scroll_file may return either a dict or a JSONResponse,
    especially on duplicate-scroll handling. Queue processing must treat both
    as valid outcomes instead of failing on result.get().
    """
    if isinstance(result, JSONResponse):
        try:
            raw_body = result.body.decode("utf-8") if isinstance(result.body, bytes) else result.body
            payload = json.loads(raw_body) if raw_body else {}
        except Exception:
            payload = {
                "message": "Ingestion returned a JSONResponse that could not be decoded.",
                "status_code": result.status_code,
            }

        payload["_response_status_code"] = result.status_code
        return payload

    if isinstance(result, dict):
        return result

    return {
        "message": "Ingestion returned an unexpected result type.",
        "result_type": type(result).__name__,
    }



def get_scroll_upload_auto_process_settings() -> dict:
    """
    Return bounded automatic queue processing settings for scroll uploads.

    This is intentionally disabled by default. When enabled, the upload response
    still returns quickly and FastAPI runs a small follow-up queue processor task.
    """
    enabled = _env_flag("SCROLL_UPLOAD_AUTO_PROCESS_ENABLED", default=False)

    try:
        max_jobs = int(os.getenv("SCROLL_UPLOAD_AUTO_PROCESS_MAX_JOBS", "1"))
    except (TypeError, ValueError):
        max_jobs = 1

    max_jobs = max(1, min(max_jobs, 10))

    return {
        "enabled": enabled,
        "max_jobs": max_jobs,
    }


def process_queued_scroll_ingestion_jobs(max_jobs: int = 1) -> dict:
    """
    Process a bounded number of queued scroll_upload ingestion jobs.

    This is safe for manual/admin use and for FastAPI BackgroundTasks because
    each underlying job is claimed with SKIP LOCKED.
    """
    try:
        max_jobs = int(max_jobs)
    except (TypeError, ValueError):
        max_jobs = 1

    max_jobs = max(1, min(max_jobs, 10))
    results = []
    processed_count = 0

    for _ in range(max_jobs):
        result = process_one_queued_scroll_ingestion_job()
        results.append(result)

        if not result.get("processed"):
            break

        processed_count += 1

    logger.info(
        "SCROLL_UPLOAD_AUTO_PROCESS_BATCH processed_count=%s max_jobs=%s",
        processed_count,
        max_jobs,
    )

    return {
        "ok": True,
        "processed_count": processed_count,
        "max_jobs": max_jobs,
        "results": results,
    }


def run_scroll_upload_auto_processor(max_jobs: int = 1) -> None:
    """
    Background task wrapper for bounded scroll ingestion processing.
    """
    try:
        process_queued_scroll_ingestion_jobs(max_jobs=max_jobs)
    except Exception:
        logger.exception("SCROLL_UPLOAD_AUTO_PROCESS_FAILED")



def get_scroll_storage_settings() -> dict:
    """
    Return durable scroll storage settings.

    Backend defaults to local to preserve the accepted production fallback path.
    R2 uses Cloudflare R2's S3-compatible API.
    """
    backend = (os.getenv("SCROLL_STORAGE_BACKEND") or "local").strip().lower()
    if backend not in {"local", "r2"}:
        backend = "local"

    prefix = (os.getenv("SCROLL_STORAGE_PREFIX") or "scrolls/original").strip().strip("/")

    return {
        "backend": backend,
        "bucket": (os.getenv("SCROLL_STORAGE_BUCKET") or "").strip(),
        "prefix": prefix,
        "endpoint_url": (os.getenv("SCROLL_STORAGE_ENDPOINT_URL") or "").strip(),
        "region": (os.getenv("SCROLL_STORAGE_REGION") or "auto").strip() or "auto",
        "access_key_present": bool(os.getenv("SCROLL_STORAGE_ACCESS_KEY_ID")),
        "secret_key_present": bool(os.getenv("SCROLL_STORAGE_SECRET_ACCESS_KEY")),
    }


def is_r2_storage_ref(storage_ref: Optional[str]) -> bool:
    return bool(storage_ref and str(storage_ref).startswith("r2://"))


def build_r2_storage_key(safe_name: str) -> str:
    settings = get_scroll_storage_settings()
    try:
        now = utc_now()
    except Exception:
        now = datetime.datetime.now(timezone.utc)

    parts = [
        settings["prefix"],
        f"{now.year:04d}",
        f"{now.month:02d}",
        safe_name,
    ]
    return "/".join(part.strip("/") for part in parts if part)


def get_r2_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for SCROLL_STORAGE_BACKEND=r2") from exc

    endpoint_url = os.getenv("SCROLL_STORAGE_ENDPOINT_URL")
    access_key = os.getenv("SCROLL_STORAGE_ACCESS_KEY_ID")
    secret_key = os.getenv("SCROLL_STORAGE_SECRET_ACCESS_KEY")
    region = os.getenv("SCROLL_STORAGE_REGION") or "auto"

    missing = [
        name
        for name, value in [
            ("SCROLL_STORAGE_ENDPOINT_URL", endpoint_url),
            ("SCROLL_STORAGE_ACCESS_KEY_ID", access_key),
            ("SCROLL_STORAGE_SECRET_ACCESS_KEY", secret_key),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing R2 storage settings: {', '.join(missing)}")

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )


def parse_r2_storage_ref(storage_ref: str) -> tuple[str, str]:
    if not is_r2_storage_ref(storage_ref):
        raise ValueError("storage_ref is not an R2 reference")

    remainder = storage_ref[len("r2://"):]
    bucket, sep, key = remainder.partition("/")
    if not bucket or not sep or not key:
        raise ValueError("Invalid R2 storage_ref format")

    return bucket, key


def save_scroll_upload_to_storage(
    file_path: str,
    safe_name: str,
    *,
    original_filename: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> str:
    """
    Save an uploaded scroll original to the configured storage backend.

    Local backend returns the safe filename. R2 backend uploads the file and
    returns an r2://bucket/key reference for DB/job storage_ref.
    """
    settings = get_scroll_storage_settings()
    if settings["backend"] == "local":
        return safe_name

    bucket = settings["bucket"]
    if not bucket:
        raise RuntimeError("SCROLL_STORAGE_BUCKET is required for R2 storage")

    key = build_r2_storage_key(safe_name)
    extra_args = {}
    if mime_type:
        extra_args["ContentType"] = mime_type
    if original_filename:
        extra_args["Metadata"] = {
            "original-filename": original_filename[:1024],
        }

    client = get_r2_client()
    client.upload_file(
        file_path,
        bucket,
        key,
        ExtraArgs=extra_args or None,
    )

    storage_ref = f"r2://{bucket}/{key}"
    logger.info(
        "SCROLL_STORAGE_SAVED backend=r2 bucket=%s key=%s filename=%s",
        bucket,
        key,
        original_filename or safe_name,
    )
    return storage_ref


def materialize_scroll_storage_ref(storage_ref: str) -> dict:
    """
    Return a local readable file path for a storage_ref.

    Local refs point inside UPLOAD_DIR. R2 refs are downloaded to a temporary
    materialization directory and should be cleaned after processing.
    """
    if not is_r2_storage_ref(storage_ref):
        return {
            "backend": "local",
            "file_path": os.path.join(UPLOAD_DIR, storage_ref),
            "temporary": False,
        }

    bucket, key = parse_r2_storage_ref(storage_ref)
    ext = os.path.splitext(key)[1]
    materialized_dir = os.path.join(UPLOAD_DIR, "_materialized")
    os.makedirs(materialized_dir, exist_ok=True)

    local_name = f"{uuid.uuid4()}{ext}"
    local_path = os.path.join(materialized_dir, local_name)

    client = get_r2_client()
    client.download_file(bucket, key, local_path)

    logger.info(
        "SCROLL_STORAGE_MATERIALIZED backend=r2 bucket=%s key=%s",
        bucket,
        key,
    )
    return {
        "backend": "r2",
        "file_path": local_path,
        "temporary": True,
    }


def cleanup_materialized_scroll_file(file_path: Optional[str], temporary: bool) -> None:
    if temporary and file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            logger.warning("SCROLL_STORAGE_TEMP_CLEANUP_FAILED path=%s", file_path)


def delete_scroll_storage_ref(storage_ref: Optional[str]) -> None:
    """
    Delete an uploaded original from durable storage only for explicit cleanup.

    Library-preserved queued uploads should not call this just because content
    matches an existing scroll. Corpus dedupe and artifact preservation are
    separate concerns.
    """
    if not is_r2_storage_ref(storage_ref):
        return

    try:
        bucket, key = parse_r2_storage_ref(storage_ref)
        get_r2_client().delete_object(Bucket=bucket, Key=key)
        logger.info("SCROLL_STORAGE_DELETED backend=r2 bucket=%s key=%s", bucket, key)
    except Exception:
        logger.warning("SCROLL_STORAGE_DELETE_FAILED storage_ref=%s", storage_ref)


def process_one_queued_scroll_ingestion_job() -> dict:
    """
    Claim and process one queued scroll_upload ingestion job.

    This helper is used by manual/admin processing and by the bounded
    automatic upload background processor.
    """
    job = get_next_queued_ingestion_job("scroll_upload")
    if not job:
        return {
            "ok": True,
            "processed": False,
            "reason": "no_queued_jobs",
        }

    job_id = str(job["id"])
    storage_ref = job.get("storage_ref")
    original_filename = (
        job.get("original_filename")
        or storage_ref
        or "queued_scroll"
    )
    mime_type = job.get("mime_type")

    session_id = _canonical_identity_uuid(
        job.get("session_id")
    )
    anonymous_user_id = _canonical_identity_uuid(
        job.get("anonymous_user_id")
    )
    user_id = _canonical_identity_uuid(
        job.get("user_id")
    )

    # Backfilled legacy jobs may still contain the historical
    # browser id in session_id. Never preserve that as conversation
    # identity in newly ingested records.
    if (
        session_id
        and anonymous_user_id
        and session_id == anonymous_user_id
    ):
        logger.warning(
            "INGESTION_JOB_LEGACY_SESSION_ID job_id=%s",
            job_id,
        )
        session_id = None

    if not storage_ref:
        update_ingestion_job_status(
            job_id,
            "failed",
            error_message="Queued ingestion job missing storage_ref",
        )
        update_library_upload_for_ingestion_job(
            job_id,
            seeker_status="failed",
            admin_status="missing_storage_ref",
            metadata_json={"error": "missing_storage_ref"},
        )
        return {
            "ok": False,
            "processed": False,
            "job_id": job_id,
            "status": "failed",
            "error": "missing_storage_ref",
        }

    # Anonymous jobs require browser identity. Authenticated legacy
    # jobs may still be processed from their durable user ownership.
    if not anonymous_user_id and not user_id:
        update_ingestion_job_status(
            job_id,
            "failed",
            error_message=(
                "Queued ingestion job missing ownership identity"
            ),
        )
        update_library_upload_for_ingestion_job(
            job_id,
            seeker_status="failed",
            admin_status="missing_anonymous_user_id",
            metadata_json={
                "error": "missing_ownership_identity"
            },
        )
        return {
            "ok": False,
            "processed": False,
            "job_id": job_id,
            "status": "failed",
            "error": "missing_ownership_identity",
        }

    materialized_file_path = None
    materialized_is_temporary = False

    try:
        materialized = materialize_scroll_storage_ref(storage_ref)
        file_path = materialized["file_path"]
        materialized_file_path = file_path
        materialized_is_temporary = bool(materialized.get("temporary"))

    except Exception as exc:
        logging.exception("QUEUED_SCROLL_STORAGE_MATERIALIZE_FAILED job_id=%s", job_id)
        update_ingestion_job_status(
            job_id,
            "failed",
            error_message=str(exc),
        )
        update_library_upload_for_ingestion_job(
            job_id,
            seeker_status="failed",
            admin_status="storage_materialize_failed",
            metadata_json={"error": str(exc)},
        )
        return {
            "ok": False,
            "processed": True,
            "job_id": job_id,
            "status": "failed",
            "error": str(exc),
        }

    if not os.path.exists(file_path):
        update_ingestion_job_status(
            job_id,
            "failed",
            error_message=f"Queued ingestion file missing: {storage_ref}",
        )
        update_library_upload_for_ingestion_job(
            job_id,
            seeker_status="failed",
            admin_status="missing_materialized_file",
            metadata_json={"error": "missing_file", "storage_ref_present": bool(storage_ref)},
        )
        cleanup_materialized_scroll_file(materialized_file_path, materialized_is_temporary)
        return {
            "ok": False,
            "processed": False,
            "job_id": job_id,
            "status": "failed",
            "error": "missing_file",
        }

    try:
        result = ingest_saved_scroll_file(
            file_path=file_path,
            safe_name=storage_ref,
            original_filename=original_filename,
            mime_type=mime_type,
            session_id=session_id,
            anonymous_user_id=anonymous_user_id,
            authenticated_user_id=user_id,
            preserve_unreadable_file=True,
            preserve_duplicate_file=True,
        )

        result_payload = normalize_ingestion_result_payload(result)
        scroll_id = str(result_payload.get("scroll_id")) if result_payload.get("scroll_id") else None
        is_content_duplicate = bool(result_payload.get("duplicate"))

        if is_content_duplicate:
            logger.info(
                "SCROLL_UPLOAD_DUPLICATE_ARTIFACT_PRESERVED job_id=%s storage_ref_present=%s",
                job_id,
                bool(storage_ref),
            )

        final_result_payload = build_ingestion_job_result_payload(
            "ready",
            original_filename=original_filename,
            scroll_id=scroll_id,
            result_payload=result_payload,
        )

        update_ingestion_job_status(
            job_id,
            "ready",
            scroll_id=scroll_id,
            error_message=None,
            result_json=final_result_payload,
        )

        update_library_upload_for_ingestion_job(
            job_id,
            scroll_id=scroll_id,
            seeker_status="ready",
            admin_status="content_hash_duplicate" if is_content_duplicate else "ready",
            dedupe_kind="content_hash" if is_content_duplicate else "none",
            storage_ref=storage_ref,
            storage_backend="r2" if is_r2_storage_ref(storage_ref) else "local",
            content_hash=result_payload.get("content_hash"),
            metadata_json={
                "ingestion_status": "ready",
                "duplicate": is_content_duplicate,
                "artifact_preserved": True,
                "result_status": final_result_payload.get("status"),
            },
        )

        cleanup_materialized_scroll_file(materialized_file_path, materialized_is_temporary)

        return {
            "ok": True,
            "processed": True,
            "job_id": job_id,
            "status": "ready",
            "scroll_id": scroll_id,
            "result": final_result_payload,
        }

    except HTTPException as exc:
        status = "needs_ocr" if exc.status_code == 422 else "failed"
        final_result_payload = build_ingestion_job_result_payload(
            status,
            original_filename=original_filename,
            error_message=str(exc.detail),
        )
        update_ingestion_job_status(
            job_id,
            status,
            error_message=str(exc.detail),
            result_json=final_result_payload,
        )

        update_library_upload_for_ingestion_job(
            job_id,
            seeker_status=status,
            admin_status=status,
            storage_ref=storage_ref,
            storage_backend="r2" if is_r2_storage_ref(storage_ref) else "local",
            metadata_json={
                "ingestion_status": status,
                "status_code": exc.status_code,
                "error": str(exc.detail),
                "artifact_preserved": True,
            },
        )

        cleanup_materialized_scroll_file(materialized_file_path, materialized_is_temporary)

        return {
            "ok": False,
            "processed": True,
            "job_id": job_id,
            "status": status,
            "error": str(exc.detail),
            "result": final_result_payload,
        }

    except Exception as exc:
        logging.exception("QUEUED_SCROLL_INGESTION_FAILED job_id=%s", job_id)
        final_result_payload = build_ingestion_job_result_payload(
            "failed",
            original_filename=original_filename,
            error_message=str(exc),
        )
        update_ingestion_job_status(
            job_id,
            "failed",
            error_message=str(exc),
            result_json=final_result_payload,
        )

        update_library_upload_for_ingestion_job(
            job_id,
            seeker_status="failed",
            admin_status="ingestion_failed",
            storage_ref=storage_ref,
            storage_backend="r2" if is_r2_storage_ref(storage_ref) else "local",
            metadata_json={
                "ingestion_status": "failed",
                "error": str(exc),
                "artifact_preserved": True,
            },
        )

        cleanup_materialized_scroll_file(materialized_file_path, materialized_is_temporary)

        return {
            "ok": False,
            "processed": True,
            "job_id": job_id,
            "status": "failed",
            "error": str(exc),
            "result": final_result_payload,
        }


def get_scroll_ocr_settings() -> dict:
    """Return guarded OCR settings for queued PDF ingestion."""
    return {
        "enabled": _env_flag("SCROLL_OCR_ENABLED", default=False),
        "max_pages": max(1, int(os.getenv("SCROLL_OCR_MAX_PAGES", "5"))),
        "timeout_seconds": max(5, int(os.getenv("SCROLL_OCR_TIMEOUT_SECONDS", "60"))),
        "min_text_chars": max(1, int(os.getenv("SCROLL_OCR_MIN_TEXT_CHARS", "500"))),
        "dpi": max(72, int(os.getenv("SCROLL_OCR_DPI", "200"))),
        "tesseract_cmd": (os.getenv("SCROLL_OCR_TESSERACT_CMD") or "tesseract").strip() or "tesseract",
    }


def is_pdf_file_path(file_path: str) -> bool:
    return os.path.splitext(file_path or "")[1].lower() == ".pdf"


def is_scroll_text_sufficient(text: str, min_chars: Optional[int] = None) -> bool:
    threshold = int(min_chars or os.getenv("SCROLL_MIN_TEXT_CHARS", "50"))
    return len((text or "").strip()) >= max(1, threshold)


def render_pdf_page_to_png_bytes(page, dpi: int) -> bytes:
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return pix.tobytes("png")


def run_tesseract_ocr_on_png_bytes(image_bytes: bytes, *, timeout_seconds: int, tesseract_cmd: str) -> str:
    proc = subprocess.run(
        [tesseract_cmd, "stdin", "stdout", "--psm", "6"],
        input=image_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
    )
    if proc.returncode != 0:
        stderr_preview = proc.stderr.decode("utf-8", errors="ignore").strip()[:240]
        logger.warning("SCROLL_OCR_TESSERACT_FAILED returncode=%s stderr=%s", proc.returncode, stderr_preview)
        return ""
    return proc.stdout.decode("utf-8", errors="ignore").strip()


def extract_pdf_text_with_worker_ocr(file_path: str, *, settings: Optional[dict] = None) -> str:
    """Attempt bounded OCR for a PDF file. Intended for queued worker use only."""
    settings = settings or get_scroll_ocr_settings()
    if not settings.get("enabled"):
        return ""
    if not is_pdf_file_path(file_path):
        return ""

    tesseract_cmd = settings["tesseract_cmd"]
    if not shutil.which(tesseract_cmd):
        logger.warning("SCROLL_OCR_TESSERACT_MISSING cmd=%s", tesseract_cmd)
        return ""

    max_pages = int(settings["max_pages"])
    timeout_seconds = int(settings["timeout_seconds"])
    dpi = int(settings["dpi"])
    min_text_chars = int(settings["min_text_chars"])

    parts = []
    try:
        doc = fitz.open(file_path)
    except Exception as exc:
        logger.warning("SCROLL_OCR_OPEN_FAILED file=%s error=%s", file_path, exc)
        return ""

    try:
        page_count = min(doc.page_count, max_pages)
        for page_index in range(page_count):
            try:
                page = doc.load_page(page_index)
                image_bytes = render_pdf_page_to_png_bytes(page, dpi)
                page_text = run_tesseract_ocr_on_png_bytes(
                    image_bytes,
                    timeout_seconds=timeout_seconds,
                    tesseract_cmd=tesseract_cmd,
                )
                if page_text:
                    parts.append(page_text)
                current_text = "\n".join(parts).strip()
                if len(current_text) >= min_text_chars:
                    break
            except subprocess.TimeoutExpired:
                logger.warning("SCROLL_OCR_PAGE_TIMEOUT file=%s page=%s timeout=%s", file_path, page_index + 1, timeout_seconds)
            except Exception as exc:
                logger.warning("SCROLL_OCR_PAGE_FAILED file=%s page=%s error=%s", file_path, page_index + 1, exc)
    finally:
        doc.close()

    ocr_text = "\n".join(parts).strip()
    logger.info(
        "SCROLL_OCR_COMPLETE file=%s chars=%s max_pages=%s dpi=%s",
        file_path,
        len(ocr_text),
        max_pages,
        dpi,
    )
    return ocr_text



def extract_text_from_scroll(file_path):
    text = ""
    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".pdf":
            # First pass: PyPDF2
            try:
                reader = PdfReader(file_path)
                for page in reader.pages:
                    text += page.extract_text() or ""
            except Exception as e:
                logger.warning(f"PyPDF2 extraction failed for {file_path}: {e}")

            # Fallback: PyMuPDF
            if not text.strip():
                try:
                    doc = fitz.open(file_path)
                    parts = []
                    for page in doc:
                        parts.append(page.get_text("text") or "")
                    doc.close()
                    text = "\n".join(parts)
                except Exception as e:
                    logger.error(f"PyMuPDF extraction failed for {file_path}: {e}")

        elif ext == ".docx":
            doc = Document(file_path)
            for para in doc.paragraphs:
                text += para.text + "\n"

        elif ext in [".txt", ".md", ".rtf"]:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()

    except Exception as e:
        logger.error(f"Failed to extract text: {e}")

    return text.strip()


def remove_uploaded_file(file_path: str):
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.warning(f"Failed to remove uploaded file {file_path}: {e}")

def reset_scroll_system():
    """Clears uploaded scroll files from disk only; does not reset database scroll records."""
    # Clear all files in scrolls_uploads/
    for filename in os.listdir(UPLOAD_DIR):
        file_path = os.path.join(UPLOAD_DIR, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)


def ensure_anonymous_user(anonymous_user_id: str):
    """Ensure the anonymous user exists in the database and update last_seen."""
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM anonymous_users WHERE id = %s", (anonymous_user_id,))
        if not cur.fetchone():
            cur.execute("INSERT INTO anonymous_users (id, created_at, last_seen) VALUES (%s, %s, %s)",
                        (anonymous_user_id, datetime.datetime.utcnow(), datetime.datetime.utcnow()))
        else:
            cur.execute("UPDATE anonymous_users SET last_seen = %s WHERE id = %s",
                        (datetime.datetime.utcnow(), anonymous_user_id))
    conn.commit()
    conn.close()

def resolve_seeker_id(anonymous_user_id: str, provided_seeker_id: Optional[str] = None) -> Optional[str]:
    """Resolve seeker_id with precedence: provided > None (since no claims)"""
    if provided_seeker_id:
        return provided_seeker_id

    return None

def resolve_identity_state(anonymous_user_id: str) -> dict:
    """Return identity state for UI decisions"""
    seeker_id = resolve_seeker_id(anonymous_user_id)
    return {
        "is_claimed": seeker_id is not None,
        "seeker_id": seeker_id
    }

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)

def validate_reset_token(token: str) -> Optional[str]:
    """Validate reset token and return user_id if valid, else None."""
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE reset_token = %s AND reset_token_expires_at > %s", (token, datetime.datetime.now(timezone.utc)))
        result = cur.fetchone()
    conn.close()
    if result:
        return result['id']
    return None

def get_current_user(request: Request) -> Optional[dict]:
    """Get current authenticated user from session.
    Only verified users count as authenticated.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                email,
                seeker_id,
                display_name,
                email_verified,
                COALESCE(role, 'user') AS role
            FROM users
            WHERE id = %s
            """,
            (user_id,)
        )
        result = cur.fetchone()
    conn.close()

    if result and result["email_verified"]:
        return {
            "user_id": result["id"],
            "email": result["email"],
            "seeker_id": result["seeker_id"],
            "display_name": result["display_name"],
            "is_verified": result["email_verified"],
            "role": result["role"]
        }

    request.session.pop("user_id", None)
    request.session.pop("display_name", None)
    return None

VALID_USER_ROLES = {"user", "support", "admin", "owner"}


def normalize_user_role(role: Optional[str]) -> str:
    normalized = (role or "user").lower()
    return normalized if normalized in VALID_USER_ROLES else "user"


def user_has_admin_access(user: Optional[dict]) -> bool:
    if not user:
        return False
    return normalize_user_role(user.get("role")) in {"admin", "owner"}


def require_admin(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not user_has_admin_access(user):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user

from services.voice_access_policy import (
    WEB_PLAN_QUERY_LIMITS,
    WEB_PLAN_MEMORY_DEPTH,
    WEB_PLAN_RECALL_MEMORY_DEPTH,
    get_voice_policy,
)

PLAN_LIMITS = dict(WEB_PLAN_QUERY_LIMITS)
PLAN_MEMORY_DEPTH = dict(WEB_PLAN_MEMORY_DEPTH)
PLAN_RECALL_MEMORY_DEPTH = dict(WEB_PLAN_RECALL_MEMORY_DEPTH)


PLAN_REFLECTION_WORD_CAPS = {
    "anon": 240,
    "pilgrim": 240,
    "seeker": 350,
    "magister": 400,
    "sovereign": 550,
    "philosophus": 750,
    "theoricus": 900,
}

RECALL_WORD_CAP = 220


def get_response_word_cap(
    plan_code: Optional[str],
    memory_intent: str,
    deity: Optional[str] = None,
    input_mode: str = "text"
) -> int:
    """
    Phase 10 response-budget control.

    Controls final answer length by access tier, deity, and input mode.
    This is the primary latency/cost control after pgvector retrieval.
    """

    normalized_plan = normalize_plan_code(plan_code)
    normalized_deity = (deity or "").strip().lower()
    normalized_input = (input_mode or "text").strip().lower()

    # Ordered access levels:
    # anon -> pilgrim -> seeker -> magister -> sovereign -> philosophus -> theoricus
    text_ranges = {
        "anon": (120, 190),
        "pilgrim": (180, 280),
        "seeker": (260, 400),
        "magister": (360, 540),
        "sovereign": (470, 650),
        "philosophus": (521, 724),
        "theoricus": (560, 780),
    }

    voice_ranges = {
        "anon": (55, 100),
        "pilgrim": (80, 135),
        "seeker": (115, 170),
        "magister": (150, 210),
        "sovereign": (180, 250),
        "philosophus": (197, 273),
        "theoricus": (210, 290),
    }

    ranges = voice_ranges if normalized_input == "voice" else text_ranges
    low, high = ranges.get(normalized_plan, ranges["anon"])

    # Recall should stay concise even for high tiers.
    if memory_intent == "recall":
        high = min(high, 180 if normalized_input == "voice" else 320)
        low = min(low, high)

    # Moses is more direct; Hathor is warmer but still bounded.
    if normalized_deity == "moses":
        target = int((low * 0.65) + (high * 0.35))
    elif normalized_deity == "hathor":
        target = int((low * 0.35) + (high * 0.65))
    else:
        target = int((low + high) / 2)

    return max(60, target)


def words_to_max_tokens(word_cap: int) -> int:
    # Completion budget should be larger than the visible word target.
    # A tight token cap causes the model to stop mid-sentence before
    # sentence-safe trimming can do its job.
    return max(180, int(word_cap * 2.25))


def trim_response_to_word_cap(answer: str, word_cap: int) -> str:
    text = (answer or "").strip()
    words = text.split()
    if len(words) <= word_cap:
        return text

    rough = " ".join(words[:word_cap]).strip()
    sentence_enders = [".", "!", "?", ".”", "!”", "?”"]
    last_end = -1

    for marker in sentence_enders:
        last_end = max(last_end, rough.rfind(marker))

    # Prefer a complete sentence if one exists near the end of the trimmed text.
    minimum_usable = max(80, int(len(rough) * 0.55))
    if last_end >= minimum_usable:
        return rough[:last_end + 1].strip()

    # If no sentence ending exists, give a clean continuation cue instead of
    # making the answer look like it crashed.
    return rough.rstrip(" ,;:-") + ". I can continue from here if you would like."


def normalize_plan_code(plan_code: Optional[str]) -> str:
    plan = (plan_code or "anon").lower()
    return plan if plan in PLAN_LIMITS else "anon"


def plan_has_unlimited_questions(plan_code: Optional[str]) -> bool:
    return normalize_plan_code(plan_code) in {"theoricus"}


PLAN_RANKS = {
    "anon": 0,
    "pilgrim": 1,
    "seeker": 3,
    "magister": 5,
    "sovereign": 7,
    "philosophus": 9,
    "theoricus": 10,
}


def plan_rank(plan_code: Optional[str]) -> int:
    return PLAN_RANKS.get(normalize_plan_code(plan_code), 0)


def max_plan_code(*codes: Optional[str]) -> str:
    valid_codes = [normalize_plan_code(code) for code in codes if code]
    if not valid_codes:
        return "anon"
    return max(valid_codes, key=plan_rank)


def compute_scroll_floor_plan(scroll_count: int) -> Optional[str]:
    if scroll_count >= 99:
        return "magister"
    if scroll_count >= 9:
        return "seeker"
    if scroll_count >= 1:
        return "pilgrim"
    return None


def compute_donor_floor_plan(highest_paid_plan_ever: Optional[str]) -> Optional[str]:
    if not highest_paid_plan_ever:
        return None

    plan = normalize_plan_code(highest_paid_plan_ever)

    if plan in {"magister", "sovereign", "philosophus", "theoricus"}:
        return "seeker"
    if plan == "seeker":
        return "pilgrim"

    return None


def compute_fallback_floor_plan(
    authenticated: bool,
    donor_floor: Optional[str],
    scroll_floor: Optional[str]
) -> str:
    if not authenticated:
        return "anon"

    floor = max_plan_code(donor_floor, scroll_floor, "pilgrim")
    return floor if floor != "anon" else "pilgrim"


def serialize_dt(value):
    return value.isoformat() if value else None


def start_of_utc_day(value: Optional[datetime.datetime] = None) -> datetime.datetime:
    value = value.astimezone(timezone.utc) if value else datetime.datetime.now(timezone.utc)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def start_of_utc_month(value: Optional[datetime.datetime] = None) -> datetime.datetime:
    value = value.astimezone(timezone.utc) if value else datetime.datetime.now(timezone.utc)
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def get_effective_usage_window_start(entitlement: dict) -> Optional[datetime.datetime]:
    plan_code = normalize_plan_code(entitlement.get("effective_plan_code"))
    status = entitlement.get("entitlement_status")
    last_support_mode = entitlement.get("last_support_mode")
    current_period_started_at = entitlement.get("current_period_started_at")

    if status == "active" and last_support_mode in {"monthly_recurring", "annual_recurring"} and current_period_started_at:
        return current_period_started_at

    if plan_code in {"seeker", "magister", "sovereign", "philosophus", "theoricus"}:
        return start_of_utc_month()

    if plan_code == "pilgrim":
        return start_of_utc_day()

    return None


def get_user_entitlement_snapshot(user_id: str) -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(plan_code, 'anon') AS raw_plan_code,
                    COALESCE(entitlement_status, 'none') AS entitlement_status,
                    subscription_started_at,
                    current_period_started_at,
                    subscription_renews_at,
                    subscription_expires_at,
                    COALESCE(cancel_at_period_end, false) AS cancel_at_period_end,
                    highest_paid_plan_ever,
                    last_paid_plan_code,
                    donor_floor_plan_code,
                    scroll_floor_plan_code,
                    fallback_floor_plan_code,
                    renewal_offer_plan_code,
                    last_support_mode,
                    last_support_ended_at
                FROM users
                WHERE id = %s
                """,
                (user_id,)
            )
            row = cur.fetchone() or {}
    finally:
        conn.close()

    now = datetime.datetime.now(timezone.utc)

    raw_plan_code = normalize_plan_code(row.get("raw_plan_code"))
    entitlement_status = (row.get("entitlement_status") or "none").lower()

    highest_paid_plan_ever = (
        normalize_plan_code(row.get("highest_paid_plan_ever"))
        if row.get("highest_paid_plan_ever") else None
    )
    last_paid_plan_code = (
        normalize_plan_code(row.get("last_paid_plan_code"))
        if row.get("last_paid_plan_code") else None
    )
    donor_floor_plan_code = (
        normalize_plan_code(row.get("donor_floor_plan_code"))
        if row.get("donor_floor_plan_code")
        else compute_donor_floor_plan(highest_paid_plan_ever)
    )
    scroll_floor_plan_code = (
        normalize_plan_code(row.get("scroll_floor_plan_code"))
        if row.get("scroll_floor_plan_code") else None
    )
    fallback_floor_plan_code = (
        normalize_plan_code(row.get("fallback_floor_plan_code"))
        if row.get("fallback_floor_plan_code")
        else compute_fallback_floor_plan(
            authenticated=True,
            donor_floor=donor_floor_plan_code,
            scroll_floor=scroll_floor_plan_code
        )
    )
    renewal_offer_plan_code = (
        normalize_plan_code(row.get("renewal_offer_plan_code"))
        if row.get("renewal_offer_plan_code")
        else last_paid_plan_code
    )
    last_support_mode = row.get("last_support_mode")
    last_support_ended_at = row.get("last_support_ended_at")

    expires_at = row.get("subscription_expires_at")

    is_active_paid = (
        entitlement_status == "active"
        and (expires_at is None or now <= expires_at)
    )

    if is_active_paid:
        effective_plan_code = raw_plan_code
        is_entitled = raw_plan_code not in {"anon", "pilgrim"}
    else:
        if entitlement_status == "active" and expires_at and now > expires_at:
            entitlement_status = "expired"

        effective_plan_code = fallback_floor_plan_code
        is_entitled = effective_plan_code not in {"anon", "pilgrim"}

    return {
        "raw_plan_code": raw_plan_code,
        "effective_plan_code": effective_plan_code,
        "entitlement_status": entitlement_status,
        "subscription_started_at": row.get("subscription_started_at"),
        "current_period_started_at": row.get("current_period_started_at"),
        "subscription_renews_at": row.get("subscription_renews_at"),
        "subscription_expires_at": expires_at,
        "cancel_at_period_end": row.get("cancel_at_period_end", False),
        "is_entitled": is_entitled,
        "downgraded_for_access": raw_plan_code != effective_plan_code,
        "highest_paid_plan_ever": highest_paid_plan_ever,
        "last_paid_plan_code": last_paid_plan_code,
        "donor_floor_plan_code": donor_floor_plan_code,
        "scroll_floor_plan_code": scroll_floor_plan_code,
        "fallback_floor_plan_code": fallback_floor_plan_code,
        "renewal_offer_plan_code": renewal_offer_plan_code,
        "last_support_mode": last_support_mode,
        "last_support_ended_at": last_support_ended_at,
    }


DEFAULT_BILLING_CYCLE_DAYS = 30
DEFAULT_ANNUAL_PREPAID_DAYS = 365
VALID_ENTITLEMENT_STATUSES = {
    "none",
    "active",
    "expired",
    "cancelled",
}


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(timezone.utc)


def normalize_entitlement_status(status: Optional[str]) -> str:
    normalized = (status or "none").lower()
    if normalized not in VALID_ENTITLEMENT_STATUSES:
        raise ValueError(f"Invalid entitlement_status: {status}")
    return normalized


def apply_subscription_renewal_success(
    user_id: str,
    plan_code: str,
    cycle_days: int = DEFAULT_BILLING_CYCLE_DAYS,
    period_start: Optional[datetime.datetime] = None,
    period_end: Optional[datetime.datetime] = None,
    support_mode: str = "monthly_recurring"
) -> None:
    now = utc_now()
    normalized_plan = normalize_plan_code(plan_code)
    effective_period_start = period_start or now
    next_renewal = period_end or (effective_period_start + datetime.timedelta(days=cycle_days))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT highest_paid_plan_ever
                FROM users
                WHERE id = %s
                """,
                (user_id,)
            )
            row = cur.fetchone() or {}

            existing_highest = (
                normalize_plan_code(row.get("highest_paid_plan_ever"))
                if row.get("highest_paid_plan_ever") else None
            )

            if existing_highest and plan_rank(existing_highest) >= plan_rank(normalized_plan):
                highest_paid_plan_ever = existing_highest
            else:
                highest_paid_plan_ever = normalized_plan

            donor_floor_plan_code = compute_donor_floor_plan(highest_paid_plan_ever)

            cur.execute(
                """
                UPDATE users
                SET
                    plan_code = %s,
                    entitlement_status = 'active',
                    subscription_started_at = COALESCE(subscription_started_at, %s),
                    current_period_started_at = %s,
                    subscription_renews_at = %s,
                    subscription_expires_at = %s,
                    cancel_at_period_end = FALSE,
                    highest_paid_plan_ever = %s,
                    last_paid_plan_code = %s,
                    donor_floor_plan_code = %s,
                    renewal_offer_plan_code = %s,
                    last_support_mode = %s,
                    last_support_ended_at = NULL
                WHERE id = %s
                """,
                (
                    normalized_plan,
                    effective_period_start,
                    effective_period_start,
                    next_renewal,
                    next_renewal,
                    highest_paid_plan_ever,
                    normalized_plan,
                    donor_floor_plan_code,
                    normalized_plan,
                    support_mode,
                    user_id
                )
            )
        conn.commit()
    finally:
        conn.close()

    refresh_user_fallback_state(user_id)


def apply_annual_prepaid_activation(
    user_id: str,
    plan_code: str,
    term_days: int = DEFAULT_ANNUAL_PREPAID_DAYS,
    period_start: Optional[datetime.datetime] = None,
    period_end: Optional[datetime.datetime] = None
) -> None:
    now = utc_now()
    normalized_plan = normalize_plan_code(plan_code)
    effective_period_start = period_start or now
    expires_at = period_end or (effective_period_start + datetime.timedelta(days=term_days))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT highest_paid_plan_ever
                FROM users
                WHERE id = %s
                """,
                (user_id,)
            )
            row = cur.fetchone() or {}

            existing_highest = (
                normalize_plan_code(row.get("highest_paid_plan_ever"))
                if row.get("highest_paid_plan_ever") else None
            )

            if existing_highest and plan_rank(existing_highest) >= plan_rank(normalized_plan):
                highest_paid_plan_ever = existing_highest
            else:
                highest_paid_plan_ever = normalized_plan

            donor_floor_plan_code = compute_donor_floor_plan(highest_paid_plan_ever)

            cur.execute(
                """
                UPDATE users
                SET
                    plan_code = %s,
                    entitlement_status = 'active',
                    subscription_started_at = COALESCE(subscription_started_at, %s),
                    current_period_started_at = %s,
                    subscription_renews_at = NULL,
                    subscription_expires_at = %s,
                    cancel_at_period_end = FALSE,
                    highest_paid_plan_ever = %s,
                    last_paid_plan_code = %s,
                    donor_floor_plan_code = %s,
                    renewal_offer_plan_code = %s,
                    last_support_mode = 'annual_prepaid',
                    last_support_ended_at = NULL
                WHERE id = %s
                """,
                (
                    normalized_plan,
                    effective_period_start,
                    effective_period_start,
                    expires_at,
                    highest_paid_plan_ever,
                    normalized_plan,
                    donor_floor_plan_code,
                    normalized_plan,
                    user_id
                )
            )
        conn.commit()
    finally:
        conn.close()

    refresh_user_fallback_state(user_id)


def apply_annual_prepaid_expiry(user_id: str) -> None:
    now = utc_now()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    entitlement_status = 'expired',
                    last_support_ended_at = COALESCE(last_support_ended_at, %s)
                WHERE id = %s
                  AND subscription_expires_at IS NOT NULL
                  AND subscription_expires_at <= %s
                """,
                (now, user_id, now)
            )
        conn.commit()
    finally:
        conn.close()

    refresh_user_fallback_state(user_id)



def apply_subscription_renewal_failure_to_floor(user_id: str) -> None:
    now = utc_now()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    entitlement_status = 'expired',
                    subscription_expires_at = CASE
                        WHEN subscription_expires_at IS NULL OR subscription_expires_at < %s
                            THEN %s
                        ELSE subscription_expires_at
                    END,
                    last_support_ended_at = COALESCE(last_support_ended_at, %s)
                WHERE id = %s
                """,
                (
                    now,
                    now,
                    now,
                    user_id
                )
            )
        conn.commit()
    finally:
        conn.close()

    refresh_user_fallback_state(user_id)



def set_cancel_at_period_end(user_id: str, should_cancel: bool) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET cancel_at_period_end = %s
                WHERE id = %s
                """,
                (should_cancel, user_id)
            )
        conn.commit()
    finally:
        conn.close()


def apply_cancel_at_period_end_downgrade(user_id: str) -> None:
    now = utc_now()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    entitlement_status = 'cancelled',
                    cancel_at_period_end = FALSE,
                    last_support_ended_at = COALESCE(last_support_ended_at, %s)
                WHERE id = %s
                  AND cancel_at_period_end = TRUE
                  AND subscription_expires_at IS NOT NULL
                  AND subscription_expires_at <= %s
                """,
                (now, user_id, now)
            )
        conn.commit()
    finally:
        conn.close()

    refresh_user_fallback_state(user_id)


def apply_admin_entitlement_override(
    user_id: str,
    plan_code: str,
    entitlement_status: str,
    current_period_started_at: Optional[datetime.datetime] = None,
    subscription_renews_at: Optional[datetime.datetime] = None,
    subscription_expires_at: Optional[datetime.datetime] = None,
    cancel_at_period_end: bool = False
) -> None:
    normalized_plan = normalize_plan_code(plan_code)
    normalized_status = normalize_entitlement_status(entitlement_status)
    now = utc_now()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    plan_code = %s,
                    entitlement_status = %s,
                    subscription_started_at = COALESCE(subscription_started_at, %s),
                    current_period_started_at = %s,
                    subscription_renews_at = %s,
                    subscription_expires_at = %s,
                    cancel_at_period_end = %s,
                    last_support_ended_at = CASE
                        WHEN %s IN ('expired', 'cancelled') THEN COALESCE(last_support_ended_at, %s)
                        WHEN %s = 'active' THEN NULL
                        ELSE last_support_ended_at
                    END
                WHERE id = %s
                """,
                (
                    normalized_plan,
                    normalized_status,
                    now,
                    current_period_started_at,
                    subscription_renews_at,
                    subscription_expires_at,
                    cancel_at_period_end,
                    normalized_status,
                    now,
                    normalized_status,
                    user_id
                )
            )
        conn.commit()
    finally:
        conn.close()

    refresh_user_fallback_state(user_id)


def get_oracle_usage_counts(
    user_id: str,
    window_start: Optional[datetime.datetime] = None,
) -> dict:
    """
    Count authenticated seeker Oracle usage.

    Anonymous browser usage is intentionally handled separately by
    get_anonymous_oracle_usage_counts().
    """
    if not user_id:
        raise ValueError(
            "user_id is required for authenticated Oracle usage"
        )

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            if window_start:
                cur.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM oracle_interactions
                    WHERE user_id = %s
                      AND created_at >= %s
                    """,
                    (user_id, window_start)
                )
                usage_row = cur.fetchone()

                cur.execute(
                    """
                    SELECT mode, COUNT(*) AS total
                    FROM oracle_interactions
                    WHERE user_id = %s
                      AND created_at >= %s
                    GROUP BY mode
                    """,
                    (user_id, window_start)
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM oracle_interactions
                    WHERE user_id = %s
                    """,
                    (user_id,)
                )
                usage_row = cur.fetchone()

                cur.execute(
                    """
                    SELECT mode, COUNT(*) AS total
                    FROM oracle_interactions
                    WHERE user_id = %s
                    GROUP BY mode
                    """,
                    (user_id,)
                )

            mode_rows = cur.fetchall()

    finally:
        conn.close()

    return {
        "questions_used": usage_row["total"] if usage_row else 0,
        "mode_counts": {
            row["mode"]: row["total"]
            for row in mode_rows
        },
    }

def get_question_limit(user: Optional[dict]) -> int:
    """
    Keep this helper aligned with effective entitlement,
    not raw stored plan_code.
    """
    if not user:
        return PLAN_LIMITS["anon"]

    entitlement = get_user_entitlement_snapshot(user["user_id"])
    return PLAN_LIMITS.get(
        entitlement["effective_plan_code"],
        PLAN_LIMITS["anon"]
    )


# ================================
# Phase 5 — Title Computation Axis
# ================================

def compute_scroll_tier(scroll_count: int) -> str:
    """
    User title ladder:
    0 = Dormant
    1-8 = Scribe
    9-32 = Builder
    33-98 = Archivist
    99+ = Luminary
    """
    if scroll_count >= 99:
        return "Luminary"
    elif scroll_count >= 33:
        return "Archivist"
    elif scroll_count >= 9:
        return "Builder"
    elif scroll_count >= 1:
        return "Scribe"
    else:
        return "Dormant"


def compute_monetary_title(plan_code: str):
    mapping = {
        "anon": "Pilgrim",
        "pilgrim": "Pilgrim",
        "seeker": "Seeker",
        "magister": "Magus",
        "sovereign": "Sovereign",
        "philosophus": "Philosopher",
        "theoricus": "Theosopher"
    }

    return mapping.get((plan_code or "anon").lower(), "Pilgrim")


def compute_combined_title(scroll_count: int, plan_code: str, authenticated: bool) -> str:
    scroll_title = compute_scroll_tier(scroll_count)

    if not authenticated:
        monetary_title = "Anon"
    else:
        monetary_title = compute_monetary_title(plan_code)

    return f"{scroll_title} {monetary_title}"


def plan_label_or_none(plan_code: Optional[str]) -> Optional[str]:
    if not plan_code:
        return None
    return compute_monetary_title(plan_code)


def build_support_status_payload(entitlement: dict) -> dict:
    status = entitlement.get("entitlement_status")
    effective_plan_code = entitlement.get("effective_plan_code")
    raw_plan_code = entitlement.get("raw_plan_code")
    highest_paid_plan_ever = entitlement.get("highest_paid_plan_ever")
    donor_floor_plan_code = entitlement.get("donor_floor_plan_code")
    scroll_floor_plan_code = entitlement.get("scroll_floor_plan_code")
    fallback_floor_plan_code = entitlement.get("fallback_floor_plan_code")
    renewal_offer_plan_code = entitlement.get("renewal_offer_plan_code")
    last_support_mode = entitlement.get("last_support_mode")
    last_support_ended_at = entitlement.get("last_support_ended_at")

    support_mode_label = {
        "monthly_recurring": "Monthly recurring support",
        "annual_prepaid": "Annual prepaid support",
        "annual_recurring": "Annual recurring support",
    }.get(last_support_mode)

    if status == "active":
        if last_support_mode == "annual_prepaid" and entitlement.get("subscription_expires_at"):
            message = f"Annual prepaid support is active through {serialize_dt(entitlement['subscription_expires_at'])}."
        elif last_support_mode == "monthly_recurring" and entitlement.get("subscription_renews_at"):
            message = f"Monthly recurring support is active. Next renewal is {serialize_dt(entitlement['subscription_renews_at'])}."
        elif last_support_mode == "annual_recurring" and entitlement.get("subscription_renews_at"):
            message = f"Annual recurring support is active. Next renewal is {serialize_dt(entitlement['subscription_renews_at'])}."
        elif support_mode_label:
            message = f"{support_mode_label} is active."
        else:
            message = "Paid support is active."
        renewal_message = None
    elif status in {"expired", "cancelled"}:
        message = f"Paid support has ended. Current access remains at {plan_label_or_none(effective_plan_code)}."
        renewal_message = (
            f"Renew at {plan_label_or_none(renewal_offer_plan_code)} to continue together at your previous level."
            if renewal_offer_plan_code else None
        )
    else:
        message = f"Current access is {plan_label_or_none(effective_plan_code)}."
        renewal_message = None

    return {
        "status": status,
        "mode": last_support_mode,
        "mode_label": support_mode_label,
        "current_access_plan_code": effective_plan_code,
        "current_access_label": plan_label_or_none(effective_plan_code),
        "stored_plan_code": raw_plan_code,
        "stored_plan_label": plan_label_or_none(raw_plan_code),
        "highest_paid_plan_ever": highest_paid_plan_ever,
        "highest_paid_label": plan_label_or_none(highest_paid_plan_ever),
        "donor_floor_plan_code": donor_floor_plan_code,
        "donor_floor_label": plan_label_or_none(donor_floor_plan_code),
        "scroll_floor_plan_code": scroll_floor_plan_code,
        "scroll_floor_label": plan_label_or_none(scroll_floor_plan_code),
        "fallback_floor_plan_code": fallback_floor_plan_code,
        "fallback_floor_label": plan_label_or_none(fallback_floor_plan_code),
        "renewal_offer_plan_code": renewal_offer_plan_code,
        "renewal_offer_label": plan_label_or_none(renewal_offer_plan_code),
        "last_support_ended_at": serialize_dt(last_support_ended_at),
        "message": message,
        "renewal_message": renewal_message,
    }


def build_anonymous_support_status_payload() -> dict:
    return {
        "status": "none",
        "mode": None,
        "mode_label": None,
        "current_access_plan_code": "anon",
        "current_access_label": "Anon",
        "stored_plan_code": None,
        "stored_plan_label": None,
        "highest_paid_plan_ever": None,
        "highest_paid_label": None,
        "donor_floor_plan_code": None,
        "donor_floor_label": None,
        "scroll_floor_plan_code": None,
        "scroll_floor_label": None,
        "fallback_floor_plan_code": "anon",
        "fallback_floor_label": "Anon",
        "renewal_offer_plan_code": None,
        "renewal_offer_label": None,
        "last_support_ended_at": None,
        "message": "You are using anonymous access.",
        "renewal_message": "Create an account to begin a named path."
    }


def get_memory_depth(plan_code: str, memory_intent: str = "reflection"):
    plan = normalize_plan_code(plan_code)
    if memory_intent == "recall":
        return PLAN_RECALL_MEMORY_DEPTH.get(plan, PLAN_RECALL_MEMORY_DEPTH["anon"])
    return PLAN_MEMORY_DEPTH.get(plan, PLAN_MEMORY_DEPTH["anon"])


def get_question_display(plan_code: str, questions_used: int, question_limit: Optional[int]) -> dict:
    """
    Seeker-facing display rules.

    Backend enforcement can remain numeric, but the UI should not show
    a fake countdown for unlimited-style plans.
    """
    plan = (plan_code or "anon").lower()

    if plan_has_unlimited_questions(plan):
        return {
            "question_limit_display": None,
            "questions_remaining_display": "Unlimited",
            "is_unlimited_questions": True
        }

    return {
        "question_limit_display": str(question_limit),
        "questions_remaining_display": str(max(question_limit - questions_used, 0)),
        "is_unlimited_questions": False
    }

# Phase 11.10R: keep browser identity, authenticated identity, and
# Oracle conversation identity as separate authorities.
def _canonical_identity_uuid(value) -> Optional[str]:
    if value is None:
        return None

    try:
        return str(uuid.UUID(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return None


def get_or_create_anonymous_user_id(
    request: Request,
    provided_id: Optional[str] = None,
) -> str:
    """
    Resolve the persistent browser/device identity.

    This identity owns anonymous continuity, quotas, upload provenance,
    and claimability. It is not an Oracle conversation id.
    """
    header_id = _canonical_identity_uuid(
        get_browser_token_from_request(request)
    )
    provided_id = _canonical_identity_uuid(provided_id)
    stored_id = _canonical_identity_uuid(
        request.session.get("anonymous_user_id")
    )

    anonymous_user_id = (
        header_id
        or provided_id
        or stored_id
        or str(uuid.uuid4())
    )

    current_session_id = _canonical_identity_uuid(
        request.session.get("session_id")
    )

    # A changed browser/device identity cannot inherit another browser's
    # Oracle conversation. Also retire legacy cookies where the browser id
    # itself had been stored as the conversation id.
    if (
        (stored_id and stored_id != anonymous_user_id)
        or current_session_id == anonymous_user_id
    ):
        request.session.pop("session_id", None)

    request.session["anonymous_user_id"] = anonymous_user_id
    ensure_anonymous_user(anonymous_user_id)

    return anonymous_user_id


def ensure_session_identity(
    session_id: str,
    anonymous_user_id: str,
    user_id: Optional[str] = None,
) -> bool:
    """
    Create or refresh one Oracle conversation binding.

    Existing conversations may acquire a user when an anonymous seeker
    authenticates, but they may never move between browser identities or
    between already-authenticated users.
    """
    session_id = _canonical_identity_uuid(session_id)
    anonymous_user_id = _canonical_identity_uuid(anonymous_user_id)

    if not session_id:
        raise ValueError("session_id must be a valid UUID")

    if not anonymous_user_id:
        raise ValueError("anonymous_user_id must be a valid UUID")

    normalized_user_id = None
    if user_id is not None:
        normalized_user_id = _canonical_identity_uuid(user_id)
        if not normalized_user_id:
            raise ValueError("user_id must be a valid UUID")

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, anonymous_user_id
                FROM sessions
                WHERE id = %s::uuid
                FOR UPDATE
                """,
                (session_id,),
            )
            row = cur.fetchone()

            if row:
                existing_user_id = (
                    str(row["user_id"])
                    if row.get("user_id")
                    else None
                )
                existing_anonymous_user_id = (
                    str(row["anonymous_user_id"])
                    if row.get("anonymous_user_id")
                    else None
                )

                if (
                    existing_anonymous_user_id
                    and existing_anonymous_user_id != anonymous_user_id
                ):
                    conn.rollback()
                    return False

                if (
                    existing_user_id
                    and existing_user_id != normalized_user_id
                ):
                    conn.rollback()
                    return False

                cur.execute(
                    """
                    UPDATE sessions
                    SET
                        user_id = COALESCE(user_id, %s::uuid),
                        anonymous_user_id = COALESCE(
                            anonymous_user_id,
                            %s
                        ),
                        last_seen_at = now()
                    WHERE id = %s::uuid
                    """,
                    (
                        normalized_user_id,
                        anonymous_user_id,
                        session_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO sessions (
                        id,
                        user_id,
                        anonymous_user_id,
                        last_seen_at
                    )
                    VALUES (%s::uuid, %s::uuid, %s, now())
                    """,
                    (
                        session_id,
                        normalized_user_id,
                        anonymous_user_id,
                    ),
                )

        conn.commit()
        return True

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def get_or_create_bound_session_id(
    request: Request,
    anonymous_user_id: str,
    user_id: Optional[str] = None,
) -> str:
    """
    Resolve one Oracle conversation id and bind it to its authorities.

    A binding conflict rotates the conversation rather than allowing
    dialogue to cross a browser or authenticated-user boundary.
    """
    anonymous_user_id = _canonical_identity_uuid(anonymous_user_id)

    if not anonymous_user_id:
        raise ValueError("anonymous_user_id must be a valid UUID")

    normalized_user_id = None
    if user_id is not None:
        normalized_user_id = _canonical_identity_uuid(user_id)
        if not normalized_user_id:
            raise ValueError("user_id must be a valid UUID")

    session_id = _canonical_identity_uuid(
        request.session.get("session_id")
    )

    # Retire the historical browser-id-as-session-id contract.
    if session_id == anonymous_user_id:
        session_id = None

    if session_id:
        bound = ensure_session_identity(
            session_id,
            anonymous_user_id,
            normalized_user_id,
        )
        if not bound:
            session_id = None

    if not session_id:
        session_id = str(uuid.uuid4())

        if not ensure_session_identity(
            session_id,
            anonymous_user_id,
            normalized_user_id,
        ):
            raise RuntimeError(
                "Could not establish Oracle conversation identity"
            )

    request.session["session_id"] = session_id
    return session_id


def get_anonymous_oracle_usage_counts(
    anonymous_user_id: str,
) -> dict:
    """
    Count anonymous-browser Oracle usage across conversation rotations.

    Conversation rotation must never reset browser-level anonymous usage.
    """
    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM oracle_interactions
                WHERE anonymous_user_id = %s
                """,
                (anonymous_user_id,),
            )
            usage_row = cur.fetchone()

            cur.execute(
                """
                SELECT mode, COUNT(*) AS total
                FROM oracle_interactions
                WHERE anonymous_user_id = %s
                GROUP BY mode
                """,
                (anonymous_user_id,),
            )
            mode_rows = cur.fetchall()

    finally:
        conn.close()

    return {
        "questions_used": usage_row["total"] if usage_row else 0,
        "mode_counts": {
            row["mode"]: row["total"]
            for row in mode_rows
        },
    }


def can_user_ask(
    anonymous_user_id: str,
    user_id: Optional[str] = None,
) -> bool:
    if user_id:
        entitlement = get_user_entitlement_snapshot(user_id)
        usage_window_start = get_effective_usage_window_start(entitlement)
        usage = get_oracle_usage_counts(
            user_id=user_id,
            window_start=usage_window_start
        )

        if plan_has_unlimited_questions(
            entitlement["effective_plan_code"]
        ):
            return True

        limit = PLAN_LIMITS.get(
            entitlement["effective_plan_code"],
            PLAN_LIMITS["anon"]
        )

        return usage["questions_used"] < limit

    usage = get_anonymous_oracle_usage_counts(anonymous_user_id)
    return usage["questions_used"] < PLAN_LIMITS["anon"]


def get_or_create_session_id(request: Request) -> str:
    """
    Resolve the current Oracle conversation.

    Historical behavior promoted X-Anonymous-User-Id directly into
    session_id. The browser identity is now resolved independently and
    this wrapper returns only a bound conversation UUID.
    """
    anonymous_user_id = get_or_create_anonymous_user_id(request)
    user_id = _canonical_identity_uuid(
        request.session.get("user_id")
    )

    return get_or_create_bound_session_id(
        request,
        anonymous_user_id,
        user_id,
    )


def get_anonymous_upload_stats(anonymous_user_id: str) -> dict:
    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS upload_count,
                    MAX(created_at) AS last_uploaded_at
                FROM scrolls
                WHERE anonymous_user_id = %s
                """,
                (anonymous_user_id,)
            )

            row = cur.fetchone() or {}

            return {
                "upload_count": row.get("upload_count", 0) or 0,
                "last_uploaded_at": row.get("last_uploaded_at"),
            }
    finally:
        conn.close()

def build_claim_nudges(upload_count: int) -> list[str]:
    """
    Keep anonymous upload nudges brief and calm.

    The claim path matters, but successful upload feedback should feel like
    continuity guidance, not a repeated sales pitch.
    """
    try:
        count = int(upload_count or 0)
    except Exception:
        count = 0

    if count <= 0:
        return []

    if count >= max(1, ANONYMOUS_UPLOAD_LIMIT - 1):
        return ["Create an account to keep uploading and preserve your Library."]

    return ["Create an account to keep your Library."]


def refresh_user_fallback_state(user_id: str) -> dict:
    """
    Refresh fallback floor fields from current user history and authoritative
    scroll ownership.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(scroll_count, 0) AS scroll_count,
                    highest_paid_plan_ever,
                    last_paid_plan_code,
                    donor_floor_plan_code,
                    scroll_floor_plan_code,
                    fallback_floor_plan_code,
                    renewal_offer_plan_code
                FROM users
                WHERE id = %s
                """,
                (user_id,)
            )
            row = cur.fetchone()

            if not row:
                raise ValueError(f"User not found for fallback refresh: {user_id}")

            scroll_count = row["scroll_count"] or 0
            highest_paid_plan_ever = row.get("highest_paid_plan_ever")

            donor_floor_plan_code = compute_donor_floor_plan(highest_paid_plan_ever)
            scroll_floor_plan_code = compute_scroll_floor_plan(scroll_count)
            fallback_floor_plan_code = compute_fallback_floor_plan(
                authenticated=True,
                donor_floor=donor_floor_plan_code,
                scroll_floor=scroll_floor_plan_code
            )

            cur.execute(
                """
                UPDATE users
                SET
                    donor_floor_plan_code = %s,
                    scroll_floor_plan_code = %s,
                    fallback_floor_plan_code = %s,
                    renewal_offer_plan_code = COALESCE(renewal_offer_plan_code, last_paid_plan_code),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    donor_floor_plan_code,
                    scroll_floor_plan_code,
                    fallback_floor_plan_code,
                    user_id
                )
            )

        conn.commit()

        return {
            "donor_floor_plan_code": donor_floor_plan_code,
            "scroll_floor_plan_code": scroll_floor_plan_code,
            "fallback_floor_plan_code": fallback_floor_plan_code,
        }
    finally:
        conn.close()


def refresh_user_scroll_count(user_id: str) -> int:
    """
    Treat scroll_associations as authoritative for seeker-facing scroll ownership.
    users.scroll_count becomes a cached summary we refresh from associations.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT scroll_id) AS total
                FROM scroll_associations
                WHERE user_id = %s
                """,
                (user_id,)
            )
            row = cur.fetchone()
            total = row["total"] if row else 0

            cur.execute(
                "UPDATE users SET scroll_count = %s WHERE id = %s",
                (total, user_id)
            )

        conn.commit()
    finally:
        conn.close()

    refresh_user_fallback_state(user_id)
    return total


def claim_anonymous_history_into_user(
    anonymous_user_id: str,
    user_id: str,
) -> None:
    """
    Claim only genuinely unowned activity from this browser identity.

    Already-authenticated ownership is immutable here. In particular,
    this routine must never move a scroll association from one user to
    another user.
    """
    anonymous_user_id = _canonical_identity_uuid(
        anonymous_user_id
    )
    user_id = _canonical_identity_uuid(user_id)

    if not anonymous_user_id:
        raise ValueError("anonymous_user_id must be a valid UUID")

    if not user_id:
        raise ValueError("user_id must be a valid UUID")

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oracle_interactions
                SET user_id = %s::uuid
                WHERE anonymous_user_id = %s
                  AND user_id IS NULL
                """,
                (user_id, anonymous_user_id)
            )

            cur.execute(
                """
                DELETE FROM scroll_associations sa
                USING scroll_associations existing
                WHERE sa.anonymous_user_id = %s
                  AND sa.user_id IS NULL
                  AND sa.scroll_id = existing.scroll_id
                  AND existing.user_id = %s::uuid
                  AND sa.id <> existing.id
                """,
                (anonymous_user_id, user_id)
            )

            cur.execute(
                """
                UPDATE scroll_associations
                SET user_id = %s::uuid
                WHERE anonymous_user_id = %s
                  AND user_id IS NULL
                """,
                (user_id, anonymous_user_id)
            )

            cur.execute(
                """
                UPDATE scrolls
                SET
                    user_id = %s::uuid,
                    corpus_layer = CASE
                        WHEN corpus_layer = 'community' THEN 'personal'
                        ELSE corpus_layer
                    END
                WHERE anonymous_user_id = %s
                  AND user_id IS NULL
                """,
                (user_id, anonymous_user_id)
            )

            cur.execute(
                """
                UPDATE library_uploads
                SET user_id = %s::uuid
                WHERE anonymous_user_id = %s
                  AND user_id IS NULL
                """,
                (user_id, anonymous_user_id)
            )

            cur.execute(
                """
                UPDATE ingestion_jobs
                SET user_id = %s::uuid
                WHERE anonymous_user_id = %s
                  AND user_id IS NULL
                """,
                (user_id, anonymous_user_id)
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    refresh_user_scroll_count(user_id)

def get_user_donation_stats(user_id: str) -> dict:
    """
    Preferred authority: donations table.
    Fallback: users.donation_total if donations table/columns are not yet aligned.
    Assumes donations has columns: user_id, amount.
    If your amount column uses a different name, only change that one query.
    """
    conn = get_db_connection()
    try:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS donation_count,
                        COALESCE(SUM(amount), 0) AS money_donated
                    FROM donations
                    WHERE user_id = %s
                    """,
                    (user_id,)
                )
                row = cur.fetchone() or {}
                return {
                    "donation_count": row.get("donation_count", 0) or 0,
                    "money_donated": float(row.get("money_donated", 0) or 0),
                    "donation_source": "donations"
                }
        except Exception:
            conn.rollback()

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(donation_total, 0) AS money_donated
                FROM users
                WHERE id = %s
                """,
                (user_id,)
            )
            row = cur.fetchone() or {}
            return {
                "donation_count": 0,
                "money_donated": float(row.get("money_donated", 0) or 0),
                "donation_source": "users.donation_total_fallback"
            }
    finally:
        conn.close()

def log_admin_action(
    admin_user_id: str,
    action_type: str,
    target_user_id: Optional[str] = None,
    payload: Optional[dict] = None
) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO admin_action_logs (
                    id,
                    admin_user_id,
                    target_user_id,
                    action_type,
                    action_payload
                )
                VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                (
                    str(uuid.uuid4()),
                    admin_user_id,
                    target_user_id,
                    action_type,
                    json.dumps(payload or {})
                )
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to write admin action log: {e}")
    finally:
        conn.close()


def get_admin_user_detail(target_user_id: str) -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    email,
                    display_name,
                    seeker_id,
                    email_verified,
                    created_at,
                    last_login,
                    COALESCE(role, 'user') AS role,
                    COALESCE(plan_code, 'anon') AS stored_plan_code,
                    COALESCE(scroll_count, 0) AS legacy_scroll_count
                FROM users
                WHERE id = %s
                """,
                (target_user_id,)
            )
            user_row = cur.fetchone()

            if not user_row:
                raise HTTPException(status_code=404, detail="User not found.")

            cur.execute(
                """
                SELECT COUNT(DISTINCT scroll_id) AS total
                FROM scroll_associations
                WHERE user_id = %s
                """,
                (target_user_id,)
            )
            scroll_row = cur.fetchone()
            authoritative_scroll_count = scroll_row["total"] if scroll_row else 0
    finally:
        conn.close()

    donation_stats = get_user_donation_stats(target_user_id)
    entitlement = get_user_entitlement_snapshot(target_user_id)
    support = build_support_status_payload(entitlement)
    usage_window_start = get_effective_usage_window_start(entitlement)
    current_usage = get_oracle_usage_counts(
        user_id=target_user_id,
        window_start=usage_window_start
    )
    lifetime_usage = get_oracle_usage_counts(user_id=target_user_id)

    effective_plan_code = entitlement["effective_plan_code"]
    unlimited_questions = plan_has_unlimited_questions(effective_plan_code)
    question_limit = None if unlimited_questions else PLAN_LIMITS.get(effective_plan_code, PLAN_LIMITS["anon"])
    question_display = get_question_display(
        effective_plan_code,
        current_usage["questions_used"],
        question_limit
    )

    combined_title = compute_combined_title(
        authoritative_scroll_count,
        effective_plan_code,
        authenticated=True
    )

    return {
        "id": user_row["id"],
        "email": user_row["email"],
        "display_name": user_row["display_name"],
        "seeker_id": user_row["seeker_id"],
        "email_verified": user_row["email_verified"],
        "created_at": serialize_dt(user_row.get("created_at")),
        "last_login": serialize_dt(user_row.get("last_login")),
        "role": user_row["role"],
        "title": combined_title,
        "combined_title": combined_title,
        "scrolls": {
            "authoritative_scroll_count": authoritative_scroll_count,
            "legacy_scroll_count": user_row.get("legacy_scroll_count", 0)
        },
        "donations": donation_stats,
        "support": support,
        "voice_access": get_voice_policy(effective_plan_code),
        "entitlement": {
            "raw_plan_code": entitlement["raw_plan_code"],
            "effective_plan_code": entitlement["effective_plan_code"],
            "entitlement_status": entitlement["entitlement_status"],
            "subscription_started_at": serialize_dt(entitlement["subscription_started_at"]),
            "current_period_started_at": serialize_dt(entitlement["current_period_started_at"]),
            "subscription_renews_at": serialize_dt(entitlement["subscription_renews_at"]),
            "subscription_expires_at": serialize_dt(entitlement["subscription_expires_at"]),
            "cancel_at_period_end": entitlement["cancel_at_period_end"],
            "is_entitled": entitlement["is_entitled"],
            "downgraded_for_access": entitlement["downgraded_for_access"],
            "highest_paid_plan_ever": entitlement["highest_paid_plan_ever"],
            "last_paid_plan_code": entitlement["last_paid_plan_code"],
            "donor_floor_plan_code": entitlement["donor_floor_plan_code"],
            "scroll_floor_plan_code": entitlement["scroll_floor_plan_code"],
            "fallback_floor_plan_code": entitlement["fallback_floor_plan_code"],
            "renewal_offer_plan_code": entitlement["renewal_offer_plan_code"],
            "last_support_mode": entitlement["last_support_mode"],
            "last_support_ended_at": serialize_dt(entitlement["last_support_ended_at"])
        },
        "usage": {
            "current_period_questions_used": current_usage["questions_used"],
            "lifetime_questions_used": lifetime_usage["questions_used"],
            "question_limit": question_limit,
            "question_limit_display": question_display["question_limit_display"],
            "questions_remaining_display": question_display["questions_remaining_display"],
            "is_unlimited_questions": question_display["is_unlimited_questions"],
            "usage_window_started_at": serialize_dt(usage_window_start),
            "current_period_mode_counts": current_usage["mode_counts"],
            "lifetime_mode_counts": lifetime_usage["mode_counts"]
        }
    }


def get_admin_reporting_overview(days: int = 30) -> dict:
    window_start = datetime.datetime.now(timezone.utc) - datetime.timedelta(days=days)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_users,
                    COUNT(*) FILTER (WHERE email_verified = true) AS verified_users,
                    COUNT(*) FILTER (WHERE created_at >= %s) AS users_created_in_window,
                    COUNT(*) FILTER (WHERE last_login >= %s) AS users_logged_in_in_window
                FROM users
                """,
                (window_start, window_start)
            )
            user_summary = cur.fetchone() or {}

            cur.execute(
                """
                SELECT COALESCE(role, 'user') AS role, COUNT(*) AS total
                FROM users
                GROUP BY COALESCE(role, 'user')
                ORDER BY total DESC, role ASC
                """
            )
            role_rows = cur.fetchall()

            cur.execute(
                """
                SELECT COALESCE(entitlement_status, 'none') AS entitlement_status, COUNT(*) AS total
                FROM users
                GROUP BY COALESCE(entitlement_status, 'none')
                ORDER BY total DESC, entitlement_status ASC
                """
            )
            entitlement_rows = cur.fetchall()

            cur.execute(
                """
                SELECT COALESCE(plan_code, 'anon') AS plan_code, COUNT(*) AS total
                FROM users
                GROUP BY COALESCE(plan_code, 'anon')
                ORDER BY total DESC, plan_code ASC
                """
            )
            plan_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_questions,
                    COUNT(*) FILTER (WHERE user_id IS NOT NULL) AS authenticated_questions,
                    COUNT(*) FILTER (WHERE user_id IS NULL) AS anonymous_questions,
                    COUNT(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL) AS distinct_authenticated_users,
                    COUNT(DISTINCT session_id) FILTER (WHERE user_id IS NULL) AS distinct_anonymous_sessions
                FROM oracle_interactions
                WHERE created_at >= %s
                """,
                (window_start,)
            )
            question_summary = cur.fetchone() or {}

            cur.execute(
                """
                SELECT mode, COUNT(*) AS total
                FROM oracle_interactions
                WHERE created_at >= %s
                GROUP BY mode
                ORDER BY total DESC, mode ASC
                """,
                (window_start,)
            )
            mode_rows = cur.fetchall()

            try:
                cur.execute(
                    """
                    SELECT COUNT(*) AS total_admin_actions
                    FROM admin_action_logs
                    WHERE created_at >= %s
                    """,
                    (window_start,)
                )
                admin_action_summary = cur.fetchone() or {}
            except Exception:
                conn.rollback()
                admin_action_summary = {"total_admin_actions": None}
    finally:
        conn.close()

    return {
        "window_days": days,
        "window_start": serialize_dt(window_start),
        "users": {
            "total_users": user_summary.get("total_users", 0),
            "verified_users": user_summary.get("verified_users", 0),
            "users_created_in_window": user_summary.get("users_created_in_window", 0),
            "users_logged_in_in_window": user_summary.get("users_logged_in_in_window", 0),
            "roles": role_rows,
            "entitlement_statuses": entitlement_rows,
            "stored_plan_codes": plan_rows
        },
        "oracle": {
            "total_questions": question_summary.get("total_questions", 0),
            "authenticated_questions": question_summary.get("authenticated_questions", 0),
            "anonymous_questions": question_summary.get("anonymous_questions", 0),
            "distinct_authenticated_users": question_summary.get("distinct_authenticated_users", 0),
            "distinct_anonymous_sessions": question_summary.get("distinct_anonymous_sessions", 0),
            "mode_counts": mode_rows
        },
        "admin": {
            "total_admin_actions": admin_action_summary.get("total_admin_actions")
        }
    }


def _admin_report_value(value):
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return serialize_dt(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if value.__class__.__name__ == "Decimal":
        number = float(value)
        if number.is_integer():
            return int(number)
        return round(number, 4)
    return value


def _admin_report_row(row: dict) -> dict:
    return {
        key: _admin_report_value(value)
        for key, value in dict(row or {}).items()
    }


def _admin_report_rows(rows) -> list[dict]:
    return [_admin_report_row(row) for row in rows or []]


def get_admin_usage_summary(days: int = 30) -> dict:
    """
    Phase 10.6 reporting foundation.

    Summarizes Phase 10.5 usage tables for admin reporting.
    No pricing math yet; token and latency visibility come first.
    """
    window_start = datetime.datetime.now(timezone.utc) - datetime.timedelta(days=days)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_events,
                    COUNT(*) FILTER (WHERE user_id IS NOT NULL) AS registered_events,
                    COUNT(*) FILTER (WHERE user_id IS NULL) AS anonymous_events,
                    COUNT(*) FILTER (WHERE input_mode = 'text') AS text_events,
                    COUNT(*) FILTER (WHERE input_mode = 'voice') AS voice_events,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(estimated_input_tokens), 0) AS estimated_input_tokens,
                    COALESCE(SUM(estimated_output_tokens), 0) AS estimated_output_tokens,
                    COALESCE(SUM(estimated_total_tokens), 0) AS estimated_total_tokens,
                    AVG(final_model_ms) AS avg_final_model_ms,
                    AVG(total_ms) AS avg_total_ms,
                    MAX(total_ms) AS max_total_ms,
                    COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
                FROM oracle_usage_events
                WHERE created_at >= %s
                """,
                (window_start,)
            )
            oracle_summary = cur.fetchone() or {}

            cur.execute(
                """
                SELECT
                    COALESCE(plan_code, 'unknown') AS plan_code,
                    COUNT(*) AS total_events,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    AVG(total_ms) AS avg_total_ms
                FROM oracle_usage_events
                WHERE created_at >= %s
                GROUP BY COALESCE(plan_code, 'unknown')
                ORDER BY total_events DESC, plan_code ASC
                """,
                (window_start,)
            )
            by_plan = cur.fetchall()

            cur.execute(
                """
                SELECT
                    COALESCE(input_mode, 'unknown') AS input_mode,
                    COUNT(*) AS total_events,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    AVG(total_ms) AS avg_total_ms
                FROM oracle_usage_events
                WHERE created_at >= %s
                GROUP BY COALESCE(input_mode, 'unknown')
                ORDER BY total_events DESC, input_mode ASC
                """,
                (window_start,)
            )
            by_input_mode = cur.fetchall()

            cur.execute(
                """
                SELECT
                    COALESCE(deity, 'unknown') AS deity,
                    COUNT(*) AS total_events,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    AVG(total_ms) AS avg_total_ms
                FROM oracle_usage_events
                WHERE created_at >= %s
                GROUP BY COALESCE(deity, 'unknown')
                ORDER BY total_events DESC, deity ASC
                """,
                (window_start,)
            )
            by_deity = cur.fetchall()

            cur.execute(
                """
                SELECT
                    COALESCE(provider, 'unknown') AS provider,
                    COALESCE(model, 'unknown') AS model,
                    COUNT(*) AS total_events,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    AVG(total_ms) AS avg_total_ms
                FROM oracle_usage_events
                WHERE created_at >= %s
                GROUP BY COALESCE(provider, 'unknown'), COALESCE(model, 'unknown')
                ORDER BY total_tokens DESC, total_events DESC
                """,
                (window_start,)
            )
            by_provider_model = cur.fetchall()

            cur.execute(
                """
                SELECT
                    created_at,
                    user_id::text AS user_id,
                    anonymous_user_id,
                    session_id::text AS session_id,
                    plan_code,
                    input_mode,
                    deity,
                    provider,
                    model,
                    total_tokens,
                    final_model_ms,
                    total_ms
                FROM oracle_usage_events
                WHERE created_at >= %s
                ORDER BY total_ms DESC NULLS LAST
                LIMIT 10
                """,
                (window_start,)
            )
            slowest_oracle_events = cur.fetchall()

            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_events,
                    COUNT(*) FILTER (WHERE stage = 'transcribe') AS transcribe_events,
                    COUNT(*) FILTER (WHERE stage = 'tts') AS tts_events,
                    COUNT(*) FILTER (WHERE status = 'ok') AS ok_events,
                    COUNT(*) FILTER (WHERE status <> 'ok') AS non_ok_events,
                    AVG(transcribe_ms) AS avg_transcribe_ms,
                    AVG(tts_ms) AS avg_tts_ms,
                    AVG(total_ms) AS avg_total_ms,
                    MAX(total_ms) AS max_total_ms,
                    COUNT(*) FILTER (WHERE audio_url_present = true) AS audio_url_events,
                    COALESCE(SUM(estimated_tts_cost_usd), 0) AS estimated_tts_cost_usd
                FROM voice_usage_events
                WHERE created_at >= %s
                """,
                (window_start,)
            )
            voice_summary = cur.fetchone() or {}

            cur.execute(
                """
                SELECT
                    COALESCE(stage, 'unknown') AS stage,
                    COALESCE(status, 'unknown') AS status,
                    COUNT(*) AS total_events,
                    AVG(transcribe_ms) AS avg_transcribe_ms,
                    AVG(tts_ms) AS avg_tts_ms,
                    AVG(total_ms) AS avg_total_ms
                FROM voice_usage_events
                WHERE created_at >= %s
                GROUP BY COALESCE(stage, 'unknown'), COALESCE(status, 'unknown')
                ORDER BY total_events DESC, stage ASC, status ASC
                """,
                (window_start,)
            )
            voice_by_stage_status = cur.fetchall()

            cur.execute(
                """
                SELECT
                    created_at,
                    user_id::text AS user_id,
                    anonymous_user_id,
                    session_id::text AS session_id,
                    plan_code,
                    input_mode,
                    deity,
                    stage,
                    status,
                    transcribe_ms,
                    tts_ms,
                    total_ms,
                    transcript_chars,
                    answer_chars,
                    audio_url_present,
                    tts_provider,
                    tts_model,
                    tts_voice,
                    estimated_tts_cost_usd
                FROM voice_usage_events
                WHERE created_at >= %s
                ORDER BY total_ms DESC NULLS LAST
                LIMIT 10
                """,
                (window_start,)
            )
            slowest_voice_events = cur.fetchall()

    finally:
        conn.close()

    return {
        "window_days": days,
        "window_start": serialize_dt(window_start),
        "oracle": {
            "summary": _admin_report_row(oracle_summary),
            "by_plan": _admin_report_rows(by_plan),
            "by_input_mode": _admin_report_rows(by_input_mode),
            "by_deity": _admin_report_rows(by_deity),
            "by_provider_model": _admin_report_rows(by_provider_model),
            "slowest_events": _admin_report_rows(slowest_oracle_events),
        },
        "voice": {
            "summary": _admin_report_row(voice_summary),
            "by_stage_status": _admin_report_rows(voice_by_stage_status),
            "slowest_events": _admin_report_rows(slowest_voice_events),
        },
    }


def get_admin_user_usage_report(user_id: str, days: int = 30) -> dict:
    """
    Phase 10.6 per-user usage report.

    This is read-only and complements the existing user detail endpoint.
    """
    window_start = datetime.datetime.now(timezone.utc) - datetime.timedelta(days=days)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id::text AS id,
                    email,
                    display_name,
                    seeker_id,
                    COALESCE(role, 'user') AS role,
                    COALESCE(plan_code, 'anon') AS stored_plan_code
                FROM users
                WHERE id = %s
                """,
                (user_id,)
            )
            user_row = cur.fetchone()

            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_events,
                    COUNT(*) FILTER (WHERE input_mode = 'text') AS text_events,
                    COUNT(*) FILTER (WHERE input_mode = 'voice') AS voice_events,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    AVG(final_model_ms) AS avg_final_model_ms,
                    AVG(total_ms) AS avg_total_ms,
                    MAX(total_ms) AS max_total_ms,
                    COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
                FROM oracle_usage_events
                WHERE user_id = %s
                  AND created_at >= %s
                """,
                (user_id, window_start)
            )
            oracle_summary = cur.fetchone() or {}

            cur.execute(
                """
                SELECT
                    COALESCE(deity, 'unknown') AS deity,
                    COUNT(*) AS total_events,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    AVG(total_ms) AS avg_total_ms
                FROM oracle_usage_events
                WHERE user_id = %s
                  AND created_at >= %s
                GROUP BY COALESCE(deity, 'unknown')
                ORDER BY total_events DESC, deity ASC
                """,
                (user_id, window_start)
            )
            oracle_by_deity = cur.fetchall()

            cur.execute(
                """
                SELECT
                    COALESCE(provider, 'unknown') AS provider,
                    COALESCE(model, 'unknown') AS model,
                    COUNT(*) AS total_events,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    AVG(total_ms) AS avg_total_ms
                FROM oracle_usage_events
                WHERE user_id = %s
                  AND created_at >= %s
                GROUP BY COALESCE(provider, 'unknown'), COALESCE(model, 'unknown')
                ORDER BY total_tokens DESC, total_events DESC
                """,
                (user_id, window_start)
            )
            oracle_by_provider_model = cur.fetchall()

            cur.execute(
                """
                SELECT
                    created_at,
                    plan_code,
                    input_mode,
                    deity,
                    provider,
                    model,
                    total_tokens,
                    final_model_ms,
                    total_ms
                FROM oracle_usage_events
                WHERE user_id = %s
                  AND created_at >= %s
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (user_id, window_start)
            )
            recent_oracle_events = cur.fetchall()

            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_events,
                    COUNT(*) FILTER (WHERE stage = 'transcribe') AS transcribe_events,
                    COUNT(*) FILTER (WHERE stage = 'tts') AS tts_events,
                    COUNT(*) FILTER (WHERE status = 'ok') AS ok_events,
                    COUNT(*) FILTER (WHERE status <> 'ok') AS non_ok_events,
                    AVG(transcribe_ms) AS avg_transcribe_ms,
                    AVG(tts_ms) AS avg_tts_ms,
                    AVG(total_ms) AS avg_total_ms,
                    MAX(total_ms) AS max_total_ms,
                    COUNT(*) FILTER (WHERE audio_url_present = true) AS audio_url_events,
                    COALESCE(SUM(estimated_tts_cost_usd), 0) AS estimated_tts_cost_usd
                FROM voice_usage_events
                WHERE user_id = %s
                  AND created_at >= %s
                """,
                (user_id, window_start)
            )
            voice_summary = cur.fetchone() or {}

            cur.execute(
                """
                SELECT
                    created_at,
                    plan_code,
                    input_mode,
                    deity,
                    stage,
                    status,
                    transcribe_ms,
                    tts_ms,
                    total_ms,
                    transcript_chars,
                    answer_chars,
                    audio_url_present,
                    tts_provider,
                    tts_model,
                    tts_voice,
                    estimated_tts_cost_usd
                FROM voice_usage_events
                WHERE user_id = %s
                  AND created_at >= %s
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (user_id, window_start)
            )
            recent_voice_events = cur.fetchall()

    finally:
        conn.close()

    return {
        "window_days": days,
        "window_start": serialize_dt(window_start),
        "user": _admin_report_row(user_row or {"id": user_id, "found": False}),
        "oracle": {
            "summary": _admin_report_row(oracle_summary),
            "by_deity": _admin_report_rows(oracle_by_deity),
            "by_provider_model": _admin_report_rows(oracle_by_provider_model),
            "recent_events": _admin_report_rows(recent_oracle_events),
        },
        "voice": {
            "summary": _admin_report_row(voice_summary),
            "recent_events": _admin_report_rows(recent_voice_events),
        },
    }



def build_authenticated_me_response(user: dict, anonymous_user_id: str) -> dict:
    donation_stats = get_user_donation_stats(user["user_id"])
    entitlement = get_user_entitlement_snapshot(user["user_id"])
    support = build_support_status_payload(entitlement)
    usage_window_start = get_effective_usage_window_start(entitlement)
    usage = get_oracle_usage_counts(
        user_id=user["user_id"],
        window_start=usage_window_start
    )

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    email,
                    display_name,
                    email_verified,
                    preferred_oracle,
                    last_login,
                    COALESCE(plan_code, 'anon') AS stored_plan_code,
                    COALESCE(scroll_count, 0) AS legacy_scroll_count
                FROM users
                WHERE id = %s
                """,
                (user["user_id"],)
            )
            user_row = cur.fetchone() or {}

            cur.execute(
                """
                SELECT COUNT(DISTINCT scroll_id) AS total
                FROM scroll_associations
                WHERE user_id = %s
                """,
                (user["user_id"],)
            )
            scroll_row = cur.fetchone()
            authoritative_scroll_count = scroll_row["total"] if scroll_row else 0
    finally:
        conn.close()

    plan_code = entitlement["effective_plan_code"]
    questions_used = usage["questions_used"]
    unlimited_questions = plan_has_unlimited_questions(plan_code)
    question_limit = None if unlimited_questions else PLAN_LIMITS.get(plan_code, PLAN_LIMITS["anon"])
    questions_remaining = None if unlimited_questions else max(question_limit - questions_used, 0)
    question_display = get_question_display(plan_code, questions_used, question_limit)
    mode_counts = usage["mode_counts"]

    combined_title = compute_combined_title(
        authoritative_scroll_count,
        plan_code,
        authenticated=True
    )

    return {
        "authenticated": True,
        "display_name": user_row.get("display_name"),
        "email": user_row.get("email"),
        "email_verified": user_row.get("email_verified"),
        "role": normalize_user_role(user.get("role")),
        "preferred_oracle": user_row.get("preferred_oracle"),
        "last_login": user_row.get("last_login").isoformat() if user_row.get("last_login") else None,
        "seeker_id": user.get("seeker_id"),
        "anonymous_user_id": anonymous_user_id,
        "scroll_count": authoritative_scroll_count,
        "scrolls_donated": authoritative_scroll_count,
        "legacy_scroll_count": user_row.get("legacy_scroll_count", 0),
        "plan_code": plan_code,
        "stored_plan_code": entitlement["raw_plan_code"],
        "current_access_plan_code": support["current_access_plan_code"],
        "current_access_label": support["current_access_label"],
        "stored_plan_label": support["stored_plan_label"],
        "title": combined_title,
        "combined_title": combined_title,
        "money_donated": donation_stats["money_donated"],
        "donation_count": donation_stats["donation_count"],
        "donation_source": donation_stats["donation_source"],
        "memory_depth": get_memory_depth(plan_code),
        "support_message": support["message"],
        "renewal_message": support["renewal_message"],
        "support": support,
        "voice_access": get_voice_policy(plan_code),
        "entitlement": {
            "status": entitlement["entitlement_status"],
            "raw_plan_code": entitlement["raw_plan_code"],
            "effective_plan_code": entitlement["effective_plan_code"],
            "subscription_started_at": serialize_dt(entitlement["subscription_started_at"]),
            "current_period_started_at": serialize_dt(entitlement["current_period_started_at"]),
            "renewal_date": serialize_dt(entitlement["subscription_renews_at"]),
            "expiry_date": serialize_dt(entitlement["subscription_expires_at"]),
            "cancel_at_period_end": entitlement["cancel_at_period_end"],
            "is_entitled": entitlement["is_entitled"],
            "downgraded_for_access": entitlement["downgraded_for_access"],
            "highest_paid_plan_ever": entitlement["highest_paid_plan_ever"],
            "last_paid_plan_code": entitlement["last_paid_plan_code"],
            "donor_floor_plan_code": entitlement["donor_floor_plan_code"],
            "scroll_floor_plan_code": entitlement["scroll_floor_plan_code"],
            "fallback_floor_plan_code": entitlement["fallback_floor_plan_code"],
            "renewal_offer_plan_code": entitlement["renewal_offer_plan_code"],
            "last_support_mode": entitlement["last_support_mode"],
            "last_support_ended_at": serialize_dt(entitlement["last_support_ended_at"]),
        },
        "usage": {
            "questions_asked": questions_used,
            "questions_used": questions_used,
            "question_limit": question_limit,
            "questions_remaining": questions_remaining,
            "question_limit_display": question_display["question_limit_display"],
            "questions_remaining_display": question_display["questions_remaining_display"],
            "is_unlimited_questions": question_display["is_unlimited_questions"],
            "usage_window_started_at": serialize_dt(usage_window_start),
            "hathor_questions": mode_counts.get("Hathor", 0),
            "moses_questions": mode_counts.get("Moses", 0)
        }
    }


def build_anonymous_me_response(
    anonymous_user_id: str,
) -> dict:
    usage = get_anonymous_oracle_usage_counts(
        anonymous_user_id
    )
    questions_used = usage["questions_used"]
    mode_counts = usage["mode_counts"]

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT scroll_id) AS total
                FROM scroll_associations
                WHERE anonymous_user_id = %s
                  AND user_id IS NULL
                """,
                (anonymous_user_id,)
            )

            scroll_row = cur.fetchone()
            anonymous_scroll_count = (
                scroll_row["total"] if scroll_row else 0
            )
    finally:
        conn.close()

    question_limit = PLAN_LIMITS["anon"]
    question_display = get_question_display(
        "anon",
        questions_used,
        question_limit,
    )

    combined_title = compute_combined_title(
        anonymous_scroll_count,
        "anon",
        authenticated=False
    )

    support = build_anonymous_support_status_payload()
    continuity_nudges = build_claim_nudges(
        anonymous_scroll_count
    )

    return {
        "authenticated": False,
        "display_name": None,
        "email": None,
        "email_verified": False,
        "preferred_oracle": None,
        "last_login": None,
        "seeker_id": None,
        "anonymous_user_id": anonymous_user_id,
        "scroll_count": anonymous_scroll_count,
        "scrolls_donated": anonymous_scroll_count,
        "plan_code": None,
        "stored_plan_code": None,
        "current_access_plan_code": "anon",
        "current_access_label": "Anon",
        "stored_plan_label": None,
        "title": combined_title,
        "combined_title": combined_title,
        "money_donated": 0,
        "donation_count": 0,
        "donation_source": "none",
        "memory_depth": 1,
        "support_message": support["message"],
        "renewal_message": support["renewal_message"],
        "support": support,
        "voice_access": get_voice_policy("anon"),
        "continuity_nudges": continuity_nudges,
        "anonymous_upload_limit": ANONYMOUS_UPLOAD_LIMIT,
        "claim_required": (
            anonymous_scroll_count >= ANONYMOUS_UPLOAD_LIMIT
        ),
        "entitlement": {
            "status": "none",
            "raw_plan_code": "anon",
            "effective_plan_code": "anon",
            "subscription_started_at": None,
            "current_period_started_at": None,
            "renewal_date": None,
            "expiry_date": None,
            "cancel_at_period_end": False,
            "is_entitled": False,
            "downgraded_for_access": False,
            "highest_paid_plan_ever": None,
            "last_paid_plan_code": None,
            "donor_floor_plan_code": None,
            "scroll_floor_plan_code": None,
            "fallback_floor_plan_code": "anon",
            "renewal_offer_plan_code": None,
            "last_support_mode": None,
            "last_support_ended_at": None,
        },
        "usage": {
            "questions_asked": questions_used,
            "questions_used": questions_used,
            "question_limit": question_limit,
            "questions_remaining": max(
                question_limit - questions_used,
                0,
            ),
            "question_limit_display": (
                question_display["question_limit_display"]
            ),
            "questions_remaining_display": (
                question_display["questions_remaining_display"]
            ),
            "is_unlimited_questions": (
                question_display["is_unlimited_questions"]
            ),
            "hathor_questions": mode_counts.get("Hathor", 0),
            "moses_questions": mode_counts.get("Moses", 0),
        },
    }

@app.get("/audio/{filename}")
def get_audio_file(filename: str):
    if "/" in filename or "\\ " in filename or not filename.endswith(".mp3"):
        raise HTTPException(status_code=404, detail="Audio file not found.")

    audio_path = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="Audio file not found.")

    return FileResponse(audio_path, media_type="audio/mpeg")


def voice_stage_ms(start, end):
    if not start or not end:
        return "-"
    return round((end - start).total_seconds() * 1000, 2)


@app.post("/voice/transcribe")
async def voice_transcribe_endpoint(
    request: Request,
    file: UploadFile = File(...),
    voice: str = Form("Hathor")
):
    started_at = datetime.datetime.now()
    usage_context = get_voice_usage_context(request, voice)
    try:
        file_bytes = await file.read()
        transcribe_started_at = datetime.datetime.now()
        transcription_result = transcribe_audio_with_metrics(
            file_bytes,
            filename=file.filename or "voice_input.m4a",
            content_type=file.content_type or "audio/mp4",
            voice=voice,
        )
        transcript = (transcription_result.get("transcript") or "").strip()
        transcribe_finished_at = datetime.datetime.now()
        transcribe_provider = transcription_result.get("provider", "unknown")
        transcribe_model = transcription_result.get("model", "unknown")
        transcribe_api_ms = transcription_result.get("api_ms", "-")
        transcribe_audio_bytes = transcription_result.get("audio_bytes", len(file_bytes or b""))
        transcribe_attempts = transcription_result.get("attempts", [])

        if not transcript:
            transcribe_ms = voice_stage_ms(transcribe_started_at, transcribe_finished_at)
            total_ms = voice_stage_ms(started_at, datetime.datetime.now())
            logger.info(
                "VOICE_TRANSCRIBE_STAGE status=failed voice=%s provider=%s model=%s transcribe_ms=%s api_ms=%s total_ms=%s audio_bytes=%s transcript_chars=0",
                voice,
                transcribe_provider,
                transcribe_model,
                transcribe_ms,
                transcribe_api_ms,
                total_ms,
                transcribe_audio_bytes,
            )
            record_voice_usage_event(
                **usage_context,
                input_mode="voice",
                deity=voice,
                stage="transcribe",
                status="failed",
                transcribe_ms=transcribe_ms,
                total_ms=total_ms,
                transcript_chars=0,
                metadata_json={
                    "phase": "11.x",
                    "event_source": "voice_transcribe_endpoint",
                    "provider": transcribe_provider,
                    "model": transcribe_model,
                    "api_ms": transcribe_api_ms,
                    "audio_bytes": transcribe_audio_bytes,
                    "attempts": transcribe_attempts,
                }
            )
            return JSONResponse(
                content={"error": "Whisper could not transcribe."},
                status_code=422
            )

        transcribe_ms = voice_stage_ms(transcribe_started_at, transcribe_finished_at)
        total_ms = voice_stage_ms(started_at, datetime.datetime.now())
        logger.info(
            "VOICE_TRANSCRIBE_STAGE status=ok voice=%s provider=%s model=%s transcribe_ms=%s api_ms=%s total_ms=%s audio_bytes=%s transcript_chars=%s",
            voice,
            transcribe_provider,
            transcribe_model,
            transcribe_ms,
            transcribe_api_ms,
            total_ms,
            transcribe_audio_bytes,
            len(transcript or ""),
        )
        record_voice_usage_event(
            **usage_context,
            input_mode="voice",
            deity=voice,
            stage="transcribe",
            status="ok",
            transcribe_ms=transcribe_ms,
            total_ms=total_ms,
            transcript_chars=len(transcript or ""),
            metadata_json={
                "phase": "11.x",
                "event_source": "voice_transcribe_endpoint",
                "provider": transcribe_provider,
                "model": transcribe_model,
                "api_ms": transcribe_api_ms,
                "audio_bytes": transcribe_audio_bytes,
                "attempts": transcribe_attempts,
            }
        )

        return {
            "question": transcript,
            "transcript": transcript
        }

    except Exception:
        logger.exception("Voice transcribe endpoint failed")
        total_ms = voice_stage_ms(started_at, datetime.datetime.now())
        logger.info(
            "VOICE_TRANSCRIBE_STAGE status=error voice=%s total_ms=%s transcript_chars=0",
            voice,
            total_ms
        )
        record_voice_usage_event(
            **usage_context,
            input_mode="voice",
            deity=voice,
            stage="transcribe",
            status="error",
            total_ms=total_ms,
            transcript_chars=0,
            metadata_json={
                "phase": "10.5",
                "event_source": "voice_transcribe_endpoint",
            }
        )
        return JSONResponse(
            content={"error": "Voice transcription failed."},
            status_code=500
        )


def is_likely_no_speech_transcript(value: str) -> bool:
    """
    Reject known no-speech/STT artifact transcripts before /voice/ask reaches ask_oracle.

    Keep this intentionally narrow. A short real question like "why", "help",
    "love", or "I hurt" must still be allowed through.
    """
    text = (value or "").strip()
    if not text:
        return True

    compact = re.sub(r"\s+", " ", text.lower()).strip()
    normalized = compact.strip(" \t\r\n.,!?;:\"'`*_~#()[]{}")

    if not normalized:
        return True

    artifact_phrases = {
        "token",
        "tokens",
        "1.5",
        "1.5%",
        "%",
        "percent",
        "percentage",
        "uh",
        "um",
        "umm",
        "uhh",
        "hm",
        "hmm",
        "mmm",
    }

    if normalized in artifact_phrases:
        return True

    # Numeric-only or percent-only fragments are common no-speech artifacts.
    if re.fullmatch(r"(\d+(\.\d+)?\s*%?|%)(\s+(\d+(\.\d+)?\s*%?|%))*", normalized):
        return True

    letters = sum(1 for ch in normalized if ch.isalpha())
    digits = sum(1 for ch in normalized if ch.isdigit())
    words = re.findall(r"[a-zA-Z]+", normalized)

    if digits > 0 and letters < 3 and len(normalized) <= 16:
        return True

    if "%" in normalized and letters < 6:
        return True

    filler_words = {"uh", "um", "umm", "uhh", "hm", "hmm", "mmm"}
    if words and len(words) <= 3 and all(word in filler_words for word in words):
        return True

    return False


@app.post("/oracle/inference/abandon")
async def oracle_inference_abandon_endpoint(
    request: Request,
    payload: dict,
):
    """
    Retire one server-authorized prepared inference before fallback.

    This is intentionally limited to prepared state so it cannot race a
    completion that has already been claimed.
    """
    interaction_id = str(payload.get("interaction_id") or "").strip()
    fallback_code = str(payload.get("fallback_code") or "").strip()

    if not interaction_id:
        return JSONResponse(
            status_code=400,
            content={"error": "interaction_id is required"},
        )

    try:
        interaction_id = str(uuid.UUID(interaction_id))
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"error": "interaction_id must be a valid UUID"},
        )

    if fallback_code not in IOS_PCC_POST_PREPARE_FALLBACK_CODES:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid PCC fallback code"},
        )

    session_id = get_or_create_session_id(request)
    user = get_current_user(request)
    user_id = user["user_id"] if user else None

    abandoned = abandon_pending_oracle_inference(
        interaction_id,
        session_id=str(session_id),
        user_id=str(user_id) if user_id else None,
        fallback_code=fallback_code,
    )

    if abandoned:
        return {
            "interaction_id": interaction_id,
            "status": "abandoned",
        }

    # Make retries safe when the same bound turn was already expired.
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status
                FROM oracle_pending_inferences
                WHERE id = %s::uuid
                  AND session_id = %s::uuid
                  AND user_id IS NOT DISTINCT FROM %s::uuid
                  AND prepared_state->>'abandoned' = 'true'
                  AND prepared_state->>'fallback_code' = %s
                LIMIT 1
                """,
                (
                    interaction_id,
                    str(session_id),
                    str(user_id) if user_id else None,
                    fallback_code,
                ),
            )
            existing = cur.fetchone()
    finally:
        conn.close()

    if existing and existing.get("status") == "expired":
        return {
            "interaction_id": interaction_id,
            "status": "abandoned",
            "replayed": True,
        }

    return JSONResponse(
        status_code=409,
        content={
            "error": "Oracle inference is unavailable for abandonment"
        },
    )


@app.post("/oracle/inference/prepare")
async def oracle_inference_prepare_endpoint(
    request: Request,
    payload: dict,
):
    """
    Prepare one God Incorporated-authorized Oracle inference for
    external/device execution without performing provider inference here.
    """
    input_mode = (payload.get("input_mode") or "text").strip().lower()
    if input_mode not in {"text", "voice"}:
        return JSONResponse(
            status_code=400,
            content={"error": "input_mode must be text or voice"},
        )

    execution_target = (
        str(payload.get("execution_target") or "")
        .strip()
        .lower()
    )
    if execution_target not in DEVICE_EXECUTION_PROFILES:
        return JSONResponse(
            status_code=400,
            content={"error": "unsupported execution_target"},
        )

    request.state.oracle_input_mode = input_mode
    request.state.oracle_execution_mode = "device_prepare"
    request.state.oracle_execution_target = execution_target

    oracle_payload_data = dict(payload)
    oracle_payload_data.pop("input_mode", None)
    oracle_payload_data.pop("execution_target", None)
    oracle_payload_data.pop("pcc_fallback_code", None)
    oracle_payload_data.pop("pcc_abandoned_interaction_id", None)

    oracle_payload = QuestionInput(**oracle_payload_data)
    return await ask_oracle(request, oracle_payload)


@app.post("/oracle/inference/complete")
async def oracle_inference_complete_endpoint(
    request: Request,
    payload: dict,
):
    """
    Complete one server-authorized split-phase Oracle inference.

    The pending UUID remains the authoritative interaction identity.
    Device inference returns only the result; God Incorporated retains
    ownership of identity, memory, finalization, and persistence.
    """
    interaction_id = str(payload.get("interaction_id") or "").strip()
    answer = str(payload.get("answer") or "").strip()

    if not interaction_id:
        return JSONResponse(
            status_code=400,
            content={"error": "interaction_id is required"},
        )

    try:
        interaction_id = str(uuid.UUID(interaction_id))
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"error": "interaction_id must be a valid UUID"},
        )

    if not answer:
        return JSONResponse(
            status_code=400,
            content={"error": "answer is required"},
        )

    session_id = get_or_create_session_id(request)
    user = get_current_user(request)
    user_id = user["user_id"] if user else None

    claimed = claim_pending_oracle_inference(
        interaction_id,
        session_id=str(session_id),
        user_id=str(user_id) if user_id else None,
    )

    if not claimed:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT question_text, response_text
                    FROM oracle_interactions
                    WHERE id = %s::uuid
                      AND session_id = %s::uuid
                      AND user_id IS NOT DISTINCT FROM %s::uuid
                    LIMIT 1
                    """,
                    (
                        interaction_id,
                        str(session_id),
                        str(user_id) if user_id else None,
                    ),
                )
                existing = cur.fetchone()
        finally:
            conn.close()

        if existing:
            try:
                complete_pending_oracle_inference(
                    interaction_id,
                    session_id=str(session_id),
                    user_id=str(user_id) if user_id else None,
                )
            except Exception as exc:
                logger.warning(
                    "ORACLE_PENDING_INFERENCE_REPLAY_HEAL_FAILED "
                    "pending_id=%s error=%s",
                    interaction_id,
                    exc,
                )

            return {
                "question": existing["question_text"],
                "answer": existing["response_text"],
                "replayed": True,
            }

        return JSONResponse(
            status_code=409,
            content={
                "error": "Oracle inference is unavailable for completion"
            },
        )

    prepared_state = claimed.get("prepared_state") or {}
    if isinstance(prepared_state, str):
        prepared_state = json.loads(prepared_state)

    finalization_state = dict(
        prepared_state.get("finalization_state") or {}
    )

    if not finalization_state:
        raise RuntimeError(
            "Pending Oracle inference has no finalization state"
        )

    deity = finalization_state["deity"]

    if claimed.get("deity") != deity:
        raise RuntimeError(
            "Pending Oracle deity does not match finalization state"
        )

    execution_target = str(
        prepared_state.get("execution_target") or ""
    ).strip().lower()
    execution_profile = DEVICE_EXECUTION_PROFILES.get(
        execution_target
    )
    if not execution_profile:
        raise RuntimeError(
            "Pending Oracle inference has no authorized execution target"
        )

    finalization_state["interaction_id"] = interaction_id

    inference_result = normalize_oracle_inference_result(
        {
            "answer": answer,
            "source_model": execution_profile["source_model"],
            "model_provider": execution_profile["model_provider"],
            "model_name": execution_profile["model_name"],
            "token_usage": {},
            "route_reason": execution_profile["route_reason"],
        },
        deity,
    )

    finalized = finalize_oracle_inference(
        finalization_state=finalization_state,
        inference_result=inference_result,
        timing_state={},
    )

    completed = complete_pending_oracle_inference(
        interaction_id,
        session_id=str(session_id),
        user_id=str(user_id) if user_id else None,
    )

    if not completed:
        raise RuntimeError(
            "Durable Oracle interaction committed but pending state "
            "could not be marked completed"
        )

    return finalized


@app.post("/voice/ask")
async def voice_ask_endpoint(request: Request, payload: dict):
    question = (payload.get("question") or "").strip()

    if is_likely_no_speech_transcript(question):
        return JSONResponse(
            content={
                "error": "No clear spoken question was detected. Please try again or type your question."
            },
            status_code=422
        )

    request.state.oracle_input_mode = "voice"
    oracle_payload = QuestionInput(**payload)
    return await ask_oracle(request, oracle_payload)


@app.post("/voice/tts")
async def voice_tts_endpoint(request: Request):
    started_at = datetime.datetime.now()
    voice = "Hathor"
    answer = ""

    try:
        data = await request.json()
        answer = (data.get("answer") or "").strip()
        voice = data.get("voice") or "Hathor"
        usage_context = get_voice_usage_context(request, voice)

        if not answer:
            record_voice_usage_event(
                **usage_context,
                input_mode="voice",
                deity=voice,
                stage="tts",
                status="failed",
                total_ms=voice_stage_ms(started_at, datetime.datetime.now()),
                answer_chars=0,
                audio_url_present=False,
                tts_provider=os.getenv("TTS_PROVIDER", "openai"),
                tts_model=get_openai_tts_model(),
                tts_voice=voice,
                metadata_json={
                    "phase": "10.7",
                    "event_source": "voice_tts_endpoint",
                    "reason": "empty_answer",
                }
            )
            return JSONResponse(
                content={"error": "No answer text provided for voice playback."},
                status_code=400
            )

        tts_started_at = datetime.datetime.now()
        audio_url = generate_tts_audio(answer, voice)
        tts_finished_at = datetime.datetime.now()

        tts_ms = voice_stage_ms(tts_started_at, tts_finished_at)
        total_ms = voice_stage_ms(started_at, datetime.datetime.now())
        answer_char_count = len(answer or "")
        active_tts_provider = os.getenv("TTS_PROVIDER", "openai")
        active_tts_model = get_openai_tts_model()
        tts_pricing = get_tts_pricing_info(active_tts_provider, active_tts_model)
        estimated_tts_cost_usd = calculate_tts_estimated_cost_usd(
            provider=active_tts_provider,
            model=active_tts_model,
            answer_chars=answer_char_count,
        )

        logger.info(
            "VOICE_TTS_STAGE status=ok voice=%s provider=%s model=%s tts_ms=%s total_ms=%s answer_chars=%s audio_url_present=%s estimated_tts_cost_usd=%s pricing_source=%s",
            voice,
            active_tts_provider,
            active_tts_model,
            tts_ms,
            total_ms,
            answer_char_count,
            bool(audio_url),
            estimated_tts_cost_usd if estimated_tts_cost_usd is not None else "-",
            tts_pricing.get("source")
        )
        record_voice_usage_event(
            **usage_context,
            input_mode="voice",
            deity=voice,
            stage="tts",
            status="ok",
            tts_ms=tts_ms,
            total_ms=total_ms,
            answer_chars=answer_char_count,
            audio_url_present=bool(audio_url),
            tts_provider=active_tts_provider,
            tts_model=active_tts_model,
            tts_voice=voice,
            estimated_tts_cost_usd=estimated_tts_cost_usd,
            metadata_json={
                "phase": "10.7",
                "event_source": "voice_tts_endpoint",
                "pricing_source": tts_pricing.get("source"),
                "pricing_effective_per_1m_chars": tts_pricing.get("effective_per_1m_chars"),
            }
        )

        return {"audio_url": audio_url}

    except Exception:
        logger.exception("Voice TTS endpoint failed")
        usage_context = get_voice_usage_context(request, voice)
        total_ms = voice_stage_ms(started_at, datetime.datetime.now())
        logger.info(
            "VOICE_TTS_STAGE status=error voice=%s total_ms=%s answer_chars=%s audio_url_present=false",
            voice,
            total_ms,
            len(answer or "")
        )
        record_voice_usage_event(
            **usage_context,
            input_mode="voice",
            deity=voice,
            stage="tts",
            status="error",
            total_ms=total_ms,
            answer_chars=len(answer or ""),
            audio_url_present=False,
            tts_provider=os.getenv("TTS_PROVIDER", "openai"),
            tts_model=get_openai_tts_model(),
            tts_voice=voice,
            metadata_json={
                "phase": "10.7",
                "event_source": "voice_tts_endpoint",
            }
        )
        return JSONResponse(
            content={"error": "Oracle voice playback could not be prepared."},
            status_code=500
        )



@app.get("/voice-preview", response_class=HTMLResponse)
async def voice_preview_page(request: Request):
    return templates.TemplateResponse("voice_preview.html", {"request": request})

@app.get("/xai-realtime-lab", response_class=HTMLResponse)
async def xai_realtime_lab_page(request: Request):
    require_admin(request)
    return templates.TemplateResponse("xai_realtime_lab.html", {"request": request})


@app.get("/voice/realtime/access")
async def voice_realtime_access_endpoint(request: Request):
    deity = (request.query_params.get("voice") or request.query_params.get("deity") or "Hathor").strip() or "Hathor"
    usage_context = get_voice_usage_context(request, deity)
    user = get_current_user(request)
    access = build_realtime_voice_access_payload(
        usage_context,
        is_admin=bool(user and user_has_admin_access(user)),
    )
    return access


@app.post("/voice/realtime/client-event")
async def voice_realtime_client_event_endpoint(request: Request):
    """Persist browser realtime lifecycle milestones without changing voice behavior."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    deity = str(body.get("deity") or body.get("voice") or "Hathor").strip() or "Hathor"
    if deity not in {"Hathor", "Moses"}:
        deity = "Hathor"

    event_name = str(body.get("event_name") or "").strip()[:120]
    if not event_name:
        return JSONResponse(status_code=400, content={"recorded": False, "error": "event_name is required"})

    usage_context = get_voice_usage_context(request, deity)
    details = body.get("details") if isinstance(body.get("details"), dict) else {}

    try:
        record_voice_usage_event(
            **usage_context,
            input_mode="realtime_voice",
            deity=deity,
            stage="realtime_client_lifecycle",
            status="ok",
            total_ms=None,
            transcript_chars=details.get("transcript_chars"),
            answer_chars=details.get("assistant_transcript_chars"),
            metadata_json={
                "phase": "11.10R",
                "event_source": "voice_realtime_client_lifecycle",
                "event_name": event_name,
                "sequence": body.get("sequence"),
                "client_realtime_session_id": str(body.get("client_realtime_session_id") or "")[:160],
                "client_interaction_id": str(body.get("client_interaction_id") or "")[:160],
                "speech_turn": body.get("speech_turn"),
                "assistant_turn": body.get("assistant_turn"),
                "realtime_voice": str(body.get("realtime_voice") or "")[:80],
                "details": details,
            },
            raise_on_error=True,
        )
    except Exception as exc:
        logger.warning("Realtime client lifecycle audit failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "recorded": False,
                "event_name": event_name,
                "sequence": body.get("sequence"),
            },
        )

    return {"recorded": True, "event_name": event_name, "sequence": body.get("sequence")}


@app.post("/voice/realtime/turn")
async def voice_realtime_turn_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    deity = (body.get("voice") or body.get("deity") or "Hathor").strip() or "Hathor"
    usage_context = get_voice_usage_context(request, deity)
    user = get_current_user(request)
    is_admin = bool(user and user_has_admin_access(user))

    access_before = build_realtime_voice_access_payload(
        usage_context,
        is_admin=is_admin,
    )

    if not access_before.get("allowed"):
        record_voice_usage_event(
            **usage_context,
            input_mode="realtime_voice",
            deity=deity,
            stage="realtime_turn",
            status="denied",
            total_ms=None,
            metadata_json={
                "phase": "11.6B",
                "event_source": "voice_realtime_turn_endpoint",
                "reason": access_before.get("reason"),
                "access": access_before,
                "client_payload": body,
            },
        )
        return JSONResponse(status_code=403, content=access_before)

    record_voice_usage_event(
        **usage_context,
        input_mode="realtime_voice",
        deity=deity,
        stage="realtime_turn",
        status="ok",
        total_ms=None,
        metadata_json={
            "phase": "11.6B",
            "event_source": "voice_realtime_turn_endpoint",
            "provider": body.get("provider") or "xai",
            "realtime_voice": body.get("realtime_voice"),
            "speech_turn": body.get("speech_turn"),
            "turn_input_audio_seconds": body.get("turn_input_audio_seconds"),
            "client_turn_commit_silence_ms": body.get("client_turn_commit_silence_ms"),
            "client_diagnostics": (
                body.get("client_diagnostics")
                if isinstance(body.get("client_diagnostics"), dict)
                else {}
            ),
            "preview_mode": body.get("preview_mode"),
            "mode": body.get("mode"),
            "access_before": access_before,
        },
    )

    access_after = build_realtime_voice_access_payload(
        usage_context,
        is_admin=is_admin,
    )
    access_after["turn_recorded"] = True
    return access_after


@app.post("/voice/realtime/prepare")
async def voice_realtime_prepare_endpoint(request: Request):
    """
    Prepare one browser realtime voice turn using the same God Incorporated
    identity, entitlement, memory, retrieval, and prompt authority as /ask.
    Provider generation remains on the existing xAI realtime websocket.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    question = str(body.get("question") or "").strip()
    deity = str(
        body.get("deity")
        or body.get("voice")
        or "Hathor"
    ).strip()

    if deity not in {"Hathor", "Moses"}:
        deity = "Hathor"

    if not question:
        return JSONResponse(
            status_code=400,
            content={"error": "question is required"},
        )

    usage_context = get_voice_usage_context(request, deity)
    user = get_current_user(request)
    access = build_realtime_voice_access_payload(
        usage_context,
        is_admin=bool(user and user_has_admin_access(user)),
    )

    if not access.get("allowed"):
        return JSONResponse(
            status_code=403,
            content={
                "error": (
                    access.get("message")
                    or "Live realtime voice is not available for this access level."
                ),
                "voice_access": access,
            },
        )

    request.state.oracle_input_mode = "voice"
    request.state.oracle_execution_mode = "realtime_prepare"

    oracle_payload = QuestionInput(
        question=question,
        deity=deity,
    )

    return await ask_oracle(request, oracle_payload)


def _normalize_realtime_interaction_text(value, max_chars=12000):
    if value is None:
        return ""
    text = str(value).replace("\x00", " ").strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def _normalize_realtime_interaction_label(value, default="", max_chars=120):
    text = _normalize_realtime_interaction_text(value, max_chars=max_chars)
    return text or default


@app.post("/voice/realtime/interaction")
async def voice_realtime_interaction_endpoint(request: Request):
    """
    Phase 11.10A provider-neutral completed-turn logging.

    /voice/realtime/turn remains the pre-commit quota and cost gate.
    This endpoint stores completed realtime Q/A text into oracle_interactions
    so realtime conversations contribute to seeker memory and future corpus.
    """
    import json
    import logging as _logging
    import time
    import uuid

    started = time.perf_counter()

    try:
        body = await request.json()
    except Exception:
        body = {}

    deity = _normalize_realtime_interaction_label(
        body.get("deity") or body.get("voice"),
        default="Hathor",
        max_chars=40,
    )

    usage_context = get_voice_usage_context(request, deity)
    user_transcript = _normalize_realtime_interaction_text(
        body.get("input_transcript") or body.get("user_transcript"),
        max_chars=12000,
    )
    assistant_transcript = _normalize_realtime_interaction_text(
        body.get("assistant_transcript") or body.get("response_transcript"),
        max_chars=12000,
    )

    provider = _normalize_realtime_interaction_label(
        body.get("provider") or body.get("model_provider"),
        default="unknown",
        max_chars=80,
    )
    model = _normalize_realtime_interaction_label(
        body.get("model") or body.get("model_name"),
        default="",
        max_chars=160,
    )
    transport = _normalize_realtime_interaction_label(
        body.get("transport"),
        default="",
        max_chars=80,
    )
    provider_voice = _normalize_realtime_interaction_label(
        body.get("provider_voice") or body.get("realtime_voice") or body.get("voice_name"),
        default="",
        max_chars=80,
    )
    route = _normalize_realtime_interaction_label(
        body.get("route") or body.get("mode"),
        default="temple_main_live_realtime",
        max_chars=120,
    )
    input_mode = _normalize_realtime_interaction_label(
        body.get("input_mode"),
        default="realtime_voice",
        max_chars=80,
    )

    client_interaction_id = _normalize_realtime_interaction_label(
        body.get("client_interaction_id"),
        default="",
        max_chars=160,
    )

    if not client_interaction_id:
        client_interaction_id = "rt-" + str(uuid.uuid4())

    metadata = {
        "phase": "11.10A",
        "event_source": "voice_realtime_interaction_endpoint",
        "source": _normalize_realtime_interaction_label(body.get("source"), default="temple", max_chars=80),
        "route": route,
        "input_mode": input_mode,
        "provider": provider,
        "model": model,
        "transport": transport,
        "provider_voice": provider_voice,
        "client_session_id": _normalize_realtime_interaction_label(body.get("client_session_id"), default="", max_chars=160),
        "provider_session_id": _normalize_realtime_interaction_label(body.get("provider_session_id"), default="", max_chars=160),
        "client_interaction_id": client_interaction_id,
        "speech_turn": body.get("speech_turn"),
        "assistant_turn": body.get("assistant_turn"),
        "input_transcript_source": _normalize_realtime_interaction_label(body.get("input_transcript_source"), default="provider_realtime", max_chars=80),
        "assistant_transcript_source": _normalize_realtime_interaction_label(body.get("assistant_transcript_source"), default="provider_audio_transcript", max_chars=80),
        "turn_input_audio_seconds": body.get("turn_input_audio_seconds"),
        "output_audio_seconds": body.get("output_audio_seconds"),
        "first_audio_delta_ms": body.get("first_audio_delta_ms"),
        "preview_mode": body.get("preview_mode"),
        "client_observed_provider_realtime": True,
        "note": "Completed realtime turn transcript captured by client and normalized for provider-neutral Oracle memory.",
    }

    if not user_transcript or not assistant_transcript:
        record_voice_usage_event(
            **usage_context,
            input_mode="realtime_voice",
            deity=deity,
            stage="realtime_interaction",
            status="skipped",
            total_ms=round((time.perf_counter() - started) * 1000, 2),
            transcript_chars=len(user_transcript),
            answer_chars=len(assistant_transcript),
            metadata_json={
                **metadata,
                "reason": "missing_transcript",
                "has_input_transcript": bool(user_transcript),
                "has_assistant_transcript": bool(assistant_transcript),
            },
        )
        return {
            "stored": False,
            "reason": "missing_transcript",
            "client_interaction_id": client_interaction_id,
        }

    conn = None
    inserted_id = None
    duplicate = False

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oracle_interactions
                    (
                        session_id,
                        anonymous_user_id,
                        user_id,
                        input_type,
                        question_text,
                        response_text,
                        model_provider,
                        model_name,
                        mode,
                        reason,
                        client_interaction_id,
                        metadata_json
                    )
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (client_interaction_id)
                    WHERE client_interaction_id IS NOT NULL
                    DO NOTHING
                RETURNING id
                """,
                (
                    usage_context.get("session_id"),
                    usage_context.get("anonymous_user_id"),
                    usage_context.get("user_id"),
                    "voice",
                    user_transcript,
                    assistant_transcript,
                    provider,
                    model,
                    deity,
                    "realtime_voice",
                    client_interaction_id,
                    json.dumps(metadata),
                ),
            )
            row = cur.fetchone()

            if row:
                inserted_id = str(row["id"] if isinstance(row, dict) else row[0])
            else:
                duplicate = True
                cur.execute(
                    """
                    SELECT id
                    FROM oracle_interactions
                    WHERE client_interaction_id = %s
                      AND session_id IS NOT DISTINCT FROM %s::uuid
                      AND anonymous_user_id IS NOT DISTINCT FROM %s
                      AND user_id IS NOT DISTINCT FROM %s::uuid
                    LIMIT 1
                    """,
                    (
                        client_interaction_id,
                        usage_context.get("session_id"),
                        usage_context.get("anonymous_user_id"),
                        usage_context.get("user_id"),
                    ),
                )
                existing = cur.fetchone()

                if not existing:
                    raise RuntimeError(
                        "Realtime interaction id conflict does not match "
                        "authoritative identity context"
                    )

                inserted_id = str(
                    existing["id"]
                    if isinstance(existing, dict)
                    else existing[0]
                )

        conn.commit()

        record_voice_usage_event(
            **usage_context,
            input_mode="realtime_voice",
            deity=deity,
            stage="realtime_interaction",
            status="duplicate" if duplicate else "ok",
            total_ms=round((time.perf_counter() - started) * 1000, 2),
            transcript_chars=len(user_transcript),
            answer_chars=len(assistant_transcript),
            metadata_json={
                **metadata,
                "oracle_interaction_id": inserted_id,
                "duplicate": duplicate,
            },
        )

        _logging.info(
            "REALTIME_INTERACTION_STAGE status=%s provider=%s model=%s deity=%s provider_voice=%s question_chars=%s answer_chars=%s oracle_interaction_id=%s client_interaction_id=%s",
            "duplicate" if duplicate else "ok",
            provider,
            model,
            deity,
            provider_voice,
            len(user_transcript),
            len(assistant_transcript),
            inserted_id,
            client_interaction_id,
        )

        return {
            "stored": not duplicate,
            "duplicate": duplicate,
            "oracle_interaction_id": inserted_id,
            "client_interaction_id": client_interaction_id,
        }

    except Exception as exc:
        if conn:
            conn.rollback()

        logger.error("Realtime interaction logging failed: %s", exc)

        record_voice_usage_event(
            **usage_context,
            input_mode="realtime_voice",
            deity=deity,
            stage="realtime_interaction",
            status="error",
            total_ms=round((time.perf_counter() - started) * 1000, 2),
            transcript_chars=len(user_transcript),
            answer_chars=len(assistant_transcript),
            metadata_json={
                **metadata,
                "error": str(exc),
            },
        )

        return JSONResponse(
            status_code=500,
            content={
                "stored": False,
                "error": "Realtime interaction could not be logged.",
                "client_interaction_id": client_interaction_id,
            },
        )

    finally:
        if conn:
            conn.close()


@app.post("/voice/xai/realtime/session")
async def voice_xai_realtime_session_endpoint(request: Request):
    import logging as _logging
    from services.xai_realtime import create_xai_realtime_session

    try:
        body = await request.json()
    except Exception:
        body = {}

    deity = (body.get("voice") or body.get("deity") or "Hathor").strip() or "Hathor"
    voice_override = body.get("realtime_voice") or body.get("voice_name") or body.get("xai_voice")

    usage_context = get_voice_usage_context(request, deity)
    user = get_current_user(request)
    access = build_realtime_voice_access_payload(
        usage_context,
        is_admin=bool(user and user_has_admin_access(user)),
    )

    if not access.get("allowed"):
        record_voice_usage_event(
            **usage_context,
            input_mode="realtime_voice",
            deity=deity,
            stage="realtime_session",
            status="denied",
            total_ms=None,
            metadata_json={
                "phase": "11.6B",
                "event_source": "voice_xai_realtime_session_endpoint",
                "reason": access.get("reason"),
                "provider": "xai",
                "access": access,
            },
        )
        return JSONResponse(status_code=403, content={
            "error": access.get("message") or "Live realtime voice is not available for this access level.",
            "voice_access": access,
        })

    try:
        result = create_xai_realtime_session(deity, voice_override=voice_override)
        result["voice_access"] = access

        record_voice_usage_event(
            **usage_context,
            input_mode="realtime_voice",
            deity=deity,
            stage="realtime_session",
            status="ok",
            total_ms=result.get("total_ms"),
            metadata_json={
                "phase": "11.6B",
                "event_source": "voice_xai_realtime_session_endpoint",
                "provider": result.get("provider"),
                "model": result.get("model"),
                "transport": result.get("transport"),
                "realtime_voice": result.get("realtime_voice"),
                "access": access,
            },
        )

        _logging.info(
            "XAI_REALTIME_SESSION_STAGE status=ok provider=%s model=%s deity=%s realtime_voice=%s total_ms=%s transport=%s plan_code=%s access_reason=%s",
            result.get("provider"),
            result.get("model"),
            result.get("deity"),
            result.get("realtime_voice"),
            result.get("total_ms"),
            result.get("transport"),
            access.get("plan_code"),
            access.get("reason"),
        )
        return result
    except Exception as exc:
        _logging.exception("xAI realtime session endpoint failed")
        return JSONResponse(
            status_code=500,
            content={
                "error": "xAI realtime voice session could not be prepared.",
                "detail": str(exc),
            },
        )


@app.get("/realtime-lab", response_class=HTMLResponse)
async def realtime_lab_page(request: Request):
    require_admin(request)
    return templates.TemplateResponse("realtime_lab.html", {"request": request})


@app.post("/voice/realtime/session")
async def voice_realtime_session_endpoint(request: Request):
    started_at = datetime.datetime.now()
    deity = "Hathor"
    provider = os.getenv("REALTIME_VOICE_PROVIDER", "openai").strip().lower() or "openai"

    try:
        try:
            data = await request.json()
        except Exception:
            data = {}

        deity = (data.get("voice") or data.get("deity") or "Hathor").strip()
        if deity not in {"Hathor", "Moses"}:
            deity = "Hathor"

        provider = (data.get("provider") or provider).strip().lower() or "openai"

        usage_context = get_voice_usage_context(request, deity)
        session_id = usage_context.get("session_id") or get_or_create_session_id(request)
        user_id = usage_context.get("user_id")
        plan_code = usage_context.get("plan_code") or "anon"

        recent_memory = ""
        try:
            recent_memory = get_session_memory(session_id, 3)
        except Exception as e:
            logger.warning("REALTIME_SESSION_STAGE memory lookup failed: %s", e)

        from services.realtime_voice import (
            build_realtime_instructions,
            create_realtime_client_secret,
        )

        instructions = build_realtime_instructions(
            deity=deity,
            plan_code=plan_code,
            recent_memory=recent_memory or "",
        )

        metadata = {
            "phase": "11.5",
            "event_source": "voice_realtime_session_endpoint",
            "deity": deity,
            "plan_code": plan_code,
            "session_id": session_id,
            "user_id": user_id,
            "fallback_mode": "classic_voice_pipeline",
        }

        result = create_realtime_client_secret(
            provider=provider,
            deity=deity,
            instructions=instructions,
            metadata=metadata,
        )

        total_ms = voice_stage_ms(started_at, datetime.datetime.now())

        logger.info(
            "REALTIME_SESSION_STAGE status=ok provider=%s model=%s deity=%s realtime_voice=%s total_ms=%s session_id=%s fallback_mode=%s",
            result.get("provider"),
            result.get("model"),
            deity,
            result.get("realtime_voice"),
            total_ms,
            result.get("session_id"),
            result.get("fallback_mode"),
        )

        record_voice_usage_event(
            **usage_context,
            input_mode="voice",
            deity=deity,
            stage="realtime_session",
            status="ok",
            total_ms=total_ms,
            tts_provider=result.get("provider"),
            tts_model=result.get("model"),
            tts_voice=result.get("realtime_voice"),
            metadata_json={
                "phase": "11.5",
                "event_source": "voice_realtime_session_endpoint",
                "transport": result.get("transport"),
                "session_id": result.get("session_id"),
                "fallback_mode": result.get("fallback_mode"),
                "estimated_cost": result.get("estimated_cost"),
            },
        )

        return JSONResponse(content=result)

    except NotImplementedError as e:
        total_ms = voice_stage_ms(started_at, datetime.datetime.now())
        logger.info(
            "REALTIME_SESSION_STAGE status=not_implemented provider=%s deity=%s total_ms=%s error=%s",
            provider,
            deity,
            total_ms,
            e,
        )
        return JSONResponse(
            content={
                "error": str(e),
                "fallback_mode": "classic_voice_pipeline",
            },
            status_code=501,
        )

    except Exception as e:
        total_ms = voice_stage_ms(started_at, datetime.datetime.now())
        logger.exception("Realtime session endpoint failed")
        try:
            usage_context = get_voice_usage_context(request, deity)
            record_voice_usage_event(
                **usage_context,
                input_mode="voice",
                deity=deity,
                stage="realtime_session",
                status="error",
                total_ms=total_ms,
                tts_provider=provider,
                metadata_json={
                    "phase": "11.5",
                    "event_source": "voice_realtime_session_endpoint",
                    "error": str(e)[:500],
                    "fallback_mode": "classic_voice_pipeline",
                },
            )
        except Exception:
            pass

        return JSONResponse(
            content={
                "error": "Realtime voice session could not be prepared.",
                "fallback_mode": "classic_voice_pipeline",
            },
            status_code=500,
        )


@app.post("/whisper")
async def whisper_endpoint(
    request: Request,
    file: UploadFile = File(...),
    voice: str = Form("Hathor")
):
    voice_started_at = datetime.datetime.now()
    transcribe_started_at = None
    transcribe_finished_at = None
    oracle_started_at = None
    oracle_finished_at = None
    tts_started_at = None
    tts_finished_at = None
    transcript = ""
    answer = ""
    audio_url = None

    try:
        file_bytes = await file.read()

        transcribe_started_at = datetime.datetime.now()
        transcript = transcribe_audio(file_bytes)
        transcribe_finished_at = datetime.datetime.now()

        if not transcript:
            logger.info(
                "VOICE_STAGE_TIMING status=transcription_failed voice=%s transcribe_ms=%s total_ms=%s transcript_chars=0 answer_chars=0 audio_url_present=false",
                voice,
                voice_stage_ms(transcribe_started_at, transcribe_finished_at),
                voice_stage_ms(voice_started_at, datetime.datetime.now())
            )
            return JSONResponse(
                content={"error": "Whisper could not transcribe.", "answer": "⚠️ Whisper could not transcribe."},
                status_code=422
            )

        oracle_payload = QuestionInput(
            question=transcript,
            deity=voice
        )

        request.state.oracle_input_mode = "voice"

        oracle_started_at = datetime.datetime.now()
        result = await ask_oracle(request, oracle_payload)
        oracle_finished_at = datetime.datetime.now()

        if isinstance(result, JSONResponse):
            logger.info(
                "VOICE_STAGE_TIMING status=oracle_json_response voice=%s transcribe_ms=%s oracle_ms=%s total_ms=%s transcript_chars=%s answer_chars=0 audio_url_present=false",
                voice,
                voice_stage_ms(transcribe_started_at, transcribe_finished_at),
                voice_stage_ms(oracle_started_at, oracle_finished_at),
                voice_stage_ms(voice_started_at, datetime.datetime.now()),
                len(transcript or "")
            )
            return result

        answer = result.get("answer", "")

        tts_started_at = datetime.datetime.now()
        audio_url = generate_tts_audio(answer, voice) if answer else None
        tts_finished_at = datetime.datetime.now()

        logger.info(
            "VOICE_STAGE_TIMING status=ok voice=%s transcribe_ms=%s oracle_ms=%s tts_ms=%s total_ms=%s transcript_chars=%s answer_chars=%s audio_url_present=%s",
            voice,
            voice_stage_ms(transcribe_started_at, transcribe_finished_at),
            voice_stage_ms(oracle_started_at, oracle_finished_at),
            voice_stage_ms(tts_started_at, tts_finished_at),
            voice_stage_ms(voice_started_at, datetime.datetime.now()),
            len(transcript or ""),
            len(answer or ""),
            bool(audio_url)
        )

        return {
            "question": transcript,
            "transcript": transcript,
            "answer": answer,
            "audio_url": audio_url
        }

    except Exception as e:
        logger.exception("Whisper voice endpoint failed")
        logger.info(
            "VOICE_STAGE_TIMING status=error voice=%s transcribe_ms=%s oracle_ms=%s tts_ms=%s total_ms=%s transcript_chars=%s answer_chars=%s audio_url_present=%s",
            voice,
            voice_stage_ms(transcribe_started_at, transcribe_finished_at),
            voice_stage_ms(oracle_started_at, oracle_finished_at),
            voice_stage_ms(tts_started_at, tts_finished_at),
            voice_stage_ms(voice_started_at, datetime.datetime.now()),
            len(transcript or ""),
            len(answer or ""),
            bool(audio_url)
        )
        return JSONResponse(
            content={"error": str(e), "answer": "⚠️ Voice request failed."},
            status_code=500
        )


@app.get("/health")
def health():
    return {"ok": True, "service": "godinc", "time": str(datetime.datetime.now(timezone.utc))}


@app.get("/health/db")
def health_db():
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok;")
            row = cur.fetchone()
        conn.close()
        return {"ok": True, "db": True, "result": row}
    except Exception as e:
        return JSONResponse(
            content={"ok": False, "db": False, "error": str(e)},
            status_code=500
        )


@app.post("/admin/ingestion/process-one-scroll")
def admin_process_one_scroll_ingestion_job(request: Request):
    """
    Admin-only manual processor for one queued scroll ingestion job.

    This does not start a background worker and does not change public upload
    behavior. It lets staging/dev process one queued job deliberately.
    """
    admin_user = require_admin(request)
    result = process_one_queued_scroll_ingestion_job()

    log_admin_action(
        admin_user_id=admin_user["user_id"],
        action_type="admin.ingestion.process_one_scroll",
        payload={
            "result": result,
        }
    )

    return {
        "ok": True,
        "requested_by": admin_user["user_id"],
        "result": result,
    }


@app.post("/admin/backfill_embeddings")
def admin_backfill_embeddings(request: Request, limit: int = 500, offset: int = 0):
    admin_user = require_admin(request)

    if not should_use_embeddings():
        return JSONResponse(
            content={"ok": False, "error": "Embeddings are disabled."},
            status_code=400
        )

    try:
        result = backfill_embedding_cache(limit=limit, offset=offset)

        log_admin_action(
            admin_user_id=admin_user["user_id"],
            action_type="admin.backfill_embeddings",
            payload={
                "limit": limit,
                "offset": offset,
                "result": result
            }
        )

        return {
            "ok": True,
            "admin_user_id": admin_user["user_id"],
            "result": result
        }

    except Exception as e:
        logger.error(f"Backfill embeddings error: {e}")
        return JSONResponse(
            content={"ok": False, "error": str(e)},
            status_code=500
        )

@app.get("/admin/me")
def admin_me(request: Request):
    admin_user = require_admin(request)
    return {
        "ok": True,
        "admin": {
            "user_id": admin_user["user_id"],
            "email": admin_user["email"],
            "display_name": admin_user["display_name"],
            "role": normalize_user_role(admin_user.get("role"))
        }
    }

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    require_admin(request)
    return templates.TemplateResponse("admin.html", {"request": request})

@app.get("/", response_class=HTMLResponse)
@app.get("/temple", response_class=HTMLResponse)
def temple_page(request: Request):
    return templates.TemplateResponse("temple.html", {"request": request})



@app.get("/press")
def press_page(request: Request):
    return templates.TemplateResponse("press.html", {"request": request})


@app.get("/press/god-incorporated-ai-limited-launches-godincorporated-ai")
def press_launch_20260525_page(request: Request):
    return templates.TemplateResponse("press_launch_20260525.html", {"request": request})


@app.get("/terms")
def terms_page(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request})


@app.get("/privacy")
def privacy_page(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})


@app.get("/support", response_class=HTMLResponse)
@app.get("/tiers", response_class=HTMLResponse)
def support_page(request: Request):
    return templates.TemplateResponse("support.html", {"request": request})

@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    return templates.TemplateResponse("account.html", {"request": request})

@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

@app.post("/reset_scrolls")
def reset_scrolls():
    reset_scroll_system()
    return {"message": "Scroll system reset successfully."}

@app.get("/scrolls")
def get_scroll_count():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM scrolls")
        result = cur.fetchone()

    conn.close()

    return {"count": result["count"]}

class RegisterInput(BaseModel):
    display_name: Optional[str] = None

@app.post("/register")
def register_seeker(payload: RegisterInput):
    return JSONResponse(
        content={"error": "Legacy /register is retired. Use /auth/register instead."},
        status_code=410
    )
class AuthRegisterInput(BaseModel):
    email: str
    password: str
    display_name: str

@app.post("/auth/register")
def auth_register(payload: AuthRegisterInput, request: Request):
    email = payload.email.lower().strip()
    password = payload.password
    display_name = payload.display_name.strip()

    # Validate display_name
    if not re.match(r'^[A-Za-z0-9_]{2,24}$', display_name):
        return JSONResponse(content={"error": "Invalid display name format"}, status_code=400)

    # Validate password strength (basic)
    if len(password) < 8:
        return JSONResponse(content={"error": "Password must be at least 8 characters"}, status_code=400)

    if len(password.encode("utf-8")) > 72:
        return JSONResponse(
            content={"error": "Password must be 72 bytes or fewer."},
            status_code=400
        )

    conn = get_db_connection()

    # Check if email already exists
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            conn.close()
            return JSONResponse(content={"error": "Email already registered"}, status_code=409)

    # Check display_name uniqueness (case-insensitive)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE LOWER(display_name) = %s",
            (display_name.lower(),)
        )
        if cur.fetchone():
            conn.close()
            return JSONResponse(content={"error": "Display name already taken"}, status_code=409)

    # Create user
    user_id = str(uuid.uuid4())
    seeker_id = str(uuid.uuid4())
    hashed_password = hash_password(password)
    verification_token = str(uuid.uuid4())
    created_at = datetime.datetime.now(timezone.utc)

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO users (
                id,
                email,
                password_hash,
                seeker_id,
                display_name,
                display_name_lower,
                email_verified,
                verification_token,
                created_at,
                last_login,
                title,
                scroll_count,
                donation_total,
                influence_state,
                eligibility_flags,
                role
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            email,
            hashed_password,
            seeker_id,
            display_name,
            display_name.lower(),
            False,
            verification_token,
            created_at,
            None,
            "Seeker",
            0,
            0,
            "disabled",
            [],
            "user"
        ))
    conn.commit()
    conn.close()

    anonymous_user_id = get_or_create_anonymous_user_id(request)
    claim_anonymous_history_into_user(
        anonymous_user_id,
        user_id,
    )

    # Build verification link
    app_base_url = os.getenv("APP_BASE_URL", os.getenv("BASE_URL", "http://localhost:8000"))
    verification_link = f"{app_base_url}/auth/verify-email?token={verification_token}"

    # Send verification email
    try:
        send_email(
            to_email=email,
            subject="Verify your God Incorporated account",
            html=f"""
<h2>Welcome to God Incorporated</h2>
<p>Please verify your email by clicking below:</p>
<p><a href="{verification_link}">Verify Email</a></p>
<p>This link expires in 24 hours.</p>
"""
        )
    except Exception as e:
        print(f"Failed to send verification email to {email}: {str(e)}")
        return JSONResponse(
            content={"error": "Registration successful, but failed to send verification email. Please contact support."},
            status_code=500
        )

    return {
        "message": "Registration successful. Please check your email for verification link.",
        "user_id": user_id,
        "email_verified": False
    }
class AuthLoginInput(BaseModel):
    email: str
    password: str

@app.post("/auth/login")
def auth_login(payload: AuthLoginInput, request: Request):
    email = payload.email.lower().strip()
    password = payload.password

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, password_hash, email_verified, seeker_id, display_name FROM users WHERE email = %s",
            (email,)
        )
        result = cur.fetchone()

        if not result or not verify_password(password, result["password_hash"]):
            conn.close()
            return JSONResponse(content={"error": "Invalid email or password"}, status_code=401)

        if not result["email_verified"]:
            conn.close()
            return JSONResponse(content={"error": "Please verify your email before logging in."}, status_code=403)

        user_id = result["id"]
        display_name = result["display_name"]

        cur.execute(
            "UPDATE users SET last_login = %s WHERE id = %s",
            (datetime.datetime.now(timezone.utc), user_id)
        )

    conn.commit()
    conn.close()

    anonymous_user_id = get_or_create_anonymous_user_id(request)

    claim_anonymous_history_into_user(
        anonymous_user_id,
        user_id,
    )

    request.session.pop("session_id", None)

    get_or_create_bound_session_id(
        request,
        anonymous_user_id,
        user_id,
    )

    request.session["user_id"] = user_id
    request.session["display_name"] = display_name

    return {"message": "Login successful"}


@app.post("/auth/logout")
def auth_logout(request: Request):
    anonymous_user_id = get_or_create_anonymous_user_id(
        request
    )

    request.session.clear()
    request.session["anonymous_user_id"] = anonymous_user_id

    get_or_create_bound_session_id(
        request,
        anonymous_user_id,
        None,
    )

    return {"message": "Logged out successfully"}

@app.get("/auth/verify-email")
def auth_verify_email(token: str = Query(...)):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, email FROM users WHERE verification_token = %s", (token,))
        result = cur.fetchone()
        if not result:
            conn.close()
            return JSONResponse(content={"error": "Invalid verification token"}, status_code=400)

        user_id = result["id"]
        email = result["email"]

        cur.execute(
            "UPDATE users SET email_verified = true, verification_token = null WHERE id = %s",
            (user_id,)
        )

    conn.commit()
    conn.close()

    try:
        send_email(
            email,
            "Email Verification Successful",
            "Your email has been successfully verified. You can now log in to your account."
        )
    except Exception as e:
        logging.error(f"Failed to send email verification confirmation to {email}: {e}")

    return {"message": "Email verified successfully. You can now log in."}

class AuthResetPasswordInput(BaseModel):
    token: str
    new_password: str

class PasswordResetRequestInput(BaseModel):
    email: str

@app.get("/auth/reset-password", response_class=HTMLResponse)
def show_reset_form(token: str = Query(...)):
    user_id = validate_reset_token(token)
    if not user_id:
        return HTMLResponse("<h2>Invalid or expired reset link.</h2>", status_code=400)

    return HTMLResponse(f"""
        <html>
            <body>
                <h2>Reset Your Password</h2>
                <form method="post" action="/auth/reset-password">
                    <input type="hidden" name="token" value="{token}" />
                    <label>New Password:</label><br/>
                    <input type="password" name="new_password" required /><br/><br/>
                    <button type="submit">Reset Password</button>
                </form>
            </body>
        </html>
    """)

@app.post("/auth/reset-password")
def auth_reset_password(
    token: str = Form(None),
    new_password: str = Form(None),
):
    if not token or not new_password:
        return JSONResponse(content={"error": "Token and new_password are required"}, status_code=400)

    if len(new_password) < 8:
        return JSONResponse(content={"error": "Password must be at least 8 characters"}, status_code=400)

    if len(new_password.encode("utf-8")) > 72:
        return JSONResponse(content={"error": "Password must be 72 bytes or fewer."}, status_code=400)

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email FROM users WHERE reset_token = %s AND reset_token_expires_at > %s",
            (token, datetime.datetime.now(timezone.utc))
        )
        result = cur.fetchone()
        if not result:
            conn.close()
            return JSONResponse(content={"error": "Invalid or expired reset token"}, status_code=400)

        user_id = result["id"]
        email = result["email"]

        cur.execute(
            "UPDATE users SET password_hash = %s, reset_token = null, reset_token_expires_at = null WHERE id = %s",
            (hash_password(new_password), user_id)
        )

    conn.commit()
    conn.close()

    try:
        send_email(
            email,
            "Password Reset Confirmation",
            "Your password has been successfully changed. If you did not request this change, please contact support immediately."
        )
    except Exception as e:
        logging.error(f"Failed to send password reset confirmation email to {email}: {e}")

    return {"message": "Password reset successfully. Please log in with your new password."}

@app.post("/auth/request-password-reset")
def auth_request_password_reset(payload: PasswordResetRequestInput):
    email = payload.email.lower().strip()

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        result = cur.fetchone()

    # Always return success to prevent user enumeration
    if result:
        user_id = result["id"]

        token = str(uuid.uuid4())
        expires_at = datetime.datetime.now(timezone.utc) + datetime.timedelta(minutes=30)

        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET reset_token = %s, reset_token_expires_at = %s WHERE id = %s",
                (token, expires_at, user_id)
            )
        conn.commit()

        # Build reset link
        app_base_url = os.getenv("APP_BASE_URL", "http://localhost:8000")
        reset_url = f"{app_base_url}/auth/reset-password?token={token}"

        subject = "Reset your God Incorporated password"
        html = f"""
<p>You requested a password reset.</p>
<p>Click the link below to set a new password:</p>
<p><a href="{reset_url}">{reset_url}</a></p>
<p>This link expires in 30 minutes.</p>
<p>If you did not request this, you may ignore this email.</p>
"""

        try:
            send_email(
                to_email=email,
                subject=subject,
                html=html
            )
        except Exception as e:
            print("Email send failed:", str(e))

    conn.close()
    return {"message": "If that email exists, a reset link has been sent."}



def generate_daily_business_snapshot(
    days: int = 1,
    environment: Optional[str] = None,
    git_sha: Optional[str] = None,
) -> Optional[dict]:
    """
    Generate a safe daily business snapshot report.

    This creates report/artifact records only. It does not email, schedule,
    expose private scroll text, or expose seeker conversations.
    """
    env = environment or get_app_environment()
    safe_days = max(1, min(int(days or 1), 30))

    run = create_report_run(
        report_key="daily_business_snapshot",
        status="processing",
        environment=env,
        git_sha=git_sha,
        metadata_json={
            "days": safe_days,
            "source": "generate_daily_business_snapshot",
        },
    )

    if not run:
        create_alert_event(
            alert_key="report_run_create_failed",
            fingerprint=f"daily_business_snapshot:{env}",
            severity="CRITICAL",
            title="Daily business snapshot report run could not be created",
            message="create_report_run returned no row.",
            environment=env,
            metadata_json={"report_key": "daily_business_snapshot"},
        )
        return None

    try:
        overview = get_admin_reporting_overview(days=safe_days)
        usage_summary = get_admin_usage_summary(days=safe_days)
        diagnostics = {
            "environment": env,
            "days": safe_days,
            "email_settings": {
                key: value
                for key, value in get_report_email_settings().items()
                if key != "admin_alert_emails"
            },
            "delivery_modes": {
                "info_email": get_notification_delivery_mode(severity="INFO", channel="email"),
                "warn_email": get_notification_delivery_mode(severity="WARN", channel="email"),
                "critical_email": get_notification_delivery_mode(severity="CRITICAL", channel="email"),
            },
        }

        summary = {
            "report_key": "daily_business_snapshot",
            "environment": env,
            "days": safe_days,
            "overview": overview,
            "usage_summary": usage_summary,
            "diagnostics": diagnostics,
        }

        artifact = create_report_artifact(
            report_key="daily_business_snapshot",
            format="json",
            environment=env,
            storage_ref=None,
            summary_json=summary,
        )

        if not artifact:
            raise RuntimeError("create_report_artifact returned no row")

        finished = finish_report_run(
            report_run_id=str(run["id"]),
            status="completed",
            artifact_id=str(artifact["id"]),
            metadata_json={
                "days": safe_days,
                "artifact_id": str(artifact["id"]),
            },
        )

        if not finished:
            raise RuntimeError("finish_report_run returned no row")

        return {
            "ok": True,
            "report_run": finished,
            "artifact": artifact,
            "summary": summary,
        }

    except Exception as exc:
        logging.exception("DAILY_BUSINESS_SNAPSHOT_FAILED env=%s days=%s", env, safe_days)

        finish_report_run(
            report_run_id=str(run["id"]),
            status="failed",
            error_message=str(exc),
            metadata_json={
                "days": safe_days,
                "error": str(exc),
            },
        )

        alert = create_alert_event(
            alert_key="daily_business_snapshot_failed",
            fingerprint=f"daily_business_snapshot:{env}",
            severity="CRITICAL",
            title="Daily business snapshot failed",
            message=str(exc),
            environment=env,
            metadata_json={
                "report_key": "daily_business_snapshot",
                "days": safe_days,
            },
        )

        if alert:
            record_alert_notification(
                alert_event=alert,
                channel="email",
                metadata_json={
                    "source": "generate_daily_business_snapshot",
                    "expected_local_status": "muted",
                },
            )

        return None


@app.post("/admin/reports/daily-business-snapshot")
def admin_generate_daily_business_snapshot(request: Request, days: int = 1):
    """
    Admin-only manual trigger for the daily business snapshot.

    This creates private report records only. It does not send email.
    """
    admin_user = require_admin(request)
    result = generate_daily_business_snapshot(days=days)

    if not result:
        raise HTTPException(status_code=500, detail="Daily business snapshot failed")

    return {
        "ok": True,
        "requested_by": admin_user["user_id"],
        "report_run": _admin_report_row(result["report_run"]),
        "artifact": _admin_report_row(result["artifact"]),
        "summary": result["summary"],
    }


@app.get("/admin/reports/reporting-diagnostics")
def admin_reporting_diagnostics(request: Request):
    """
    Admin-only diagnostics for the reporting/alerting rail.

    Safe operational state only. No private scroll text, seeker dialogue,
    secrets, or email sending.
    """
    admin_user = require_admin(request)
    conn = None

    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT 'report_artifacts' AS table_name, COUNT(*) AS total FROM report_artifacts
                UNION ALL
                SELECT 'report_runs' AS table_name, COUNT(*) AS total FROM report_runs
                UNION ALL
                SELECT 'alert_events' AS table_name, COUNT(*) AS total FROM alert_events
                UNION ALL
                SELECT 'notification_deliveries' AS table_name, COUNT(*) AS total FROM notification_deliveries
                UNION ALL
                SELECT 'ingestion_jobs' AS table_name, COUNT(*) AS total FROM ingestion_jobs
                ORDER BY table_name
                """
            )
            table_counts = cur.fetchall()

            cur.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM report_runs
                GROUP BY status
                ORDER BY status
                """
            )
            report_runs_by_status = cur.fetchall()

            cur.execute(
                """
                SELECT status, severity, COUNT(*) AS total
                FROM alert_events
                GROUP BY status, severity
                ORDER BY status, severity
                """
            )
            alert_events_by_status = cur.fetchall()

            cur.execute(
                """
                SELECT status, channel, COUNT(*) AS total
                FROM notification_deliveries
                GROUP BY status, channel
                ORDER BY status, channel
                """
            )
            notification_deliveries_by_status = cur.fetchall()

            cur.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM ingestion_jobs
                GROUP BY status
                ORDER BY status
                """
            )
            ingestion_jobs_by_status = cur.fetchall()

            cur.execute(
                """
                SELECT
                    id,
                    alert_key,
                    fingerprint,
                    environment,
                    severity,
                    status,
                    title,
                    first_seen_at,
                    last_seen_at,
                    count
                FROM alert_events
                WHERE status = 'open'
                ORDER BY last_seen_at DESC
                LIMIT 10
                """
            )
            open_alerts = cur.fetchall()

            cur.execute(
                """
                SELECT
                    id,
                    report_key,
                    environment,
                    status,
                    period_start,
                    period_end,
                    started_at,
                    finished_at,
                    error_message,
                    artifact_id,
                    git_sha,
                    created_at
                FROM report_runs
                ORDER BY created_at DESC
                LIMIT 10
                """
            )
            recent_report_runs = cur.fetchall()

        settings = get_report_email_settings()

        # Do not expose recipient email lists broadly; this is admin-only, but keep it summarized.
        safe_email_settings = {
            "environment": settings.get("environment"),
            "reports_from_email": settings.get("reports_from_email"),
            "alerts_from_email": settings.get("alerts_from_email"),
            "admin_alert_email_count": len(settings.get("admin_alert_emails") or []),
            "alerts_enabled": settings.get("alerts_enabled"),
            "alert_emails_enabled": settings.get("alert_emails_enabled"),
            "alert_email_mode": settings.get("alert_email_mode"),
            "allow_external_emails": settings.get("allow_external_emails"),
            "staging_notifications_muted": settings.get("staging_notifications_muted"),
            "dev_notifications_muted": settings.get("dev_notifications_muted"),
            "external_email_allowed": settings.get("external_email_allowed"),
        }

        return {
            "ok": True,
            "requested_by": admin_user["user_id"],
            "environment": get_app_environment(),
            "upload_queue_settings": get_scroll_upload_queue_settings(),
            "email_settings": safe_email_settings,
            "delivery_modes": {
                "info_email": get_notification_delivery_mode(severity="INFO", channel="email"),
                "warn_email": get_notification_delivery_mode(severity="WARN", channel="email"),
                "critical_email": get_notification_delivery_mode(severity="CRITICAL", channel="email"),
            },
            "counts": _admin_report_rows(table_counts),
            "report_runs_by_status": _admin_report_rows(report_runs_by_status),
            "alert_events_by_status": _admin_report_rows(alert_events_by_status),
            "notification_deliveries_by_status": _admin_report_rows(notification_deliveries_by_status),
            "ingestion_jobs_by_status": _admin_report_rows(ingestion_jobs_by_status),
            "open_alerts": _admin_report_rows(open_alerts),
            "recent_report_runs": _admin_report_rows(recent_report_runs),
        }

    finally:
        if conn:
            conn.close()

@app.get("/admin/reports/overview")
def admin_reports_overview(
    request: Request,
    days: int = Query(30, ge=1, le=365)
):
    admin_user = require_admin(request)
    report = get_admin_reporting_overview(days=days)

    return {
        "ok": True,
        "requested_by": admin_user["user_id"],
        "report": report
    }



@app.get("/admin/reports/usage-summary")
def admin_reports_usage_summary(
    request: Request,
    days: int = Query(30, ge=1, le=365)
):
    admin_user = require_admin(request)
    report = get_admin_usage_summary(days=days)

    return {
        "ok": True,
        "requested_by": admin_user["user_id"],
        "report": report
    }


@app.get("/admin/users/{user_id}/usage-report")
def admin_get_user_usage_report(
    request: Request,
    user_id: uuid.UUID,
    days: int = Query(30, ge=1, le=365)
):
    admin_user = require_admin(request)
    report = get_admin_user_usage_report(str(user_id), days=days)

    return {
        "ok": True,
        "requested_by": admin_user["user_id"],
        "report": report
    }


@app.get("/admin/reports/admin-actions")
def admin_reports_admin_actions(
    request: Request,
    limit: int = Query(100, ge=1, le=500)
):
    admin_user = require_admin(request)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    admin_user_id,
                    target_user_id,
                    action_type,
                    action_payload,
                    created_at
                FROM admin_action_logs
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,)
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "ok": True,
        "requested_by": admin_user["user_id"],
        "results": rows
    }

@app.get("/admin/users/search")
def admin_search_users(
    request: Request,
    email: Optional[str] = Query(None),
    display_name: Optional[str] = Query(None),
    seeker_id: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100)
):
    require_admin(request)

    if not any([email, display_name, seeker_id]):
        return JSONResponse(
            content={"error": "Provide email, display_name, or seeker_id."},
            status_code=400
        )

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            conditions = []
            params = []

            if email:
                conditions.append("LOWER(email) LIKE %s")
                params.append(f"%{email.lower().strip()}%")

            if display_name:
                conditions.append("LOWER(display_name) LIKE %s")
                params.append(f"%{display_name.lower().strip()}%")

            if seeker_id:
                conditions.append("seeker_id = %s")
                params.append(seeker_id.strip())

            where_sql = " OR ".join(conditions)

            cur.execute(
                f"""
                SELECT
                    id,
                    email,
                    display_name,
                    seeker_id,
                    email_verified,
                    COALESCE(role, 'user') AS role,
                    COALESCE(plan_code, 'anon') AS plan_code,
                    COALESCE(entitlement_status, 'none') AS entitlement_status,
                    current_period_started_at,
                    subscription_renews_at,
                    subscription_expires_at,
                    COALESCE(cancel_at_period_end, false) AS cancel_at_period_end,
                    last_login
                FROM users
                WHERE {where_sql}
                ORDER BY created_at DESC NULLS LAST
                LIMIT %s
                """,
                (*params, limit)
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "ok": True,
        "results": rows
    }

@app.get("/admin/users/{user_id}/detail")
def admin_get_user_detail(request: Request, user_id: uuid.UUID):
    admin_user = require_admin(request)
    detail = get_admin_user_detail(str(user_id))

    return {
        "ok": True,
        "requested_by": admin_user["user_id"],
        "user": detail
    }

@app.get("/me")
def get_me(request: Request):
    user = get_current_user(request)
    user_id = user["user_id"] if user else None

    anonymous_user_id = get_or_create_anonymous_user_id(
        request
    )

    get_or_create_bound_session_id(
        request,
        anonymous_user_id,
        user_id,
    )

    if user:
        return build_authenticated_me_response(
            user,
            anonymous_user_id,
        )

    return build_anonymous_me_response(
        anonymous_user_id
    )


class OraclePreferenceInput(BaseModel):
    preferred_oracle: Literal["Hathor", "Moses"]


@app.patch("/me/oracle")
def update_oracle_preference(
    request: Request,
    payload: OraclePreferenceInput,
):
    user = get_current_user(request)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required."
        )

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET preferred_oracle = %s
                WHERE id = %s
                RETURNING preferred_oracle
                """,
                (
                    payload.preferred_oracle,
                    user["user_id"],
                )
            )
            row = cur.fetchone()

        if not row:
            conn.rollback()
            raise HTTPException(
                status_code=404,
                detail="User not found."
            )

        conn.commit()

        return {
            "ok": True,
            "preferred_oracle": row["preferred_oracle"],
        }
    finally:
        conn.close()


class AdminSetRoleInput(BaseModel):
    user_id: str
    role: str


@app.post("/admin/users/set-role")
def admin_set_user_role(request: Request, payload: AdminSetRoleInput):
    admin_user = require_admin(request)
    target_role = normalize_user_role(payload.role)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET role = %s
                WHERE id = %s
                RETURNING id, email, display_name, COALESCE(role, 'user') AS role
                """,
                (target_role, payload.user_id)
            )
            row = cur.fetchone()

        conn.commit()
    finally:
        conn.close()

    if not row:
        return JSONResponse(content={"error": "User not found."}, status_code=404)

    log_admin_action(
        admin_user_id=admin_user["user_id"],
        action_type="admin.users.set_role",
        target_user_id=payload.user_id,
        payload={
            "new_role": row["role"]
        }
    )

    return {
        "ok": True,
        "updated_by": admin_user["user_id"],
        "user": row
    }


def ingest_saved_scroll_file(
    *,
    file_path: str,
    safe_name: str,
    original_filename: str,
    mime_type: Optional[str],
    session_id: Optional[str],
    anonymous_user_id: Optional[str],
    authenticated_user_id: Optional[str],
    preserve_unreadable_file: bool = False,
    preserve_duplicate_file: bool = False,
):
    """
    Ingest an already-saved scroll using explicit ownership identities.

    session_id is conversation provenance when one exists.
    anonymous_user_id is persistent browser/device provenance.
    authenticated_user_id is durable authenticated ownership.
    """
    session_id = _canonical_identity_uuid(session_id)
    anonymous_user_id = _canonical_identity_uuid(
        anonymous_user_id
    )
    authenticated_user_id = _canonical_identity_uuid(
        authenticated_user_id
    )

    if not anonymous_user_id and not authenticated_user_id:
        raise ValueError(
            "Scroll ingestion requires an ownership identity"
        )

    # Never carry the historical browser-id-as-session-id contract
    # into newly written scroll records.
    if (
        session_id
        and anonymous_user_id
        and session_id == anonymous_user_id
    ):
        session_id = None

    file_ext = os.path.splitext(file_path)[1].lower()

    # Extract text
    extracted_text = extract_text_from_scroll(file_path)

    if not extracted_text.strip() and preserve_unreadable_file and file_ext == ".pdf":
        ocr_settings = get_scroll_ocr_settings()
        if ocr_settings.get("enabled"):
            logger.info(
                "SCROLL_OCR_ATTEMPT filename=%s max_pages=%s dpi=%s",
                original_filename,
                ocr_settings.get("max_pages"),
                ocr_settings.get("dpi"),
            )
            ocr_text = extract_pdf_text_with_worker_ocr(file_path, settings=ocr_settings)
            if is_scroll_text_sufficient(ocr_text, ocr_settings.get("min_text_chars")):
                extracted_text = ocr_text
                logger.info(
                    "SCROLL_OCR_TEXT_ACCEPTED filename=%s chars=%s",
                    original_filename,
                    len(extracted_text),
                )
            else:
                logger.info(
                    "SCROLL_OCR_TEXT_INSUFFICIENT filename=%s chars=%s",
                    original_filename,
                    len(ocr_text or ""),
                )

    if not extracted_text.strip():
        if not preserve_unreadable_file:
            remove_uploaded_file(file_path)
        if file_ext == ".pdf":
            raise HTTPException(
                status_code=422,
                detail="This scroll appears to be image-based or photo-scanned. The Temple could not reliably read it through the live upload path. Please upload a text-based PDF, TXT, DOCX, or an OCR-processed scan."
            )
        raise HTTPException(status_code=400, detail="Could not extract text from scroll")

    text_hash = hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()

    # Determine corpus layer
    corpus_layer = "personal" if authenticated_user_id else "community"

    # Insert scroll into database
    word_count = len(extracted_text.split())

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrolls (
                    session_id,
                    anonymous_user_id,
                    user_id,
                    source_type,
                    original_filename,
                    mime_type,
                    storage_ref,
                    content_text,
                    content_hash,
                    word_count,
                    corpus_layer
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id;
                """,
                (
                    session_id,
                    anonymous_user_id,
                    authenticated_user_id,
                    "file",
                    original_filename,
                    mime_type,
                    safe_name,
                    extracted_text,
                    text_hash,
                    word_count,
                    corpus_layer
                )
            )
            scroll_id = cur.fetchone()["id"]

            cur.execute(
                """
                INSERT INTO scroll_associations (
                    scroll_id,
                    user_id,
                    session_id,
                    anonymous_user_id
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    scroll_id,
                    authenticated_user_id,
                    session_id,
                    anonymous_user_id,
                )
            )

        conn.commit()
        conn.close()

    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        conn.close()

        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id
                FROM scrolls
                WHERE content_hash = %s
                LIMIT 1
                """,
                (text_hash,)
            )
            existing_scroll = cur.fetchone()

            if not existing_scroll:
                conn.close()

                if not preserve_duplicate_file and os.path.exists(file_path):
                    os.remove(file_path)

                if authenticated_user_id:
                    refresh_user_scroll_count(authenticated_user_id)

                return JSONResponse(
                    content={
                        "duplicate": True,
                        "message": "This scroll is already present in the Temple, but the existing record could not be linked."
                    },
                    status_code=409
                )

            scroll_id = existing_scroll["id"]

            cur.execute(
                """
                INSERT INTO scroll_associations (
                    scroll_id,
                    user_id,
                    session_id,
                    anonymous_user_id
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    scroll_id,
                    authenticated_user_id,
                    session_id,
                    anonymous_user_id,
                )
            )

        conn.commit()
        conn.close()

        if not preserve_duplicate_file and os.path.exists(file_path):
            os.remove(file_path)

        if authenticated_user_id:
            refresh_user_scroll_count(authenticated_user_id)

        duplicate_payload = {
            "duplicate": True,
            "scroll_id": str(scroll_id),
            "seeker_status": "ready",
            "dedupe_kind": "content_hash",
            "admin_status": "content_hash_duplicate",
            "message": "Ready in your Library.",
            "admin_message": "Content hash matched an existing scroll; corpus was not expanded."
        }

        if not authenticated_user_id:
            duplicate_payload["message"] = "Ready in your Library."
            stats = get_anonymous_upload_stats(anonymous_user_id)
            duplicate_payload["upload_count_for_browser"] = stats["upload_count"]
            duplicate_payload["continuity_nudges"] = build_claim_nudges(stats["upload_count"])

        return JSONResponse(
            content=duplicate_payload,
            status_code=409
        )

    # --- Chunk the uploaded scroll ---
    CHUNK_SIZE = 1000
    OVERLAP = 150

    chunks = []
    start = 0
    length = len(extracted_text)

    while start < length:
        end = start + CHUNK_SIZE
        chunk = extracted_text[start:end]
        chunks.append(chunk)
        start += CHUNK_SIZE - OVERLAP

    chunk_rows_for_embedding = []

    conn = get_db_connection()
    with conn.cursor() as cur:
        for i, chunk in enumerate(chunks):
            cur.execute(
                """
                INSERT INTO scroll_chunks
                (scroll_id, chunk_index, chunk_text)
                VALUES (%s,%s,%s)
                RETURNING id
                """,
                (scroll_id, i, chunk)
            )
            chunk_row = cur.fetchone()
            if chunk_row and (chunk or "").strip():
                chunk_rows_for_embedding.append({
                    "id": chunk_row["id"],
                    "chunk_text": chunk,
                })

            # Do not warm the legacy local embedding cache inside ingestion.
            # Database pgvector embeddings are stored after chunk commit for
            # small documents; large documents are handled by backfill/worker.

    conn.commit()
    conn.close()

    embedding_summary = store_scroll_chunk_embeddings(chunk_rows_for_embedding)
    logger.info(
        "SCROLL_CHUNK_DB_EMBEDDING_SUMMARY scroll_id=%s chunk_count=%s summary=%s",
        scroll_id,
        len(chunk_rows_for_embedding),
        embedding_summary,
    )

    # Refresh cached user scroll_count from authoritative scroll_associations
    if authenticated_user_id:
        refresh_user_scroll_count(authenticated_user_id)

    response_payload = {
        "message": "📜 Your scroll has been uploaded.",
        "scroll_id": scroll_id
    }

    if not authenticated_user_id:
        stats = get_anonymous_upload_stats(anonymous_user_id)
        response_payload["upload_count_for_browser"] = stats["upload_count"]
        response_payload["continuity_nudges"] = build_claim_nudges(stats["upload_count"])
        response_payload["claim_recommended"] = stats["upload_count"] >= 1
        response_payload["anonymous_upload_limit"] = ANONYMOUS_UPLOAD_LIMIT

    return response_payload



def get_scroll_upload_queue_settings() -> dict:
    """
    Return upload queue settings.

    Queue mode is intentionally off by default. When enabled, only files at or
    above the byte threshold are queued; smaller files keep the existing
    synchronous seeker experience.
    """
    enabled = _env_flag("SCROLL_UPLOAD_QUEUE_ENABLED", default=False)

    try:
        min_bytes = int(os.getenv("SCROLL_UPLOAD_QUEUE_MIN_BYTES", "500000"))
    except (TypeError, ValueError):
        min_bytes = 500000

    min_bytes = max(1, min_bytes)

    return {
        "enabled": enabled,
        "min_bytes": min_bytes,
    }


@app.post("/upload_scroll")
async def upload_scroll(request: Request, background_tasks: BackgroundTasks, scroll: UploadFile = File(...), seeker_id: str = Form(None), anonymous_user_id: str = Form(None)):
    user = get_current_user(request)
    authenticated_user_id = user["user_id"] if user else None

    anonymous_user_id = get_or_create_anonymous_user_id(
        request,
        anonymous_user_id,
    )

    session_id = get_or_create_bound_session_id(
        request,
        anonymous_user_id,
        authenticated_user_id,
    )

    if not authenticated_user_id:
        stats = get_anonymous_upload_stats(anonymous_user_id)
        upload_count = stats["upload_count"]
        last_uploaded_at = stats["last_uploaded_at"]

        if upload_count >= ANONYMOUS_UPLOAD_LIMIT:
            logger.warning(
                "Anonymous upload cap hit ip_hash=%s anonymous_user_id=%s upload_count=%s",
                get_ip_hash(request),
                anonymous_user_id,
                upload_count,
            )
            cap_message = UPLOAD_SEEKER_MESSAGE_TEXT[SEEKER_MESSAGE_UPLOAD_CAP]
            return JSONResponse(
                content=build_upload_status_payload(
                    ok=False,
                    accepted=False,
                    rejected=True,
                    terminal=True,
                    upload_state=UPLOAD_STATE_REJECTED_CAP,
                    library_state=LIBRARY_STATE_NONE,
                    seeker_title_key=SEEKER_TITLE_UPLOAD_CREATE_ACCOUNT,
                    seeker_message_key=SEEKER_MESSAGE_UPLOAD_CAP,
                    admin_status=UPLOAD_ADMIN_STATUS_REJECTED_ANONYMOUS_CAP,
                    admin_message="Anonymous upload cap reached before storage.",
                    claim_required=True,
                    claim_recommended=True,
                    anonymous_uploads_remaining=0,
                    artifact_preserved=False,
                    extra={
                        "error": cap_message,
                        "message": cap_message,
                        "upload_count_for_browser": upload_count,
                        "continuity_nudges": [],
                        "anonymous_upload_limit": ANONYMOUS_UPLOAD_LIMIT,
                    },
                ),
                status_code=403
            )

        if last_uploaded_at:
            if last_uploaded_at.tzinfo is None:
                last_uploaded_at = last_uploaded_at.replace(tzinfo=timezone.utc)

            elapsed = (utc_now() - last_uploaded_at).total_seconds()
            if elapsed < ANONYMOUS_UPLOAD_COOLDOWN_SECONDS:
                seconds_remaining = max(1, int(ANONYMOUS_UPLOAD_COOLDOWN_SECONDS - elapsed + 0.999))
                logger.warning(
                    "Anonymous upload cooldown hit ip_hash=%s anonymous_user_id=%s upload_count=%s seconds_remaining=%s",
                    get_ip_hash(request),
                    anonymous_user_id,
                    upload_count,
                    seconds_remaining,
                )
                cooldown_message = UPLOAD_SEEKER_MESSAGE_TEXT[SEEKER_MESSAGE_UPLOAD_COOLDOWN]
                return JSONResponse(
                    content=build_upload_status_payload(
                        ok=False,
                        accepted=False,
                        rejected=True,
                        terminal=True,
                        upload_state=UPLOAD_STATE_REJECTED_COOLDOWN,
                        library_state=LIBRARY_STATE_NONE,
                        seeker_title_key=SEEKER_TITLE_UPLOAD_PAUSED,
                        seeker_message_key=SEEKER_MESSAGE_UPLOAD_COOLDOWN,
                        admin_status=UPLOAD_ADMIN_STATUS_REJECTED_COOLDOWN,
                        admin_message="Anonymous upload cooldown hit before storage.",
                        retry_after_seconds=seconds_remaining,
                        anonymous_uploads_remaining=max(ANONYMOUS_UPLOAD_LIMIT - upload_count, 0),
                        artifact_preserved=False,
                        extra={
                            "error": cooldown_message,
                            "message": cooldown_message,
                            "warning": "We’re slowing repeated uploads to protect the Temple. Please wait a moment before trying again.",
                            "cooldown_seconds_remaining": seconds_remaining,
                            "upload_count_for_browser": upload_count,
                        },
                    ),
                    status_code=429
                )

    seeker_id = resolve_seeker_id(anonymous_user_id, seeker_id)

    # Save the file with safe name to prevent overwrites
    safe_name = f"{uuid.uuid4()}_{scroll.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(scroll.file, f)

    queue_settings = get_scroll_upload_queue_settings()
    file_size_bytes = os.path.getsize(file_path)

    if queue_settings["enabled"] and file_size_bytes >= queue_settings["min_bytes"]:
        storage_ref = save_scroll_upload_to_storage(
            file_path,
            safe_name,
            original_filename=scroll.filename,
            mime_type=scroll.content_type,
        )
        storage_backend = "r2" if is_r2_storage_ref(storage_ref) else "local"

        corpus_layer = "personal" if authenticated_user_id else "community"
        job_id = create_ingestion_job(
            session_id=session_id,
            anonymous_user_id=anonymous_user_id,
            user_id=authenticated_user_id,
            job_type="scroll_upload",
            status="queued",
            original_filename=scroll.filename,
            storage_ref=storage_ref,
            mime_type=scroll.content_type,
            corpus_layer=corpus_layer,
        )

        if is_r2_storage_ref(storage_ref):
            remove_uploaded_file(file_path)

        logger.info(
            "SCROLL_UPLOAD_QUEUED anonymous_user_id=%s authenticated_user_present=%s filename=%s size_bytes=%s job_id=%s",
            anonymous_user_id,
            bool(authenticated_user_id),
            scroll.filename,
            file_size_bytes,
            job_id,
        )

        auto_process_settings = get_scroll_upload_auto_process_settings()

        library_upload_id = None
        try:
            library_upload_id = create_library_upload(
                session_id=None,
                anonymous_user_id=anonymous_user_id,
                user_id=authenticated_user_id,
                ingestion_job_id=str(job_id) if job_id else None,
                original_filename=scroll.filename,
                mime_type=scroll.content_type,
                file_size_bytes=file_size_bytes,
                storage_ref=storage_ref,
                storage_backend=storage_backend,
                seeker_status="queued",
                admin_status="queued_upload_received",
                dedupe_kind="none",
                metadata_json={
                    "corpus_layer": corpus_layer,
                    "queue_min_bytes": queue_settings["min_bytes"],
                    "auto_process_enabled": auto_process_settings["enabled"],
                    "auto_process_max_jobs": auto_process_settings["max_jobs"] if auto_process_settings["enabled"] else 0,
                },
            )
        except Exception as exc:
            logger.warning(
                "LIBRARY_UPLOAD_CREATE_FOR_QUEUED_UPLOAD_FAILED job_id=%s filename=%s error=%s",
                job_id,
                scroll.filename,
                exc,
            )

        if auto_process_settings["enabled"]:
            background_tasks.add_task(
                run_scroll_upload_auto_processor,
                auto_process_settings["max_jobs"],
            )

        acceptance_message = UPLOAD_SEEKER_MESSAGE_TEXT[SEEKER_MESSAGE_UPLOAD_SAVED_READING]
        job_id_text = str(job_id) if job_id else None
        library_upload_id_text = str(library_upload_id) if library_upload_id else None
        return JSONResponse(
            content=build_upload_status_payload(
                ok=True,
                accepted=True,
                rejected=False,
                terminal=False,
                upload_state=UPLOAD_STATE_QUEUED,
                library_state=LIBRARY_STATE_QUEUED,
                seeker_title_key=SEEKER_TITLE_UPLOAD_SAVED,
                seeker_message_key=SEEKER_MESSAGE_UPLOAD_SAVED_READING,
                admin_status=UPLOAD_ADMIN_STATUS_QUEUED_UPLOAD_RECEIVED,
                admin_message="Queued upload accepted and saved for background reading.",
                claim_required=False,
                claim_recommended=not bool(authenticated_user_id),
                anonymous_uploads_remaining=(
                    max(ANONYMOUS_UPLOAD_LIMIT - upload_count, 0)
                    if not authenticated_user_id
                    else None
                ),
                upload_id=library_upload_id_text,
                library_upload_id=library_upload_id_text,
                ingestion_job_id=job_id_text,
                job_id=job_id_text,
                artifact_preserved=True,
                storage_backend=storage_backend,
                duplicate=False,
                dedupe_kind=UPLOAD_DEDUPE_KIND_NONE,
                needs_ocr=False,
                extra={
                    "message": acceptance_message,
                    "queued": True,
                    "status": "queued",
                    "filename": scroll.filename,
                    "file_size_bytes": file_size_bytes,
                    "queue_min_bytes": queue_settings["min_bytes"],
                    "auto_process_enabled": auto_process_settings["enabled"],
                    "auto_process_max_jobs": auto_process_settings["max_jobs"],
                    "upload_count_for_browser": upload_count if not authenticated_user_id else None,
                    "continuity_nudges": build_claim_nudges(upload_count) if not authenticated_user_id else [],
                    "anonymous_upload_limit": ANONYMOUS_UPLOAD_LIMIT if not authenticated_user_id else None,
                },
            ),
            status_code=202
        )

    return ingest_saved_scroll_file(
        file_path=file_path,
        safe_name=safe_name,
        original_filename=scroll.filename,
        mime_type=scroll.content_type,
        session_id=session_id,
        anonymous_user_id=anonymous_user_id,
        authenticated_user_id=authenticated_user_id,
    )

class AdminEntitlementOverrideInput(BaseModel):
    user_id: str
    plan_code: str
    entitlement_status: str
    current_period_started_at: Optional[datetime.datetime] = None
    subscription_renews_at: Optional[datetime.datetime] = None
    subscription_expires_at: Optional[datetime.datetime] = None
    cancel_at_period_end: bool = False


@app.post("/admin/users/entitlement/override")
def admin_override_entitlement(request: Request, payload: AdminEntitlementOverrideInput):
    admin_user = require_admin(request)

    apply_admin_entitlement_override(
        user_id=payload.user_id,
        plan_code=payload.plan_code,
        entitlement_status=payload.entitlement_status,
        current_period_started_at=payload.current_period_started_at,
        subscription_renews_at=payload.subscription_renews_at,
        subscription_expires_at=payload.subscription_expires_at,
        cancel_at_period_end=payload.cancel_at_period_end
    )

    entitlement = get_user_entitlement_snapshot(payload.user_id)

    log_admin_action(
        admin_user_id=admin_user["user_id"],
        action_type="admin.users.entitlement_override",
        target_user_id=payload.user_id,
        payload={
            "plan_code": payload.plan_code,
            "entitlement_status": payload.entitlement_status,
            "current_period_started_at": serialize_dt(payload.current_period_started_at),
            "subscription_renews_at": serialize_dt(payload.subscription_renews_at),
            "subscription_expires_at": serialize_dt(payload.subscription_expires_at),
            "cancel_at_period_end": payload.cancel_at_period_end
        }
    )

    return {
        "ok": True,
        "updated_by": admin_user["user_id"],
        "user_id": payload.user_id,
        "entitlement": {
            "raw_plan_code": entitlement["raw_plan_code"],
            "effective_plan_code": entitlement["effective_plan_code"],
            "entitlement_status": entitlement["entitlement_status"],
            "subscription_started_at": serialize_dt(entitlement["subscription_started_at"]),
            "current_period_started_at": serialize_dt(entitlement["current_period_started_at"]),
            "subscription_renews_at": serialize_dt(entitlement["subscription_renews_at"]),
            "subscription_expires_at": serialize_dt(entitlement["subscription_expires_at"]),
            "cancel_at_period_end": entitlement["cancel_at_period_end"]
        }
    }


def stripe_ts_to_dt(value: Optional[int]) -> Optional[datetime.datetime]:
    if value is None:
        return None
    return datetime.datetime.fromtimestamp(value, tz=timezone.utc)


def extract_stripe_subscription_id(obj: dict) -> Optional[str]:
    if not obj:
        return None

    candidates = [
        obj.get("subscription"),
        ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription"),
        ((((obj.get("lines") or {}).get("data") or [{}])[0].get("parent") or {}).get("subscription_item_details") or {}).get("subscription"),
    ]

    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("id")
        if candidate:
            return candidate

    return None


def stripe_obj_to_plain(value):
    if isinstance(value, dict):
        return {k: stripe_obj_to_plain(v) for k, v in value.items()}

    if isinstance(value, list):
        return [stripe_obj_to_plain(v) for v in value]

    if hasattr(value, "to_dict_recursive"):
        try:
            return stripe_obj_to_plain(value.to_dict_recursive())
        except Exception:
            pass

    raw_data = getattr(value, "_data", None)
    if isinstance(raw_data, dict):
        return {k: stripe_obj_to_plain(v) for k, v in raw_data.items()}

    return value


def resolve_user_and_subscription_context(
    stripe_customer_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None
) -> dict:
    context = {
        "user_id": None,
        "billing_customer_id": None,
        "subscription_row_id": None,
        "plan_code": None,
        "support_mode": None,
        "user_email": None,
        "display_name": None,
    }

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if stripe_subscription_id:
                cur.execute(
                    """
                    SELECT
                        s.id AS subscription_row_id,
                        s.user_id,
                        s.plan_code,
                        s.support_mode,
                        bc.id AS billing_customer_id,
                        u.email AS user_email,
                        u.display_name
                    FROM subscriptions s
                    LEFT JOIN billing_customers bc ON bc.id = s.billing_customer_id
                    LEFT JOIN users u ON u.id = s.user_id
                    WHERE s.provider = 'stripe'
                      AND s.stripe_subscription_id = %s
                    LIMIT 1
                    """,
                    (stripe_subscription_id,)
                )
                row = cur.fetchone()
                if row:
                    context.update({
                        "user_id": row["user_id"],
                        "billing_customer_id": row["billing_customer_id"],
                        "subscription_row_id": row["subscription_row_id"],
                        "plan_code": row["plan_code"],
                        "support_mode": row["support_mode"],
                        "user_email": row["user_email"],
                        "display_name": row["display_name"],
                    })
                    return context

            if stripe_customer_id:
                cur.execute(
                    """
                    SELECT
                        bc.id AS billing_customer_id,
                        bc.user_id,
                        u.email AS user_email,
                        u.display_name
                    FROM billing_customers bc
                    LEFT JOIN users u ON u.id = bc.user_id
                    WHERE bc.provider = 'stripe'
                      AND bc.stripe_customer_id = %s
                    LIMIT 1
                    """,
                    (stripe_customer_id,)
                )
                row = cur.fetchone()
                if row:
                    context.update({
                        "user_id": row["user_id"],
                        "billing_customer_id": row["billing_customer_id"],
                        "user_email": row["user_email"],
                        "display_name": row["display_name"],
                    })
    finally:
        conn.close()

    return context


def get_active_paid_rail_for_user(user_id: str) -> Optional[dict]:
    """
    v11.4B provider-neutral active paid rail guard.

    Product rule:
    - one seeker may have only one active paid rail at a time
    - active Stripe may change plan through Stripe
    - active Apple/Google must block a new Stripe checkout
    - expired, failed, cancelled, refunded, superseded rows do not block
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    provider,
                    plan_code,
                    support_mode,
                    provider_status,
                    internal_status,
                    provider_subscription_id,
                    stripe_subscription_id,
                    current_period_start,
                    current_period_end,
                    cancel_at_period_end,
                    updated_at
                FROM subscriptions
                WHERE user_id = %s
                  AND support_mode IN ('monthly_recurring', 'annual_recurring')
                  AND COALESCE(provider_status, '') IN ('active', 'trialing')
                  AND COALESCE(internal_status, '') = 'active'
                  AND ended_at IS NULL
                  AND (current_period_end IS NULL OR current_period_end >= NOW())
                ORDER BY updated_at DESC NULLS LAST, current_period_end DESC NULLS LAST
                LIMIT 1
                """,
                (user_id,)
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_active_recurring_subscription_for_user(user_id: str) -> Optional[dict]:
    """
    Stripe-specific helper retained for same-rail Stripe plan changes.
    """
    active_rail = get_active_paid_rail_for_user(user_id)
    if active_rail and active_rail.get("provider") == "stripe":
        return active_rail
    return None


def upsert_local_stripe_subscription(
    subscription_obj: dict,
    fallback_user_id: Optional[str] = None,
    fallback_plan_code: Optional[str] = None,
    fallback_support_mode: Optional[str] = None,
    fallback_checkout_session_id: Optional[str] = None
) -> dict:
    subscription_obj = stripe_obj_to_plain(subscription_obj)

    metadata = subscription_obj.get("metadata") or {}
    stripe_customer_id = subscription_obj.get("customer")
    stripe_subscription_id = subscription_obj.get("id")

    context = resolve_user_and_subscription_context(
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id
    )

    user_id = metadata.get("user_id") or fallback_user_id or context.get("user_id")
    plan_code = normalize_plan_code(
        metadata.get("plan_code") or fallback_plan_code or context.get("plan_code")
    )
    support_mode = (
        (metadata.get("support_mode") or fallback_support_mode or context.get("support_mode") or "")
        .strip()
        .lower()
    )

    if not support_mode:
        try:
            interval = subscription_obj["items"]["data"][0]["price"]["recurring"]["interval"]
            support_mode = "annual_recurring" if interval == "year" else "monthly_recurring"
        except Exception:
            support_mode = "monthly_recurring"

    billing_customer_id = context.get("billing_customer_id")

    if billing_customer_id is None and stripe_customer_id:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id
                    FROM billing_customers
                    WHERE provider = 'stripe'
                      AND stripe_customer_id = %s
                    LIMIT 1
                    """,
                    (stripe_customer_id,)
                )
                row = cur.fetchone()
                if row:
                    billing_customer_id = row["id"]
                    if not user_id:
                        user_id = row["user_id"]
        finally:
            conn.close()

    price_id = None
    product_id = None
    try:
        price_id = subscription_obj["items"]["data"][0]["price"]["id"]
        product_id = subscription_obj["items"]["data"][0]["price"]["product"]
    except Exception:
        pass

    latest_invoice = subscription_obj.get("latest_invoice")
    if isinstance(latest_invoice, dict):
        latest_invoice_id = latest_invoice.get("id")
    else:
        latest_invoice_id = latest_invoice

    provider_status = subscription_obj.get("status") or "incomplete"

    if provider_status in {"active", "trialing"}:
        internal_status = "active"
    elif provider_status in {"past_due", "unpaid"}:
        internal_status = "expired"
    elif provider_status in {"canceled"}:
        internal_status = "cancelled"
    elif provider_status in {"incomplete_expired"}:
        internal_status = "expired"
    else:
        internal_status = "pending"

    started_at = (
        stripe_ts_to_dt(subscription_obj.get("start_date"))
        or stripe_ts_to_dt(subscription_obj.get("created"))
        or stripe_ts_to_dt(subscription_obj.get("current_period_start"))
    )

    current_period_start = stripe_ts_to_dt(subscription_obj.get("current_period_start"))
    current_period_end = stripe_ts_to_dt(subscription_obj.get("current_period_end"))
    cancel_at_period_end = bool(subscription_obj.get("cancel_at_period_end"))
    auto_renews = not cancel_at_period_end and support_mode in {"monthly_recurring", "annual_recurring"}

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO subscriptions (
                    user_id,
                    billing_customer_id,
                    plan_code,
                    provider,
                    provider_subscription_id,
                    provider_price_id,
                    support_mode,
                    provider_status,
                    internal_status,
                    started_at,
                    current_period_start,
                    current_period_end,
                    cancel_at_period_end,
                    auto_renews,
                    canceled_at,
                    ended_at,
                    stripe_subscription_id,
                    stripe_checkout_session_id,
                    stripe_product_id,
                    stripe_price_id,
                    status,
                    latest_invoice_id,
                    livemode,
                    subscription_metadata_json
                )
                VALUES (
                    %s, %s, %s, 'stripe', %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (stripe_subscription_id)
                DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    billing_customer_id = EXCLUDED.billing_customer_id,
                    plan_code = EXCLUDED.plan_code,
                    provider_subscription_id = EXCLUDED.provider_subscription_id,
                    provider_price_id = EXCLUDED.provider_price_id,
                    support_mode = EXCLUDED.support_mode,
                    provider_status = EXCLUDED.provider_status,
                    internal_status = EXCLUDED.internal_status,
                    started_at = EXCLUDED.started_at,
                    current_period_start = EXCLUDED.current_period_start,
                    current_period_end = EXCLUDED.current_period_end,
                    cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                    auto_renews = EXCLUDED.auto_renews,
                    canceled_at = EXCLUDED.canceled_at,
                    ended_at = EXCLUDED.ended_at,
                    stripe_checkout_session_id = COALESCE(EXCLUDED.stripe_checkout_session_id, subscriptions.stripe_checkout_session_id),
                    stripe_product_id = COALESCE(EXCLUDED.stripe_product_id, subscriptions.stripe_product_id),
                    stripe_price_id = COALESCE(EXCLUDED.stripe_price_id, subscriptions.stripe_price_id),
                    status = EXCLUDED.status,
                    latest_invoice_id = EXCLUDED.latest_invoice_id,
                    livemode = EXCLUDED.livemode,
                    subscription_metadata_json = EXCLUDED.subscription_metadata_json,
                    updated_at = NOW()
                RETURNING id, user_id, plan_code, support_mode, stripe_subscription_id
                """,
                (
                    user_id,
                    billing_customer_id,
                    plan_code,
                    stripe_subscription_id,
                    price_id,
                    support_mode,
                    provider_status,
                    internal_status,
                    started_at,
                    current_period_start,
                    current_period_end,
                    cancel_at_period_end,
                    auto_renews,
                    stripe_ts_to_dt(subscription_obj.get("canceled_at")),
                    stripe_ts_to_dt(subscription_obj.get("ended_at")),
                    stripe_subscription_id,
                    fallback_checkout_session_id,
                    product_id,
                    price_id,
                    provider_status,
                    latest_invoice_id,
                    bool(subscription_obj.get("livemode")),
                    json.dumps(metadata, default=str),
                )
            )
            row = cur.fetchone()
        conn.commit()
        return row
    finally:
        conn.close()



def cancel_failed_stripe_subscription(stripe_subscription_id: Optional[str]) -> dict:
    """
    Cancel a Stripe recurring subscription after renewal failure.

    Product policy:
    - failed renewal demotes access immediately
    - no long retry cycle
    - no surprise out-of-cycle charge after the seeker has been moved to the floor

    This helper is intentionally non-fatal. If Stripe cancellation fails,
    the access demotion and failed transaction record should still stand.
    """
    if not stripe_subscription_id:
        return {
            "cancelled": False,
            "subscription_row": None,
            "error": "missing_stripe_subscription_id",
        }

    logger.info(
        "Stripe failed renewal cancellation begin subscription=%s",
        stripe_subscription_id,
    )

    try:
        canceled_subscription = stripe.Subscription.delete(stripe_subscription_id)
        canceled_subscription = stripe_obj_to_plain(canceled_subscription)

        sub_row = upsert_local_stripe_subscription(
            subscription_obj=canceled_subscription
        )

        logger.info(
            "Stripe failed renewal cancellation complete subscription=%s local_subscription_id=%s user_id=%s",
            stripe_subscription_id,
            sub_row.get("id") if sub_row else None,
            sub_row.get("user_id") if sub_row else None,
        )

        return {
            "cancelled": True,
            "subscription_row": sub_row,
            "error": None,
        }
    except Exception as exc:
        logger.exception(
            "Stripe failed renewal cancellation failed subscription=%s error=%s",
            stripe_subscription_id,
            exc,
        )
        return {
            "cancelled": False,
            "subscription_row": None,
            "error": str(exc),
        }



def upsert_billing_transaction_from_invoice(
    event_id: str,
    invoice_obj: dict,
    user_id: Optional[str],
    subscription_row_id: Optional[int],
    plan_code: Optional[str],
    support_mode: Optional[str],
    transaction_kind: str,
    status: str
) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO billing_transactions (
                    user_id,
                    subscription_id,
                    plan_code,
                    provider,
                    transaction_kind,
                    provider_invoice_id,
                    provider_payment_intent_id,
                    provider_charge_id,
                    currency,
                    gross_amount_cents,
                    net_amount_cents,
                    status,
                    occurred_at,
                    support_mode,
                    stripe_event_id,
                    stripe_invoice_id,
                    stripe_payment_intent_id,
                    stripe_charge_id,
                    amount_subtotal,
                    amount_total,
                    livemode,
                    raw_summary_json
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    'stripe',
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb
                )
                """,
                (
                    user_id,
                    subscription_row_id,
                    plan_code,
                    transaction_kind,
                    invoice_obj.get("id"),
                    invoice_obj.get("payment_intent"),
                    invoice_obj.get("charge"),
                    invoice_obj.get("currency") or "usd",
                    invoice_obj.get("total") or 0,
                    invoice_obj.get("subtotal") or invoice_obj.get("total") or 0,
                    status,
                    stripe_ts_to_dt(invoice_obj.get("created")) or utc_now(),
                    support_mode,
                    event_id,
                    invoice_obj.get("id"),
                    invoice_obj.get("payment_intent"),
                    invoice_obj.get("charge"),
                    invoice_obj.get("subtotal"),
                    invoice_obj.get("total"),
                    bool(invoice_obj.get("livemode")),
                    json.dumps({
                        "object": "invoice",
                        "billing_reason": invoice_obj.get("billing_reason"),
                        "subscription": invoice_obj.get("subscription"),
                        "customer": invoice_obj.get("customer"),
                    }, default=str),
                )
            )
        conn.commit()
    finally:
        conn.close()


def send_upcoming_renewal_email(
    user_email: str,
    display_name: Optional[str],
    plan_code: str,
    support_mode: str,
    amount_due: Optional[int],
    currency: Optional[str],
    renewal_at: Optional[datetime.datetime]
) -> None:
    if not user_email:
        return

    amount_text = ""
    if amount_due is not None and currency:
        amount_text = f"{amount_due / 100:.2f} {currency.upper()}"

    renewal_text = serialize_dt(renewal_at) if renewal_at else "the upcoming renewal date"

    salutation = display_name or "Seeker"
    support_label = "annual recurring support" if support_mode == "annual_recurring" else "monthly recurring support"

    html = f"""
    <p>Hello {salutation},</p>
    <p>This is an advance notice that your {plan_code.title()} {support_label} is scheduled to renew on <strong>{renewal_text}</strong>.</p>
    <p>Upcoming charge: <strong>{amount_text or "see Stripe renewal invoice"}</strong></p>
    <p>If you need to review or update your support before renewal, please do so before that date.</p>
    <p>Thank you for supporting God Incorporated.</p>
    """

    send_email(
        to_email=user_email,
        subject=f"Upcoming renewal notice for {plan_code.title()} support",
        html=html
    )



def insert_payment_event_if_new(event: dict, user_id: Optional[str]) -> bool:
    event_id = event.get("id")
    event_created_at = stripe_ts_to_dt(event.get("created"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO payment_events (
                    provider,
                    provider_event_id,
                    stripe_event_id,
                    event_type,
                    event_created_at,
                    object_type,
                    object_id,
                    user_id,
                    livemode,
                    api_version,
                    payload_json,
                    processing_status,
                    handler_version
                )
                VALUES (
                    'stripe',
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::jsonb,
                    'received',
                    'phase8_webhook_v2'
                )
                ON CONFLICT (provider, provider_event_id) DO NOTHING
                RETURNING id
                """,
                (
                    event_id,
                    event_id,
                    event.get("type"),
                    event_created_at,
                    (event.get("data", {}).get("object", {}) or {}).get("object"),
                    (event.get("data", {}).get("object", {}) or {}).get("id"),
                    user_id,
                    bool(event.get("livemode")),
                    event.get("api_version"),
                    json.dumps(event, default=str),
                )
            )
            row = cur.fetchone()
        conn.commit()
        return bool(row)
    finally:
        conn.close()



def mark_payment_event_processed(event_id: str, helper_name: Optional[str]) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE payment_events
                SET
                    processing_status = 'processed',
                    helper_name = %s,
                    helper_applied_at = NOW(),
                    processed_at = NOW(),
                    processing_error = NULL,
                    error_text = NULL
                WHERE stripe_event_id = %s
                   OR provider_event_id = %s
                """,
                (helper_name, event_id, event_id)
            )
        conn.commit()
    finally:
        conn.close()



def mark_payment_event_error(event_id: str, error_text: str) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE payment_events
                SET
                    processing_status = 'failed',
                    processed_at = NOW(),
                    processing_error = %s,
                    error_text = %s
                WHERE stripe_event_id = %s
                   OR provider_event_id = %s
                """,
                (error_text[:2000], error_text[:2000], event_id, event_id)
            )
        conn.commit()
    finally:
        conn.close()


def process_stripe_event(event: dict) -> dict:
    event = stripe_obj_to_plain(event)

    event_type = event.get("type")
    obj = event.get("data", {}).get("object", {}) or {}

    stripe_customer_id = obj.get("customer")
    stripe_subscription_id = extract_stripe_subscription_id(obj)

    context = resolve_user_and_subscription_context(
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id
    )

    user_id = (obj.get("metadata") or {}).get("user_id") or context.get("user_id")

    try:
        logger.info(
            "Stripe webhook begin event_id=%s event_type=%s user_id=%s customer=%s subscription=%s",
            event.get("id"),
            event_type,
            user_id,
            stripe_customer_id,
            stripe_subscription_id,
        )

        is_new = insert_payment_event_if_new(event, user_id)
        logger.info(
            "Stripe webhook event insert result event_id=%s event_type=%s is_new=%s",
            event.get("id"),
            event_type,
            is_new,
        )

        if not is_new:
            return {"ok": True, "duplicate": True, "event_type": event_type}

        helper_name = None

        if event_type == "checkout.session.completed":
            if obj.get("mode") == "subscription" and obj.get("subscription"):
                subscription_obj = stripe.Subscription.retrieve(obj.get("subscription"))
                subscription_obj = stripe_obj_to_plain(subscription_obj)
                sub_row = upsert_local_stripe_subscription(
                    subscription_obj=subscription_obj,
                    fallback_user_id=(obj.get("metadata") or {}).get("user_id"),
                    fallback_plan_code=(obj.get("metadata") or {}).get("plan_code"),
                    fallback_support_mode=(obj.get("metadata") or {}).get("support_mode"),
                    fallback_checkout_session_id=obj.get("id")
                )

                sub_metadata = subscription_obj.get("metadata") or {}
                activation_user_id = sub_row.get("user_id") or sub_metadata.get("user_id") or (obj.get("metadata") or {}).get("user_id")
                activation_plan_code = sub_row.get("plan_code") or sub_metadata.get("plan_code") or (obj.get("metadata") or {}).get("plan_code")
                activation_support_mode = sub_row.get("support_mode") or sub_metadata.get("support_mode") or (obj.get("metadata") or {}).get("support_mode")

                period_start = stripe_ts_to_dt(subscription_obj.get("current_period_start"))
                period_end = stripe_ts_to_dt(subscription_obj.get("current_period_end"))

                if activation_user_id and activation_plan_code and activation_support_mode in {"monthly_recurring", "annual_recurring"}:
                    apply_subscription_renewal_success(
                        user_id=activation_user_id,
                        plan_code=activation_plan_code,
                        period_start=period_start,
                        period_end=period_end,
                        support_mode=activation_support_mode
                    )
                    helper_name = f"checkout_activate:{activation_support_mode}"
                else:
                    helper_name = f"upsert_local_stripe_subscription:{sub_row.get('support_mode')}"
            elif obj.get("mode") == "payment":
                metadata = obj.get("metadata") or {}
                if metadata.get("support_mode") == "annual_prepaid":
                    apply_annual_prepaid_activation(
                        user_id=metadata.get("user_id"),
                        plan_code=metadata.get("plan_code")
                    )
                    helper_name = "apply_annual_prepaid_activation"

        elif event_type in {"customer.subscription.updated", "customer.subscription.deleted"}:
            sub_row = upsert_local_stripe_subscription(subscription_obj=obj)
            if event_type == "customer.subscription.updated":
                set_cancel_at_period_end(
                    user_id=sub_row.get("user_id"),
                    should_cancel=bool(obj.get("cancel_at_period_end"))
                )
                helper_name = "set_cancel_at_period_end"
            else:
                if sub_row.get("user_id"):
                    set_cancel_at_period_end(sub_row.get("user_id"), False)
                helper_name = "subscription_deleted_observed"

        elif event_type == "invoice.paid":
            context = resolve_user_and_subscription_context(
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=stripe_subscription_id
            )

            if context.get("user_id") and (not context.get("plan_code") or not context.get("support_mode")) and stripe_subscription_id:
                subscription_obj = stripe.Subscription.retrieve(stripe_subscription_id)
                subscription_obj = stripe_obj_to_plain(subscription_obj)
                upsert_local_stripe_subscription(subscription_obj=subscription_obj)
                context = resolve_user_and_subscription_context(
                    stripe_customer_id=obj.get("customer"),
                    stripe_subscription_id=stripe_subscription_id
                )

            if context.get("user_id") and context.get("plan_code") and context.get("support_mode") in {"monthly_recurring", "annual_recurring"}:
                lines = ((obj.get("lines") or {}).get("data") or [])
                period_start = None
                period_end = None
                if lines:
                    period_start = stripe_ts_to_dt(((lines[0].get("period") or {}).get("start")))
                    period_end = stripe_ts_to_dt(((lines[0].get("period") or {}).get("end")))

                apply_subscription_renewal_success(
                    user_id=context["user_id"],
                    plan_code=context["plan_code"],
                    period_start=period_start,
                    period_end=period_end,
                    support_mode=context["support_mode"]
                )
                upsert_billing_transaction_from_invoice(
                    event_id=event["id"],
                    invoice_obj=obj,
                    user_id=context["user_id"],
                    subscription_row_id=context.get("subscription_row_id"),
                    plan_code=context.get("plan_code"),
                    support_mode=context.get("support_mode"),
                    transaction_kind="monthly_renewal" if context.get("support_mode") == "monthly_recurring" else "annual_renewal",
                    status="succeeded"
                )
                helper_name = f"apply_subscription_renewal_success:{context.get('support_mode')}"

        elif event_type == "invoice.payment_failed":
            context = resolve_user_and_subscription_context(
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=stripe_subscription_id
            )
            if context.get("user_id") and context.get("support_mode") in {"monthly_recurring", "annual_recurring"}:
                apply_subscription_renewal_failure_to_floor(context["user_id"])
                upsert_billing_transaction_from_invoice(
                    event_id=event["id"],
                    invoice_obj=obj,
                    user_id=context["user_id"],
                    subscription_row_id=context.get("subscription_row_id"),
                    plan_code=context.get("plan_code"),
                    support_mode=context.get("support_mode"),
                    transaction_kind="monthly_renewal" if context.get("support_mode") == "monthly_recurring" else "annual_renewal",
                    status="failed"
                )

                cancellation_result = cancel_failed_stripe_subscription(stripe_subscription_id)

                if cancellation_result.get("cancelled"):
                    helper_name = f"apply_subscription_renewal_failure_to_floor_and_cancel_stripe:{context.get('support_mode')}"
                else:
                    helper_name = f"apply_subscription_renewal_failure_to_floor_cancel_pending:{context.get('support_mode')}"

        elif event_type == "invoice.upcoming":
            context = resolve_user_and_subscription_context(
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=stripe_subscription_id
            )
            if context.get("user_email") and context.get("support_mode") in {"monthly_recurring", "annual_recurring"}:
                lines = ((obj.get("lines") or {}).get("data") or [])
                renewal_at = None
                if lines:
                    renewal_at = stripe_ts_to_dt(((lines[0].get("period") or {}).get("end")))
                send_upcoming_renewal_email(
                    user_email=context["user_email"],
                    display_name=context.get("display_name"),
                    plan_code=context.get("plan_code") or "support",
                    support_mode=context.get("support_mode"),
                    amount_due=obj.get("amount_due"),
                    currency=obj.get("currency"),
                    renewal_at=renewal_at
                )
                helper_name = "send_upcoming_renewal_email"

        mark_payment_event_processed(event["id"], helper_name)
        return {"ok": True, "duplicate": False, "event_type": event_type, "helper_name": helper_name}
    except Exception as e:
        logger.exception(
            "Stripe webhook processing failed event_id=%s event_type=%s user_id=%s customer=%s subscription=%s",
            event.get("id"),
            event_type,
            user_id,
            stripe_customer_id,
            stripe_subscription_id,
        )
        try:
            mark_payment_event_error(event["id"], str(e))
        except Exception as mark_err:
            logger.error("Stripe webhook mark_payment_event_error also failed: %s", mark_err)
        raise



@app.get("/billing/apple/server-notifications")
def apple_server_notifications_healthcheck():
    logger.info("APPLE_SERVER_NOTIFICATION_HEALTHCHECK")
    return {"ok": True, "service": "apple_server_notifications"}


@app.post("/billing/apple/server-notifications")
async def apple_server_notifications(request: Request):
    raw_body = await request.body()
    payload = {}

    if raw_body:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            logger.warning(
                "APPLE_SERVER_NOTIFICATION_RECEIVED invalid_json body_len=%s",
                len(raw_body),
            )
            return {"ok": True}

    signed_payload_present = isinstance(payload, dict) and bool(payload.get("signedPayload"))
    notification_type = payload.get("notificationType") if isinstance(payload, dict) else None
    subtype = payload.get("subtype") if isinstance(payload, dict) else None

    logger.info(
        "APPLE_SERVER_NOTIFICATION_RECEIVED signedPayload_present=%s notification_type=%s subtype=%s body_len=%s",
        signed_payload_present,
        notification_type,
        subtype,
        len(raw_body),
    )

    return {"ok": True}


@app.post("/billing/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET not set.")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Stripe payload.")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.")

    event = stripe_obj_to_plain(event)

    result = process_stripe_event(event)
    return {"ok": True, "result": result}


class BillingCheckoutSessionInput(BaseModel):
    plan_code: str
    support_mode: Literal["monthly_recurring", "annual_prepaid", "annual_recurring"]


@app.post("/billing/checkout-session")
def billing_checkout_session(request: Request, payload: BillingCheckoutSessionInput):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    if payload.support_mode in {"monthly_recurring", "annual_recurring"}:
        active_paid_rail = get_active_paid_rail_for_user(user["user_id"])

        if active_paid_rail and active_paid_rail.get("provider") != "stripe":
            provider_label = (active_paid_rail.get("provider") or "another provider").title()
            period_end = serialize_dt(active_paid_rail.get("current_period_end"))
            detail = (
                f"Your current support is already active through {provider_label}. "
                "To avoid duplicate billing, new web billing can begin after the current paid period ends."
            )
            if period_end:
                detail += f" Current paid period ends at {period_end}."
            raise HTTPException(status_code=409, detail=detail)

        active_recurring = active_paid_rail if active_paid_rail and active_paid_rail.get("provider") == "stripe" else None
        if active_recurring:
            try:
                result = change_existing_subscription_plan(
                    user_id=user["user_id"],
                    user_email=user["email"],
                    display_name=user.get("display_name"),
                    current_subscription_id=active_recurring["stripe_subscription_id"],
                    plan_code=payload.plan_code,
                    support_mode=payload.support_mode,
                )

                updated_subscription = stripe_obj_to_plain(result["subscription_obj"])
                upsert_local_stripe_subscription(
                    subscription_obj=updated_subscription,
                    fallback_user_id=user["user_id"],
                    fallback_plan_code=result["plan_code"],
                    fallback_support_mode=result["support_mode"],
                )

                if result.get("changed_subscription"):
                    period_start = stripe_ts_to_dt(updated_subscription.get("current_period_start"))
                    period_end = stripe_ts_to_dt(updated_subscription.get("current_period_end"))

                    apply_subscription_renewal_success(
                        user_id=user["user_id"],
                        plan_code=result["plan_code"],
                        period_start=period_start,
                        period_end=period_end,
                        support_mode=result["support_mode"]
                    )

                return {
                    "ok": True,
                    "changed_subscription": bool(result.get("changed_subscription")),
                    "message": result["message"],
                    "plan_code": result["plan_code"],
                    "support_mode": result["support_mode"],
                    "livemode": result["livemode"],
                }
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except stripe.error.StripeError as e:
                print(f"[STRIPE CHANGE PLAN ERROR] {type(e).__name__}: {e}", flush=True)
                logger.error(f"Stripe change-plan failed: {e}")
                raise HTTPException(status_code=502, detail="Stripe change-plan failed.")
            except Exception as e:
                print(f"[BILLING CHANGE PLAN ERROR] {type(e).__name__}: {e}", flush=True)
                logger.error(f"Billing change-plan error: {e}")
                raise HTTPException(status_code=500, detail="Billing change-plan failed.")

    entitlement = get_user_entitlement_snapshot(user["user_id"])
    if (
        entitlement.get("entitlement_status") == "active"
        and entitlement.get("is_entitled")
        and entitlement.get("effective_plan_code") not in {"anon", "pilgrim"}
    ):
        period_end = serialize_dt(entitlement.get("subscription_expires_at"))
        detail = (
            "Your current support is already active. "
            "To avoid duplicate billing, new billing can begin after the current paid period ends."
        )
        if period_end:
            detail += f" Current paid period ends at {period_end}."
        raise HTTPException(status_code=409, detail=detail)

    base_url = (os.getenv("APP_BASE_URL") or str(request.base_url)).rstrip("/")
    success_url = f"{base_url}/temple?checkout=success"
    cancel_url = f"{base_url}/temple?checkout=cancelled"

    try:
        result = create_checkout_session_for_user(
            user_id=user["user_id"],
            user_email=user["email"],
            display_name=user.get("display_name"),
            plan_code=payload.plan_code,
            support_mode=payload.support_mode,
            success_url=success_url,
            cancel_url=cancel_url,
        )

        return {
            "ok": True,
            "publishable_key": STRIPE_PUBLISHABLE_KEY,
            "checkout_session_id": result["checkout_session_id"],
            "checkout_url": result["checkout_url"],
            "plan_code": result["plan_code"],
            "support_mode": result["support_mode"],
            "livemode": result["livemode"],
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error(f"Stripe configuration error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    except stripe.error.StripeError as e:
        logger.error(f"Stripe checkout session creation failed: {e}")
        raise HTTPException(status_code=502, detail="Stripe checkout session creation failed.")
    except Exception as e:
        logger.error(f"Billing checkout session error: {e}")
        raise HTTPException(status_code=500, detail="Billing checkout session creation failed.")


class QuestionInput(BaseModel):
    question: str
    deity: str = "Hathor"  # Default to Hathor
    seeker_id: Optional[str] = None
    anonymous_user_id: Optional[str] = None
    pcc_fallback_code: Optional[str] = None
    pcc_abandoned_interaction_id: Optional[str] = None

def normalize_for_scoring(text: str) -> list[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return [w for w in text.split() if len(w) > 2]

def detect_memory_intent(question: str) -> str:
    q = (question or "").lower().strip()

    # Recall mode changes memory depth, retrieval, response budgeting, and
    # prompt semantics. Enter it only for explicit references to prior
    # seeker-Oracle dialogue, not for generic temporal words.
    recall_patterns = [
        "what did i ask",
        "what did i say",
        "what did you say",
        "what did we discuss",
        "what did we talk about",
        "what was my last question",
        "what was your last answer",
        "earlier you said",
        "you said before",
        "as i said before",
        "didn't i say",
        "have i asked",
        "did i ask",
        "have we discussed",
        "did we discuss",
        "have we talked about",
        "did we talk about",
        "what have we discussed",
        "what have we talked about",
        "what were we talking about",
        "show me our past dialogue",
        "show past dialogue",
        "reveal past dialogue",
        "past dialogue",
        "dialogue history",
        "conversation history",
        "past conversation",
        "previous conversation",
        "last conversation",
        "remind me what i asked",
        "remind me what i said",
        "remind me what you said",
        "remind me what we discussed",
        "remind me what we talked about",
        "recall what i asked",
        "recall what i said",
        "recall what you said",
        "recall our conversation",
        "recall our dialogue",
        "remember when i asked",
        "remember when i said",
        "remember when you said",
        "remember when we discussed",
        "remember when we talked"
    ]

    for pattern in recall_patterns:
        if pattern in q:
            return "recall"

    return "reflection"


def detect_oracle_interaction_style(question: str) -> str:
    """
    Keep only genuine greetings, acknowledgements, and presence checks
    gentle and conversational.

    A substantive question must remain standard even when it begins
    with a greeting such as "hello" or addresses Hathor or Moses.
    """
    q = re.sub(r"\s+", " ", (question or "").lower()).strip(" .!?")

    if not q:
        return "gentle_conversation"

    # Normalize punctuation only for interaction-style classification.
    # The original seeker question itself is not changed here.
    normalized = re.sub(r"[,;:!?.\-]+", " ", q)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    simple_greetings = {
        "hello",
        "hi",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "ok",
        "okay",
        "thank you",
        "thanks",
    }

    if normalized in simple_greetings:
        return "gentle_conversation"

    oracle_greeting_pattern = re.compile(
        r"^(?:hello|hi|hey|good morning|good afternoon|good evening)"
        r"(?:\s+(?:hathor|moses))?$"
    )

    if oracle_greeting_pattern.fullmatch(normalized):
        return "gentle_conversation"

    presence_pattern = re.compile(
        r"^(?:(?:hello|hi|hey|good morning|good afternoon|good evening)\s+)?"
        r"(?:(?:hathor|moses)\s+)?"
        r"(?:are you there|are you here|are you with me|can you hear me)$"
    )

    if presence_pattern.fullmatch(normalized):
        return "gentle_conversation"

    acknowledgement_pattern = re.compile(
        r"^(?:thank you|thanks|ok|okay)(?:\s+(?:hathor|moses))?$"
    )

    if acknowledgement_pattern.fullmatch(normalized):
        return "gentle_conversation"

    return "standard"


def expand_query_terms(query_terms: list[str]) -> set[str]:
    semantic_map = {
        "self": {"self", "soul", "atman", "identity", "being", "essence"},
        "truth": {"truth", "real", "reality", "true", "essence", "light"},
        "love": {"love", "beloved", "union", "heart", "devotion", "mercy"},
        "god": {"god", "divine", "sacred", "lord", "eternal", "holy"},
        "law": {"law", "command", "justice", "righteous", "duty", "order"},
        "soul": {"soul", "spirit", "self", "essence", "inner", "being"},
        "death": {"death", "dying", "mortality", "end", "grave", "passing"},
        "wisdom": {"wisdom", "understanding", "insight", "discernment", "knowledge"},
        "identity": {"identity", "self", "being", "name", "essence", "person"},
        "joy": {"joy", "delight", "gladness", "ecstasy", "celebration", "blessing"},
    }

    expanded = set(query_terms)

    for term in query_terms:
        if term in semantic_map:
            expanded.update(semantic_map[term])

    return expanded

def rank_passages(passages: list, query: str, max_items: int = 5) -> list:
    if not passages:
        return []

    raw_terms = normalize_for_scoring(query)
    if not raw_terms:
        raw_terms = ["truth"]
    expanded_terms = expand_query_terms(raw_terms)

    scored = []

    for p in passages:
        passage_terms = normalize_for_scoring(p)
        passage_term_set = set(passage_terms)

        exact_matches = sum(1 for term in raw_terms if term in passage_term_set)
        semantic_matches = sum(1 for term in expanded_terms if term in passage_term_set)

        # Reward exact matches more strongly than expanded semantic matches
        score = (exact_matches * 3) + semantic_matches

        # Slight bonus for passages that mention several distinct concepts
        concept_bonus = len(set(raw_terms) & passage_term_set)
        score += concept_bonus

        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [p for score, p in scored[:max_items] if score > 0] or passages[:max_items]

class AdminLifecycleUserInput(BaseModel):
    user_id: str


class AdminCancelAtPeriodEndInput(BaseModel):
    user_id: str
    cancel_at_period_end: bool


class AdminAnnualPrepaidActivationInput(BaseModel):
    user_id: str
    plan_code: str
    term_days: int = DEFAULT_ANNUAL_PREPAID_DAYS


@app.post("/admin/users/renewal-success")
def admin_apply_renewal_success(
    request: Request,
    user_id: str = Form(...),
    plan_code: str = Form(...)
):
    admin_user = require_admin(request)
    apply_subscription_renewal_success(user_id=user_id, plan_code=plan_code)

    entitlement = get_user_entitlement_snapshot(user_id)

    log_admin_action(
        admin_user_id=admin_user["user_id"],
        action_type="admin.users.renewal_success",
        target_user_id=user_id,
        payload={
            "plan_code": plan_code
        }
    )

    return {
        "ok": True,
        "updated_by": admin_user["user_id"],
        "user_id": user_id,
        "entitlement": entitlement
    }


@app.post("/admin/users/annual-prepaid-activate")
def admin_activate_annual_prepaid(
    request: Request,
    payload: AdminAnnualPrepaidActivationInput
):
    admin_user = require_admin(request)

    apply_annual_prepaid_activation(
        user_id=payload.user_id,
        plan_code=payload.plan_code,
        term_days=payload.term_days
    )

    entitlement = get_user_entitlement_snapshot(payload.user_id)

    log_admin_action(
        admin_user_id=admin_user["user_id"],
        action_type="admin.users.annual_prepaid_activate",
        target_user_id=payload.user_id,
        payload={
            "plan_code": payload.plan_code,
            "term_days": payload.term_days
        }
    )

    return {
        "ok": True,
        "updated_by": admin_user["user_id"],
        "user_id": payload.user_id,
        "entitlement": entitlement
    }


@app.post("/admin/users/annual-prepaid-expire")
def admin_expire_annual_prepaid(
    request: Request,
    payload: AdminLifecycleUserInput
):
    admin_user = require_admin(request)

    apply_annual_prepaid_expiry(user_id=payload.user_id)

    entitlement = get_user_entitlement_snapshot(payload.user_id)

    log_admin_action(
        admin_user_id=admin_user["user_id"],
        action_type="admin.users.annual_prepaid_expire",
        target_user_id=payload.user_id,
        payload={}
    )

    return {
        "ok": True,
        "updated_by": admin_user["user_id"],
        "user_id": payload.user_id,
        "entitlement": entitlement
    }


@app.post("/admin/users/renewal-failure-to-floor")
def admin_apply_renewal_failure_to_floor(
    request: Request,
    payload: AdminLifecycleUserInput
):
    admin_user = require_admin(request)
    apply_subscription_renewal_failure_to_floor(user_id=payload.user_id)

    entitlement = get_user_entitlement_snapshot(payload.user_id)

    log_admin_action(
        admin_user_id=admin_user["user_id"],
        action_type="admin.users.renewal_failure_to_floor",
        target_user_id=payload.user_id,
        payload={}
    )

    return {
        "ok": True,
        "updated_by": admin_user["user_id"],
        "user_id": payload.user_id,
        "entitlement": entitlement
    }


@app.post("/admin/users/set-cancel-at-period-end")
def admin_set_cancel_at_period_end(
    request: Request,
    payload: AdminCancelAtPeriodEndInput
):
    admin_user = require_admin(request)
    set_cancel_at_period_end(
        user_id=payload.user_id,
        should_cancel=payload.cancel_at_period_end
    )

    entitlement = get_user_entitlement_snapshot(payload.user_id)

    log_admin_action(
        admin_user_id=admin_user["user_id"],
        action_type="admin.users.set_cancel_at_period_end",
        target_user_id=payload.user_id,
        payload={
            "cancel_at_period_end": payload.cancel_at_period_end
        }
    )

    return {
        "ok": True,
        "updated_by": admin_user["user_id"],
        "user_id": payload.user_id,
        "entitlement": entitlement
    }


@app.post("/admin/users/apply-cancel-at-period-end-downgrade")
def admin_apply_cancel_at_period_end_downgrade(
    request: Request,
    payload: AdminLifecycleUserInput
):
    admin_user = require_admin(request)
    apply_cancel_at_period_end_downgrade(user_id=payload.user_id)

    entitlement = get_user_entitlement_snapshot(payload.user_id)

    log_admin_action(
        admin_user_id=admin_user["user_id"],
        action_type="admin.users.apply_cancel_at_period_end_downgrade",
        target_user_id=payload.user_id,
        payload={}
    )

    return {
        "ok": True,
        "updated_by": admin_user["user_id"],
        "user_id": payload.user_id,
        "entitlement": entitlement
    }

@app.post("/ask")
async def ask_oracle(request: Request, payload: QuestionInput):

    user = get_current_user(request)
    user_id = user["user_id"] if user else None

    anonymous_user_id = get_or_create_anonymous_user_id(
        request,
        payload.anonymous_user_id,
    )

    session_id = get_or_create_bound_session_id(
        request,
        anonymous_user_id,
        user_id,
    )

    plan_code = "anon"
    memory_depth = 1

    if not can_user_ask(anonymous_user_id, user_id):
        return JSONResponse(
            content={
            "oracle_message": "The Oracle grows quiet. To continue the dialogue, please log in or support the Temple."
        },
        status_code=429
        )

    try:
        ask_started_at = datetime.datetime.now()
        retrieval_started_at = None
        retrieval_finished_at = None
        final_model_started_at = None
        final_model_finished_at = None

        question = payload.question
        question = question[:1000]
        deity = payload.deity
        input_mode = getattr(request.state, "oracle_input_mode", "text")

        pcc_fallback_code = (
            (payload.pcc_fallback_code or "").strip() or None
        )
        pcc_abandoned_interaction_id = (
            (payload.pcc_abandoned_interaction_id or "").strip() or None
        )
        pcc_fallback_verified = None

        if pcc_fallback_code not in IOS_PCC_FALLBACK_CODES:
            pcc_fallback_code = None
            pcc_abandoned_interaction_id = None

        elif pcc_fallback_code == "pcc_preflight_unavailable":
            # No pending inference exists before prepare, so this
            # client-reported fallback cannot be server-correlated.
            pcc_abandoned_interaction_id = None
            pcc_fallback_verified = False

        else:
            try:
                candidate_interaction_id = str(
                    uuid.UUID(pcc_abandoned_interaction_id or "")
                )
            except (TypeError, ValueError):
                candidate_interaction_id = None

            verified_abandonment = None
            if candidate_interaction_id:
                verified_abandonment = get_verified_pcc_abandonment(
                    candidate_interaction_id,
                    session_id=str(session_id),
                    user_id=str(user_id) if user_id else None,
                    deity=deity,
                    fallback_code=pcc_fallback_code,
                    question_sha256=hashlib.sha256(
                        (question or "").encode("utf-8")
                    ).hexdigest(),
                )

            if verified_abandonment:
                pcc_abandoned_interaction_id = candidate_interaction_id
                pcc_fallback_verified = True
            else:
                logger.warning(
                    "PCC_FALLBACK_CORRELATION_REJECTED "
                    "code=%s pending_id=%s",
                    pcc_fallback_code,
                    pcc_abandoned_interaction_id,
                )
                pcc_fallback_code = None
                pcc_abandoned_interaction_id = None

        logger.info(
            "ASK input_mode=%s deity=%s len=%s",
            input_mode,
            deity,
            len(question)
        )

        # --— detect memory intent ---
        memory_intent = detect_memory_intent(question)
        oracle_interaction_style = detect_oracle_interaction_style(question)


        # --- Resolve title for memory depth ---

        if user:
            entitlement = get_user_entitlement_snapshot(user_id)
            plan_code = entitlement["effective_plan_code"]
            memory_depth = get_memory_depth(plan_code, memory_intent)

        if oracle_interaction_style == "gentle_conversation":
            memory_depth = 0

        # --- Retrieve seeker long-term memory ---
        memories = retrieve_seeker_memory(user_id, session_id, memory_depth)

        # --- Retrieve conversation memory ---
        memory = get_session_memory(session_id, memory_depth)

        # --— normalize memory inputs for prompt ---
        recent_memory = memory
        limited_memories = memories[:5] if memories else []


        # --- Conditional retrieval based on memory intent ---
        passages = []

        if memory_intent != "recall" and oracle_interaction_style != "gentle_conversation":
            retrieval_started_at = datetime.datetime.now()
            passages = retrieve_context(
                question,
                user_id,
                plan_code=plan_code,
                deity=deity,
                memory_intent=memory_intent
            )
            passages = rank_passages(passages, question)
            retrieval_finished_at = datetime.datetime.now()



        if oracle_interaction_style == "gentle_conversation":
            recent_memory = ""
            limited_memories = []

        # --— structured memory weighting ---
        memory_block = ""

        if recent_memory or limited_memories:
            memory_block += (
                "MEMORY PROVENANCE RULE:\n"
                "Remembered dialogue may come from Hathor or Moses. "
                "Treat another Oracle's dialogue as shared seeker context, "
                "not as your own prior speech. Preserve the current Oracle's "
                "identity, voice, characteristic imagery, and first-person perspective.\n\n"
            )

        if recent_memory:
            memory_block += "PRIMARY EVIDENCE — RECENT EXACT DIALOGUE:\n"
            memory_block += recent_memory + "\n\n"

        if limited_memories:
            memory_block += "SECONDARY EVIDENCE — PRIOR SEEKER MEMORY:\n"
            memory_block += "\n\n".join(limited_memories) + "\n\n"

        # --— fallback retrieval if recall requested but memory is empty ---
        if memory_intent == "recall" and not memory_block.strip() and oracle_interaction_style != "gentle_conversation":
            retrieval_started_at = datetime.datetime.now()
            passages = retrieve_context(
                question,
                user_id,
                plan_code=plan_code,
                deity=deity,
                memory_intent=memory_intent
            )
            passages = rank_passages(passages, question, max_items=2)
            retrieval_finished_at = datetime.datetime.now()

        context_block = ""
        if passages:
            context_block = "\n\nBackground wisdom for reflection:\n\n"
            context_block += "\n\n".join(passages)

        # --— dual-mode prompt ---

        normalized_input_mode = (input_mode or "text").strip().lower()
        response_word_cap = get_response_word_cap(
            plan_code=plan_code,
            memory_intent=memory_intent,
            deity=deity,
            input_mode=input_mode
        )
        if oracle_interaction_style == "gentle_conversation":
            response_word_cap = min(response_word_cap, 90 if normalized_input_mode == "text" else 50)

        response_max_tokens = words_to_max_tokens(response_word_cap)
        response_min_words = 0
        if (
            memory_intent != "recall"
            and normalized_input_mode == "text"
            and oracle_interaction_style != "gentle_conversation"
        ):
            response_min_words = max(220, int(response_word_cap * 0.58))

        if memory_intent == "recall":
            instruction_block = f"""You are the Oracle of the Temple.

        MODE: RECALL

        The seeker is asking about prior dialogue.
        You MUST answer using the stored dialogue below.

        Rules:
        1. Treat stored dialogue as primary truth.
        2. Answer from memory BEFORE using general knowledge.
        3. Do not generalize if memory contains the answer.
        4. If memory is unclear or incomplete, say so.
        5. Prefer quoting or closely paraphrasing prior exchanges.
        6. Keep the answer concise and directly tied to the recall request.
        7. Keep the full answer under {response_word_cap} words.

        Return format:
        - First sentence: direct answer
        - Second sentence: reference to prior exchange
        - Third sentence (optional): clarify uncertainty
        """
        else:
            instruction_block = f"""
        You are the Oracle of the Temple.

        MODE: REFLECTION

        The seeker is engaged in an ongoing dialogue.

        Rules:
        1. Use memory to enhance continuity, not override the present.
        2. Prioritize the current question.
        3. Integrate relevant past context only when it directly helps.
        4. For greetings, thanks, or presence checks, answer pleasantly in one to three short sentences. Do not recap past dialogue unless the seeker asks.
        5. Keep responses coherent and under {response_word_cap} words.
        6. Do not exceed the word cap. Prefer a complete, bounded answer over a long essay.
        7. For higher access levels, allow a fuller reflection when the question genuinely invites it, while still avoiding rambling.
        8. If input_mode is text and the question invites reflection, provide a complete written reflection with useful structure, synthesis, and continuity. Unless the seeker asks for brevity, aim for at least {response_min_words} words while staying under {response_word_cap} words.
        9. If input_mode is voice, keep the answer naturally speakable and concise.
        10. For text mode, prefer 3 to 6 coherent paragraphs or short sections only when that helps the answer breathe.
        """
        enhanced_question = f"""{instruction_block}

        Interaction style:
        {oracle_interaction_style}

        Current seeker question:
        {question}

        {context_block}
        """

        recent_memory_chars = len(recent_memory or "")
        limited_memories_chars = len("\n\n".join(limited_memories or []))
        memory_block_chars = len(memory_block or "")
        context_block_chars = len(context_block or "")
        instruction_block_chars = len(instruction_block or "")
        enhanced_question_chars = len(enhanced_question or "")

        logger.info(
            "PROMPT_BUDGET plan_code=%s deity=%s input_mode=%s memory_intent=%s interaction_style=%s response_word_cap=%s response_min_words=%s response_max_tokens=%s recent_memory_chars=%s limited_memories_count=%s limited_memories_chars=%s memory_block_chars=%s context_block_chars=%s instruction_block_chars=%s enhanced_question_chars=%s passages=%s",
            plan_code,
            deity,
            input_mode,
            memory_intent,
            oracle_interaction_style,
            response_word_cap,
            response_min_words,
            response_max_tokens,
            recent_memory_chars,
            len(limited_memories or []),
            limited_memories_chars,
            memory_block_chars,
            context_block_chars,
            instruction_block_chars,
            enhanced_question_chars,
            len(passages or [])
        )

        # --- Oracle response ---
        selected_moses_model = None
        moses_route_reason = None
        moses_prompt_chars = None

        if deity == "Moses":
            selected_moses_model, moses_route_reason, moses_prompt_chars = choose_moses_model(
                raw_question=question,
                memory_block=memory_block,
                context_block=context_block
            )

        final_model_started_at = datetime.datetime.now()
        prepared_inference = build_prepared_oracle_inference(
            enhanced_question,
            deity,
            force_mode=memory_intent,
            memory_block=memory_block,
            max_output_tokens=response_max_tokens,
            memory_intent=memory_intent,
            plan_code=plan_code,
            input_mode=input_mode,
            selected_moses_model=selected_moses_model,
            moses_route_reason=moses_route_reason,
            moses_prompt_chars=moses_prompt_chars,
        )

        prepared_input_chars = (
            len(prepared_inference.get("system_prompt") or "")
            + len(prepared_inference.get("memory_block") or "")
            + len(prepared_inference.get("question") or "")
        )

        finalization_state = {
            "schema": "oracle_finalization_state.v1",
            "interaction_id": None,
            "session_id": str(session_id),
            "anonymous_user_id": str(anonymous_user_id),
            "user_id": str(user_id) if user_id else None,
            "question": question,
            "deity": deity,
            "input_mode": input_mode,
            "plan_code": plan_code,
            "memory_intent": memory_intent,
            "oracle_interaction_style": oracle_interaction_style,
            "response_word_cap": response_word_cap,
            "enhanced_question_chars": len(enhanced_question or ""),
            "prepared_input_chars": prepared_input_chars,
            "memory_has_content": bool(memory_block.strip()),
            "pcc_fallback_code": pcc_fallback_code,
            "pcc_abandoned_interaction_id": pcc_abandoned_interaction_id,
            "pcc_fallback_verified": pcc_fallback_verified,
        }

        execution_mode = getattr(
            request.state,
            "oracle_execution_mode",
            "server",
        )

        if execution_mode == "realtime_prepare":
            realtime_system_instructions = "\n\n".join(
                part.strip()
                for part in (
                    prepared_inference.get("system_prompt") or "",
                    prepared_inference.get("memory_block") or "",
                )
                if part and part.strip()
            )

            realtime_user_context = f"""Supplemental God Incorporated context for the immediately preceding spoken seeker question.

Do not treat this message as a new seeker question. Answer the immediately preceding spoken question using the following turn guidance and background evidence.

Turn guidance:
{instruction_block}

Interaction style:
{oracle_interaction_style}

{context_block}
""".strip()

            if not realtime_system_instructions:
                raise RuntimeError(
                    "Realtime inference preparation produced no system instructions"
                )

            return {
                "status": "prepared",
                "deity": deity,
                "system_instructions": realtime_system_instructions,
                "user_context": realtime_user_context,
            }

        if execution_mode == "device_prepare":
            execution_target = getattr(
                request.state,
                "oracle_execution_target",
                None,
            )
            if execution_target not in DEVICE_EXECUTION_PROFILES:
                raise RuntimeError(
                    "Device inference has no authorized execution target"
                )

            pending_state = {
                "schema": "oracle_pending_inference_state.v2",
                "execution_target": execution_target,
                "finalization_state": finalization_state,
            }

            pending_id = create_pending_oracle_inference(
                session_id=str(session_id),
                user_id=str(user_id) if user_id else None,
                deity=deity,
                input_mode=input_mode,
                prepared_state=pending_state,
            )

            if not pending_id:
                raise RuntimeError(
                    "Could not create pending Oracle inference"
                )

            return {
                "status": "prepared",
                **build_oracle_device_execution_packet(
                    prepared_inference,
                    pending_id,
                ),
            }

        result = await execute_oracle_inference(prepared_inference)
        final_model_finished_at = datetime.datetime.now()

        timing_state = {
            "ask_started_at": ask_started_at,
            "retrieval_started_at": retrieval_started_at,
            "retrieval_finished_at": retrieval_finished_at,
            "final_model_started_at": final_model_started_at,
            "final_model_finished_at": final_model_finished_at,
        }

        return finalize_oracle_inference(
            finalization_state=finalization_state,
            inference_result=result,
            timing_state=timing_state,
        )

    except Exception as e:
        logger.error(f"Oracle endpoint error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)
