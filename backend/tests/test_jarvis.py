"""Tests for the Agentic OS backend: rules, ReAct loop, tool security, auth, memory, scheduler, and audit log."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import agent, auth, config, db, memory, runtime, scheduler
from app.tools import BLOCKED, CONFIRM, SAFE, ToolContext, ToolError, files, load_builtin_tools, shell, web


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test_jarvis.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "WORKSPACE_DIR", (tmp_path / "workspace").resolve())
    config.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    monkeypatch.setattr(config, "TRUSTED_TAILSCALE_USERS", set())
    monkeypatch.setattr(config, "TOOL_MODE", "confirm")
    load_builtin_tools()
    db.init_db()
    yield


# --- Phase 0: baseline agent rules & JSON parsing ---------------------------

def test_extract_json_handles_fences_and_shorthand():
    raw = '```json\n{"action": "openTab", "target": "Finances", "response": "Opening."}\n```'
    parsed = agent._extract_json(raw)
    assert parsed == {"actions": [{"tool": "openTab", "args": {"target": "Finances"}}], "response": "Opening."}


def test_rule_based_navigation_and_finance():
    models = db.list_models()
    plan = agent.rule_based_plan("open finances and check my balance in euros", models)
    tools_called = [a["tool"] for a in plan["actions"]]
    assert "set_currency" in tools_called
    assert "open_tab" in tools_called
    assert "check_balance" in tools_called


# --- Phase 1: ReAct loop, workspace sandbox, shell classifier, SSRF ---------

def test_multi_step_react_loop(monkeypatch: pytest.MonkeyPatch):
    calls = []

    async def fake_chat(model_id, messages, json_mode=True):
        calls.append(messages[-1]["content"])
        if len(calls) == 1:
            return '{"thought":"write a note","actions":[{"tool":"write_file","args":{"path":"note.txt","content":"hello vps"}}],"response":""}', 25, "qwen2.5:7b-instruct"
        return '{"thought":"read it back","actions":[],"response":"Saved note.txt with hello vps, sir."}', 20, "qwen2.5:7b-instruct"

    monkeypatch.setattr("app.llm.chat", fake_chat)
    res = asyncio.run(runtime.run("save hello vps to note.txt"))
    assert res.engine == "llm"
    assert "Saved note.txt" in res.response
    assert len(calls) == 2
    assert "Observation:" in calls[1]
    assert (config.WORKSPACE_DIR / "note.txt").read_text(encoding="utf-8") == "hello vps"


def test_workspace_blocks_path_traversal():
    with pytest.raises(ToolError):
        files.safe_path("../../etc/passwd")
    with pytest.raises(ToolError):
        files.safe_path("/etc/shadow")
    inside = files.safe_path("subdir/report.md")
    assert config.WORKSPACE_DIR in inside.parents


def test_shell_classifier_tiers():
    assert shell.classify("df -h") == SAFE
    assert shell.classify("systemctl is-active jarvis") == SAFE
    assert shell.classify("tailscale status") == SAFE
    assert shell.classify("ls -la") == SAFE
    assert shell.classify("cat /etc/passwd") == CONFIRM
    assert shell.classify("df -h | head -n 5") == CONFIRM
    assert shell.classify("apt update") == CONFIRM
    assert shell.classify("sudo rm -rf /") == BLOCKED
    assert shell.classify("ufw disable") == BLOCKED
    assert shell.classify("tailscale down") == BLOCKED
    assert shell.classify("cat /etc/jarvis/jarvis.env") == BLOCKED


def test_ssrf_guard_blocks_loopback_and_tailscale():
    for bad in (
        "http://127.0.0.1:11434/api/tags",
        "http://localhost:8000/api/config",
        "http://100.64.0.1/",
        "https://jarvis-vps.tailnet.ts.net/",
        "file:///etc/passwd",
    ):
        with pytest.raises(ToolError):
            asyncio.run(web.assert_public_url(bad))


# --- Phase 2: memory & scheduler -------------------------------------------

def test_memory_save_search_forget(monkeypatch: pytest.MonkeyPatch):
    async def no_embed(_text: str):
        return None

    monkeypatch.setattr("app.llm.embed", no_embed)
    saved = asyncio.run(memory.save("The VPS runs on ZAP-Hosting in Frankfurt.", tags="vps,infra", source="owner"))
    hits = asyncio.run(memory.search("Which hosting provider is the VPS on?"))
    assert hits and hits[0]["id"] == saved["id"]
    assert memory.forget(saved["id"]) is True
    assert memory.listing() == []


def test_scheduler_creates_and_runs_due_job(monkeypatch: pytest.MonkeyPatch):
    job = scheduler.create_job("Health check", "what is the system status?", role="ops", every_minutes=10)
    # Rewind next_run into the past so tick() picks it up immediately.
    past = scheduler.iso(scheduler.utcnow() - timedelta(minutes=1))
    db.update_job(job["id"], next_run=past)
    ran = asyncio.run(scheduler.scheduler.tick())
    assert ran == 1
    inbox = db.list_inbox()
    assert inbox and "Health check" in inbox[0]["title"]


# --- Phase 3: approval gating, append-only audit log, auth -----------------

def test_confirm_tool_denied_without_approver_and_logged():
    ctx = ToolContext(actor="scheduler", origin="job:1", role="ops")
    ok, obs, status = asyncio.run(runtime.execute_tool("run_shell", {"command": "apt update"}, ctx))
    assert not ok and status == "denied"
    audit = db.list_audit()
    assert audit[0]["tool"] == "run_shell"
    assert audit[0]["decision"] == "denied"

    # Verify the SQLite trigger prevents tampering with audit_log rows.
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as c:
            c.execute("UPDATE audit_log SET decision = 'auto'")


def test_confirm_tool_runs_when_approved():
    async def approve(_tool: str, _args: dict, _reason: str) -> str:
        return "approved"

    ctx = ToolContext(actor="owner", origin="ws", role="jarvis", approver=approve)
    (config.WORKSPACE_DIR / "temp.txt").write_text("x", encoding="utf-8")
    ok, _obs, status = asyncio.run(runtime.execute_tool("delete_file", {"path": "temp.txt"}, ctx))
    assert ok and status == "done"
    assert not (config.WORKSPACE_DIR / "temp.txt").exists()


def test_auth_cookie_and_tailscale_header_spoofing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "AUTH_TOKEN", "s" * 32)
    monkeypatch.setattr(config, "TRUSTED_TAILSCALE_USERS", {"alice@github"})

    cookie = auth.make_cookie("owner")
    assert auth.read_cookie(cookie) == "owner"
    assert auth.read_cookie(cookie + "tampered") is None

    # Spoofed Tailscale header from a non-loopback peer must be rejected.
    spoofed = SimpleNamespace(
        headers={"tailscale-user-login": "alice@github"},
        cookies={},
        client=SimpleNamespace(host="203.0.113.9"),
    )
    assert auth.identify(spoofed) is None

    # Same header from 127.0.0.1 (`tailscale serve` local proxy) is accepted.
    local_proxy = SimpleNamespace(
        headers={"tailscale-user-login": "alice@github"},
        cookies={},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    assert auth.identify(local_proxy) == "alice@github"


def test_fastapi_endpoints_and_websocket():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"] is True
        assert client.get("/api/auth/status").json()["authenticated"] is True
        roles_res = client.get("/api/roles").json()["roles"]
        assert any(r["id"] == "ops" for r in roles_res)

        with client.websocket_connect("/ws/jarvis") as ws:
            ws.send_json({"type": "hello", "state": {"currentTab": "Dashboard"}})
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            ws.send_json({"type": "user_text", "text": "open finances"})
            seen_types = set()
            for _ in range(6):
                msg = ws.receive_json()
                seen_types.add(msg["type"])
                if msg["type"] == "reply":
                    break
            assert "action" in seen_types
            assert "reply" in seen_types
