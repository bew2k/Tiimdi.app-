import os


def _int(name: str, default: int) -> int:
    """Number from an environment variable; empty string or garbage -> default value."""
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _flag(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    return default if not v else v in ("1", "true", "yes", "on")


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tiimdi:tiimdi@localhost:5432/tiimdi")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
CACHE_TTL = _int("CACHE_TTL", 60)

# Behind a reverse proxy (nginx, Caddy) the real client IP is in X-Forwarded-For.
# If the API is exposed to the internet directly, without a proxy, set TRUST_PROXY=0.
TRUST_PROXY = _flag("TRUST_PROXY", True)

# --- AI (see app/ai.py and .env.example) ---
AI_PROVIDER = os.getenv("AI_PROVIDER", "mock")      # mock | openai_compat
AI_BASE_URL = os.getenv("AI_BASE_URL", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "")
AI_TIMEOUT = _float("AI_TIMEOUT", 45)               # seconds, request with a menu photo
AI_TEXT_TIMEOUT = _float("AI_TEXT_TIMEOUT", 8)      # seconds, short text advice
AI_DAILY_LIMIT = _int("AI_DAILY_LIMIT", 200)        # calls per day per provider; 0 = no limit

# Fallback provider (optional): used if the primary is unavailable or its limit is exhausted.
AI_FALLBACK_BASE_URL = os.getenv("AI_FALLBACK_BASE_URL", "")
AI_FALLBACK_API_KEY = os.getenv("AI_FALLBACK_API_KEY", "")
AI_FALLBACK_MODEL = os.getenv("AI_FALLBACK_MODEL", "")
AI_FALLBACK_VISION = _flag("AI_FALLBACK_VISION", False)   # whether the fallback model can read photos

# Fictional venues for development (data/demo).
SHOW_DEMO_DATA = _flag("SHOW_DEMO_DATA", False)
