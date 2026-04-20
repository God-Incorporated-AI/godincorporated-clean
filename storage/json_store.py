import os
import json
import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

UPLOAD_DIR = os.path.join(BASE_DIR, "scrolls_uploads")
AUDIO_DIR = os.getenv("AUDIO_DIR", os.path.join(UPLOAD_DIR, "audio"))
TRANSCRIPT_LOG = os.path.join(BASE_DIR, "oracle_log.json")
SCROLL_DB = os.path.join(BASE_DIR, "scroll_data.json")
SEEKERS_DB = os.path.join(BASE_DIR, "seekers.json")
VISITORS_DB = os.path.join(BASE_DIR, "visitors.json")
IDENTITY_CLAIMS_DB = os.path.join(BASE_DIR, "identity_claims.json")
USERS_DB = os.path.join(BASE_DIR, "users.json")
VERIFICATION_TOKENS_DB = os.path.join(BASE_DIR, "verification_tokens.json")
RESET_TOKENS_DB = os.path.join(BASE_DIR, "reset_tokens.json")

def save_log(entry):
    try:
        logs = []
        if os.path.exists(TRANSCRIPT_LOG):
            with open(TRANSCRIPT_LOG, "r") as f:
                content = f.read().strip()
                logs = json.loads(content) if content else []
        logs.append(entry)
        with open(TRANSCRIPT_LOG, "w") as f:
            json.dump(logs, f, indent=2)
    except Exception as e:
        print("⚠️ Logging failed:", e)

def load_scroll_data():
    """Load scroll data from JSON, return list of scrolls."""
    try:
        with open(SCROLL_DB, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []

def save_scroll_data(scrolls):
    """Save list of scrolls to JSON."""
    with open(SCROLL_DB, "w") as f:
        json.dump(scrolls, f, indent=2)

def load_seekers():
    """Load seekers data from JSON, return dict of seekers."""
    try:
        with open(SEEKERS_DB, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}

def save_seekers(seekers):
    """Save dict of seekers to JSON."""
    with open(SEEKERS_DB, "w") as f:
        json.dump(seekers, f, indent=2)

def load_visitors():
    """Load visitors data from JSON, return dict of visitors."""
    try:
        with open(VISITORS_DB, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}

def save_visitors(visitors):
    """Save dict of visitors to JSON."""
    with open(VISITORS_DB, "w") as f:
        json.dump(visitors, f, indent=2)

def load_identity_claims():
    """Load identity claims from JSON, return list of claims."""
    try:
        with open(IDENTITY_CLAIMS_DB, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []

def save_identity_claims(claims):
    """Save list of identity claims to JSON."""
    with open(IDENTITY_CLAIMS_DB, "w") as f:
        json.dump(claims, f, indent=2)

def load_users():
    """Load users from JSON, return dict of users."""
    try:
        with open(USERS_DB, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                # Migrate existing users to have is_verified
                migrated = False
                for user in data.values():
                    if "is_verified" not in user:
                        user["is_verified"] = True
                        migrated = True
                if migrated:
                    save_users(data)
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}

def save_users(users):
    """Save dict of users to JSON."""
    with open(USERS_DB, "w") as f:
        json.dump(users, f, indent=2)

def load_verification_tokens():
    """Load verification tokens from JSON, return list of tokens."""
    try:
        with open(VERIFICATION_TOKENS_DB, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []

def save_verification_tokens(tokens):
    """Save list of verification tokens to JSON."""
    with open(VERIFICATION_TOKENS_DB, "w") as f:
        json.dump(tokens, f, indent=2)

def load_reset_tokens():
    """Load reset tokens from JSON, return list of tokens."""
    try:
        with open(RESET_TOKENS_DB, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []

def save_reset_tokens(tokens):
    """Save list of reset tokens to JSON."""
    with open(RESET_TOKENS_DB, "w") as f:
        json.dump(tokens, f, indent=2)
