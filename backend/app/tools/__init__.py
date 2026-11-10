"""Tool registry. Every tool declares a risk level that the runtime enforces:

    safe     -> runs immediately
    confirm  -> needs explicit user approval (unless TOOL_MODE=auto; denied when TOOL_MODE=readonly
                or when no human is available, e.g. scheduled jobs / REST calls)
    blocked  -> never runs

`risk` may also be a function of the arguments (e.g. run_shell is safe for `df -h`, confirm for `apt update`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

SAFE, CONFIRM, BLOCKED = "safe", "confirm", "blocked"


class ToolError(Exception):
    """Expected, user-facing tool failure (bad path, unreachable URL, ...)."""


@dataclass
class ToolContext:
    actor: str = "user"                 # who asked (login name, 'scheduler', ...)
    origin: str = "rest"                # ws | rest | job:<id>
    role: str = "jarvis"
    depth: int = 0                      # delegation depth (sub-agents run at depth 1)
    state: dict = field(default_factory=dict)        # dashboard UI state
    commands: list[dict] = field(default_factory=list)  # UI commands for the browser
    emit: Callable[[dict], Awaitable[None]] | None = None
    approver: Callable[[str, dict, str], Awaitable[str]] | None = None  # -> approved | denied | timeout
    tokens: int = 0

    @property
    def interactive(self) -> bool:
        return self.approver is not None


@dataclass
class Tool:
    name: str
    description: str
    args: dict[str, str]
    fn: Callable[[dict, ToolContext], Awaitable[str]]
    risk: str | Callable[[dict], str] = SAFE
    observe: bool = True    # result must be fed back to the LLM before it answers
    kind: str = "server"    # server | ui

    def risk_for(self, args: dict) -> str:
        return self.risk(args) if callable(self.risk) else self.risk

    def spec_line(self) -> str:
        args = ", ".join(f"{k}: {v}" for k, v in self.args.items())
        flag = " [may need approval]" if self.risk != SAFE else ""
        return f"- {self.name}({args}): {self.description}{flag}"


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, args: dict[str, str] | None = None, *, risk: Any = SAFE,
         observe: bool = True, kind: str = "server"):
    def deco(fn):
        REGISTRY[name] = Tool(name, description, args or {}, fn, risk, observe, kind)
        return fn
    return deco


def get(name: str | None) -> Tool | None:
    return REGISTRY.get(name or "")


def load_builtin_tools() -> None:
    # Importing registers the tools via the @tool decorator.
    from . import files, memory_tools, orchestration, shell, system, ui, web  # noqa: F401
