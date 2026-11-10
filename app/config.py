import os


def _int(name: str, default: int) -> int:
    """Число из переменной окружения; пустая строка или мусор -> значение по умолчанию."""
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

# За обратным прокси (nginx, Caddy) настоящий IP клиента лежит в X-Forwarded-For.
# Если API открыт в интернет напрямую, без прокси, поставьте TRUST_PROXY=0.
TRUST_PROXY = _flag("TRUST_PROXY", True)

# --- ИИ (см. app/ai.py и .env.example) ---
AI_PROVIDER = os.getenv("AI_PROVIDER", "mock")      # mock | openai_compat
AI_BASE_URL = os.getenv("AI_BASE_URL", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "")
AI_TIMEOUT = _float("AI_TIMEOUT", 45)               # секунд, запрос с фото меню
AI_TEXT_TIMEOUT = _float("AI_TEXT_TIMEOUT", 8)      # секунд, короткий текстовый совет
AI_DAILY_LIMIT = _int("AI_DAILY_LIMIT", 200)        # вызовов в сутки на провайдера; 0 = без лимита

# Запасной провайдер (необязательно): включается, если основной недоступен или лимит исчерпан.
AI_FALLBACK_BASE_URL = os.getenv("AI_FALLBACK_BASE_URL", "")
AI_FALLBACK_API_KEY = os.getenv("AI_FALLBACK_API_KEY", "")
AI_FALLBACK_MODEL = os.getenv("AI_FALLBACK_MODEL", "")
AI_FALLBACK_VISION = _flag("AI_FALLBACK_VISION", False)   # умеет ли запасная модель читать фото

# Вымышленные заведения для разработки (data/demo).
SHOW_DEMO_DATA = _flag("SHOW_DEMO_DATA", False)
