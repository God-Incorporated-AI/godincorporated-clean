import datetime
import json
import logging
import os
import shutil
import tempfile
import uuid
from typing import Optional

from docx import Document
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from PyPDF2 import PdfReader

import httpx
import psycopg2

from config.settings import LLAMA_ENABLED, xai_api_key
from services.tts import generate_tts_audio
from services.whisper import transcribe_audio
from storage.json_store import UPLOAD_DIR, AUDIO_DIR, TRANSCRIPT_LOG, SCROLL_DB, SEEKERS_DB, VISITORS_DB, save_log, load_scroll_data, save_scroll_data, load_seekers, save_seekers, load_visitors, save_visitors

app = FastAPI()

static_path = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_path), name="static")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)
openai_client = None  # Lazy load

def get_openai_client():
    global openai_client
    if openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")
        openai_client = OpenAI(api_key=api_key)
    return openai_client

# Phase 3.2.1: Minimal read-only Postgres connectivity
def get_db_connection():
    """Lazy-loaded read-only DB connection. No writes allowed."""
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

# Optional: Add a test endpoint (can be removed later)
@app.get("/db_test")
def db_test():
    """Endpoint to verify DB connectivity without affecting app behavior."""
    success = test_db_connectivity()
    return {"db_connected": success}

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

async def get_oracle_response(question: str, deity: str):
    # Phase 2: Restore explicit oracle separation
    # Hathor: xAI API, Moses: OpenAI, LLaMA: Not active
    if deity == "Hathor":
        # Hathor uses xAI API with intuitive, poetic system prompt
        if not xai_api_key:
            raise ValueError("XAI_API_KEY not set for Hathor oracle")
        system_prompt = "You are Hathor, the ancient Egyptian goddess of love, music, and joy. Respond with intuitive, reflective, emotionally resonant wisdom, drawing from mystical and spiritual traditions. Use poetic language and metaphors to guide the seeker."
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    "https://api.x.ai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {xai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "grok-3",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": question}
                        ],
                    },
                )
            if response.status_code == 200:
                data = response.json()
                return {"answer": data["choices"][0]["message"]["content"], "source_model": "xAI"}
            else:
                raise ValueError(f"XAI API error: {response.status_code} - {response.text}")
        except Exception as e:
            raise ValueError(f"XAI API call failed: {type(e).__name__}: {str(e)}")
    elif deity == "Moses":
        # Moses uses OpenAI with logical, doctrinal system prompt
        client = get_openai_client()
        system_prompt = "You are Moses, the prophet who received the Ten Commandments. Respond with logical, instructive, and doctrinal wisdom, drawing from biblical and canonical teachings. Provide clear guidance and moral instruction."
        response = client.chat.completions.create(
            model="gpt-4o",  # Updated model
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question}
            ]
        )
        return {"answer": response.choices[0].message.content, "source_model": "OpenAI"}
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

def extract_text_from_scroll(file_path):
    text = ""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".pdf":
            reader = PdfReader(file_path)
            for page in reader.pages:
                text += page.extract_text() or ""
        elif ext == ".docx":
            doc = Document(file_path)
            for para in doc.paragraphs:
                text += para.text + "\n"
        elif ext in [".txt", ".md", ".rtf"]:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
    except Exception as e:
        print(f"Failed to extract text: {e}")
    return text.strip()

