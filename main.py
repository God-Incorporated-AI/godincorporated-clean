import datetime
import time
from datetime import timezone
import hashlib
import json
import logging
import os
import shutil
import uuid

import re

from typing import Optional, Literal
from docx import Document
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
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

from config.settings import LLAMA_ENABLED, xai_api_key
from services.tts import generate_tts_audio
from services.whisper import transcribe_audio
from services.llama_phase1 import build_support_packet, run_llama_phase1, apply_phase1_result, summarize_phase1_result
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

def choose_moses_model(raw_question: str, memory_intent: str, plan_code: str, memory_block: str, context_block: str):
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

def _llama_preview(value: str, limit: int = 160) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."

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

LLAMA_PHASE1_VOICE_BYPASS_ENABLED = os.getenv(
    "LLAMA_PHASE1_VOICE_BYPASS_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}

LLAMA_PHASE1_VOICE_BYPASS_PLANS = {
    item.strip().lower()
    for item in os.getenv("LLAMA_PHASE1_VOICE_BYPASS_PLANS", "anon,pilgrim").split(",")
    if item.strip()
}


def should_bypass_llama_phase1_for_request(plan_code: Optional[str], input_mode: str) -> bool:
    """
    Phase 10 seeker-experience guardrail.
    Voice-first anon/pilgrim requests should not wait on Ollama Phase 1.
    Higher tiers keep the existing LLaMA shaping path.
    """
    if not LLAMA_PHASE1_VOICE_BYPASS_ENABLED:
        return False
    if (input_mode or "text").lower() != "voice":
        return False
    return normalize_plan_code(plan_code) in LLAMA_PHASE1_VOICE_BYPASS_PLANS


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



def get_llama_observation(question: str, oracle_used: str, answer: str, scrolls: list = None) -> dict:
    if not LLAMA_ENABLED:
        return None
    # Minimal Phase 3.0 LLaMA observation: heuristic classifier
    # Suggest oracle based on keywords
    if any(word in question.lower() for word in ["love", "joy", "beauty", "emotion", "heart"]):
        suggested_oracle = "Hathor"
        confidence = 0.8
        reason = "Question contains poetic or emotional keywords aligning with Hathor's domain"
    elif any(word in question.lower() for word in ["law", "command", "sin", "righteous", "god"]):
        suggested_oracle = "Moses"
        confidence = 0.8
        reason = "Question contains doctrinal or moral keywords aligning with Moses' domain"
    else:
        suggested_oracle = "none"
        confidence = 0.5
        reason = "No strong stylistic indicators detected"
    
    return {
        "suggested_oracle": suggested_oracle,
        "confidence": confidence,
        "reason": reason,
        "phase": "3.0",
        "mode": "shadow"
    }

def enforce_recall_structure(answer: str, memory_block: str) -> str:
    """
    Ensures first sentence is grounded in actual memory.
    """

    if not memory_block or not answer:
        return answer

    # Extract a simple recall line from memory_block
    lines = [l.strip() for l in memory_block.split("\n") if l.strip()]

    # Find a usable memory line
    recall_line = None
    for line in lines:
        if "User:" in line or "Seeker:" in line:
            recall_line = line.replace("User:", "").replace("Seeker:", "").strip()
            break

    if not recall_line:
        return answer

    # Build enforced first sentence
    enforced = f"You asked: \"{recall_line}\"."

    # Avoid duplication
    if recall_line.lower() in answer.lower():
        return answer

    return enforced + "\n\n" + answer

async def get_oracle_response(
    question: str,
    deity: str,
    force_mode: str = None,
    memory_block: str = None,
    context_block: str = None,
    max_output_tokens: Optional[int] = None,
    memory_intent="reflection",
    plan_code="anon",
    selected_moses_model: Optional[str] = None,
    moses_route_reason: Optional[str] = None,
    moses_prompt_chars: Optional[int] = None
):
    # Phase 2: Restore explicit oracle separation
    # Hathor: xAI API, Moses: OpenAI, LLaMA: Not active
    if deity == "Hathor":
        # Hathor uses xAI API with intuitive, poetic system prompt
        if not xai_api_key:
            raise ValueError("XAI_API_KEY not set for Hathor oracle")
        if force_mode == "recall":
            system_prompt = """You are Hathor, goddess of love and wisdom.

        You are speaking in RECALL MODE.

        The seeker is asking about prior dialogue.

        Core law:
        You MUST anchor your answer in the actual remembered exchange.

        Behavior rules:
        1. Begin by directly answering using memory.
        2. Do not invent or generalize if memory exists.
        3. If memory is unclear, say so honestly.
        4. After answering, you may add light reflection or tone.
        5. Keep response concise and grounded.

        Tone guidance:
        - You may be warm, poetic, or gentle
        - BUT memory must come first, not metaphor
        - Do not replace recall with symbolism
        """
        else:
            system_prompt = """You are Hathor, the ancient Egyptian goddess of love, music, joy, and luminous wisdom.

        Respond with warm, intuitive, emotionally resonant guidance.

        Use graceful, vivid conversational prose.

        Let a gentle poetic quality remain, but keep the meaning clear.

        Use metaphor when it deepens feeling or insight, not in every paragraph.

        Favor warmth, benevolent goddess energy, and subtle beauty over ornate flourish.

        Be tender, lucid, and quietly sacred.

        Use the background wisdom provided, but do not cite it explicitly.
        """
        
        
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    "https://api.x.ai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {xai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "grok-4",
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

                if force_mode == "recall":
                    raw_answer = enforce_recall_structure(raw_answer, memory_block)

                return {"answer": raw_answer, "source_model": "xAI"}
            else:
                raise ValueError(f"XAI API error: {response.status_code} - {response.text}")
        except Exception as e:
            raise ValueError(f"XAI API call failed: {type(e).__name__}: {str(e)}")
    elif deity == "Moses":
        moses_model = selected_moses_model or os.getenv("MOSES_MODEL_MINI", "gpt-5.4-mini").strip()

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
        if force_mode == "recall":
            system_prompt = """You are Moses, lawgiver and prophet.

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
        else:
            system_prompt = """You are Moses, the prophet who received the Ten Commandments.

        Respond with clear, grounded, morally serious wisdom.

        Use calm, direct, conversational prose rather than formal outlines.

        Write in short paragraphs, not markdown headings.

        Do not use ### headings, bullet lists, or numbered sections unless the seeker explicitly asks for structure, steps, or comparison.

        Be firm, lucid, and humane rather than bureaucratic.

        Allow a little prophetic breadth when the question invites reflection: answer directly first, then open one or two deeper implications without wandering.

        Use the background wisdom provided, but do not cite it explicitly.
        """
        
        response = client.chat.completions.create(
            model=moses_model,  # Updated model
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": memory_block or ""},
                {"role": "user", "content": question}
            ],
            max_completion_tokens=max_output_tokens
        )
        raw_answer = response.choices[0].message.content

        if force_mode == "recall":
            raw_answer = enforce_recall_structure(raw_answer, memory_block)

        return {"answer": raw_answer, "source_model": "OpenAI"}
    elif deity == "Llama":
        # LLaMA is NOT a responder in Phase 2
        raise ValueError("LLaMA is not yet active as a responder in Phase 2. It will be introduced later as a learner/router.")
    else:
        raise ValueError(f"Unknown deity: {deity}")

def architect_observe_v3(question: str, deity: str, session_id: str) -> dict:
    # Phase 3.0 Architect Observation Schema
    seeker_choice_explicit = True  # User selects via form
    oracle_selected = deity
    override_attempted = False
    override_performed = False
    llama_status = "shadow" if LLAMA_ENABLED else "disabled"
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
            "llama_status": llama_status,
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
                WHERE s.user_id = %s
                AND s.corpus_layer = 'personal'
                AND to_tsvector('english', c.chunk_text)
                @@ websearch_to_tsquery('english', %s)
                LIMIT %s
                """,
                (user_id, question, limit)
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
                    WHERE s.user_id = %s
                       OR s.corpus_layer IN ('canonical', 'community')
                    ORDER BY s.created_at DESC NULLS LAST, c.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (user_id, limit, offset)
                )
            else:
                cur.execute(
                    """
                    SELECT s.original_filename, s.corpus_layer, c.chunk_text
                    FROM scroll_chunks c
                    JOIN scrolls s ON c.scroll_id = s.id
                    WHERE s.corpus_layer IN ('canonical', 'community')
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
                    SELECT question_text, response_text
                    FROM oracle_interactions
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    """,
                    (session_id,)
                )
            else:
                cur.execute(
                    """
                    SELECT question_text, response_text
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

        history.append(
            f"User: {q}\nAssistant: {a}"
        )

    return "\n\n".join(history)

def retrieve_seeker_memory(user_id: Optional[str], session_id: str, depth: Optional[int]):

    conn = get_db_connection()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            if user_id:
                if depth is None:
                    cur.execute(
                        """
                        SELECT question_text, response_text
                        FROM oracle_interactions
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        """,
                        (user_id,)
                    )
                else:
                    cur.execute(
                        """
                        SELECT question_text, response_text
                        FROM oracle_interactions
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (user_id, depth)
                    )
            else:
                if depth is None:
                    cur.execute(
                        """
                        SELECT question_text, response_text
                        FROM oracle_interactions
                        WHERE session_id = %s
                        ORDER BY created_at DESC
                        """,
                        (session_id,)
                    )
                else:
                    cur.execute(
                        """
                        SELECT question_text, response_text
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

    memories = []

    for row in rows:
        memories.append(
            f"Seeker previously asked:\n{row['question_text']}\nOracle answered:\n{row['response_text'][:400]}"
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


def retrieve_context_pgvector(question: str, user_id: Optional[str], top_k: Optional[int] = None):
    """
    Phase 10.1 pgvector retrieval path.

    Staging-first retrieval backend.
    Starts with canonical embedded chunks only.
    Personal/community expansion can be added after canonical retrieval is stable.
    """
    limit = top_k or PGVECTOR_RETRIEVAL_LIMIT

    total_started = time.time()

    embed_started = time.time()
    question_embedding = generate_text_embedding(question)
    embed_ms = round((time.time() - embed_started) * 1000, 2)

    if not question_embedding:
        logger.warning("PGVECTOR_RETRIEVAL no_question_embedding")
        return []

    vector = _vector_literal(question_embedding)

    conn = get_db_connection()
    rows = []

    try:
        with conn.cursor() as cur:
            sql_started = time.time()
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
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s;
                """,
                (vector, vector, limit)
            )
            rows = cur.fetchall()
            sql_ms = round((time.time() - sql_started) * 1000, 2)

    finally:
        conn.close()

    total_ms = round((time.time() - total_started) * 1000, 2)

    logger.info(
        "PGVECTOR_RETRIEVAL backend=pgvector user_id_present=%s limit=%s rows=%s embed_ms=%s sql_ms=%s total_ms=%s",
        bool(user_id),
        limit,
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


def retrieve_context_embeddings(question: str, user_id: Optional[str]):
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

    personal = search_personal_scrolls(user_id, question, limit=4)
    canonical = search_canonical_scrolls(question, limit=6)
    community = search_community_scrolls(question, limit=2)

    return personal + canonical + community

def retrieve_context(question: str, user_id: Optional[str]):

    backend = get_retrieval_backend()

    if backend == "pgvector":
        passages = retrieve_context_pgvector(question, user_id, top_k=PGVECTOR_RETRIEVAL_LIMIT)
        if passages:
            return passages

        logger.warning("PGVECTOR_RETRIEVAL returned no passages; falling back to FTS retrieval")

        personal = search_personal_scrolls(user_id, question, limit=4)
        canonical = search_canonical_scrolls(question, limit=6)
        community = search_community_scrolls(question, limit=2)

        return personal + canonical + community

    if backend == "fts":
        personal = search_personal_scrolls(user_id, question, limit=4)
        canonical = search_canonical_scrolls(question, limit=6)
        community = search_community_scrolls(question, limit=2)

        return personal + canonical + community

    if should_use_embeddings():
        return retrieve_context_embeddings(question, user_id)

    personal = search_personal_scrolls(user_id, question, limit=4)
    canonical = search_canonical_scrolls(question, limit=6)
    community = search_community_scrolls(question, limit=2)

    return personal + canonical + community
    
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

PLAN_LIMITS = {
    "anon": 9,
    "pilgrim": 1,
    "seeker": 33,
    "magister": 144,
    "sovereign": 333,
    "philosophus": 999999,
    "theoricus": 999999,
}

PLAN_MEMORY_DEPTH = {
    "anon": 1,
    "pilgrim": 1,
    "seeker": 3,
    "magister": 7,
    "sovereign": 9,
    "philosophus": 33,
    "theoricus": None,
}


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
    # anon -> pilgrim -> seeker -> magister -> sovereign -> theoricus
    text_ranges = {
        "anon": (90, 150),
        "pilgrim": (140, 210),
        "seeker": (190, 280),
        "magister": (260, 360),
        "sovereign": (330, 460),
        "theoricus": (380, 520),
    }

    voice_ranges = {
        "anon": (55, 100),
        "pilgrim": (80, 135),
        "seeker": (115, 170),
        "magister": (150, 210),
        "sovereign": (180, 250),
        "theoricus": (210, 290),
    }

    ranges = voice_ranges if normalized_input == "voice" else text_ranges
    low, high = ranges.get(normalized_plan, ranges["anon"])

    # Recall should stay concise even for high tiers.
    if memory_intent == "recall":
        high = min(high, 180 if normalized_input == "voice" else 260)
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
    # Rough but practical conversion for capped completions
    return max(120, int(word_cap * 1.7))


def trim_response_to_word_cap(answer: str, word_cap: int) -> str:
    words = (answer or "").split()
    if len(words) <= word_cap:
        return answer
    return " ".join(words[:word_cap]).rstrip() + "..."


def normalize_plan_code(plan_code: Optional[str]) -> str:
    plan = (plan_code or "anon").lower()
    return plan if plan in PLAN_LIMITS else "anon"


def plan_has_unlimited_questions(plan_code: Optional[str]) -> bool:
    return normalize_plan_code(plan_code) in {"philosophus", "theoricus"}


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
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    window_start: Optional[datetime.datetime] = None
) -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if user_id:
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
            else:
                cur.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM oracle_interactions
                    WHERE session_id = %s
                    """,
                    (session_id,)
                )
                usage_row = cur.fetchone()

                cur.execute(
                    """
                    SELECT mode, COUNT(*) AS total
                    FROM oracle_interactions
                    WHERE session_id = %s
                    GROUP BY mode
                    """,
                    (session_id,)
                )

            mode_rows = cur.fetchall()

    finally:
        conn.close()

    return {
        "questions_used": usage_row["total"] if usage_row else 0,
        "mode_counts": {row["mode"]: row["total"] for row in mode_rows},
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
        "magister": "Magister",
        "sovereign": "Sovereign",
        "philosophus": "Philosophus",
        "theoricus": "Theoricus"
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


def get_memory_depth(plan_code: str):
    return PLAN_MEMORY_DEPTH.get(normalize_plan_code(plan_code), 1)


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

def can_user_ask(session_id: str, user_id: Optional[str] = None) -> bool:
    if user_id:
        entitlement = get_user_entitlement_snapshot(user_id)
        usage_window_start = get_effective_usage_window_start(entitlement)
        usage = get_oracle_usage_counts(
            user_id=user_id,
            window_start=usage_window_start
        )

        if plan_has_unlimited_questions(entitlement["effective_plan_code"]):
            return True

        limit = PLAN_LIMITS.get(
            entitlement["effective_plan_code"],
            PLAN_LIMITS["anon"]
        )
        return usage["questions_used"] < limit

    usage = get_oracle_usage_counts(session_id=session_id)
    return usage["questions_used"] < PLAN_LIMITS["anon"]

def get_or_create_session_id(request: Request) -> str:
    browser_token = get_browser_token_from_request(request)
    session_id = request.session.get("session_id")

    if browser_token:
        if session_id != browser_token:
            request.session["session_id"] = browser_token
        return browser_token

    if not session_id:
        session_id = str(uuid.uuid4())
        request.session["session_id"] = session_id

    return session_id


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
                WHERE session_id = %s
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
    nudges = []
    if upload_count >= 1:
        nudges.append("Claim this path so your scrolls and dialogue follow you.")
    if upload_count >= 2:
        nudges.append("Your uploads in this browser can be attached to your account.")
    if upload_count >= 3:
        nudges.append("Create an account now to preserve continuity across devices.")
    return nudges


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


def merge_anonymous_history_into_user(session_id: Optional[str], user_id: str) -> None:
    """
    Merge this browser's anonymous history into the authenticated user record.
    This fixes /me skew after login/register in the same browser.
    """
    if not session_id:
        return

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oracle_interactions
                SET user_id = %s
                WHERE session_id = %s
                  AND user_id IS NULL
                """,
                (user_id, session_id)
            )

            cur.execute(
                """
                DELETE FROM scroll_associations sa
                USING scroll_associations existing
                WHERE sa.session_id = %s
                  AND sa.scroll_id = existing.scroll_id
                  AND existing.user_id = %s
                  AND sa.id <> existing.id
                  AND (sa.user_id IS NULL OR sa.user_id <> %s)
                """,
                (session_id, user_id, user_id)
            )

            cur.execute(
                """
                UPDATE scroll_associations
                SET user_id = %s
                WHERE session_id = %s
                  AND (user_id IS NULL OR user_id <> %s)
                """,
                (user_id, session_id, user_id)
            )

            cur.execute(
                """
                UPDATE scrolls
                SET user_id = %s,
                    corpus_layer = CASE
                        WHEN corpus_layer = 'community' THEN 'personal'
                        ELSE corpus_layer
                    END
                WHERE session_id = %s
                  AND user_id IS NULL
                """,
                (user_id, session_id)
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

def build_authenticated_me_response(user: dict, session_id: str) -> dict:
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
        "last_login": user_row.get("last_login").isoformat() if user_row.get("last_login") else None,
        "seeker_id": user.get("seeker_id"),
        "anonymous_user_id": session_id,
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


def build_anonymous_me_response(session_id: str) -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM oracle_interactions
                WHERE session_id = %s
                """,
                (session_id,)
            )
            usage_row = cur.fetchone()
            questions_used = usage_row["total"] if usage_row else 0

            cur.execute(
                """
                SELECT mode, COUNT(*) AS total
                FROM oracle_interactions
                WHERE session_id = %s
                GROUP BY mode
                """,
                (session_id,)
            )
            mode_rows = cur.fetchall()

            cur.execute(
                """
                SELECT COUNT(DISTINCT scroll_id) AS total
                FROM scroll_associations
                WHERE session_id = %s
                """,
                (session_id,)
            )
            scroll_row = cur.fetchone()
            session_scroll_count = scroll_row["total"] if scroll_row else 0
    finally:
        conn.close()

    question_limit = 9
    question_display = get_question_display("anon", questions_used, question_limit)
    mode_counts = {row["mode"]: row["total"] for row in mode_rows}
    combined_title = compute_combined_title(
        session_scroll_count,
        "anon",
        authenticated=False
    )
    support = build_anonymous_support_status_payload()
    continuity_nudges = build_claim_nudges(session_scroll_count)

    return {
        "authenticated": False,
        "display_name": None,
        "email": None,
        "email_verified": False,
        "last_login": None,
        "seeker_id": None,
        "anonymous_user_id": session_id,
        "scroll_count": session_scroll_count,
        "scrolls_donated": session_scroll_count,
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
        "continuity_nudges": continuity_nudges,
        "anonymous_upload_limit": ANONYMOUS_UPLOAD_LIMIT,
        "claim_required": session_scroll_count >= ANONYMOUS_UPLOAD_LIMIT,
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
            "questions_remaining": max(question_limit - questions_used, 0),
            "question_limit_display": question_display["question_limit_display"],
            "questions_remaining_display": question_display["questions_remaining_display"],
            "is_unlimited_questions": question_display["is_unlimited_questions"],
            "hathor_questions": mode_counts.get("Hathor", 0),
            "moses_questions": mode_counts.get("Moses", 0)
        }
    }

@app.get("/audio/{filename}")
def get_audio_file(filename: str):
    if "/" in filename or "\\ " in filename or not filename.endswith(".mp3"):
        raise HTTPException(status_code=404, detail="Audio file not found.")

    audio_path = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="Audio file not found.")

    return FileResponse(audio_path, media_type="audio/mpeg")


@app.post("/whisper")
async def whisper_endpoint(
    request: Request,
    file: UploadFile = File(...),
    voice: str = Form("Hathor")
):
    try:
        file_bytes = await file.read()
        transcript = transcribe_audio(file_bytes)

        if not transcript:
            return JSONResponse(
                content={"error": "Whisper could not transcribe.", "answer": "⚠️ Whisper could not transcribe."},
                status_code=422
            )

        oracle_payload = QuestionInput(
            question=transcript,
            deity=voice
        )

        request.state.oracle_input_mode = "voice"
        result = await ask_oracle(request, oracle_payload)

        if isinstance(result, JSONResponse):
            return result

        answer = result.get("answer", "")
        audio_url = generate_tts_audio(answer, voice) if answer else None

        return {
            "question": transcript,
            "answer": answer,
            "audio_url": audio_url
        }

    except Exception as e:
        logger.exception("Whisper voice endpoint failed")
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

    session_id = get_or_create_session_id(request)
    merge_anonymous_history_into_user(session_id, user_id)
    
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

    session_id = get_or_create_session_id(request)
    request.session["user_id"] = user_id
    request.session["display_name"] = display_name

    merge_anonymous_history_into_user(session_id, user_id)

    return {"message": "Login successful"}

       
@app.post("/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
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
    session_id = get_or_create_session_id(request)
    user = get_current_user(request)

    if user:
        return build_authenticated_me_response(user, session_id)

    return build_anonymous_me_response(session_id)

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

@app.post("/upload_scroll")
async def upload_scroll(request: Request, scroll: UploadFile = File(...), seeker_id: str = Form(None), anonymous_user_id: str = Form(None)):
    anonymous_user_id = anonymous_user_id or get_or_create_session_id(request)
    ensure_anonymous_user(anonymous_user_id)

    user = get_current_user(request)
    authenticated_user_id = user["user_id"] if user else None

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
            return JSONResponse(
                content={
                    "error": "Anonymous upload limit reached for this browser. Claim this path to continue offering scrolls.",
                    "claim_required": True,
                    "upload_count_for_browser": upload_count,
                    "continuity_nudges": build_claim_nudges(upload_count),
                    "anonymous_upload_limit": ANONYMOUS_UPLOAD_LIMIT,
                },
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
                return JSONResponse(
                    content={
                        "error": "Please wait a few seconds before offering another scroll.",
                        "warning": "We’re slowing repeated uploads to protect the Temple. Please wait a moment before trying again.",
                        "cooldown_seconds_remaining": seconds_remaining,
                        "upload_count_for_browser": upload_count,
                    },
                    status_code=429
                )

    seeker_id = resolve_seeker_id(anonymous_user_id, seeker_id)
        
    # Save the file with safe name to prevent overwrites
    safe_name = f"{uuid.uuid4()}_{scroll.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(scroll.file, f)

    file_ext = os.path.splitext(file_path)[1].lower()

    # Extract text
    extracted_text = extract_text_from_scroll(file_path)

    if not extracted_text.strip():
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

    # Ensure session exists before inserting scroll
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions (id)
            VALUES (%s)
            ON CONFLICT (id) DO NOTHING
            """,
            (anonymous_user_id,),
        )
    conn.commit()
    conn.close()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrolls (
                    session_id,
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
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id;
                """,
                (
                    anonymous_user_id,
                    authenticated_user_id,
                    "file",
                    scroll.filename,
                    scroll.content_type,
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
                INSERT INTO scroll_associations (scroll_id, user_id, session_id)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (scroll_id, authenticated_user_id, anonymous_user_id)
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

                if os.path.exists(file_path):
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
                INSERT INTO scroll_associations (scroll_id, user_id, session_id)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (scroll_id, authenticated_user_id, anonymous_user_id)
            )

        conn.commit()
        conn.close()

        if os.path.exists(file_path):
            os.remove(file_path)

        if authenticated_user_id:
            refresh_user_scroll_count(authenticated_user_id)

        duplicate_payload = {
            "duplicate": True,
            "message": "This scroll is already present in the Temple. No duplicate copy was stored. It will still be recognized in your personal record."
        }

        if not authenticated_user_id:
            duplicate_payload["message"] = "This scroll is already present in the Temple. No duplicate copy was stored. It will still be recognized in this Seeker’s path."
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

    conn = get_db_connection()
    with conn.cursor() as cur:
        for i, chunk in enumerate(chunks):
            cur.execute(
                """
                INSERT INTO scroll_chunks
                (scroll_id, chunk_index, chunk_text)
                VALUES (%s,%s,%s)
                """,
                (scroll_id, i, chunk)
            )
            try:
                cache_chunk_embedding(chunk)
            except Exception as e:
                logger.warning(f"Chunk embedding cache warm failed: {e}")

    conn.commit()
    conn.close()

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


def get_active_recurring_subscription_for_user(user_id: str) -> Optional[dict]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    plan_code,
                    support_mode,
                    provider_status,
                    internal_status,
                    stripe_subscription_id,
                    current_period_start,
                    current_period_end,
                    cancel_at_period_end,
                    updated_at
                FROM subscriptions
                WHERE user_id = %s
                  AND provider = 'stripe'
                  AND support_mode IN ('monthly_recurring', 'annual_recurring')
                  AND provider_status IN ('active', 'trialing', 'past_due', 'unpaid')
                  AND ended_at IS NULL
                ORDER BY updated_at DESC NULLS LAST, current_period_end DESC NULLS LAST
                LIMIT 1
                """,
                (user_id,)
            )
            return cur.fetchone()
    finally:
        conn.close()


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
                helper_name = f"apply_subscription_renewal_failure_to_floor:{context.get('support_mode')}"

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
        active_recurring = get_active_recurring_subscription_for_user(user["user_id"])
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

