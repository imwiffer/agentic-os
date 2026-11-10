"""Agentic OS - Jarvis Brain (FastAPI).

REST:
  GET  /api/health
  GET  /api/user
  GET  /api/dashboard/stats      -> numbers for the cards
  GET  /api/models               -> Local / Cloud model dropdown
  GET  /api/config               -> active voice engines, currencies
  POST /api/jarvis/chat          -> {"input": "..."}  (REST fallback for the WebSocket)
WebSocket:
  /ws/jarvis  (alias /ws/audio)  -> bi-directional text, PCM audio in, actions + TTS audio out
Static:
  /                              -> frontend/
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, config, db
from .voice import STT, TTS, available_stt_engines, available_tts_engines

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("jarvis")

stt = STT(config.STT_ENGINE)
tts = TTS(config.TTS_ENGINE)
TOKENS = {"total": 195}  # "Your data: +N tokens processed" ribbon counter
MAX_AUDIO_BYTES = 16000 * 2 * 30  # 30 s of 16 kHz PCM16
STATE_KEYS = {"currentTab", "businessTab", "currency", "model", "voiceActive"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    log.info("DB ready at %s | STT=%s | TTS=%s | default model=%s",
             config.DB_PATH, stt.engine, tts.engine, config.DEFAULT_MODEL)
    yield


app = FastAPI(title="Agentic OS - Jarvis Brain", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ChatRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=2000)
    state: dict = Field(default_factory=dict)


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/user")
async def user():
    return db.get_user()


@app.get("/api/dashboard/stats")
async def dashboard_stats():
    stats = db.dashboard_stats()
    stats["rates"] = config.CURRENCY_RATES
    stats["tokens_processed"] = TOKENS["total"]
    return stats


@app.get("/api/models")
async def models():
    return await agent.list_models()


@app.get("/api/config")
async def app_config():
    return {
        "stt": stt.engine, "stt_available": available_stt_engines(),
        "tts": tts.engine, "tts_available": available_tts_engines(),
        "currencies": list(config.CURRENCY_RATES), "default_model": config.DEFAULT_MODEL,
    }


@app.post("/api/jarvis/chat")
async def jarvis_chat(req: ChatRequest):
    result = await agent.run(req.input, req.state)
    TOKENS["total"] += result.tokens
    return {**result.to_dict(), "tokens_total": TOKENS["total"]}


# --- WebSocket --------------------------------------------------------------

async def _handle_text(ws: WebSocket, text: str, state: dict, history: list[dict]) -> None:
    await ws.send_json({"type": "status", "status": "thinking"})
    result = await agent.run(text, state, history)
    history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": result.raw_plan}])
    del history[:-config.HISTORY_TURNS * 2]
    TOKENS["total"] += result.tokens
    log.info("[%s %dms] %r -> %s | %r", result.engine, result.latency_ms, text,
             [a["action"] for a in result.actions], result.response)

    for cmd in result.actions:  # keep the server's view of the UI in sync
        if cmd["action"] == "selectModel":
            state["model"] = cmd["target"]
        elif cmd["action"] == "setCurrency":
            state["currency"] = cmd["target"]
        elif cmd["action"] == "openTab":
            key = "businessTab" if cmd["target"] in agent.BUSINESS_TABS else "currentTab"
            state[key] = cmd["target"]
        await ws.send_json({"type": "action", **cmd})

    await ws.send_json({
        "type": "reply", "text": result.response, "engine": result.engine, "model": result.model,
        "tokens": result.tokens, "tokens_total": TOKENS["total"], "latency_ms": result.latency_ms,
        "note": result.error, "audio": tts.enabled,
    })

    if tts.enabled:  # stream synthesized speech back as binary frames
        await ws.send_json({"type": "audio_start", "mime": tts.mime})
        try:
            async for chunk in tts.stream(result.response):
                await ws.send_bytes(chunk)
            await ws.send_json({"type": "audio_end"})
        except Exception as exc:
            log.warning("TTS failed: %s", exc)
            await ws.send_json({"type": "audio_end", "error": str(exc)})


async def jarvis_ws(ws: WebSocket) -> None:
    await ws.accept()
    state: dict = {"model": config.DEFAULT_MODEL, "currency": "USD"}
    history: list[dict] = []
    audio = bytearray()
    sample_rate = 16000
    log.info("WebSocket client connected")
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:  # PCM16 mic audio
                if len(audio) < MAX_AUDIO_BYTES:
                    audio.extend(msg["bytes"])
                continue
            try:
                data = json.loads(msg.get("text") or "{}")
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON message"})
                continue

            kind = data.get("type")
            if kind in ("hello", "state"):
                state.update({k: v for k, v in (data.get("state") or {}).items() if k in STATE_KEYS})
                if kind == "hello":
                    await ws.send_json({"type": "ready", "stt": stt.engine, "tts": tts.engine})
            elif kind == "user_text":
                text = str(data.get("text") or "").strip()[:2000]
                if text:
                    await _handle_text(ws, text, state, history)
            elif kind == "audio_start":
                audio.clear()
                sample_rate = int(data.get("sample_rate") or 16000)
                await ws.send_json({"type": "status", "status": "listening"})
            elif kind == "audio_end":
                pcm = bytes(audio)
                audio.clear()
                await ws.send_json({"type": "status", "status": "transcribing"})
                try:
                    text = await stt.transcribe(pcm, sample_rate)
                except Exception as exc:
                    log.exception("STT failed")
                    await ws.send_json({"type": "error", "message": f"Transcription failed: {exc}"})
                    continue
                if not text:
                    await ws.send_json({"type": "error", "message": "I didn't catch that.", "recoverable": True})
                    continue
                await ws.send_json({"type": "transcript", "text": text})
                await _handle_text(ws, text, state, history)
            elif kind == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        log.info("WebSocket client disconnected")


app.add_api_websocket_route("/ws/jarvis", jarvis_ws)
app.add_api_websocket_route("/ws/audio", jarvis_ws)

# Serve the dashboard (must be registered last so /api and /ws win).
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
