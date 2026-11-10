"""Central configuration. Every value can be overridden via environment variables or backend/.env."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
FRONTEND_DIR = BASE_DIR.parent / "frontend"

try:  # optional: load backend/.env
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover
    pass


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


# --- Server -----------------------------------------------------------------
# Always bind to loopback in production; Tailscale (`tailscale serve`) is the only door in.
HOST = os.getenv("JARVIS_HOST", "127.0.0.1")
PORT = int(os.getenv("JARVIS_PORT", "8000"))

# --- Data -------------------------------------------------------------------
DATA_DIR = Path(os.getenv("JARVIS_DATA_DIR", str(BASE_DIR)))
DB_PATH = Path(os.getenv("JARVIS_DB", str(DATA_DIR / "jarvis.db")))
# The only directory the agent's file / shell tools may touch.
WORKSPACE_DIR = Path(os.getenv("JARVIS_WORKSPACE", str(BASE_DIR / "workspace"))).resolve()

# --- Security ---------------------------------------------------------------
# Shared secret for the dashboard login. Required whenever HOST is not loopback.
AUTH_TOKEN = os.getenv("JARVIS_AUTH_TOKEN", "")
# Secret used to sign session cookies (defaults to a key derived from AUTH_TOKEN).
SESSION_SECRET = os.getenv("JARVIS_SESSION_SECRET", "")
SESSION_DAYS = int(os.getenv("JARVIS_SESSION_DAYS", "30"))
# Tailnet logins (e.g. you@github) trusted via the `Tailscale-User-Login` header set by `tailscale serve`.
TRUSTED_TAILSCALE_USERS = {u.lower() for u in _list("JARVIS_TAILSCALE_USERS")}
# Extra CORS origins (leave empty: the dashboard is served same-origin).
ALLOWED_ORIGINS = _list("JARVIS_ALLOWED_ORIGINS")
RATE_LIMIT_PER_MIN = int(os.getenv("JARVIS_RATE_LIMIT_PER_MIN", "40"))

# --- LLM ("the brain") ------------------------------------------------------
# Local models are served by Ollama (https://ollama.com). Cloud models use any
# OpenAI-compatible endpoint (OpenRouter by default, works with OpenAI, Groq, ...).
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# CPU-friendly default (~4.7 GB RAM at Q4).
DEFAULT_MODEL = os.getenv("JARVIS_DEFAULT_MODEL", "qwen2.5:7b-instruct")
# Cloud model used automatically when the local model fails / times out (needs OPENAI_API_KEY).
FALLBACK_MODEL = os.getenv("JARVIS_FALLBACK_MODEL", "qwen/qwen-2.5-72b-instruct")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))
HISTORY_TURNS = int(os.getenv("JARVIS_HISTORY_TURNS", "6"))
MAX_AGENT_STEPS = int(os.getenv("JARVIS_MAX_STEPS", "6"))

# --- Agent tools ------------------------------------------------------------
# readonly: only "safe" tools run      confirm: risky tools need your approval (default)
# auto:     risky tools run without asking (NOT recommended)        "blocked" tools never run.
TOOL_MODE = os.getenv("JARVIS_TOOL_MODE", "confirm").lower()
APPROVAL_TIMEOUT = float(os.getenv("JARVIS_APPROVAL_TIMEOUT", "120"))
SHELL_TIMEOUT = float(os.getenv("JARVIS_SHELL_TIMEOUT", "30"))
TOOL_OUTPUT_LIMIT = int(os.getenv("JARVIS_TOOL_OUTPUT_LIMIT", "4000"))
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")  # optional; DuckDuckGo HTML is used otherwise

# --- Memory -----------------------------------------------------------------
EMBED_MODEL = os.getenv("JARVIS_EMBED_MODEL", "nomic-embed-text")
MEMORY_TOP_K = int(os.getenv("JARVIS_MEMORY_TOP_K", "4"))

# --- Scheduler --------------------------------------------------------------
SCHEDULER_ENABLED = _bool("JARVIS_SCHEDULER", True)
SCHEDULER_TICK = float(os.getenv("JARVIS_SCHEDULER_TICK", "20"))
TIMEZONE = os.getenv("JARVIS_TZ", "")  # e.g. America/New_York; empty = server local time

# --- Voice ------------------------------------------------------------------
# STT: "browser" (Web Speech API, default) | "whisper" (faster-whisper) | "vosk"
STT_ENGINE = os.getenv("STT_ENGINE", "browser").lower()
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small.en")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "")

# TTS: "browser" (speechSynthesis, default) | "elevenlabs" | "coqui"
TTS_ENGINE = os.getenv("TTS_ENGINE", "browser").lower()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")  # "George" (British)
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
COQUI_MODEL = os.getenv("COQUI_MODEL", "tts_models/en/ljspeech/vits")

# --- Display ----------------------------------------------------------------
# Static demo rates (base currency USD). Swap for a live FX API if needed.
CURRENCY_RATES = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "JPY": 151.0, "CAD": 1.37}
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CAD": "CA$"}


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")
