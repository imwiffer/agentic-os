"""Persistent job scheduler (no external deps). Jobs are agent prompts that run in the background;
results land in the Inbox. Background runs are non-interactive: tools needing approval are denied.

kinds:  interval -> value = minutes (>= MIN_INTERVAL)
        daily    -> value = "HH:MM" in JARVIS_TZ (or server local time)
        once     -> value = ISO datetime (local time if no offset given)
All next_run values are stored as naive UTC ISO strings so they compare correctly as text.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone, tzinfo

from . import config, db, hub

log = logging.getLogger("jarvis.scheduler")
MIN_INTERVAL = 5
JOB_TIMEOUT = 600


def local_tz() -> tzinfo:
    if config.TIMEZONE:
        from zoneinfo import ZoneInfo
        return ZoneInfo(config.TIMEZONE)
    return datetime.now().astimezone().tzinfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def to_local(iso_utc: str | None) -> str | None:
    if not iso_utc:
        return None
    dt = datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc).astimezone(local_tz())
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def next_run(kind: str, value: str, after: datetime) -> datetime | None:
    """Next run strictly after `after` (naive UTC)."""
    if kind == "interval":
        return after + timedelta(minutes=float(value))
    if kind == "daily":
        hh, mm = (int(x) for x in value.split(":"))
        local_after = after.replace(tzinfo=timezone.utc).astimezone(local_tz())
        cand = local_after.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= local_after:
            cand = (cand + timedelta(days=1)).replace(hour=hh, minute=mm)
        return cand.astimezone(timezone.utc).replace(tzinfo=None)
    if kind == "once":
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=local_tz())
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
        return dt if dt > after else None
    raise ValueError(f"Unknown schedule kind {kind!r}")


def create_job(name: str, prompt: str, role: str = "jarvis", every_minutes=None, daily_at=None, run_at=None) -> dict:
    from .roles import ROLES

    given = [x for x in (every_minutes, daily_at, run_at) if x not in (None, "", 0)]
    if len(given) != 1:
        raise ValueError("Give exactly one of every_minutes, daily_at (HH:MM) or run_at (ISO datetime).")
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}. Choose one of {list(ROLES)}.")
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("The job needs a prompt.")
    if every_minutes not in (None, "", 0):
        minutes = float(every_minutes)
        if minutes < MIN_INTERVAL:
            raise ValueError(f"Minimum interval is {MIN_INTERVAL} minutes.")
        kind, value = "interval", str(int(minutes) if minutes.is_integer() else minutes)
    elif daily_at:
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(daily_at))
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError("daily_at must be HH:MM (24h).")
        kind, value = "daily", f"{int(m.group(1)):02d}:{m.group(2)}"
    else:
        try:
            datetime.fromisoformat(str(run_at))
        except ValueError as exc:
            raise ValueError("run_at must be an ISO datetime like 2026-11-11T08:00.") from exc
        kind, value = "once", str(run_at)
    nr = next_run(kind, value, utcnow())
    if nr is None:
        raise ValueError("That time is in the past.")
    name = (name or prompt[:40]).strip()[:80]
    job_id = db.add_job(name, prompt[:2000], role, kind, value, iso(nr))
    return {"id": job_id, "name": name, "kind": kind, "value": value, "next_run": iso(nr), "next_run_local": to_local(iso(nr))}


def describe(job: dict) -> str:
    when = {"interval": f"every {job['value']} min", "daily": f"daily at {job['value']}",
            "once": f"once at {job['value']}"}.get(job["kind"], job["kind"])
    return f"#{job['id']} {job['name']} ({when}, next {to_local(job['next_run']) or 'never'}, role {job['role']})"


def public(job: dict) -> dict:
    return {**job, "next_run_local": to_local(job.get("next_run")), "last_run_local": to_local(job.get("last_run")),
            "description": describe(job)}


class Scheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()  # one background job at a time (CPU-bound LLM)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="jarvis-scheduler")
            log.info("Scheduler started (tick %.0fs, tz %s)", config.SCHEDULER_TICK, local_tz())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Scheduler tick failed")
            await asyncio.sleep(config.SCHEDULER_TICK)

    async def tick(self) -> int:
        jobs = db.due_jobs(iso(utcnow()))
        for job in jobs:
            await self.run_job(job)
        return len(jobs)

    async def run_job(self, job: dict, manual: bool = False) -> dict:
        if not manual:  # advance first so a crash can't cause a tight re-run loop
            nr = next_run(job["kind"], job["value"], utcnow())
            db.update_job(job["id"], next_run=iso(nr), enabled=1 if nr else 0)
        async with self._lock:
            from . import runtime

            log.info("Running job %s", describe(job))
            try:
                result = await asyncio.wait_for(
                    runtime.run(job["prompt"], role=job["role"], actor="scheduler", origin=f"job:{job['id']}"),
                    timeout=JOB_TIMEOUT)
                body, status = result.response, ("ok" if result.engine != "rules" or not result.error else "degraded")
            except Exception as exc:
                log.exception("Job %s failed", job["id"])
                body, status = f"Job failed: {type(exc).__name__}: {exc}", "error"
            item = db.add_inbox(f"⏰ {job['name']}", body, source="scheduler", job_id=job["id"])
            db.update_job(job["id"], last_run=iso(utcnow()), last_status=status)
        await hub.broadcast({"type": "inbox", "item": item})
        return item


scheduler = Scheduler()
