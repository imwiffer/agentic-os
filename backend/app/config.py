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

# --- Data -------------------------------------------------------------------
DB_PATH = Path(os.getenv("JARVIS_DB", str(BASE_DIR / "jarvis.db")))

# --- LLM ("the brain") ------------------------------------------------------
# Local models are served by Ollama (https://ollama.com). Cloud models use any
# OpenAI-compatible endpoint (OpenRouter by default, works with OpenAI, Groq, ...).
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEFAULT_MODEL = os.getenv("JARVIS_DEFAULT_MODEL", "qwen3.8:27b")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "45"))
HISTORY_TURNS = int(os.getenv("JARVIS_HISTORY_TURNS", "6"))

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
