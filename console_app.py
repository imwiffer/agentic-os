# language: Python 3.11+, file: console_app.py, target: Windows 11 / SSH Console
"""Agentic OS — Real Interactive Console Application (TUI / CLI + ReAct Agent + Live Telemetry).

Connects directly to the SQLite database, ReAct tool registry, and Remote Ubuntu Server Ollama
(http://147.189.168.95:11434) with automatic failsafe to Local AirLLM.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import agent, config, db, hw, memory, roles, runtime  # noqa: E402
from app.tools import ToolContext, load_builtin_tools, system as system_tool, web as web_tool  # noqa: E402

# Enable ANSI escape sequences on Windows cmd.exe / PowerShell
if os.name == "nt":
    os.system("")

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_PURPLE = "\033[38;5;141m"
C_GREEN = "\033[38;5;84m"
C_CYAN = "\033[38;5;117m"
C_YELLOW = "\033[38;5;221m"
C_RED = "\033[38;5;203m"
C_GRAY = "\033[38;5;245m"


def banner() -> str:
    return (
        f"\n{C_PURPLE}{C_BOLD}╔══════════════════════════════════════════════════════════════════════════════╗{C_RESET}\n"
        f"{C_PURPLE}{C_BOLD}║  AGENTIC OS · CONSOLE APPLICATION  │  VPS OLLAMA + LOCAL AIRLLM FAILSAFE     ║{C_RESET}\n"
        f"{C_PURPLE}{C_BOLD}╚══════════════════════════════════════════════════════════════════════════════╝{C_RESET}"
    )


async def probe_connectors() -> list[dict]:
    import httpx
    connectors = []
    for cid, name, url, probe in [
        ("vps_ollama", "VPS Ollama Primary", config.OLLAMA_URL, f"{config.OLLAMA_URL.rstrip('/')}/api/tags"),
        ("local_ollama", "Local Ollama Failsafe", config.FAILSAFE_OLLAMA_URL, f"{config.FAILSAFE_OLLAMA_URL.rstrip('/')}/api/tags"),
        ("local_airllm", "Local Vanta AirLLM", config.VANTA_AIRLLM_URL, f"{config.VANTA_AIRLLM_URL.rstrip('/')}/api/ping"),
    ]:
        ok = False
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(probe)
                ok = r.status_code == 200
        except Exception:
            ok = False
        connectors.append({"id": cid, "name": name, "url": url, "online": ok})
    return connectors


async def render_dashboard(state: dict) -> None:
    stats = db.dashboard_stats()
    sys_info = await system_tool.snapshot()
    models_info = await agent.list_models()
    conns = await probe_connectors()
    inbox_items = db.list_inbox()
    unread = sum(1 for x in inbox_items if not x["read"])
    jobs = db.list_jobs(include_disabled=True)

    print(banner())
    user = stats.get("user", {})
    owner_name = user.get("name", "dj")
    vps_online = any(c["id"] == "vps_ollama" and c["online"] for c in conns)
    vps_host = config.OLLAMA_URL.replace("http://", "").replace("https://", "")
    print(
        f"  {C_CYAN}{C_BOLD}J.A.R.V.I.S.:{C_RESET} \"Welcome back, {owner_name}. "
        f"Ubuntu Server {vps_host} is {'ONLINE' if vps_online else 'ON STANDBY'} ({state['model']}), "
        f"Main PC RTX 5070 12GB failsafe is {'ARMED' if config.FAILSAFE_ENABLED else 'OFF'}, "
        f"and {unread} inbox reports await.\""
    )
    print(
        f"  {C_BOLD}Operator:{C_RESET} {owner_name} ({user.get('title', 'Operator')})   "
        f"{C_BOLD}Role:{C_RESET} {C_CYAN}{state['role']}{C_RESET}   "
        f"{C_BOLD}Model:{C_RESET} {C_GREEN}{state['model']}{C_RESET}   "
        f"{C_BOLD}Policy:{C_RESET} {C_YELLOW}{config.TOOL_MODE.upper()}{C_RESET}   "
        f"{C_BOLD}Failsafe:{C_RESET} {C_GREEN if config.FAILSAFE_ENABLED else C_RED}{'ARMED' if config.FAILSAFE_ENABLED else 'OFF'}{C_RESET}"
    )
    print(f"{C_GRAY}────────────────────────────────────────────────────────────────────────────────{C_RESET}")

    conn_strs = []
    for c in conns:
        dot = f"{C_GREEN}● ONLINE{C_RESET}" if c["online"] else f"{C_RED}○ STANDBY{C_RESET}"
        conn_strs.append(f"{c['name']} [{c['url']}]: {dot}")
    print(f"  {C_BOLD}CONNECTORS:{C_RESET} " + "  │  ".join(conn_strs))

    loaded = ", ".join(sys_info.get("ollama_loaded") or []) or "idle"
    counts = db.live_telemetry_counts()
    print(
        f"  {C_BOLD}HOST TELEMETRY:{C_RESET} {sys_info.get('hostname')} ({sys_info.get('os')}) · "
        f"CPU {C_CYAN}{sys_info.get('cpu_percent', 0)}%{C_RESET} · "
        f"RAM {C_CYAN}{sys_info.get('mem_percent', 0)}%{C_RESET} · "
        f"Disk {C_CYAN}{sys_info.get('disk_percent', 0)}%{C_RESET} · "
        f"Audits {C_PURPLE}{counts['audit_total']}{C_RESET} · "
        f"VPS Loaded: {C_GREEN}{loaded}{C_RESET}"
    )
    print(f"{C_GRAY}────────────────────────────────────────────────────────────────────────────────{C_RESET}")

    overview = stats.get("tabs", {}).get("Overview", [])
    finances = stats.get("tabs", {}).get("Finances", [])
    progress = stats.get("tabs", {}).get("Progress", [])
    print(f"  {C_BOLD}OVERVIEW & FINANCES (SQLite Live):{C_RESET}")
    for c in overview + finances[:2]:
        val = c.get("value", 0)
        fmt = f"${val:,.2f}" if c.get("format") == "currency" else (f"{val:.1f}%" if c.get("format") == "percent" else f"{val:,.0f}")
        print(f"    • {c.get('label', c.get('id')):<24} {C_GREEN}{fmt}{C_RESET}")

    if progress:
        prog_line = "   ".join(f"{c['label']}: {C_CYAN}{c['value']:.0f}{C_RESET}" for c in progress)
        print(f"  {C_BOLD}PROGRESS:{C_RESET}     {prog_line}")

    usage = stats.get("usage", [])
    if usage:
        u_line = "   ".join(f"{u['label']}: {C_PURPLE}{u['percent']}%{C_RESET}" for u in usage)
        print(f"  {C_BOLD}USAGE GAUGES:{C_RESET} {u_line}")

    print(f"{C_GRAY}────────────────────────────────────────────────────────────────────────────────{C_RESET}")
    avail_models = [m for m in models_info.get("models", []) if m.get("available")]
    print(
        f"  {C_BOLD}MODELS READY ({len(avail_models)}/{len(models_info.get('models', []))}):{C_RESET} "
        + ", ".join(f"{C_GREEN}{m['id']}{C_RESET}" for m in avail_models[:6])
    )
    print(
        f"  {C_BOLD}JOBS:{C_RESET} {len(jobs)} scheduled   "
        f"{C_BOLD}INBOX:{C_RESET} {unread} unread (of {len(inbox_items)} recent)"
    )
    print(f"{C_GRAY}════════════════════════════════════════════════════════════════════════════════{C_RESET}")
    print(
        f"  {C_DIM}Commands: /dash  /sync  /models  /pull <tag>  /model <id>  /role <id>  /mode <auto|confirm|readonly>{C_RESET}\n"
        f"  {C_DIM}          /failsafe <on|off>  /mute  /finance  /ps <cmd>  /files  /web <q>  /fetch <url>  /memory  /jobs  /inbox  /audit  /gui  /quit{C_RESET}\n"
        f"  {C_DIM}Or type any prompt to run the ReAct Agent with live tool calls.{C_RESET}\n"
    )


async def sync_live_telemetry() -> None:
    snap = await system_tool.snapshot()
    counts = db.live_telemetry_counts()
    cpu = float(snap.get("cpu_percent") or 12.0)
    mem = float(snap.get("mem_percent") or 34.0)
    db.upsert_metric("Progress", "tasks_done", "Agent tool executions", float(counts["audit_total"]), "number", "fas fa-list-check", "green")
    db.upsert_metric("Progress", "hours_saved", "Conversation turns", float(counts["messages"]), "number", "fas fa-comments", "blue")
    db.upsert_metric("Progress", "memories_stored", "Memories indexed", float(counts["memories"]), "number", "fas fa-brain", "amber")
    db.upsert_usage("claude", "VPS / Host CPU Load", round(cpu, 1))
    db.upsert_usage("codex", "System RAM Used", round(mem, 1))
    print(f"{C_GREEN}✓ Synced live CPU ({cpu}%), RAM ({mem}%), and SQLite counters ({counts['audit_total']} audits, {counts['messages']} msgs).{C_RESET}")


async def handle_slash(line: str, state: dict) -> bool:
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit", "/q"):
        return False

    if cmd in ("/dash", "/status", "/help"):
        await render_dashboard(state)
        return True

    if cmd in ("/hw", "/hardware", "/specs"):
        rep = await hw.auto_configure_compute_fabric()
        state["model"] = rep["selected_default_model"]
        ls = rep["local_specs"]
        print(f"\n{C_BOLD}=== AUTOMATIC HARDWARE & COMPUTE FABRIC DETECTION ==={C_RESET}")
        print(f"  {C_BOLD}Local Host:{C_RESET}   {ls['hostname']} ({ls['os']}) · {ls['cpu_cores']}C/{ls['cpu_threads']}T · {ls['ram_gb']} GB RAM")
        if ls["gpus"]:
            for g in ls["gpus"]:
                print(f"  {C_BOLD}Local GPU:{C_RESET}    {C_GREEN}{g['name']} ({g['vram_gb']} GB VRAM){C_RESET}")
        else:
            print(f"  {C_BOLD}Local GPU:{C_RESET}    {C_YELLOW}None (CPU-only mode){C_RESET}")
        vps = rep["vps_ollama"]
        print(f"  {C_BOLD}VPS Ollama:{C_RESET}   {vps['url']} -> {'ONLINE' if vps['online'] else 'OFFLINE'} (mode={vps.get('mode', 'cpu_only')}, installed={vps.get('installed', [])})")
        print(f"  {C_BOLD}Auto Decision:{C_RESET} {C_CYAN}{rep['decision_reason']}{C_RESET}")
        print(f"  {C_BOLD}Selected Model:{C_RESET} {C_GREEN}{rep['selected_default_model']}{C_RESET}\n")
        return True

    if cmd == "/sync":
        await sync_live_telemetry()
        await render_dashboard(state)
        return True

    if cmd == "/models":
        info = await agent.list_models()
        print(f"\n{C_BOLD}Registered & Discovered Models (VPS Ollama = {info.get('ollama_url')}):{C_RESET}")
        for m in info.get("models", []):
            status = f"{C_GREEN}READY ({m.get('size') or 'online'}){C_RESET}" if m.get("available") else f"{C_YELLOW}{m.get('status', 'offline')}{C_RESET}"
            mark = f"{C_PURPLE}*{C_RESET}" if m["id"] == state["model"] else " "
            print(f" {mark} {m['id']:<52} [{m['location']:<5}] {status}")
        print()
        return True

    if cmd == "/model":
        if not arg:
            print(f"Current model: {C_GREEN}{state['model']}{C_RESET}. Usage: /model <model-id>")
        else:
            state["model"] = arg
            db.add_model(arg, arg, "ollama", "local")
            print(f"{C_GREEN}✓ Active model switched to {arg}{C_RESET}")
        return True

    if cmd == "/pull":
        if not arg:
            print("Usage: /pull <ollama-model-tag> (e.g. /pull qwen2.5-coder:7b)")
            return True
        import httpx
        print(f"{C_CYAN}Pulling {arg} on VPS Ollama ({config.OLLAMA_URL})…{C_RESET}")
        db.add_model(arg, arg, "ollama", "local")
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                async with client.stream("POST", f"{config.OLLAMA_URL.rstrip('/')}/api/pull", json={"name": arg, "stream": True}) as resp:
                    async for raw in resp.aiter_lines():
                        if not raw:
                            continue
                        data = json.loads(raw)
                        st = data.get("status", "")
                        completed = data.get("completed")
                        total = data.get("total")
                        if completed and total:
                            pct = int(completed * 100 / total)
                            print(f"\r  {st}: {pct}% ({completed // (1024*1024)}MB / {total // (1024*1024)}MB)    ", end="", flush=True)
                        else:
                            print(f"\r  {st:<60}", end="", flush=True)
            print(f"\n{C_GREEN}✓ Pulled {arg} on VPS Ollama.{C_RESET}")
            state["model"] = arg
        except Exception as e:
            print(f"\n{C_RED}✗ Pull failed: {e}{C_RESET}")
        return True

    if cmd == "/role":
        avail = [r["id"] for r in roles.public_roles()]
        if not arg or arg not in avail:
            print(f"Available roles: {', '.join(avail)}. Current: {C_CYAN}{state['role']}{C_RESET}")
        else:
            state["role"] = arg
            print(f"{C_GREEN}✓ Active specialist agent set to {arg}{C_RESET}")
        return True

    if cmd == "/mode":
        if arg.lower() not in ("auto", "confirm", "readonly"):
            print(f"Current mode: {C_YELLOW}{config.TOOL_MODE}{C_RESET}. Usage: /mode <auto|confirm|readonly>")
        else:
            config.TOOL_MODE = arg.lower()
            print(f"{C_GREEN}✓ Tool execution policy set to {config.TOOL_MODE.upper()}{C_RESET}")
        return True

    if cmd == "/failsafe":
        if arg.lower() in ("on", "true", "1"):
            config.FAILSAFE_ENABLED = True
        elif arg.lower() in ("off", "false", "0"):
            config.FAILSAFE_ENABLED = False
        else:
            config.FAILSAFE_ENABLED = not config.FAILSAFE_ENABLED
        print(f"{C_GREEN}✓ Local AirLLM failsafe {'ARMED' if config.FAILSAFE_ENABLED else 'DISABLED'}{C_RESET}")
        return True

    if cmd == "/mute":
        muted = not state.get("muted", False)
        state["muted"] = muted
        print(f"{C_YELLOW if muted else C_GREEN}✓ Assistant speech output: {'MUTED (silent)' if muted else 'UNMUTED (active)'}{C_RESET}")
        return True

    if cmd == "/finance":
        fin = db.get_finance()
        print(f"Current Finance: {json.dumps(fin)}")
        try:
            cash = input(f"  Cash on hand [{fin['cash_on_hand']}]: ").strip()
            inc = input(f"  Monthly income [{fin['monthly_income']}]: ").strip()
            exp = input(f"  Monthly expenses [{fin['monthly_expenses']}]: ").strip()
            rev = input(f"  Revenue [{fin['revenue']}]: ").strip()
            tgt = input(f"  Revenue target [{fin['revenue_target']}]: ").strip()
            db.update_finance(
                cash_on_hand=float(cash) if cash else None,
                monthly_income=float(inc) if inc else None,
                monthly_expenses=float(exp) if exp else None,
                revenue=float(rev) if rev else None,
                revenue_target=float(tgt) if tgt else None,
            )
            print(f"{C_GREEN}✓ Saved financial figures to SQLite.{C_RESET}")
        except Exception as e:
            print(f"{C_RED}Invalid number: {e}{C_RESET}")
        return True

    if cmd == "/ps":
        if not arg:
            print("Usage: /ps <PowerShell command>")
            return True
        res = await asyncio.to_thread(
            subprocess.run,
            ["powershell", "-NoProfile", "-Command", arg],
            cwd=str(state["cwd"]),
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = (res.stdout or "") + (res.stderr or "")
        print(f"{C_GREEN if res.returncode == 0 else C_RED}{out.strip() or '(no output)'}{C_RESET}")
        return True

    if cmd == "/files":
        target = (state["cwd"] / arg).resolve() if arg else state["cwd"]
        if not target.exists():
            print(f"{C_RED}Path not found: {target}{C_RESET}")
            return True
        print(f"\n{C_BOLD}Workspace [{target}]:{C_RESET}")
        for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:60]:
            kind = f"{C_CYAN}<DIR>{C_RESET}" if p.is_dir() else f"{p.stat().st_size:>8} B"
            print(f"  {kind:<14} {p.name}")
        print()
        return True

    if cmd == "/web":
        if not arg:
            print("Usage: /web <search query>")
            return True
        ctx = ToolContext(actor="console", origin="cli", role="researcher")
        out = await web_tool.web_search({"query": arg}, ctx)
        print(f"\n{C_CYAN}{out}{C_RESET}\n")
        return True

    if cmd == "/fetch":
        if not arg:
            print("Usage: /fetch <https://url>")
            return True
        ctx = ToolContext(actor="console", origin="cli", role="researcher")
        out = await web_tool.web_fetch({"url": arg}, ctx)
        print(f"\n{C_CYAN}{out}{C_RESET}\n")
        return True

    if cmd == "/memory":
        if arg.startswith("add "):
            text = arg[4:].strip()
            m = await memory.save(text, "console", source="console")
            print(f"{C_GREEN}✓ Stored memory #{m['id']}{C_RESET}")
        elif arg.startswith("del "):
            mid = int(arg[4:].strip())
            memory.forget(mid)
            print(f"{C_YELLOW}Deleted memory #{mid}{C_RESET}")
        else:
            mems = await memory.search(arg, k=20, min_score=0.1) if arg else memory.listing()
            print(f"\n{C_BOLD}Long-Term Memories ({len(mems)}):{C_RESET}")
            for m in mems:
                print(f"  #{m['id']:<3} [{m.get('tags', '')}] {m['text']} ({m.get('created_at', '')})")
            print("  (Use `/memory add <fact>` or `/memory del <id>`)")
        return True

    if cmd == "/jobs":
        for j in db.list_jobs(include_disabled=True):
            print(f"  #{j['id']} {j['name']} [{j['schedule_type']}={j['schedule_value']}] role={j['role']} last={j['last_status']}")
        return True

    if cmd == "/inbox":
        items = db.list_inbox()
        db.mark_inbox_read(None)
        if not items:
            print("  Inbox is empty.")
        for it in items[:15]:
            print(f"\n  {C_PURPLE}[{it['source']} · {it['created_at']}] {C_BOLD}{it['title']}{C_RESET}\n  {it['body']}")
        return True

    if cmd == "/audit":
        for a in db.list_audit(20):
            color = C_GREEN if a["ok"] else C_RED
            print(f"  {a['ts']} · {a['actor']}/{a['role']} · {color}{a['tool']} [{a['decision']}]{C_RESET} -> {a['result']}")
        return True

    if cmd in ("/gui", "/serve"):
        print(f"{C_CYAN}Launching Native Desktop Window + FastAPI Server…{C_RESET}")
        subprocess.Popen([sys.executable, str(ROOT_DIR / "desktop_app.py")], cwd=str(ROOT_DIR))
        return True

    print(f"{C_YELLOW}Unknown command: {cmd}. Type /dash for help.{C_RESET}")
    return True


async def run_agent_prompt(text: str, state: dict) -> None:
    async def on_emit(ev: dict) -> None:
        if ev.get("type") != "step":
            return
        st = ev.get("status", "")
        tool_name = ev.get("tool", "")
        if st == "running":
            print(f"  {C_PURPLE}⚙ {ev.get('role', 'jarvis')} → {tool_name}({json.dumps(ev.get('args', {}))}){C_RESET}")
        elif st == "done":
            print(f"  {C_GREEN}✓ {tool_name}: {str(ev.get('result', ''))[:160]}{C_RESET}")
        elif st in ("denied", "error"):
            print(f"  {C_RED}✗ {tool_name} {st}: {ev.get('result', '')}{C_RESET}")

    async def confirm_cb(tool_name: str, args: dict, reason: str) -> str:
        ans = input(f"  {C_YELLOW}⚠ Approve {tool_name}({json.dumps(args)}) [{reason}]? (y/N): {C_RESET}").strip().lower()
        return "approved" if ans in ("y", "yes") else "denied"

    history = [{"role": m["role"], "content": m["content"]} for m in db.recent_messages("console", config.HISTORY_TURNS * 2)]
    result = await runtime.run(
        text,
        state,
        history,
        role=state["role"],
        actor="console",
        origin="cli",
        emit=on_emit,
        approver=confirm_cb,
    )
    db.add_message("console", "user", text)
    db.add_message("console", "assistant", result.response)
    meta = f"{result.role} · {result.engine} ({result.model}) · {result.latency_ms}ms"
    print(f"\n{C_CYAN}{C_BOLD}[{meta}]{C_RESET}\n{result.response}\n")
    if result.note:
        print(f"{C_YELLOW}Note: {result.note}{C_RESET}\n")


async def main() -> None:
    load_builtin_tools()
    db.init_db()
    config.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    await hw.auto_configure_compute_fabric()
    state = {
        "currentTab": "Dashboard",
        "businessTab": "Overview",
        "currency": "USD",
        "model": config.DEFAULT_MODEL,
        "role": "jarvis",
        "cwd": config.WORKSPACE_DIR,
    }
    if len(sys.argv) > 1:
        arg_line = " ".join(sys.argv[1:])
        if arg_line.startswith("/"):
            await handle_slash(arg_line, state)
        else:
            await run_agent_prompt(arg_line, state)
        return

    await render_dashboard(state)
    while True:
        try:
            prompt_str = f"{C_PURPLE}AgenticOS{C_RESET}({C_CYAN}{state['role']}{C_RESET}@{C_GREEN}{state['model']}{C_RESET}) ❯ "
            line = await asyncio.to_thread(input, prompt_str)
        except (EOFError, KeyboardInterrupt):
            print("\nExiting Console.")
            break
        line = line.strip()
        if not line:
            continue
        if line.startswith("!"):
            await handle_slash("/ps " + line[1:], state)
        elif line.startswith("/"):
            keep_going = await handle_slash(line, state)
            if not keep_going:
                break
        else:
            await run_agent_prompt(line, state)


if __name__ == "__main__":
    asyncio.run(main())
