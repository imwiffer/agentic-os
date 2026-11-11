"""Agent runtime: a multi-step tool-using loop.

    user text ─► LLM (JSON) ─► tool calls ─► policy (safe/confirm/blocked) ─► execute ─► audit
                    ▲                                                            │
                    └──────────────────── observations ◄─────────────────────────┘

Simple UI commands finish in ONE LLM call (important for CPU latency); information tools trigger
follow-up steps until the model answers with an empty action list (or MAX_AGENT_STEPS is reached).
If no LLM is reachable a deterministic rule engine produces the plan instead.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Awaitable, Callable

from . import agent, config, db, llm, memory, scheduler, token_opt
from .roles import ROLES, tools_for
from .tools import BLOCKED, CONFIRM, REGISTRY, ToolContext, ToolError

log = logging.getLogger("jarvis.runtime")

TOOL_ALIASES = {
    "search": "web_search", "google": "web_search", "websearch": "web_search", "search_web": "web_search",
    "fetch": "web_fetch", "browse": "web_fetch", "open_url": "web_fetch", "fetch_url": "web_fetch",
    "shell": "run_shell", "bash": "run_shell", "run_command": "run_shell", "exec": "run_shell", "terminal": "run_shell",
    "status": "system_status", "system_health": "system_status", "health": "system_status",
    "save_memory": "remember", "memorize": "remember", "search_memory": "recall", "memory_search": "recall",
    "schedule": "schedule_task", "create_task": "schedule_task", "add_task": "schedule_task",
    "ls": "list_files", "cat": "read_file", "write": "write_file", "save_file": "write_file",
    "notify_user": "notify", "send_notification": "notify", "handoff": "delegate", "ask_agent": "delegate",
}

SYSTEM_PROMPT = """You are {title}, {description}
Owner: {user_name}. Time: {now}. State: {state}
{memories}
TOOLS:
{tools}

