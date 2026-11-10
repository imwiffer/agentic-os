"""VPS health and introspection."""
from __future__ import annotations

import asyncio
import os
import platform
import shutil
import time

import httpx

from .. import config
from . import ToolContext, tool

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


async def _ollama_loaded() -> list[str] | None:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(f"{config.OLLAMA_URL}/api/ps")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return None


async def snapshot() -> dict:
    """Structured health data (also served at GET /api/system)."""
    disk = shutil.disk_usage(config.DATA_DIR if config.DATA_DIR.exists() else "/")
    data = {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu_count": os.cpu_count(),
        "disk_total": disk.total, "disk_used": disk.used,
        "disk_percent": round(disk.used / disk.total * 100, 1) if disk.total else 0,
        "ollama_loaded": await _ollama_loaded(),
    }
    if psutil:
        cpu = await asyncio.to_thread(psutil.cpu_percent, 0.4)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        data.update({
            "cpu_percent": cpu, "mem_total": mem.total, "mem_used": mem.total - mem.available,
            "mem_percent": mem.percent, "swap_percent": swap.percent,
            "uptime_s": int(time.time() - psutil.boot_time()),
        })
    if hasattr(os, "getloadavg"):
        data["load_avg"] = [round(x, 2) for x in os.getloadavg()]
    return data


@tool("system_status", "Report VPS health: CPU, RAM, disk, uptime, load and which models are loaded.")
async def system_status(args: dict, ctx: ToolContext) -> str:
    s = await snapshot()
    parts = [f"Host {s['hostname']} ({s['os']}, {s['cpu_count']} vCPU)."]
    if "cpu_percent" in s:
        hours = s["uptime_s"] // 3600
        parts.append(f"CPU {s['cpu_percent']:.0f}%, RAM {s['mem_percent']:.0f}% of {_fmt_bytes(s['mem_total'])}, "
                     f"swap {s['swap_percent']:.0f}%, uptime {hours // 24}d {hours % 24}h.")
    parts.append(f"Disk {s['disk_percent']}% used of {_fmt_bytes(s['disk_total'])}.")
    if "load_avg" in s:
        parts.append(f"Load average {s['load_avg'][0]}, {s['load_avg'][1]}, {s['load_avg'][2]}.")
    loaded = s["ollama_loaded"]
    parts.append("Ollama offline." if loaded is None else f"Models in memory: {', '.join(loaded) or 'none'}.")
    return " ".join(parts)
