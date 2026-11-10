"""Agentic OS - Jarvis Brain (FastAPI).

Public (no auth):
  GET  /api/health
  GET  /api/auth/status
  POST /api/login
  POST /api/logout
Authenticated (session cookie, Tailscale identity header, or Bearer token):
  GET  /api/user                 GET  /api/dashboard/stats
  GET  /api/models               GET  /api/config
  GET  /api/system               GET  /api/roles
  GET  /api/memories             POST /api/memories            DELETE /api/memories/{id}
  GET  /api/jobs                 POST /api/jobs                DELETE /api/jobs/{id}       POST /api/jobs/{id}/run
  GET  /api/inbox                POST /api/inbox/read
  GET  /api/audit
  POST /api/jarvis/chat
  WS   /ws/jarvis  (alias /ws/audio)
Static:
  /                              -> frontend/
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, auth, config, db, hub, memory, roles, runtime, scheduler
from .tools import REGISTRY, load_builtin_tools, system as system_tool
from .voice import STT, TTS, available_stt_engines, available_tts_engines

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("jarvis")

stt = STT(config.STT_ENGINE)
tts = TTS(config.TTS_ENGINE)
TOKENS = {"total": 195}  # "Your data: +N tokens processed" ribbon counter
MAX_AUDIO_BYTES = 16000 * 2 * 30  # 30 s of 16 kHz PCM16
STATE_KEYS = {"currentTab", "businessTab", "currency", "model", "voiceActive", "role"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    auth.validate_startup()
    load_builtin_tools()
    db.init_db()
    config.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    if config.SCHEDULER_ENABLED:
        scheduler.scheduler.start()
    log.info("Ready | bind=%s:%s | auth=%s | tool_mode=%s | default_model=%s | tools=%d",
             config.HOST, config.PORT, "on" if auth.enabled() else "dev-off",
             config.TOOL_MODE, config.DEFAULT_MODEL, len(REGISTRY))
    yield
    await scheduler.scheduler.stop()


app = FastAPI(title="Agentic OS - Jarvis Brain", version="0.2.0", lifespan=lifespan)
if config.ALLOWED_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, allow_credentials=True,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    res = await call_next(request)
    res.headers["X-Content-Type-Options"] = "nosniff"
    res.headers["X-Frame-Options"] = "DENY"
    res.headers["Referrer-Policy"] = "same-origin"
    return res


# --- Models (Pydantic) ------------------------------------------------------

class LoginRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)


class ChatRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=2000)
    state: dict = Field(default_factory=dict)
    role: str = Field(default="jarvis")
    conversation_id: str = Field(default="default", max_length=64)


class MemoryRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    tags: str = Field(default="", max_length=200)


class JobRequest(BaseModel):
    name: str = Field(default="", max_length=80)
    prompt: str = Field(..., min_length=1, max_length=2000)
    role: str = Field(default="jarvis")
    every_minutes: float | None = None
    daily_at: str | None = None
    run_at: str | None = None


class InboxReadRequest(BaseModel):
    id: int | None = None


# --- Public routes ----------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"ok": True, "clients": hub.client_count()}


@app.get("/api/auth/status")
async def auth_status(request: Request):
    user = auth.identify(request)
    return {"auth_required": auth.enabled(), "authenticated": user is not None, "user": user}


@app.post("/api/login")
async def login(req: LoginRequest, request: Request, response: Response):
    peer = request.client.host if request.client else "unknown"
    if not auth.login_limiter.allow(peer):
        raise HTTPException(status_code=429, detail="Too many login attempts; wait a minute.")
    if not auth.check_token(req.token):
        raise HTTPException(status_code=401, detail="Invalid access token.")
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(auth.COOKIE, auth.make_cookie("owner"), max_age=config.SESSION_DAYS * 86400,
                        httponly=True, samesite="strict", secure=secure, path="/")
    return {"ok": True, "user": "owner"}


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


# --- Authenticated REST routes ---------------------------------------------

api = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


@api.get("/user")
async def get_user():
    return db.get_user()


@api.get("/dashboard/stats")
async def dashboard_stats():
    stats = db.dashboard_stats()
    stats["rates"] = config.CURRENCY_RATES
    stats["tokens_processed"] = TOKENS["total"]
    return stats


@api.get("/models")
async def models():
    return await agent.list_models()


@api.get("/config")
async def app_config():
    return {
        "stt": stt.engine, "stt_available": available_stt_engines(),
        "tts": tts.engine, "tts_available": available_tts_engines(),
        "currencies": list(config.CURRENCY_RATES), "default_model": config.DEFAULT_MODEL,
        "fallback_model": config.FALLBACK_MODEL, "tool_mode": config.TOOL_MODE,
        "tools": [{"name": t.name, "description": t.description, "kind": t.kind,
                   "risk": t.risk if isinstance(t.risk, str) else "dynamic"} for t in REGISTRY.values()],
    }


@api.get("/system")
async def system_info():
    return await system_tool.snapshot()


@api.get("/roles")
async def list_roles():
    return {"roles": roles.public_roles()}


@api.get("/memories")
async def get_memories(q: str = ""):
    items = await memory.search(q, k=20, min_score=0.15) if q.strip() else memory.listing()
    return {"memories": items}


@api.post("/memories")
async def create_memory(req: MemoryRequest, request: Request):
    return await memory.save(req.text, req.tags, source=request.state.user)


@api.delete("/memories/{memory_id}")
async def remove_memory(memory_id: int):
    if not memory.forget(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True}


@api.get("/jobs")
async def get_jobs():
    return {"jobs": [scheduler.public(j) for j in db.list_jobs(include_disabled=True)]}


@api.post("/jobs")
async def create_job(req: JobRequest):
    try:
        return scheduler.create_job(req.name, req.prompt, req.role, req.every_minutes, req.daily_at, req.run_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.delete("/jobs/{job_id}")
async def remove_job(job_id: int):
    if not db.delete_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


@api.post("/jobs/{job_id}/run")
async def trigger_job(job_id: int):
    jobs = [j for j in db.list_jobs(include_disabled=True) if j["id"] == job_id]
    if not jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    item = await scheduler.scheduler.run_job(jobs[0], manual=True)
    return {"ok": True, "inbox": item}


@api.get("/inbox")
async def get_inbox():
    items = db.list_inbox()
    return {"items": items, "unread": sum(1 for x in items if not x["read"])}


@api.post("/inbox/read")
async def read_inbox(req: InboxReadRequest):
    db.mark_inbox_read(req.id)
    return {"ok": True}


@api.get("/audit")
async def get_audit(limit: int = 100):
    return {"audit": db.list_audit(min(max(limit, 1), 500))}


@api.post("/jarvis/chat")
async def jarvis_chat(req: ChatRequest, request: Request):
    user = request.state.user
    if not auth.chat_limiter.allow(user):
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")
    history = [{"role": m["role"], "content": m["content"]} for m in db.recent_messages(req.conversation_id, config.HISTORY_TURNS * 2)]
    result = await runtime.run(req.input, req.state, history, role=req.role or req.state.get("role", "jarvis"),
                               actor=user, origin="rest")
    db.add_message(req.conversation_id, "user", req.input)
    db.add_message(req.conversation_id, "assistant", result.raw_plan)
    TOKENS["total"] += result.tokens
    return {**result.to_dict(), "tokens_total": TOKENS["total"]}


app.include_router(api)


# --- WebSocket --------------------------------------------------------------

async def _run_turn(ws: WebSocket, text: str, state: dict, conv_id: str, actor: str,
                    approvals: dict[str, asyncio.Future]) -> None:
    if not auth.chat_limiter.allow(actor):
        await ws.send_json({"type": "error", "message": "Rate limit exceeded; slow down a moment."})
        return

    async def emit(event: dict) -> None:
        await ws.send_json(event)

    async def approver(tool_name: str, args: dict, reason: str) -> str:
        req_id = secrets.token_hex(8)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        approvals[req_id] = fut
        await ws.send_json({"type": "approval_request", "id": req_id, "tool": tool_name, "args": args, "reason": reason})
        try:
            ok = await asyncio.wait_for(fut, timeout=config.APPROVAL_TIMEOUT)
            return "approved" if ok else "denied"
        except asyncio.TimeoutError:
            return "timeout"
        finally:
            approvals.pop(req_id, None)

    await ws.send_json({"type": "status", "status": "thinking"})
    history = [{"role": m["role"], "content": m["content"]} for m in db.recent_messages(conv_id, config.HISTORY_TURNS * 2)]
    role = state.get("role", "jarvis")
    result = await runtime.run(text, state, history, role=role, actor=actor, origin="ws", emit=emit, approver=approver)
    db.add_message(conv_id, "user", text)
    db.add_message(conv_id, "assistant", result.raw_plan)
    TOKENS["total"] += result.tokens
    log.info("[%s/%s %dms] %r -> %s | %r", result.role, result.engine, result.latency_ms, text,
             [a["action"] for a in result.actions], result.response)

    for cmd in result.actions:
        if cmd["action"] == "selectModel":
            state["model"] = cmd["target"]
        elif cmd["action"] == "setCurrency":
            state["currency"] = cmd["target"]
        elif cmd["action"] == "openTab":
            key = "businessTab" if cmd["target"] in agent.BUSINESS_TABS else "currentTab"
            state[key] = cmd["target"]
        await ws.send_json({"type": "action", **cmd})

    await ws.send_json({
        "type": "reply", "text": result.response, "engine": result.engine, "model": result.model, "role": result.role,
        "steps": result.steps, "tokens": result.tokens, "tokens_total": TOKENS["total"],
        "latency_ms": result.latency_ms, "note": result.error, "audio": tts.enabled,
    })

    if tts.enabled:
        await ws.send_json({"type": "audio_start", "mime": tts.mime})
        try:
            async for chunk in tts.stream(result.response):
                await ws.send_bytes(chunk)
            await ws.send_json({"type": "audio_end"})
        except Exception as exc:
            log.warning("TTS failed: %s", exc)
            await ws.send_json({"type": "audio_end", "error": str(exc)})


async def jarvis_ws(ws: WebSocket) -> None:
    if not auth.origin_ok(ws):
        await ws.close(code=1008)
        return
    actor = auth.identify(ws)
    if actor is None:
        await ws.close(code=4401)
        return

    await ws.accept()
    state: dict = {"model": config.DEFAULT_MODEL, "currency": "USD", "role": "jarvis"}
    conv_id = "default"
    audio = bytearray()
    sample_rate = 16000
    approvals: dict[str, asyncio.Future] = {}
    turn_task: asyncio.Task | None = None
    hub.register(ws.send_json)
    log.info("WebSocket connected (%s)", actor)

    def start_turn(text: str) -> None:
        nonlocal turn_task
        if turn_task and not turn_task.done():
            return
        turn_task = asyncio.create_task(_run_turn(ws, text, state, conv_id, actor, approvals))

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
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
                if data.get("conversation_id"):
                    conv_id = str(data["conversation_id"])[:64]
                if kind == "hello":
                    await ws.send_json({"type": "ready", "stt": stt.engine, "tts": tts.engine, "user": actor,
                                        "tool_mode": config.TOOL_MODE})
            elif kind == "approval":
                fut = approvals.get(str(data.get("id") or ""))
                if fut and not fut.done():
                    fut.set_result(bool(data.get("approved")))
            elif kind == "user_text":
                text = str(data.get("text") or "").strip()[:2000]
                if text:
                    start_turn(text)
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
                start_turn(text)
            elif kind == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(ws.send_json)
        if turn_task and not turn_task.done():
            turn_task.cancel()
        for fut in approvals.values():
            if not fut.done():
                fut.set_result(False)
        log.info("WebSocket disconnected (%s)", actor)


app.add_api_websocket_route("/ws/jarvis", jarvis_ws)
app.add_api_websocket_route("/ws/audio", jarvis_ws)

# Serve the dashboard (must be registered last so /api and /ws win).
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
