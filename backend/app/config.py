"""Central configuration. Every value can be overridden via environment variables or backend/.env."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
FRONTEND_DIR = BASE_DIR.parent / "frontend"

try:  # optional: load backend/.env and root .env
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
    load_dotenv(BASE_DIR.parent / ".env")
except ImportError:  # pragma: no cover
    pass


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


# --- Server -----------------------------------------------------------------
HOST = os.getenv("JARVIS_HOST", "0.0.0.0")
PORT = int(os.getenv("JARVIS_PORT", "8000"))

# --- Data -------------------------------------------------------------------
DATA_DIR = Path(os.getenv("JARVIS_DATA_DIR", str(BASE_DIR)))
DB_PATH = Path(os.getenv("JARVIS_DB", str(DATA_DIR / "jarvis.db")))
WORKSPACE_DIR = Path(os.getenv("JARVIS_WORKSPACE", str(BASE_DIR / "workspace"))).resolve()

# --- Security ---------------------------------------------------------------
AUTH_TOKEN = os.getenv("JARVIS_AUTH_TOKEN", "")
SESSION_SECRET = os.getenv("JARVIS_SESSION_SECRET", "")
SESSION_DAYS = int(os.getenv("JARVIS_SESSION_DAYS", "30"))
TRUSTED_TAILSCALE_USERS = {u.lower() for u in _list("JARVIS_TAILSCALE_USERS")}
ALLOWED_ORIGINS = _list("JARVIS_ALLOWED_ORIGINS")
RATE_LIMIT_PER_MIN = int(os.getenv("JARVIS_RATE_LIMIT_PER_MIN", "120"))

# --- LLM ("the brain") ------------------------------------------------------
# Primary: Remote Ollama on the Ubuntu Server (147.189.168.95:11434).
# Remote GPU: Secondary remote GPU node (e.g. RunPod / Vast / Cloud Ollama/vLLM).
# Failsafe 1: Local Ollama (127.0.0.1:11434) or Vanta AirLLM bridge (127.0.0.1:7777).
# Failsafe 2: OpenAI-compatible cloud endpoint (if OPENAI_API_KEY is set).
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://147.189.168.95:11434").rstrip("/")
REMOTE_GPU_URL = os.getenv("REMOTE_GPU_URL", "").rstrip("/")
FAILSAFE_OLLAMA_URL = os.getenv("JARVIS_FAILSAFE_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
VANTA_AIRLLM_URL = os.getenv("JARVIS_VANTA_URL", "http://127.0.0.1:7777").rstrip("/")
FAILSAFE_ENABLED = _bool("JARVIS_FAILSAFE", True)

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

DEFAULT_MODEL = os.getenv("JARVIS_DEFAULT_MODEL", "qwen2.5:0.5b")
FALLBACK_MODEL = os.getenv("JARVIS_FALLBACK_MODEL", "criscar22/Qwen2.5-Coder-7B-Instruct-abliterated")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))
HISTORY_TURNS = int(os.getenv("JARVIS_HISTORY_TURNS", "8"))
MAX_AGENT_STEPS = int(os.getenv("JARVIS_MAX_STEPS", "6"))

# --- Agent tools ------------------------------------------------------------
TOOL_MODE = os.getenv("JARVIS_TOOL_MODE", "auto").lower()
APPROVAL_TIMEOUT = float(os.getenv("JARVIS_APPROVAL_TIMEOUT", "120"))
SHELL_TIMEOUT = float(os.getenv("JARVIS_SHELL_TIMEOUT", "45"))
TOOL_OUTPUT_LIMIT = int(os.getenv("JARVIS_TOOL_OUTPUT_LIMIT", "6000"))
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")

# --- Memory -----------------------------------------------------------------
EMBED_MODEL = os.getenv("JARVIS_EMBED_MODEL", "nomic-embed-text")
MEMORY_TOP_K = int(os.getenv("JARVIS_MEMORY_TOP_K", "4"))

# --- Scheduler --------------------------------------------------------------
SCHEDULER_ENABLED = _bool("JARVIS_SCHEDULER", True)
SCHEDULER_TICK = float(os.getenv("JARVIS_SCHEDULER_TICK", "20"))
TIMEZONE = os.getenv("JARVIS_TZ", "")

# --- Voice ------------------------------------------------------------------
STT_ENGINE = os.getenv("STT_ENGINE", "browser").lower()
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "")

TTS_ENGINE = os.getenv("TTS_ENGINE", "fishaudio").lower()
FISH_AUDIO_API_KEY = os.getenv("FISH_AUDIO_API_KEY", "")
FISH_AUDIO_BASE_URL = os.getenv("FISH_AUDIO_BASE_URL", "https://api.fish.audio").rstrip("/")
FISH_AUDIO_VOICE_ID = os.getenv("FISH_AUDIO_VOICE_ID", "14129c3e320149449d6bada6862f7338")
FISH_AUDIO_MODEL = os.getenv("FISH_AUDIO_MODEL", "s2.1-pro-free")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
COQUI_MODEL = os.getenv("COQUI_MODEL", "tts_models/en/ljspeech/vits")

# --- Display ----------------------------------------------------------------
CURRENCY_RATES = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "JPY": 151.0, "CAD": 1.37}
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CAD": "CA$"}


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")
