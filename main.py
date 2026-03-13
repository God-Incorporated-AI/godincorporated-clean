import datetime
from datetime import timezone
import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
from typing import Optional

from docx import Document
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import Signer, BadSignature
from passlib.context import CryptContext
from starlette.middleware.sessions import SessionMiddleware
from openai import OpenAI
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from PyPDF2 import PdfReader

import httpx
import psycopg2

from config.settings import LLAMA_ENABLED, xai_api_key
from services.tts import generate_tts_audio
from services.whisper import transcribe_audio
from services.mail import send_email
from storage.json_store import UPLOAD_DIR, AUDIO_DIR, TRANSCRIPT_LOG, save_log

load_dotenv()

def get_ip_hash(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()



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
        system_prompt = "You are Hathor, the ancient Egyptian goddess of love, music, and joy. Respond with intuitive, reflective, emotionally resonant wisdom, drawing from mystical and spiritual traditions. Use poetic language and metaphors to guide the seeker. Use the background wisdom provided to inform your response, but do not cite or reference the sources explicitly."
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
        system_prompt = "You are Moses, the prophet who received the Ten Commandments. Respond with logical, instructive, and doctrinal wisdom rooted in biblical and canonical traditions. Use the background wisdom provided to inform your answer, but do not cite or reference the sources explicitly."
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

def search_canonical_scrolls(question: str, limit: int = 6):

    conn = get_db_connection()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

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


def retrieve_context(question: str, user_id: Optional[str]):

    personal = search_personal_scrolls(user_id, question, limit=4)
    canonical = search_canonical_scrolls(question, limit=6)
    community = search_community_scrolls(question, limit=2)

    return personal + canonical + community
    
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
    """Get current authenticated user from session."""
    user_id = request.session.get("user_id")
    if user_id:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id, email, seeker_id, display_name, email_verified FROM users WHERE id = %s", (user_id,))
            result = cur.fetchone()
        conn.close()
        if result:
            return {
                "user_id": result['id'],
                "email": result['email'],
                "seeker_id": result['seeker_id'],
                "display_name": result['display_name'],
                "is_verified": result['email_verified']
            }
    return None

def get_question_limit(user: Optional[dict]) -> int:
    """
    Central entitlement authority.
    Monetary plan-driven limits.
    """

    # True anonymous (not logged in, no DB user)
    if not user:
        return 9

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT plan_code FROM users WHERE id = %s",
                (user["user_id"],)
            )
            row = cur.fetchone()

            if not row:
                return 9

            plan = (row.get("plan_code") or "anon").lower()

    finally:
        conn.close()

    plan_limits = {
        "anon": 9,           # Registered but unpaid
        "minerval": 33,
        "adept": 99,
        "magus": 333,
        "ipsissimus": 10000  # Human-scale unlimited
    }

    return plan_limits.get(plan, 9)


# ================================
# Phase 5 — Title Computation Axis
# ================================

def compute_scroll_tier(scroll_count: int) -> str:
    if scroll_count >= 99:
        return "Ipsissimus"
    elif scroll_count >= 33:
        return "Luminary"
    elif scroll_count >= 9:
        return "Archivist"
    else:
        return "Dormant"


def compute_monetary_title(plan_code: str) -> str:
    mapping = {
        "anon": "Anon",
        "minerval": "Minerval",
        "adept": "Adept",
        "magus": "Magus",
        "ipsissimus": "Ipsissimus"
    }
    return mapping.get(plan_code, "Anon")


def compute_combined_title(scroll_count: int, plan_code: str, authenticated: bool) -> str:
    scroll_title = compute_scroll_tier(scroll_count)

    if not authenticated:
        monetary_title = "Anon"
    else:
        monetary_title = compute_monetary_title(plan_code)

    return f"{scroll_title} {monetary_title}"

def can_user_ask(session_id: str, user_id: Optional[str] = None) -> bool:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if user_id:
                cur.execute(
                    "SELECT COUNT(*) AS total FROM oracle_interactions WHERE user_id = %s",
                    (user_id,)
                )
            else:
                cur.execute(
                    "SELECT COUNT(*) AS total FROM oracle_interactions WHERE session_id = %s",
                    (session_id,)
                )

            row = cur.fetchone()
            count = row["total"] if row else 0

        user = {"user_id": user_id} if user_id else None
        limit = get_question_limit(user)

        return count < limit

    finally:
        conn.close()


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
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM scrolls")
        result = cur.fetchone()

    conn.close()

    return {"count": result["count"]}

class RegisterInput(BaseModel):
    display_name: Optional[str] = None  # Optional

@app.post("/register")
def register_seeker(payload: RegisterInput):
    seeker_id = str(uuid.uuid4())
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO users (id, seeker_id, display_name, display_name_lower, title, scroll_count, donation_total, influence_state, eligibility_flags, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (seeker_id, seeker_id, payload.display_name, (payload.display_name or "").lower(), "Seeker", 0, 0, "disabled", [], datetime.datetime.now(timezone.utc)))
    conn.commit()
    conn.close()
    return {"seeker_id": seeker_id, "message": "Registration successful. Welcome to the temple."}

