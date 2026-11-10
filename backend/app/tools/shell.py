"""Shell access with a three-tier policy.

safe     read-only diagnostics (df, free, uptime, systemctl status, file reads inside the workspace, ...)
confirm  anything else, including pipes/redirects -> the dashboard asks you first
blocked  destructive or lock-out commands (rm -rf /, mkfs, shutdown, sudo, ufw/tailscale changes, ...)

Commands run as the unprivileged service user, with a scrubbed environment (no API keys), cwd = workspace,
and a hard timeout.
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import subprocess
import sys

from .. import config
from . import BLOCKED, CONFIRM, SAFE, ToolContext, ToolError, tool
from .files import safe_path

SAFE_BINARIES = {"df", "free", "uptime", "uname", "whoami", "id", "date", "ps", "pwd", "hostname", "nproc",
                 "lscpu", "ss", "ip", "ping", "echo", "vmstat", "iostat", "lsblk", "sensors"}
# Binaries that are safe only for specific subcommands.
SAFE_SUBCOMMANDS = {
    "systemctl": {"status", "is-active", "is-enabled", "list-units", "list-timers", "--failed"},
    "ollama": {"list", "ps", "show", "--version"},
    "tailscale": {"status", "ip", "netcheck", "ping", "version", "whois"},
    "journalctl": None,  # read-only; limited further by the user's group membership
    "docker": {"ps", "images", "stats"},
    "git": {"status", "log", "diff", "show", "branch"},
}
# Read-only binaries whose path arguments must stay inside the workspace.
PATH_BINARIES = {"ls", "cat", "head", "tail", "wc", "grep", "find", "stat", "du", "tree", "sort", "uniq", "file"}

BLOCK_PATTERNS = [
    r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\*|/\*)(\s|$)", r"\bmkfs", r"\bdd\s+if=", r"\b(shutdown|reboot|poweroff|halt)\b",
    r":\(\)\s*\{", r"\bsudo\b", r"\bsu\s", r"(?:^|[|;&\s])passwd(?:\s|$)", r"\b(useradd|usermod|userdel|visudo)\b",
    r"\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b", r"\bnc\b.*\s-e\s", r"/proc/\S*environ", r"jarvis\.env",
    r"\bufw\b", r"\biptables\b", r"\bnft\b", r"\btailscale\s+(down|logout|up|set|serve|funnel)\b",
    r"\bsystemctl\s+(stop|disable|mask|kill)\s+(tailscaled|ssh|sshd|jarvis)", r">\s*/dev/sd", r"\bchmod\s+-R\s+777\s+/",
    r"\bcrontab\s+-r\b", r"\bhistory\s+-c\b",
]
SHELL_META = re.compile(r"[|;&<>`$(){}\n]")


def classify(command: str) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return SAFE
    if any(re.search(p, cmd, re.I) for p in BLOCK_PATTERNS):
        return BLOCKED
    if SHELL_META.search(cmd):
        return CONFIRM
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return CONFIRM
    binary, rest = os.path.basename(parts[0]), parts[1:]
    if binary in SAFE_BINARIES:
        return SAFE
    if binary in SAFE_SUBCOMMANDS:
        allowed = SAFE_SUBCOMMANDS[binary]
        sub = next((a for a in rest if not a.startswith("-")), rest[0] if rest else "")
        return SAFE if allowed is None or sub in allowed else CONFIRM
    if binary in PATH_BINARIES:
        paths = [a for a in rest if not a.startswith("-")]
        if binary == "grep" and paths:
            paths = paths[1:]  # first positional is the pattern
        try:
            for a in paths or ["."]:
                safe_path(a)
            return SAFE
        except ToolError:
            return CONFIRM
    return CONFIRM


def _risk(args: dict) -> str:
    return classify(str(args.get("command") or ""))


def _env() -> dict:
    keep = {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


def _run(command: str, use_shell: bool) -> tuple[int, str]:
    config.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    if use_shell:
        argv = command if sys.platform == "win32" else ["/bin/bash", "-c", command]
    else:
        argv = shlex.split(command)
    proc = subprocess.run(argv, cwd=config.WORKSPACE_DIR, env=_env(), capture_output=True, text=True,
                          timeout=config.SHELL_TIMEOUT, shell=use_shell and sys.platform == "win32",
                          stdin=subprocess.DEVNULL)
    return proc.returncode, (proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")).strip()


@tool("run_shell", "Run a shell command on the VPS (read-only diagnostics run instantly; changes need approval).",
      {"command": "the command line"}, risk=_risk)
async def run_shell(args: dict, ctx: ToolContext) -> str:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ToolError("Empty command.")
    try:
        code, out = await asyncio.to_thread(_run, command, bool(SHELL_META.search(command)))
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"Command timed out after {config.SHELL_TIMEOUT:.0f}s.") from exc
    except FileNotFoundError as exc:
        raise ToolError(f"Command not found: {exc.filename}") from exc
    out = out or "(no output)"
    if len(out) > config.TOOL_OUTPUT_LIMIT:
        out = out[: config.TOOL_OUTPUT_LIMIT] + "\n…[truncated]"
    return f"exit {code}\n{out}"
