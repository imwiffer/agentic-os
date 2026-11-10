"""File tools confined to the agent workspace (config.WORKSPACE_DIR). Symlinks / `..` cannot escape it."""
from __future__ import annotations

from pathlib import Path

from .. import config
from . import CONFIRM, ToolContext, ToolError, tool

MAX_WRITE = 200_000


def safe_path(rel: str | None) -> Path:
    root = config.WORKSPACE_DIR
    root.mkdir(parents=True, exist_ok=True)
    rel = (rel or ".").strip().replace("\\", "/")
    candidate = Path(rel)
    if candidate.is_absolute() or rel.startswith("/"):
        # Allow absolute paths only if they already point inside the workspace.
        target = candidate.resolve() if candidate.is_absolute() else Path(rel).resolve()
        if rel.startswith("/") and not candidate.is_absolute():
            raise ToolError("Path is outside the agent workspace.")
    else:
        target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ToolError("Path is outside the agent workspace.")
    return target


def _rel(p: Path) -> str:
    return str(p.relative_to(config.WORKSPACE_DIR)).replace("\\", "/") or "."


@tool("list_files", "List files in the agent workspace.", {"path": "folder relative to the workspace (default .)"})
async def list_files(args: dict, ctx: ToolContext) -> str:
    p = safe_path(args.get("path"))
    if not p.exists():
        raise ToolError(f"{_rel(p)} does not exist.")
    if p.is_file():
        return f"{_rel(p)} ({p.stat().st_size} bytes)"
    entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))[:200]
    if not entries:
        return f"{_rel(p)} is empty."
    return "\n".join(f"{e.name}/" if e.is_dir() else f"{e.name} ({e.stat().st_size} B)" for e in entries)


@tool("read_file", "Read a text file from the workspace.", {"path": "file path relative to the workspace"})
async def read_file(args: dict, ctx: ToolContext) -> str:
    p = safe_path(args.get("path"))
    if not p.is_file():
        raise ToolError(f"{_rel(p)} is not a file.")
    data = p.read_bytes()[: config.TOOL_OUTPUT_LIMIT * 4]
    if b"\x00" in data[:1024]:
        raise ToolError("That looks like a binary file.")
    return data.decode("utf-8", errors="replace")


@tool("write_file", "Create or overwrite a text file in the workspace (append=true to add to it).",
      {"path": "file path relative to the workspace", "content": "text", "append": "true | false"})
async def write_file(args: dict, ctx: ToolContext) -> str:
    p = safe_path(args.get("path"))
    content = str(args.get("content") or "")
    if len(content) > MAX_WRITE:
        raise ToolError("Content too large.")
    if p == config.WORKSPACE_DIR or p.is_dir():
        raise ToolError("Give a file name, not a folder.")
    p.parent.mkdir(parents=True, exist_ok=True)
    append = str(args.get("append", "false")).lower() in ("1", "true", "yes")
    with p.open("a" if append else "w", encoding="utf-8") as fh:
        fh.write(content)
    return f"{'Appended to' if append else 'Wrote'} {_rel(p)} ({len(content)} chars)."


@tool("delete_file", "Delete a file from the workspace.", {"path": "file path relative to the workspace"},
      risk=CONFIRM)
async def delete_file(args: dict, ctx: ToolContext) -> str:
    p = safe_path(args.get("path"))
    if not p.is_file():
        raise ToolError(f"{_rel(p)} is not a file.")
    p.unlink()
    return f"Deleted {_rel(p)}."