class AuthRegisterInput(BaseModel):
    email: str
    password: str
    display_name: str

@app.post("/auth/register")
def auth_register(payload: AuthRegisterInput, request: Request):
    import re
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
            INSERT INTO users (id, email, password_hash, seeker_id, display_name, display_name_lower, email_verified, verification_token, created_at, last_login, title, scroll_count, donation_total, influence_state, eligibility_flags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (user_id, email, hashed_password, seeker_id, display_name, display_name.lower(), False, verification_token, created_at, None, "Seeker", 0, 0, "disabled", []))
    conn.commit()
    conn.close()
    
    # Build verification link
    base_url = os.getenv("BASE_URL", "http://localhost:8000")
    verification_link = f"{base_url}/auth/verify-email?token={verification_token}"
    
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
        return JSONResponse(content={"error": "Registration successful, but failed to send verification email. Please contact support."}, status_code=500)
    
    # Set session
    request.session["user_id"] = user_id
    
       
    return {"message": "Registration successful. Please check your email for verification link.", "user_id": user_id}

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

        if not result or not verify_password(password, result['password_hash']):
            conn.close()
            return JSONResponse(content={"error": "Invalid email or password"}, status_code=401)
        
        if not result['email_verified']:
            conn.close()
            return JSONResponse(content={"error": "Please verify your email before logging in."}, status_code=403)
        
        user_id = result['id']

        # Update last login
        cur.execute(
            "UPDATE users SET last_login = %s WHERE id = %s",
            (datetime.datetime.now(timezone.utc), user_id)
        )

        # 🔐 Establish session
        request.session["user_id"] = user_id
        request.session["display_name"] = result["display_name"]

    conn.commit()
    conn.close()

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
        
        user_id = result['id']
        email = result['email']
        
        # Mark user as verified and clear token
        cur.execute("UPDATE users SET email_verified = true, verification_token = null WHERE id = %s", (user_id,))
    conn.commit()
    conn.close()
    
    # Send confirmation email
    try:
        send_email(
            email,
            "Email Verification Successful",
            "Your email has been successfully verified. You can now log in to your account."
        )
    except Exception as e:
        logging.error(f"Failed to send email verification confirmation to {email}: {e}")
        # Log the error but continue, as verification succeeded
    
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
    
    # Validate password
    if len(new_password) < 8:
        return JSONResponse(content={"error": "Password must be at least 8 characters"}, status_code=400)
    
    if len(new_password.encode("utf-8")) > 72:
        return JSONResponse(content={"error": "Password must be 72 bytes or fewer."}, status_code=400)
    
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, email FROM users WHERE reset_token = %s AND reset_token_expires_at > %s", (token, datetime.datetime.now(timezone.utc)))
        result = cur.fetchone()
        if not result:
            conn.close()
            return JSONResponse(content={"error": "Invalid or expired reset token"}, status_code=400)
        
        user_id = result['id']
        email = result['email']
        
        # Update password and clear token
        cur.execute("UPDATE users SET password_hash = %s, reset_token = null, reset_token_expires_at = null WHERE id = %s", (hash_password(new_password), user_id))
    conn.commit()
    conn.close()
    
    # Send confirmation email
    try:
        send_email(
            email,
            "Password Reset Confirmation",
            "Your password has been successfully changed. If you did not request this change, please contact support immediately."
        )
    except Exception as e:
        logging.error(f"Failed to send password reset confirmation email to {email}: {e}")
        # Log the error but continue, as password reset succeeded
    
    # Invalidate sessions (clear session store - since we use in-memory, this is a no-op for now)
    # In a real app, you'd clear all sessions for this user
    
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
        user_id = result['id']
        # Create reset token
        token = str(uuid.uuid4())
        expires_at = datetime.datetime.now(timezone.utc) + datetime.timedelta(minutes=30)
        
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET reset_token = %s, reset_token_expires_at = %s WHERE id = %s", (token, expires_at, user_id))
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