Return ONE JSON object only:
{{"thought": "<short>", "actions": [{{"tool": "<name>", "args": {{...}}}}], "response": "<1 short casual sentence for voice. Natural, direct, under 20 words. Never recite tables, code blocks, or raw data>"}}
If no tool is needed, set "actions": []."""

OBS_SUFFIX = "Continue. Call more tools if needed, otherwise answer with \"actions\": []."
FINAL_SUFFIX = "This is your final step. Answer now with \"actions\": [] and a complete \"response\"."


def resolve_tool_name(name) -> str | None:
    if not name:
        return None
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", str(name).strip()).lower().replace(" ", "_").replace("-", "_")
    snake = re.sub(r"_+", "_", snake)
    if snake in REGISTRY:
        return snake
    return agent.normalize_tool(snake) or TOOL_ALIASES.get(snake)


def _short(value, n: int = 300) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


async def _emit(ctx: ToolContext, event: dict) -> None:
    if ctx.emit:
        try:
            await ctx.emit(event)
        except Exception as exc:  # client went away; keep working
            log.debug("emit failed: %s", exc)


async def execute_tool(name: str, args: dict, ctx: ToolContext) -> tuple[bool, str, str]:
    """Policy + execution + audit. Returns (ok, observation_text, status)."""
    tool = REGISTRY.get(name)
    if tool is None or name not in tools_for(ctx.role, ctx.depth):
        return False, f"Tool {name!r} is not available to the {ctx.role} agent.", "error"

    risk = tool.risk_for(args)
    decision = "auto"
    if risk == BLOCKED:
        decision = "blocked"
    elif risk == CONFIRM:
        if config.TOOL_MODE == "readonly":
            decision = "denied"
        elif config.TOOL_MODE == "auto":
            decision = "auto"
        elif not ctx.interactive:
            decision = "denied"
        else:
            reason = f"{ROLES.get(ctx.role, {}).get('title', ctx.role)} wants to run {name}"
            await _emit(ctx, {"type": "step", "role": ctx.role, "tool": name, "args": args, "status": "awaiting_approval"})
            decision = await ctx.approver(name, args, reason)

    if decision in ("auto", "approved"):
        await _emit(ctx, {"type": "step", "role": ctx.role, "tool": name, "args": args, "status": "running"})
        try:
            text, ok = await tool.fn(args, ctx), True
        except ToolError as exc:
            text, ok = f"Error: {exc}", False
        except Exception as exc:  # unexpected bug in a tool: report, don't crash the turn
            log.exception("Tool %s crashed", name)
            text, ok = f"Error: {type(exc).__name__}: {exc}", False
        status = "done" if ok else "error"
    else:
        ok = False
        status = decision
        text = {
            "blocked": "Blocked by the security policy. Do not retry; tell the owner it isn't allowed.",
            "denied": ("The owner denied this action." if ctx.interactive and config.TOOL_MODE == "confirm"
                       else "This action needs the owner's approval, which isn't available here."),
            "timeout": "No approval received in time; the action was not run.",
        }.get(decision, "Not run.")

    text = str(text)
    if len(text) > config.TOOL_OUTPUT_LIMIT:
        text = text[: config.TOOL_OUTPUT_LIMIT] + "\n…[truncated]"
    audit_args = {k: (_short(v, 500) if isinstance(v, str) else v) for k, v in args.items()}
    try:
        db.add_audit(ctx.actor, ctx.origin, ctx.role, name, json.dumps(audit_args, ensure_ascii=False), risk,
                     decision, ok, _short(text, 1000))
    except Exception:
        log.exception("Audit write failed")
    await _emit(ctx, {"type": "step", "role": ctx.role, "tool": name, "args": audit_args, "status": status,
                      "result": _short(text, 400)})
    return ok, text, status


# --- Rule-based fallback ----------------------------------------------------

def rules_plan(text: str, models: list[dict]) -> dict:
    t = text.lower().strip()
    extra: list[dict] = []
    m = re.match(r"^\s*(?:please\s+)?(?:remember|note)(?:\s+that)?\s+(.+)$", text, re.I)
    if m:
        extra.append({"tool": "remember", "args": {"text": m.group(1).strip()}})
    elif (m := re.search(r"what do you (?:remember|know) about (.+)|\brecall (.+)", t)):
        extra.append({"tool": "recall", "args": {"query": (m.group(1) or m.group(2)).strip(" ?")}})
    elif re.search(r"\b(system|server|vps|machine|host)\b.*\b(status|health|load|usage|doing)\b"
                   r"|\b(cpu|ram|memory usage|disk space|uptime)\b", t):
        extra.append({"tool": "system_status", "args": {}})
    elif re.search(r"\b(list|show|what are)\b.*\b(jobs|tasks|schedules?|reminders)\b", t):
        extra.append({"tool": "list_tasks", "args": {}})
    if extra:
        return {"actions": extra, "response": ""}
    return agent.rule_based_plan(text, models)


# --- Main entry -------------------------------------------------------------

def _build_system(role: str, depth: int, state: dict, model_id: str, mems: list[dict], user_prompt: str = "") -> str:
    r = ROLES.get(role, ROLES["jarvis"])
    allowed_tools = tools_for(role, depth)
    # Token Optimization Layer 1: Intent-Filtered Tool Injection (saves ~70% prompt tokens)
    active_tools = token_opt.filter_tools_for_prompt(user_prompt, allowed_tools) if user_prompt else allowed_tools
    tools = "\n".join(REGISTRY[n].spec_line() for n in active_tools if n in REGISTRY)
    public_state = {
        "current_tab": state.get("currentTab", "Dashboard"), "business_tab": state.get("businessTab", "Overview"),
        "currency": state.get("currency", "USD"), "active_model": model_id,
    }
    mem_block = ""
    if mems:
        mem_block = "RELEVANT MEMORIES\n" + "\n".join(f"- {m['text']}" for m in mems) + "\n"
    now = datetime.now(scheduler.local_tz()).strftime("%A %Y-%m-%d %H:%M %Z")
    return SYSTEM_PROMPT.format(title=r["title"], description=r["description"], user_name=db.get_user().get("name", "the owner"),
                                now=now, tools=tools, state=json.dumps(public_state), memories=mem_block)


async def run(text: str, state: dict | None = None, history: list[dict] | None = None, *, role: str = "jarvis",
              actor: str = "user", origin: str = "rest", model: str | None = None,
              emit: Callable[[dict], Awaitable[None]] | None = None,
              approver: Callable[[str, dict, str], Awaitable[str]] | None = None,
              parent: ToolContext | None = None) -> agent.AgentResult:
    started = time.perf_counter()
    role = role if role in ROLES else "jarvis"
    if parent is not None:
        ctx = ToolContext(actor=parent.actor, origin=parent.origin, role=role, depth=parent.depth + 1,
                          state=parent.state, commands=parent.commands, emit=parent.emit, approver=parent.approver)
        model = model or parent.state.get("model")
        history = []
    else:
        ctx = ToolContext(actor=actor, origin=origin, role=role, state=dict(state or {}), emit=emit, approver=approver)
    model_id = llm.resolve_model(model or ctx.state.get("model"))["id"]
    models = db.list_models()

    # Token Optimization Layer 2: Tier-0 Zero-Token Fast Path (<5ms, 0 tokens)
    fast_plan = token_opt.try_zero_token_fast_path(text) if ctx.depth == 0 else None
    if fast_plan:
        steps: list[dict] = []
        for call in fast_plan.get("actions", []):
            name = resolve_tool_name(call.get("tool"))
            args = call.get("args") or {}
            if name:
                ok, obs, status = await execute_tool(name, args, ctx)
                steps.append({"tool": name, "args": args, "status": status, "result": _short(obs, 400)})
        resp = fast_plan.get("response", "Done.")
        return agent.AgentResult(
            response=resp, actions=ctx.commands if parent is None else [], engine="fast_path",
            model="zero-token-fast-path", tokens=0, latency_ms=int((time.perf_counter() - started) * 1000),
            raw_plan=json.dumps(fast_plan), error=None, steps=steps, role=role,
        )

    try:
        mems = await memory.search(text) if ctx.depth == 0 else []
    except Exception:
        log.exception("Memory search failed")
        mems = []

    # Token Optimization Layer 3: Sliding-Window History Compaction
    compacted_hist = token_opt.compact_history(history or [], max_verbatim=4)
    messages = [{"role": "system", "content": _build_system(role, ctx.depth, ctx.state, model_id, mems, text)},
                *compacted_hist, {"role": "user", "content": text}]

    engine, error, response, used_model = "llm", None, "", model_id
    steps: list[dict] = []
    observations: list[str] = []
    raw = ""

    for step in range(config.MAX_AGENT_STEPS):
        final_step = step == config.MAX_AGENT_STEPS - 1
        try:
            raw, tokens, used_model = await llm.chat(model_id, messages)
            ctx.tokens += tokens
            plan = agent._extract_json(raw)
            if plan is None:
                raise ValueError("invalid JSON from model")
        except Exception as exc:
            if step == 0:
                engine = "rules"
                error = f"LLM unavailable ({type(exc).__name__}); used rule engine"
                log.info("%s: %s", error, exc)
                plan = rules_plan(text, models)
                raw = json.dumps(plan)
                ctx.tokens += len(text.split()) + 12
            else:
                error = f"LLM failed mid-task ({type(exc).__name__})"
                break

        response = (plan.get("response") or "").strip() or response
        actions = plan.get("actions", [])[:6]
        if not actions or (final_step and engine == "llm"):
            break

        needs_followup = False
        step_obs = []
        for call in actions:
            name = resolve_tool_name(call.get("tool") or call.get("name"))
            args = call.get("args") or call.get("arguments") or {}
            if not isinstance(args, dict):
                args = {"target": args}
            if not name:
                step_obs.append(f"{call.get('tool')}: unknown tool")
                needs_followup = True
                continue
            ok, obs, status = await execute_tool(name, args, ctx)
            obs = token_opt.prune_observation(obs)
            steps.append({"tool": name, "args": args, "status": status, "result": _short(obs, 400)})
            step_obs.append(f"{name}: {obs}")
            observations.append(obs)
            if name not in REGISTRY or REGISTRY[name].observe or not ok:  # info tools and failures need a follow-up
                needs_followup = True

        if engine == "rules":
            if not response:
                response = " ".join(o for o in observations if o) or "Done."
            break
        if not needs_followup:
            break
        messages.append({"role": "assistant", "content": raw})
        suffix = FINAL_SUFFIX if step + 1 == config.MAX_AGENT_STEPS - 1 else OBS_SUFFIX
        messages.append({"role": "user", "content": "Observation:\n" + "\n".join(step_obs) + "\n\n" + suffix})
        response = ""  # the pre-tool response ("let me check") is superseded by the follow-up answer

    facts = agent.finance_facts(agent.normalize_currency(ctx.state.get("currency")) or "USD")
    response = re.sub(r"\{(\w+)\}", lambda m: facts.get(m.group(1), m.group(0)), response)
    if not response:
        response = _short(" ".join(observations[-2:]), 600) if observations else "Done."

    if parent is not None:
        parent.tokens += ctx.tokens
    return agent.AgentResult(
        response=response, actions=ctx.commands if parent is None else [], engine=engine, model=used_model,
        tokens=ctx.tokens, latency_ms=int((time.perf_counter() - started) * 1000),
        raw_plan=json.dumps({"actions": [], "response": response}), error=error, steps=steps, role=role,
    )
