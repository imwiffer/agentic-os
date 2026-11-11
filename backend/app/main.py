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
import os
import secrets
import shutil
import subprocess
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, auth, config, db, extensions, fish_audio, hub, hw, memory, roles, runtime, scheduler, token_opt
from .tools import REGISTRY, ToolContext, ToolError, files as files_tool, load_builtin_tools, system as system_tool, web as web_tool
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
    await hw.auto_configure_compute_fabric()
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


class UserUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    title: str = Field(default="Operator Profile", max_length=120)
    email: str = Field(default="", max_length=160)


class FinanceUpdateRequest(BaseModel):
    cash_on_hand: float | None = None
    monthly_income: float | None = None
    monthly_expenses: float | None = None
    revenue: float | None = None
    revenue_target: float | None = None
    base_currency: str | None = None


class MetricUpsertRequest(BaseModel):
    tab: str = Field(default="Progress", max_length=40)
    key: str = Field(..., min_length=1, max_length=60)
    label: str = Field(..., min_length=1, max_length=120)
    value: float
    format: str = Field(default="number", max_length=20)
    icon: str = Field(default="fas fa-chart-simple", max_length=60)
    color: str = Field(default="purple", max_length=30)


class UsageUpsertRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=40)
    label: str = Field(..., min_length=1, max_length=120)
    percent: float
    image: str = Field(default="", max_length=400)


class ModelAddRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=160)
    name: str = Field(default="", max_length=160)
    provider: str = Field(default="ollama", max_length=40)
    location: str = Field(default="local", max_length=20)
    size: str = Field(default="", max_length=20)
    pull: bool = False


class ToolModeRequest(BaseModel):
    tool_mode: str | None = None
    failsafe: bool | None = None
    ollama_url: str | None = None


class ConsoleExecRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=4000)
    cwd: str = Field(default="", max_length=500)


class WebSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=400)


class WebFetchRequest(BaseModel):
    url: str = Field(..., min_length=4, max_length=1000)


class FileWriteRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=300)
    content: str = Field(default="")
    append: bool = False


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
    db.add_message(req.conversation_id, "assistant", result.response)
    TOKENS["total"] += result.tokens
    return {**result.to_dict(), "tokens_total": TOKENS["total"]}


@api.get("/chat/history")
async def get_chat_history(conversation_id: str = "default", limit: int = 60):
    return {"messages": db.recent_messages(conversation_id, min(max(limit, 1), 200))}


@api.delete("/chat/history")
async def clear_chat_history(conversation_id: str = "default"):
    db.clear_messages(conversation_id)
    return {"ok": True}


@api.post("/user")
async def save_user(req: UserUpdateRequest):
    return db.update_user(req.name, req.title, req.email)


@api.post("/finance")
async def save_finance(req: FinanceUpdateRequest):
    db.update_finance(
        cash_on_hand=req.cash_on_hand,
        monthly_income=req.monthly_income,
        monthly_expenses=req.monthly_expenses,
        revenue=req.revenue,
        revenue_target=req.revenue_target,
        base_currency=req.base_currency,
    )
    stats = db.dashboard_stats()
    stats["rates"] = config.CURRENCY_RATES
    stats["tokens_processed"] = TOKENS["total"]
    return stats


@api.post("/metrics")
async def save_metric(req: MetricUpsertRequest):
    db.upsert_metric(req.tab, req.key, req.label, req.value, req.format, req.icon, req.color)
    stats = db.dashboard_stats()
    stats["rates"] = config.CURRENCY_RATES
    stats["tokens_processed"] = TOKENS["total"]
    return stats


@api.delete("/metrics/{key}")
async def remove_metric(key: str):
    db.delete_metric(key)
    return {"ok": True}


@api.post("/usage")
async def save_usage(req: UsageUpsertRequest):
    db.upsert_usage(req.id, req.label, req.percent, req.image)
    stats = db.dashboard_stats()
    stats["rates"] = config.CURRENCY_RATES
    stats["tokens_processed"] = TOKENS["total"]
    return stats