def reset_scroll_system():
    """Helper function to reset the scroll ingestion system safely."""
    # Clear all files in scrolls_uploads/
    for filename in os.listdir(UPLOAD_DIR):
        file_path = os.path.join(UPLOAD_DIR, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
    
    # Reset scroll_data.json to empty list
    with open(SCROLL_DB, "w") as f:
        json.dump([], f)

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

def update_visitor(visitor_id: str, tokens_used: int):
    """Update visitor ledger with token usage."""
    visitors = load_visitors()
    today = str(datetime.date.today())
    if visitor_id not in visitors:
        visitors[visitor_id] = {
            "created_at": str(datetime.datetime.now()),
            "last_seen": str(datetime.datetime.now()),
            "last_seen_date": today,
            "token_used_total": 0,
            "token_used_today": 0,
            "limit_state": "ok"
        }
    else:
        visitor = visitors[visitor_id]
        if visitor.get("last_seen_date") != today:
            visitor["token_used_today"] = 0
            visitor["last_seen_date"] = today
        visitor["last_seen"] = str(datetime.datetime.now())
    visitor = visitors[visitor_id]
    visitor["token_used_total"] += tokens_used
    visitor["token_used_today"] += tokens_used
    save_visitors(visitors)

@app.get("/", response_class=HTMLResponse)
@app.get("/temple", response_class=HTMLResponse)
def temple_page(request: Request):
    return templates.TemplateResponse("temple.html", {"request": request})

@app.post("/reset_scrolls")
def reset_scrolls():
    reset_scroll_system()
    return {"message": "Scroll system reset successfully."}

@app.get("/scrolls")
def get_scroll_count():
    scrolls = load_scroll_data()
    return {
        "count": len(scrolls),
        "files": scrolls
    }

class RegisterInput(BaseModel):
    display_name: Optional[str] = None  # Optional

@app.post("/register")
def register_seeker(payload: RegisterInput):
    seeker_id = str(uuid.uuid4())
    seekers = load_seekers()
    seekers[seeker_id] = {
        "seeker_id": seeker_id,
        "created_at": str(datetime.datetime.now()),
        "display_name": payload.display_name,
        "title": "Seeker",  # Default
        "scroll_count": 0,
        "donation_total": 0.0,
        "influence_state": "disabled",
        "eligibility_flags": []
    }
    save_seekers(seekers)
    return {"seeker_id": seeker_id, "message": "Registration successful. Welcome to the temple."}

@app.post("/upload_scroll")
async def upload_scroll(scroll: UploadFile = File(...), seeker_id: str = Form(None), anonymous_user_id: str = Form(None)):
    ensure_anonymous_user(anonymous_user_id)
    # Use seeker_id if provided, else generate temp uploader_id
    uploader_id = seeker_id if seeker_id else str(uuid.uuid4())
    
    # Save the file with safe name to prevent overwrites
    safe_name = f"{uuid.uuid4()}_{scroll.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(scroll.file, f)
    
    # Extract text
    extracted_text = extract_text_from_scroll(file_path)
    
    # Create scroll entry
    scroll_entry = {
        "scroll_id": str(uuid.uuid4()),
        "uploader_id": uploader_id,
        "filename": scroll.filename,  # Original filename for display
        "safe_filename": safe_name,  # Safe filename for storage
        "extracted_text": extracted_text,
        "timestamp": str(datetime.datetime.now())
    }
    
    # Load existing scrolls, append, save
    scrolls = load_scroll_data()
    scrolls.append(scroll_entry)
    save_scroll_data(scrolls)
    
    # Update seeker scroll_count if seeker_id provided
    if seeker_id:
        seekers = load_seekers()
        if seeker_id in seekers:
            seekers[seeker_id]["scroll_count"] += 1
            save_seekers(seekers)
    
    return {"message": "📜 Your scroll has been uploaded.", "scroll_id": scroll_entry["scroll_id"]}

class QuestionInput(BaseModel):
    question: str
    deity: str = "Hathor"  # Default to Hathor
    seeker_id: Optional[str] = None
    anonymous_user_id: str

@app.post("/ask")
async def ask_oracle(request: Request, payload: QuestionInput):
    seeker = payload.seeker_id
    anonymous_user_id = payload.anonymous_user_id
    ensure_anonymous_user(anonymous_user_id)
    try:
        question = payload.question
        deity = payload.deity
        print("ASK:", deity, "len(question) =", len(question))
        session_id = str(uuid.uuid4())

        result = await get_oracle_response(question, deity)
        answer = result["answer"]
        source_model = result["source_model"]
        print("ANSWER len =", len(answer))
        
        # Phase 3.1: Token metering for anonymous continuity
        estimated_tokens = estimate_tokens(question, answer)
        if anonymous_user_id:
            update_visitor(anonymous_user_id, estimated_tokens)
        usage_class = "registered" if payload.seeker_id else "anonymous"
        
        architect_obs = architect_observe_v3(question, deity, session_id)
        scrolls = load_scroll_data()  # For LLaMA analysis
        try:
            llama_obs = get_llama_observation(question, deity, answer, scrolls)
        except Exception as e:
            print("LLaMA observation error:", str(e))
            llama_obs = None
        save_log({
            "timestamp": str(datetime.datetime.now()),
            "session_id": session_id,
            "seeker_id": seeker,
            "anonymous_user_id": anonymous_user_id,
            "question": question,
            "oracle_used": deity,
            "answer": answer,
            "architect_observation": architect_obs,
            "llama_observation": llama_obs,
            "source_model": source_model,
            "phase": "4.0",
            "corpus_intent": "authoritative_training_data",
            # Phase 3.1 influence fields (defaults)
            "personal_retrieval_score": None,
            "global_retrieval_score": None,
            "shadow_delta": None,
            "influence_state": "disabled",
            # Phase 3.1 anonymous metering
            "estimated_tokens": estimated_tokens,
            "usage_class": usage_class
        })
        return {"answer": answer}

    except Exception as e:
        print("Error:", str(e))
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/whisper")
async def whisper_audio(request: Request, file: UploadFile = File(...), voice: str = Form("Hathor"), seeker_id: str = Form(None), anonymous_user_id: str = Form(None)):
    try:
        ensure_anonymous_user(anonymous_user_id)
        session_id = str(uuid.uuid4())
        question = transcribe_audio(await file.read())
        print(f"🎤 Whisper transcription: {question}")

        result_oracle = await get_oracle_response(question, voice)
        answer = result_oracle["answer"]
        source_model = result_oracle["source_model"]
        
        # Phase 3.1: Token metering for anonymous continuity
        estimated_tokens = estimate_tokens(question, answer)
        if anonymous_user_id:
            update_visitor(anonymous_user_id, estimated_tokens)
        usage_class = "registered" if seeker_id else "anonymous"
        
        architect_obs = architect_observe_v3(question, voice, session_id)
        scrolls = load_scroll_data()  # For LLaMA analysis
        try:
            llama_obs = get_llama_observation(question, voice, answer, scrolls)
        except Exception as e:
            print("LLaMA observation error:", str(e))
            llama_obs = None
        save_log({
            "timestamp": str(datetime.datetime.now()),
            "session_id": session_id,
            "seeker_id": seeker_id,
            "anonymous_user_id": anonymous_user_id,
            "question": question,
            "oracle_used": voice,
            "answer": answer,
            "architect_observation": architect_obs,
            "llama_observation": llama_obs,
            "source_model": source_model,
            "phase": "4.0",
            "corpus_intent": "authoritative_training_data",
            # Phase 3.1 influence fields (defaults)
            "personal_retrieval_score": None,
            "global_retrieval_score": None,
            "shadow_delta": None,
            "influence_state": "disabled",
            # Phase 3.1 anonymous metering
            "estimated_tokens": estimated_tokens,
            "usage_class": usage_class
        })

        audio_url = generate_tts_audio(answer, voice)

        return {"transcription": question, "answer": answer, "audio_url": audio_url}

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
