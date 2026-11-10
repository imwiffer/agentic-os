"""Agent roles: a persona + the subset of tools it may use. The main assistant (jarvis) can delegate to the others."""
from __future__ import annotations

UI_TOOLS = ["open_tab", "check_balance", "get_income", "set_currency", "scroll_page", "select_model", "speak_text"]
MEMORY_TOOLS = ["remember", "recall", "forget_memory"]
FILE_TOOLS = ["list_files", "read_file", "write_file", "delete_file"]
SCHEDULE_TOOLS = ["schedule_task", "list_tasks", "cancel_task", "notify"]

ROLES: dict[str, dict] = {
    "jarvis": {
        "title": "JARVIS",
        "description": "the main assistant and OS controller. Handle requests directly when simple; delegate deep "
                       "research, server operations or multi-step planning to a specialist via `delegate`.",
        "icon": "fas fa-robot",
        "tools": UI_TOOLS + MEMORY_TOOLS + FILE_TOOLS + SCHEDULE_TOOLS
                 + ["system_status", "web_search", "web_fetch", "run_shell", "delegate"],
    },
    "researcher": {
        "title": "Researcher",
        "description": "a meticulous research agent. Search the web, read the best sources, cross-check facts and "
                       "return a concise, sourced summary. Save notable findings to files when asked.",
        "icon": "fas fa-magnifying-glass",
        "tools": ["web_search", "web_fetch", "recall", "remember", "write_file", "read_file", "list_files", "notify"],
    },
    "ops": {
        "title": "Ops Engineer",
        "description": "a careful Linux/SRE agent responsible for the VPS. Diagnose with read-only commands first, "
                       "explain findings plainly, and only propose changes that are necessary.",
        "icon": "fas fa-server",
        "tools": ["system_status", "run_shell", "list_files", "read_file", "write_file", "recall", "notify"],
    },
    "planner": {
        "title": "Planner",
        "description": "a planning agent. Break goals into concrete steps, set up recurring jobs and reminders, and "
                       "keep track of commitments in memory.",
        "icon": "fas fa-diagram-project",
        "tools": SCHEDULE_TOOLS + ["recall", "remember", "write_file", "read_file", "list_files"],
    },
    "finance": {
        "title": "Finance Analyst",
        "description": "a finance analyst. Read the owner's figures with the finance tools, compute ratios and give "
                       "short, practical insights. Never invent numbers.",
        "icon": "fas fa-chart-pie",
        "tools": ["check_balance", "get_income", "set_currency", "open_tab", "recall", "remember", "notify"],
    },
}


def tools_for(role: str, depth: int = 0) -> list[str]:
    names = list(ROLES.get(role, ROLES["jarvis"])["tools"])
    if depth > 0:  # sub-agents can't delegate further or drive the UI's model picker
        names = [n for n in names if n not in ("delegate", "select_model")]
    return names


def public_roles() -> list[dict]:
    return [{"id": k, "title": v["title"], "description": v["description"], "icon": v["icon"], "tools": v["tools"]}
            for k, v in ROLES.items()]