@api.post("/dashboard/sync-live")
async def sync_live_telemetry():
    """Sync live SQLite telemetry (tasks run, memories, VPS Ollama load) into dashboard cards."""
    counts = db.live_telemetry_counts()
    sys_snap = await system_tool.snapshot()
    db.upsert_metric("Progress", "tasks_done", "Agent tool executions", float(counts["audit_total"]), "number", "fas fa-list-check", "green")
    db.upsert_metric("Progress", "hours_saved", "Conversation turns", float(counts["messages"]), "number", "fas fa-comments", "blue")
    db.upsert_metric("Progress", "memories_stored", "Memories indexed", float(counts["memories"]), "number", "fas fa-brain", "amber")
    cpu_pct = float(sys_snap.get("cpu_percent") or 12.0)
    mem_pct = float(sys_snap.get("mem_percent") or 34.0)
    db.upsert_usage("claude", "VPS / Host CPU Load", round(cpu_pct, 1))
    db.upsert_usage("codex", "System RAM Used", round(mem_pct, 1))
    stats = db.dashboard_stats()
    stats["rates"] = config.CURRENCY_RATES
    stats["tokens_processed"] = TOKENS["total"]
    return stats


@api.post("/models")
async def register_model(req: ModelAddRequest):
    m = db.add_model(req.id, req.name or req.id, req.provider, req.location, req.size)
    pulled = False
    pull_status = ""
    if req.pull and req.location == "local" and req.provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                r = await client.post(f"{config.OLLAMA_URL}/api/pull", json={"name": req.id, "stream": False})
                r.raise_for_status()
                pulled = True
                pull_status = r.json().get("status", "pulled")
        except Exception as exc:
            pull_status = f"registered (pull warning: {exc})"
    return {"ok": True, "model": m, "pulled": pulled, "pull_status": pull_status, **(await agent.list_models())}


@api.delete("/models/{model_id:path}")
async def remove_model(model_id: str):
    db.delete_model(model_id)
    return await agent.list_models()


@api.post("/config/tool-mode")
async def update_runtime_config(req: ToolModeRequest):
    if req.tool_mode and req.tool_mode.lower() in ("auto", "confirm", "readonly"):
        config.TOOL_MODE = req.tool_mode.lower()
    if req.failsafe is not None:
        config.FAILSAFE_ENABLED = bool(req.failsafe)
    if req.ollama_url and req.ollama_url.strip():
        url = req.ollama_url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        config.OLLAMA_URL = url
    await hub.broadcast({"type": "ready", "stt": stt.engine, "tts": tts.engine, "user": "owner", "tool_mode": config.TOOL_MODE})
    return {
        "ok": True,
        "tool_mode": config.TOOL_MODE,
        "failsafe": config.FAILSAFE_ENABLED,
        "ollama_url": config.OLLAMA_URL,
    }


