"""Orchestration tools: scheduling, inbox notifications and delegation to specialist sub-agents."""
from __future__ import annotations

from .. import db, hub, scheduler
from ..roles import ROLES
from . import ToolContext, ToolError, tool


@tool("schedule_task", "Schedule a background agent job; its result goes to the Inbox. Give exactly one of "
      "every_minutes, daily_at or run_at.",
      {"name": "short title", "prompt": "what the agent should do each run", "every_minutes": "number (>=5)",
       "daily_at": "HH:MM 24h local time", "run_at": "ISO datetime for a one-off", "role": f"one of {list(ROLES)}"})
async def schedule_task(args: dict, ctx: ToolContext) -> str:
    try:
        job = scheduler.create_job(str(args.get("name") or ""), str(args.get("prompt") or args.get("task") or ""),
                                   str(args.get("role") or "jarvis"), args.get("every_minutes"),
                                   args.get("daily_at"), args.get("run_at"))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return f"Scheduled job #{job['id']} '{job['name']}', first run {job['next_run_local']}."


@tool("list_tasks", "List scheduled background jobs.")
async def list_tasks(args: dict, ctx: ToolContext) -> str:
    jobs = db.list_jobs()
    return "\n".join(scheduler.describe(j) for j in jobs) if jobs else "No scheduled jobs."


@tool("cancel_task", "Cancel a scheduled job by id.", {"id": "job id number"})
async def cancel_task(args: dict, ctx: ToolContext) -> str:
    try:
        job_id = int(str(args.get("id") or "").lstrip("#"))
    except ValueError as exc:
        raise ToolError("Give the numeric job id.") from exc
    if not db.delete_job(job_id):
        raise ToolError(f"Job #{job_id} not found.")
    return f"Cancelled job #{job_id}."


@tool("notify", "Post a message to the owner's Inbox.", {"title": "short title", "body": "message text"})
async def notify(args: dict, ctx: ToolContext) -> str:
    body = str(args.get("body") or args.get("text") or "").strip()
    if not body:
        raise ToolError("Empty notification.")
    item = db.add_inbox(str(args.get("title") or "Jarvis")[:120], body[:8000], source=ctx.role)
    await hub.broadcast({"type": "inbox", "item": item})
    return f"Posted to Inbox (#{item['id']})."


@tool("delegate", "Hand a sub-task to a specialist agent and get its answer back.",
      {"role": "researcher | ops | planner | finance", "task": "clear, self-contained instructions"})
async def delegate(args: dict, ctx: ToolContext) -> str:
    from .. import runtime

    role = str(args.get("role") or "").lower()
    task = str(args.get("task") or args.get("prompt") or "").strip()
    if role not in ROLES or role == "jarvis":
        raise ToolError(f"Unknown specialist {role!r}. Choose researcher, ops, planner or finance.")
    if ctx.depth >= 1:
        raise ToolError("Sub-agents cannot delegate further.")
    if not task:
        raise ToolError("The task is empty.")
    sub = await runtime.run(task, role=role, parent=ctx)
    return f"[{ROLES[role]['title']}] {sub.response}"