@app.get("/me")
def get_me(request: Request):

    # Ensure stable anonymous session_id
    session_id = request.session.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        request.session["session_id"] = session_id

    user = get_current_user(request)

        # Authenticated branch
    if user:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                # Count usage
                cur.execute(
                    "SELECT COUNT(*) AS total FROM oracle_interactions WHERE user_id = %s",
                    (user["user_id"],)
                )
                row = cur.fetchone()
                questions_asked = row["total"] if row else 0

                # Get scroll + plan metadata
                cur.execute(
                    "SELECT scroll_count, plan_code FROM users WHERE id = %s",
                    (user["user_id"],)
                )
                meta = cur.fetchone()
                scroll_count = meta["scroll_count"] if meta else 0
                plan_code = (meta["plan_code"] or "anon") if meta else "anon"

        finally:
            conn.close()

        combined_title = compute_combined_title(
            scroll_count,
            plan_code,
            authenticated=True
        )

        return {
            "authenticated": True,
            "display_name": user["display_name"],
            "anonymous_user_id": session_id,
            "scroll_count": scroll_count,
            "plan_code": plan_code,
            "title": combined_title,
            "usage": {
                "questions_asked": questions_asked,
                "question_limit": get_question_limit(user)
            }
        }
    

    # Anonymous branch
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM oracle_interactions WHERE session_id = %s",
                (session_id,)
            )
            row = cur.fetchone()
            questions_asked = row["total"] if row else 0
    finally:
        conn.close()

    combined_title = compute_combined_title(
        0,
        "anon",
        authenticated=False
    )

    return {
        "authenticated": False,
        "anonymous_user_id": session_id,
        "scroll_count": 0,
        "plan_code": None,
        "title": combined_title,
        "usage": {
            "questions_asked": questions_asked,
            "question_limit": 9
        }
    }


@app.post("/upload_scroll")
async def upload_scroll(scroll: UploadFile = File(...), seeker_id: str = Form(None), anonymous_user_id: str = Form(None)):
    ensure_anonymous_user(anonymous_user_id)
    seeker_id = resolve_seeker_id(anonymous_user_id, seeker_id)
    # Use seeker_id if provided, else generate temp uploader_id
    uploader_id = seeker_id if seeker_id else str(uuid.uuid4())
    
    # Save the file with safe name to prevent overwrites
    safe_name = f"{uuid.uuid4()}_{scroll.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(scroll.file, f)
           
    # Extract text
    extracted_text = extract_text_from_scroll(file_path)

    text_hash = hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()

    if not extracted_text.strip():
       raise HTTPException(status_code=400, detail="Could not extract text from scroll")

    # Determine corpus layer
    corpus_layer = "personal" if seeker_id else "community" 
    
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
                seeker_id,
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

        for i, chunk in enumerate(chunks):
            cur.execute(
                """
                INSERT INTO scroll_chunks
                (scroll_id, chunk_index, chunk_text)
                VALUES (%s,%s,%s)
                """,
                (scroll_id, i, chunk)
            )

    conn.commit()
    conn.close()

    # Update seeker scroll_count if seeker_id provided
    if seeker_id:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET scroll_count = scroll_count + 1 WHERE seeker_id = %s",
                (seeker_id,)
            )
        conn.commit()
        conn.close()

    return {"message": "📜 Your scroll has been uploaded.", "scroll_id": scroll_id}

class QuestionInput(BaseModel):
    question: str
    deity: str = "Hathor"  # Default to Hathor
    seeker_id: Optional[str] = None
    anonymous_user_id: Optional[str] = None

@app.post("/ask")
async def ask_oracle(request: Request, payload: QuestionInput):

    session_id = request.session.get("session_id")
    if not session_id:
        session_id = str(uuid.uuid4())
        request.session["session_id"] = session_id

    user = get_current_user(request)
    user_id = user["user_id"] if user else None

    if not can_user_ask(session_id, user_id):
        return JSONResponse(
            content={"error": "Usage limit reached. Please log in or try again later."},
            status_code=429
        )

    try:
        question = payload.question
        question = question[:1000]
        deity = payload.deity
        print("ASK:", deity, "len(question) =", len(question))

        # --- Layered retrieval ---
        passages = retrieve_context(question, user_id)

        context_block = ""
        if passages:
            context_block = "\n\nBackground wisdom for reflection:\n\n"
            context_block += "\n\n".join(passages)

        enhanced_question = f"""
        Seeker question:
        {question}

        Relevant passages from the Temple corpus:

        {context_block}
        """
        
        # --- Oracle response ---
        result = await get_oracle_response(enhanced_question, deity)

        answer = result["answer"]
        source_model = result["source_model"]


        print("ANSWER len =", len(answer))

        # --- Token metering ---
        estimated_tokens = estimate_tokens(question, answer)
        usage_class = "registered" if payload.seeker_id else "anonymous"

        # --- Architect observation ---
        architect_obs = architect_observe_v3(question, deity, session_id)

        # --- LLaMA observation ---
        try:
            llama_obs = get_llama_observation(question, deity, answer, None)
        except Exception as e:
            print("LLaMA observation error:", str(e))
            llama_obs = None

        # --- Logging ---
        save_log({
            "timestamp": str(datetime.datetime.now()),
            "session_id": session_id,
            "seeker_id": user_id,
            "anonymous_user_id": session_id,
            "question": question,
            "oracle_used": deity,
            "answer": answer,
            "architect_observation": architect_obs,
            "llama_observation": llama_obs,
            "source_model": source_model,
            "phase": "4.0",
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
                    answer,
                    "openai",
                    source_model,
                    deity
                )
            )

        conn.commit()
        conn.close()

        return {"answer": answer}

    except Exception as e:
        print("Error:", str(e))
        return JSONResponse(content={"error": str(e)}, status_code=500)