@api.get("/connectors")
async def check_connectors():
    """Live status & latency for every connector shown on the dashboard."""
    t0 = time.perf_counter()
    ollama_ok, ollama_ms, ollama_models = False, 0, []
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            r = await client.get(f"{config.OLLAMA_URL}/api/tags")
            r.raise_for_status()
            ollama_ok = True
            ollama_ms = int((time.perf_counter() - t0) * 1000)
            ollama_models = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        pass

    t1 = time.perf_counter()
    airllm_ok, airllm_ms = False, 0
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"{config.VANTA_AIRLLM_URL}/api/ping")
            if r.status_code == 200:
                airllm_ok = True
                airllm_ms = int((time.perf_counter() - t1) * 1000)
    except Exception:
        pass

    ts_ok = "100." in config.OLLAMA_URL or shutil.which("tailscale") is not None
    db_size = config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0
    local_specs = hw.LAST_FABRIC_REPORT.get("local_specs") or hw.detect_local_specs()
    gpu_name = local_specs["gpus"][0]["name"] if local_specs.get("gpus") else "CPU Only"
    remote_gpu_ok = bool(hw.LAST_FABRIC_REPORT.get("remote_gpu", {}).get("online"))
    remote_gpu_detail = hw.LAST_FABRIC_REPORT.get("remote_gpu", {}).get("recommended_model", "standby") if config.REMOTE_GPU_URL else "ready to pair"
    return {
        "connectors": [
            {
                "id": "network",
                "name": "Network Link",
                "icon": "fas fa-network-wired",
                "color": "emerald",
                "online": ollama_ok or ts_ok,
                "detail": config.OLLAMA_URL.replace("http://", "").replace("https://", ""),
            },
            {
                "id": "ollama",
                "name": "Ubuntu VPS (CPU Node)",
                "icon": "fas fa-microchip",
                "color": "sky",
                "online": ollama_ok,
                "detail": f"{ollama_ms}ms · {', '.join(ollama_models[:2]) or config.DEFAULT_MODEL}" if ollama_ok else "offline",
            },
            {
                "id": "main_pc_gpu",
                "name": f"Main PC GPU ({gpu_name})",
                "icon": "fas fa-bolt",
                "color": "emerald",
                "online": bool(local_specs.get("has_gpu")),
                "detail": f"{local_specs.get('max_vram_gb', 0)}GB VRAM · {'AirLLM :7777 live' if airllm_ok else 'auto-bridge ready'}",
            },
            {
                "id": "remote_gpu",
                "name": "Remote GPU (Cloud/Cluster)",
                "icon": "fas fa-server",
                "color": "purple",
                "online": remote_gpu_ok,
                "detail": f"{config.REMOTE_GPU_URL} ({remote_gpu_detail})" if config.REMOTE_GPU_URL else "pairable (RunPod/Vast/vLLM)",
            },
            {
                "id": "fish_audio",
                "name": "Fish Audio Neural Voice",
                "icon": "fas fa-wave-square",
                "color": "pink",
                "online": bool(config.FISH_AUDIO_API_KEY),
                "detail": f"{config.FISH_AUDIO_MODEL} · Jarvis Persona ({config.FISH_AUDIO_VOICE_ID[:8]}…)" if config.FISH_AUDIO_API_KEY else "not configured",
            },
            {
                "id": "failsafe",
                "name": "Local AirLLM Failsafe",
                "icon": "fas fa-life-ring",
                "color": "amber",
                "online": config.FAILSAFE_ENABLED,
                "detail": f"armed ({'Vanta :7777 live' if airllm_ok else config.FALLBACK_MODEL.split('/')[-1]})" if config.FAILSAFE_ENABLED else "disabled",
            },
            {
                "id": "openrouter",
                "name": "OpenRouter Cloud",
                "icon": "fas fa-cloud",
                "color": "purple",
                "online": bool(config.OPENAI_API_KEY),
                "detail": "key configured" if config.OPENAI_API_KEY else "no API key",
            },
            {
                "id": "web",
                "name": "Web Search & Scraper",
                "icon": "fas fa-globe",
                "color": "pink",
                "online": True,
                "detail": "Brave API" if config.BRAVE_API_KEY else "DuckDuckGo Live",
            },
            {
                "id": "sqlite",
                "name": "SQLite WAL",
                "icon": "fas fa-database",
                "color": "amber",
                "online": True,
                "detail": f"{db_size // 1024} KB · {db.live_telemetry_counts()['audit_total']} audits",
            },
        ],
        "tool_mode": config.TOOL_MODE,
        "failsafe": config.FAILSAFE_ENABLED,
        "ollama_url": config.OLLAMA_URL,
        "hardware": hw.LAST_FABRIC_REPORT or {"local_specs": local_specs, "selected_default_model": config.DEFAULT_MODEL},
    }


@api.get("/hardware")
async def get_hardware_fabric():
    if not hw.LAST_FABRIC_REPORT:
        return await hw.auto_configure_compute_fabric()
    return hw.LAST_FABRIC_REPORT


@api.post("/hardware/detect")
async def trigger_hardware_detect():
    return await hw.auto_configure_compute_fabric()


@api.get("/jarvis/welcome")
async def jarvis_welcome():
    """Return a live telemetry-backed welcome greeting from Jarvis on dashboard launch."""
    conn_data = await check_connectors()
    sys_snap = await system_tool.snapshot()
    user = db.get_user()
    owner_name = user.get("name") or "dj"
    if owner_name.lower() in ("jack roberts", "owner", "user"):
        owner_name = "dj"

    vps_conn = next((c for c in conn_data["connectors"] if c["id"] == "ollama"), {})
    gpu_conn = next((c for c in conn_data["connectors"] if c["id"] == "main_pc_gpu"), {})
    vps_online = bool(vps_conn.get("online"))
    vps_detail = vps_conn.get("detail", "offline")
    gpu_name = gpu_conn.get("name", "Main PC GPU").replace("Main PC GPU (", "").rstrip(")")
    unread = sum(1 for x in db.list_inbox() if not x["read"])

    vps_host_display = config.OLLAMA_URL.replace("http://", "").replace("https://", "")
    if vps_online:
        greeting = f"Welcome back, {owner_name}. Systems are green and ready."
    else:
        greeting = f"Welcome back, {owner_name}. Local GPU fabric is ready."

    return {
        "ok": True,
        "owner": owner_name,
        "greeting": greeting,
        "model": config.DEFAULT_MODEL,
        "vps_online": vps_online,
        "vps_detail": vps_detail,
        "gpu_name": gpu_name,
        "system": sys_snap,
        "connectors": conn_data["connectors"],
        "hardware": conn_data["hardware"],
        "recent_audit": db.list_audit(6),
    }


