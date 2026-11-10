"""Long-term memory tools."""
from __future__ import annotations

from .. import memory
from . import CONFIRM, ToolContext, ToolError, tool


@tool("remember", "Save a durable fact or preference to long-term memory.",
      {"text": "the fact, written as a full sentence", "tags": "optional comma-separated tags"})
async def remember(args: dict, ctx: ToolContext) -> str:
    text = str(args.get("text") or args.get("fact") or "").strip()
    if not text:
        raise ToolError("Nothing to remember.")
    m = await memory.save(text, str(args.get("tags") or ""), source=ctx.actor)
    return f"Saved to memory (#{m['id']})."


@tool("recall", "Search long-term memory.", {"query": "what to look for"})
async def recall(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query") or args.get("text") or "").strip()
    hits = await memory.search(query, k=6, min_score=0.2)
    if not hits:
        return "No matching memories."
    return "\n".join(f"#{h['id']} ({h['created_at'][:10]}): {h['text']}" for h in hits)


@tool("forget_memory", "Delete a memory by id.", {"id": "memory id number"}, risk=CONFIRM)
async def forget_memory(args: dict, ctx: ToolContext) -> str:
    try:
        mid = int(str(args.get("id") or "").lstrip("#"))
    except ValueError as exc:
        raise ToolError("Give the numeric memory id.") from exc
    if not memory.forget(mid):
        raise ToolError(f"Memory #{mid} not found.")
    return f"Forgot memory #{mid}."