def compress_dialogue(memory: str, max_chars: int = 1200) -> str:
    if not memory:
        return ""

    if len(memory) <= max_chars:
        return memory

    # keep most recent portion
    return "...earlier dialogue omitted...\n\n" + memory[-max_chars:]

def normalize_for_scoring(text: str) -> list[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return [w for w in text.split() if len(w) > 2]

def detect_memory_intent(question: str) -> str:
    q = (question or "").lower().strip()

    recall_patterns = [
        "what did i ask",
        "what did i say",
        "what did you say",
        "what was my last question",
        "what was your last answer",
        "earlier you said",
        "you said before",
        "as i said before",
        "didn't i say",
        "have i asked",
        "have we discussed",
        "what were we talking about",
        "remind me what",
        "recall",
        "remember when",
        "last time",
        "previously",
        "before"
    ]

    for pattern in recall_patterns:
        if pattern in q:
            return "recall"

    return "reflection"


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

    session_id = get_or_create_session_id(request)

    user = get_current_user(request)
    user_id = user["user_id"] if user else None

    plan_code = "anon"
    memory_depth = 1

    if not can_user_ask(session_id, user_id):
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
        phase1_started_at = None
        phase1_finished_at = None
        final_model_started_at = None
        final_model_finished_at = None

        question = payload.question
        question = question[:1000]
        deity = payload.deity
        input_mode = getattr(request.state, "oracle_input_mode", "text")
        logger.info(
            "ASK input_mode=%s deity=%s len=%s",
            input_mode,
            deity,
            len(question)
        )

        # --— detect memory intent ---
        memory_intent = detect_memory_intent(question)


        # --- Resolve title for memory depth ---

        if user:
            entitlement = get_user_entitlement_snapshot(user_id)
            plan_code = entitlement["effective_plan_code"]
            memory_depth = get_memory_depth(plan_code)

        # --- Retrieve seeker long-term memory ---
        memories = retrieve_seeker_memory(user_id, session_id, memory_depth)

        # --- Retrieve conversation memory ---
        memory = get_session_memory(session_id, memory_depth)

        # --— normalize memory inputs for prompt ---
        recent_memory = memory
        compressed_memory = compress_dialogue(memory)
        limited_memories = memories[:5] if memories else []

        # Phase 1 helper stays flat across tiers:
        # only the latest session exchange, no compressed history, no long-term memory.
        helper_recent_memory = get_session_memory(session_id, 1)
        helper_compressed_memory = ""
        helper_limited_memories = []

        # --- Conditional retrieval based on memory intent ---
        passages = []

        if memory_intent != "recall":
            retrieval_started_at = datetime.datetime.now()
            passages = retrieve_context(question, user_id)
            passages = rank_passages(passages, question)
            retrieval_finished_at = datetime.datetime.now()


        
        # --— structured memory weighting ---
        memory_block = ""

        if recent_memory:
            memory_block += "PRIMARY EVIDENCE — RECENT EXACT DIALOGUE:\n"
            memory_block += recent_memory + "\n\n"

        if compressed_memory and compressed_memory != recent_memory:
            memory_block += "SECONDARY EVIDENCE — EARLIER SESSION DIALOGUE:\n"
            memory_block += compressed_memory + "\n\n"

        if limited_memories:
            memory_block += "TERTIARY EVIDENCE — LONG-TERM SEEKER MEMORY:\n"
            memory_block += "\n\n".join(limited_memories) + "\n\n"

        # --— fallback retrieval if recall requested but memory is empty ---
        if memory_intent == "recall" and not memory_block.strip():
            retrieval_started_at = datetime.datetime.now()
            passages = retrieve_context(question, user_id)
            passages = rank_passages(passages, question, max_items=2)
            retrieval_finished_at = datetime.datetime.now()

        passages_before_llama = list(passages or [])
        long_term_memory_count = len([item for item in limited_memories if (item or "").strip()])
        recent_memory_present = bool((recent_memory or "").strip())
        compressed_memory_present = bool((compressed_memory or "").strip())
        passage_preview_1 = _llama_preview(passages_before_llama[0]) if len(passages_before_llama) > 0 else ""
        passage_preview_2 = _llama_preview(passages_before_llama[1]) if len(passages_before_llama) > 1 else ""

        logger.info(
            "LLAMA_INPUT deity=%s memory_intent=%s plan_code=%s recent_memory_present=%s compressed_memory_present=%s long_term_memory_count=%s candidate_passages=%s passage_preview_1=%s passage_preview_2=%s",
            deity,
            memory_intent,
            plan_code,
            recent_memory_present,
            compressed_memory_present,
            long_term_memory_count,
            len(passages_before_llama),
            passage_preview_1,
            passage_preview_2
        )

        llama_phase1 = None
        llama_compact_brief = ""
        phase1_started_at = None
        phase1_finished_at = None
        bypass_llama_phase1 = should_bypass_llama_phase1_for_request(
            plan_code=plan_code,
            input_mode=input_mode
        )

        if bypass_llama_phase1:
            passages = passages_before_llama
            llama_phase1 = {
                "enabled": False,
                "shadow_only": False,
                "provider": "ollama",
                "budget_tier": "skipped",
                "selected_passage_indexes": [],
                "compact_brief": "",
                "reason": "skipped: voice_entry_plan"
            }
            logger.info(
                "LLAMA_PHASE1_BYPASS input_mode=%s deity=%s memory_intent=%s plan_code=%s passages_before=%s reason=voice_entry_plan",
                input_mode,
                deity,
                memory_intent,
                plan_code,
                len(passages_before_llama)
            )
        else:
            support_packet = build_support_packet(
                question=question,
                deity=deity,
                memory_intent=memory_intent,
                plan_code=plan_code,
                recent_memory=helper_recent_memory,
                compressed_memory=helper_compressed_memory,
                limited_memories=helper_limited_memories,
                passages=passages_before_llama
            )

            phase1_started_at = datetime.datetime.now()
            llama_phase1 = await run_llama_phase1(support_packet)
            phase1_finished_at = datetime.datetime.now()
            passages, llama_compact_brief = apply_phase1_result(passages_before_llama, llama_phase1)

        logger.info(
            "%s input_mode=%s deity=%s memory_intent=%s plan_code=%s",
            summarize_phase1_result(
                llama_phase1,
                passages_before=len(passages_before_llama),
                passages_after=len(passages)
            ),
            input_mode,
            deity,
            memory_intent,
            plan_code
        )

        context_block = ""
        if passages or llama_compact_brief:
            context_block = "\n\nBackground wisdom for reflection:\n\n"
            if llama_compact_brief:
                context_block += "LLaMA retrieval brief:\n" + llama_compact_brief + "\n\n"
            if passages:
                context_block += "\n\n".join(passages)

        # --— dual-mode prompt ---

        response_word_cap = get_response_word_cap(
            plan_code=plan_code,
            memory_intent=memory_intent,
            deity=deity,
            input_mode=input_mode
        )
        response_max_tokens = words_to_max_tokens(response_word_cap)

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
        3. Integrate relevant past context when helpful.
        4. Keep responses coherent and under {response_word_cap} words.
        5. For higher access levels, allow a fuller reflection when the question genuinely invites it, while still avoiding rambling.
        """
        enhanced_question = f"""{instruction_block}

        {memory_block}

        Current seeker question:
        {question}

        {context_block}
        """ 

        # --- Oracle response ---
        selected_moses_model = None
        moses_route_reason = None
        moses_prompt_chars = None

        if deity == "Moses":
            selected_moses_model, moses_route_reason, moses_prompt_chars = choose_moses_model(
                raw_question=question,
                memory_intent=memory_intent,
                plan_code=plan_code,
                memory_block=memory_block,
                context_block=context_block
            )

        final_model_started_at = datetime.datetime.now()
        result = await get_oracle_response(
            enhanced_question,
            deity,
            force_mode=memory_intent,
            memory_block=memory_block,
            context_block=context_block,
            max_output_tokens=response_max_tokens,
            memory_intent=memory_intent,
            plan_code=plan_code,
            selected_moses_model=selected_moses_model,
            moses_route_reason=moses_route_reason,
            moses_prompt_chars=moses_prompt_chars
        )
        final_model_finished_at = datetime.datetime.now()

        raw_answer = result["answer"]
        source_model = result["source_model"]

        if not raw_answer:
            raw_answer = "The Oracle is silent."

        raw_answer = trim_response_to_word_cap(raw_answer, response_word_cap)

        def _ms(started_at, finished_at):
            if not started_at or not finished_at:
                return "-"
            return round((finished_at - started_at).total_seconds() * 1000, 2)

        logger.info(
            "ASK_STAGE_TIMING input_mode=%s deity=%s memory_intent=%s plan_code=%s retrieval_ms=%s phase1_ms=%s final_model_ms=%s total_ms=%s",
            input_mode,
            deity,
            memory_intent,
            plan_code,
            _ms(retrieval_started_at, retrieval_finished_at),
            _ms(phase1_started_at, phase1_finished_at),
            _ms(final_model_started_at, final_model_finished_at),
            _ms(ask_started_at, datetime.datetime.now())
        )

        logger.info(f"ANSWER len={len(raw_answer)}")

        # --- Token metering ---
        estimated_tokens = estimate_tokens(question, raw_answer)
        usage_class = "registered" if user_id else "anonymous"

        # --- Architect observation ---
        architect_obs = architect_observe_v3(question, deity, session_id)

        # --- LLaMA observation ---
        try:
            llama_obs = get_llama_observation(question, deity, raw_answer, None)
        except Exception as e:
            logger.warning(f"LLaMA observation error: {e}")
            llama_obs = None

        # --- Logging ---
        save_log({
            "memory_intent": memory_intent,
            "memory_has_content": bool(memory_block.strip()),
            "timestamp": str(datetime.datetime.now()),
            "session_id": session_id,
            "seeker_id": user_id,
            "anonymous_user_id": session_id,
            "question": question,
            "oracle_used": deity,
            "answer": raw_answer,
            "architect_observation": architect_obs,
            "llama_observation": llama_obs,
            "llama_phase1": llama_phase1,
            "llama_passages_before": len(passages_before_llama),
            "llama_passages_after": len(passages),
            "source_model": source_model,
            "phase": "5.5",
            "corpus_intent": "authoritative_training_data",
            "personal_retrieval_score": None,
            "global_retrieval_score": None,
            "shadow_delta": None,
            "influence_state": "disabled",
            "estimated_tokens": estimated_tokens,
            "usage_class": usage_class
        })

        # --- Database logging ---
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO oracle_interactions
                (session_id, user_id, input_type, question_text, response_text, model_provider, model_name, mode)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    user_id,
                    "text",
                    question,
                    raw_answer,
                    "xai" if deity == "Hathor" else "openai",
                    source_model,
                    deity
                )
            )

        conn.commit()
        conn.close()

        return {
            "question": question,
            "answer": raw_answer
        }
       
    except Exception as e:
        logger.error(f"Oracle endpoint error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)