@api.post("/console/exec")
async def console_exec(req: ConsoleExecRequest, request: Request):
    """Execute a real command from the interactive Console view."""
    cmd = req.command.strip()
    cwd = req.cwd.strip() or str(config.WORKSPACE_DIR)
    if not os.path.isdir(cwd):
        cwd = str(config.WORKSPACE_DIR)
    shell_bin = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    t0 = time.perf_counter()
    try:
        def _run_ps():
            if os.name == "nt":
                return subprocess.run(
                    [shell_bin, "-NoLogo", "-NoProfile", "-Command", f"Set-Location -LiteralPath '{cwd}'; {cmd}; Write-Output \"`n__VANTA_CWD__=$((Get-Location).Path)\""],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=config.SHELL_TIMEOUT,
                )
            return subprocess.run(
                ["/bin/bash", "-c", f"cd '{cwd}' && {cmd}; echo \"\n__VANTA_CWD__=$(pwd)\""],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=config.SHELL_TIMEOUT,
            )

        proc = await asyncio.to_thread(_run_ps)
        stdout = proc.stdout or ""
        new_cwd = cwd
        if "__VANTA_CWD__=" in stdout:
            parts = stdout.rsplit("__VANTA_CWD__=", 1)
            stdout = parts[0].rstrip()
            parsed_cwd = parts[1].strip().splitlines()[0].strip()
            if os.path.isdir(parsed_cwd):
                new_cwd = parsed_cwd
        stderr = (proc.stderr or "").strip()
        output = stdout + (f"\n[stderr]\n{stderr}" if stderr else "")
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        db.add_audit(request.state.user, "console", "ops", "console_exec", json.dumps({"command": cmd[:200]}),
                     "safe", "auto", proc.returncode == 0, (output or "(no output)")[:600])
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": output.strip() or "(no output)",
            "cwd": new_cwd,
            "elapsed_ms": elapsed_ms,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": -1, "output": f"Timed out after {config.SHELL_TIMEOUT}s", "cwd": cwd, "elapsed_ms": int((time.perf_counter() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "returncode": -1, "output": f"Error: {exc}", "cwd": cwd, "elapsed_ms": 0}


@api.get("/files")
async def list_workspace_files(path: str = "."):
    try:
        p = files_tool.safe_path(path)
        if not p.exists():
            return {"files": [], "root": str(config.WORKSPACE_DIR)}
        items = []
        for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:200]:
            items.append({
                "name": e.name,
                "path": str(e.relative_to(config.WORKSPACE_DIR)).replace("\\", "/"),
                "is_dir": e.is_dir(),
                "size": 0 if e.is_dir() else e.stat().st_size,
            })
        return {"files": items, "root": str(config.WORKSPACE_DIR)}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/files/read")
async def read_workspace_file(path: str):
    ctx = ToolContext(actor="owner", origin="rest", role="jarvis")
    try:
        content = await files_tool.read_file({"path": path}, ctx)
        return {"path": path, "content": content}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/files/write")
async def write_workspace_file(req: FileWriteRequest):
    ctx = ToolContext(actor="owner", origin="rest", role="jarvis")
    try:
        msg = await files_tool.write_file({"path": req.path, "content": req.content, "append": req.append}, ctx)
        return {"ok": True, "message": msg, "path": req.path}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/web/search")
async def run_web_search(req: WebSearchRequest, request: Request):
    ctx = ToolContext(actor=request.state.user, origin="rest", role="researcher")
    try:
        out = await web_tool.web_search({"query": req.query}, ctx)
        db.add_audit(request.state.user, "rest", "researcher", "web_search", json.dumps({"query": req.query}), "safe", "auto", True, out[:600])
        return {"ok": True, "query": req.query, "result": out}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/web/fetch")
async def run_web_fetch(req: WebFetchRequest, request: Request):
    ctx = ToolContext(actor=request.state.user, origin="rest", role="researcher")
    try:
        out = await web_tool.web_fetch({"url": req.url}, ctx)
        db.add_audit(request.state.user, "rest", "researcher", "web_fetch", json.dumps({"url": req.url}), "safe", "auto", True, out[:600])
        return {"ok": True, "url": req.url, "content": out}
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/tokens/stats")
async def get_token_stats():
    """Return live token optimization telemetry."""
    return {"ok": True, "stats": token_opt.STATS}


@api.get("/extensions/search")
async def search_extensions(q: str = "theme"):
    """Search OpenVSX for VS Code extensions, themes, grammars, and snippets."""
    items = await extensions.search_openvsx(q, limit=12)
    return {"ok": True, "query": q, "extensions": items}


@api.post("/extensions/install")
async def install_extension(req: dict):
    """Download and install a VSIX extension package from OpenVSX."""
    namespace = req.get("namespace", "")
    name = req.get("name", "")
    version = req.get("version", "latest")
    if not namespace or not name:
        raise HTTPException(status_code=400, detail="namespace and name required")
    res = await extensions.install_vsix(namespace, name, version)
    return res


@api.get("/extensions/list")
async def list_extensions():
    """List installed VS Code extensions and plugins."""
    return {"ok": True, "extensions": extensions.list_installed_extensions()}


@api.post("/remote_gpu/configure")
async def configure_remote_gpu(req: dict):
    """Set or update remote GPU endpoint (RunPod/Vast/vLLM/Ollama)."""
    url = str(req.get("url") or "").rstrip("/")
    if url:
        config.REMOTE_GPU_URL = url
        probe = await hw.probe_ollama_node(url)
        return {"ok": True, "remote_gpu_url": url, "probe": probe}
    return {"ok": False, "detail": "Empty URL provided"}


CURATED_PERSONAS = [
    {"id": "14129c3e320149449d6bada6862f7338", "name": "J.A.R.V.I.S. (Classic British)", "lang": "en", "tag": "ironman", "author": "Community"},
    {"id": "88b183bfbdda44a09ad4474712b967f4", "name": "J.A.R.V.I.S. (Cinematic High-Def)", "lang": "en", "tag": "ironman", "author": "Community"},
    {"id": "05b36da8574341d0803391491850db20", "name": "J.A.R.V.I.S. (Crisp Tactical)", "lang": "en", "tag": "tactical", "author": "Community"},
    {"id": "e13fa398a7f445a685316a3de6089ce7", "name": "J.A.R.V.I.S. (Deep Assistant)", "lang": "en", "tag": "deep", "author": "Community"},
    {"id": "90e65eaaf50e4470b8e6d43ee6afd7d5", "name": "Announcer (Epic / Powerful)", "lang": "en", "tag": "announcer", "author": "Community"},
    {"id": "", "name": "Fish Audio Default (s2.1-pro-free)", "lang": "multi", "tag": "default", "author": "Fish Audio"},
]


@api.get("/voice/personas")
async def get_voice_personas():
    """Return available voice personas and current configuration."""
    return {
        "ok": True,
        "engine": config.TTS_ENGINE,
        "active_voice_id": config.FISH_AUDIO_VOICE_ID,
        "active_model": config.FISH_AUDIO_MODEL,
        "personas": CURATED_PERSONAS,
    }


@api.post("/voice/persona")
async def set_voice_persona(req: dict):
    """Switch the active voice persona."""
    vid = str(req.get("voice_id", "")).strip()
    config.FISH_AUDIO_VOICE_ID = vid
    return {"ok": True, "active_voice_id": vid, "message": f"Active voice persona set to {vid or 'default'}."}


@api.get("/fish_audio/agents")
async def list_fish_agents():
    """List conversational agents configured on Fish Audio."""
    try:
        agents = await fish_audio.list_agents()
        return {"ok": True, "agents": agents}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@api.post("/fish_audio/speak")
async def fish_audio_speak(req: dict):
    """Generate and return MP3 speech audio for given text."""
    text = str(req.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text required")
    vid = req.get("voice_id") or config.FISH_AUDIO_VOICE_ID

    chunks = []
    try:
        async for chunk in fish_audio.stream_tts(text, reference_id=vid):
            chunks.append(chunk)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    audio_bytes = b"".join(chunks)
    return Response(content=audio_bytes, media_type="audio/mpeg")


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
