"""SQLite data layer: users, finance, metrics, usage and the LLM model registry."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    title       TEXT,
    email       TEXT,
    avatar_url  TEXT
);

CREATE TABLE IF NOT EXISTS finance (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    cash_on_hand     REAL NOT NULL,
    monthly_income   REAL NOT NULL,
    monthly_expenses REAL NOT NULL,
    revenue          REAL NOT NULL,   -- revenue month-to-date
    revenue_target   REAL NOT NULL,
    base_currency    TEXT NOT NULL DEFAULT 'USD',
    updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS metrics (
    id      INTEGER PRIMARY KEY,
    tab     TEXT NOT NULL,       -- Progress | Audience
    key     TEXT NOT NULL,
    label   TEXT NOT NULL,
    value   REAL NOT NULL,
    format  TEXT NOT NULL,       -- currency | percent | number
    icon    TEXT NOT NULL,
    color   TEXT NOT NULL,
    sort    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS usage (
    id      TEXT PRIMARY KEY,
    label   TEXT NOT NULL,
    percent REAL NOT NULL,
    image   TEXT
);

CREATE TABLE IF NOT EXISTS models (
    id        TEXT PRIMARY KEY,  -- Ollama tag or provider model id
    name      TEXT NOT NULL,
    provider  TEXT NOT NULL,     -- ollama | openai-compatible
    location  TEXT NOT NULL,     -- local | cloud
    size      TEXT,
    sort      INTEGER NOT NULL DEFAULT 0
);

-- Persistent conversation history (one conversation per dashboard client).
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,       -- user | assistant
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);

-- Long-term memory. embedding = float32 array (BLOB) from the embedding model, NULL if unavailable.
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY,
    text       TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'agent',
    embedding  BLOB,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Scheduled agent jobs. kind: interval (value = minutes) | daily (value = HH:MM) | once (value = ISO datetime)
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'jarvis',
    kind        TEXT NOT NULL,
    value       TEXT NOT NULL,
    next_run    TEXT,
    last_run    TEXT,
    last_status TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Results of background jobs and agent notifications.
CREATE TABLE IF NOT EXISTS inbox (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'agent',
    job_id     INTEGER,
    read       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Append-only record of every tool call the agent attempted.
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor      TEXT NOT NULL,      -- user login / 'scheduler'
    origin     TEXT NOT NULL,      -- ws | rest | job:<id>
    role       TEXT NOT NULL,
    tool       TEXT NOT NULL,
    args       TEXT NOT NULL,
    risk       TEXT NOT NULL,      -- safe | confirm | blocked
    decision   TEXT NOT NULL,      -- auto | approved | denied | blocked | timeout
    ok         INTEGER NOT NULL,
    result     TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""

SEED_MODELS = [
    ("qwen2.5-coder:7b", "Qwen 2.5 Coder 7B (VPS)", "ollama", "local", "7B", 0),
    ("qwen2.5:7b-instruct", "Qwen 2.5 7B", "ollama", "local", "7B", 1),
    ("llama3.1:8b", "Llama 3.1 8B", "ollama", "local", "8B", 2),
    ("llama3.2:3b", "Llama 3.2 3B", "ollama", "local", "3B", 3),
    ("criscar22/Qwen2.5-Coder-7B-Instruct-abliterated", "Qwen 2.5 Coder 7B (AirLLM Failsafe)", "airllm", "local", "7B", 4),
    ("qwen/qwen-2.5-72b-instruct", "Qwen 2.5 72B", "openai-compatible", "cloud", "72B", 5),
    ("meta-llama/llama-3.1-70b-instruct", "Llama 3.1 70B", "openai-compatible", "cloud", "70B", 6),
    ("mistralai/mistral-large", "Mistral Large", "openai-compatible", "cloud", "123B", 7),
]
# Too big / non-existent for a CPU VPS; removed from the registry on start-up.
DEPRECATED_MODELS = ("qwen3.8:27b", "llama3:8b", "mistral:7b")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            _seed(c)
        c.executemany("DELETE FROM models WHERE id = ?", [(m,) for m in DEPRECATED_MODELS])
        c.executemany("INSERT OR IGNORE INTO models (id, name, provider, location, size, sort) VALUES (?,?,?,?,?,?)",
                      SEED_MODELS)


def _seed(c: sqlite3.Connection) -> None:
    c.execute(
        "INSERT INTO users (id, name, title, email) VALUES (1, 'Jack Roberts', 'Personal Profile', 'jack@example.com')"
    )
    c.execute(
        """INSERT INTO finance (user_id, cash_on_hand, monthly_income, monthly_expenses, revenue, revenue_target)
           VALUES (1, 14204, 8320, 5140, 11870, 25000)"""
    )
    c.executemany(
        "INSERT INTO metrics (tab, key, label, value, format, icon, color, sort) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("Progress", "tasks_done", "Agent tasks completed", 128, "number", "fas fa-list-check", "green", 2),
            ("Progress", "hours_saved", "Hours saved by automation", 36, "number", "fas fa-clock", "blue", 3),
            ("Audience", "subscribers", "YouTube subscribers", 48210, "number", "fab fa-youtube", "red", 1),
            ("Audience", "newsletter", "Newsletter readers", 6930, "number", "fas fa-envelope-open-text", "amber", 2),
            ("Audience", "engagement", "Engagement rate", 5.8, "percent", "fas fa-heart", "pink", 3),
        ],
    )
    c.executemany(
        "INSERT INTO usage (id, label, percent, image) VALUES (?,?,?,?)",
        [
            ("claude", "Claude weekly used", 16,
             "https://images.unsplash.com/photo-1541963463532-d68292c34b19?q=80&w=256&auto=format&fit=crop"),
            ("codex", "Codex weekly used", 9,
             "https://images.unsplash.com/photo-1614728853970-00e10288011a?q=80&w=256&auto=format&fit=crop"),
        ],
    )


# --- Queries ----------------------------------------------------------------

def get_user(user_id: int = 1) -> dict:
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else {}


def update_user(name: str, title: str = "Operator Profile", email: str = "", user_id: int = 1) -> dict:
    with connect() as c:
        c.execute(
            "UPDATE users SET name = ?, title = ?, email = COALESCE(NULLIF(?, ''), email) WHERE id = ?",
            (name.strip() or "dj", title.strip() or "Operator Profile", email.strip(), user_id),
        )
    return get_user(user_id)


def get_finance(user_id: int = 1) -> dict:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM finance WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
    return dict(row) if row else {}


def update_finance(
    cash_on_hand: float | None = None,
    monthly_income: float | None = None,
    monthly_expenses: float | None = None,
    revenue: float | None = None,
    revenue_target: float | None = None,
    base_currency: str | None = None,
    user_id: int = 1,
) -> dict:
    cur = get_finance(user_id)
    if not cur:
        return {}
    new_vals = {
        "cash_on_hand": float(cash_on_hand if cash_on_hand is not None else cur["cash_on_hand"]),
        "monthly_income": float(monthly_income if monthly_income is not None else cur["monthly_income"]),
        "monthly_expenses": float(monthly_expenses if monthly_expenses is not None else cur["monthly_expenses"]),
        "revenue": float(revenue if revenue is not None else cur["revenue"]),
        "revenue_target": float(revenue_target if revenue_target is not None else cur["revenue_target"]),
        "base_currency": (base_currency or cur.get("base_currency") or "USD").upper(),
    }
    with connect() as c:
        c.execute(
            """UPDATE finance
               SET cash_on_hand = ?, monthly_income = ?, monthly_expenses = ?,
                   revenue = ?, revenue_target = ?, base_currency = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                new_vals["cash_on_hand"],
                new_vals["monthly_income"],
                new_vals["monthly_expenses"],
                new_vals["revenue"],
                new_vals["revenue_target"],
                new_vals["base_currency"],
                cur["id"],
            ),
        )
    return get_finance(user_id)


def get_metrics() -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM metrics ORDER BY tab, sort").fetchall()
    return [dict(r) for r in rows]


def upsert_metric(
    tab: str,
    key: str,
    label: str,
    value: float,
    fmt: str = "number",
    icon: str = "fas fa-chart-simple",
    color: str = "purple",
    sort: int = 10,
) -> dict:
    with connect() as c:
        existing = c.execute("SELECT id FROM metrics WHERE key = ?", (key,)).fetchone()
        if existing:
            c.execute(
                "UPDATE metrics SET tab = ?, label = ?, value = ?, format = ?, icon = ?, color = ? WHERE id = ?",
                (tab, label, float(value), fmt, icon, color, existing["id"]),
            )
        else:
            c.execute(
                "INSERT INTO metrics (tab, key, label, value, format, icon, color, sort) VALUES (?,?,?,?,?,?,?,?)",
                (tab, key, label, float(value), fmt, icon, color, sort),
            )
        row = c.execute("SELECT * FROM metrics WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else {}


def delete_metric(key: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM metrics WHERE key = ?", (key,)).rowcount > 0


def get_usage() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM usage ORDER BY id").fetchall()]


def upsert_usage(uid: str, label: str, percent: float, image: str = "") -> dict:
    with connect() as c:
        existing = c.execute("SELECT image FROM usage WHERE id = ?", (uid,)).fetchone()
        img = image or (existing["image"] if existing else "https://images.unsplash.com/photo-1614728853970-00e10288011a?q=80&w=256&auto=format&fit=crop")
        c.execute(
            "INSERT INTO usage (id, label, percent, image) VALUES (?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET label = excluded.label, percent = excluded.percent, image = excluded.image",
            (uid, label, max(0.0, min(100.0, float(percent))), img),
        )
        row = c.execute("SELECT * FROM usage WHERE id = ?", (uid,)).fetchone()
    return dict(row) if row else {}


def list_models() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM models ORDER BY sort").fetchall()]


def add_model(model_id: str, name: str, provider: str = "ollama", location: str = "local", size: str = "") -> dict:
    with connect() as c:
        c.execute(
            "INSERT INTO models (id, name, provider, location, size, sort) VALUES (?,?,?,?,?,10) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, provider = excluded.provider, "
            "location = excluded.location, size = excluded.size",
            (model_id.strip(), name.strip() or model_id.strip(), provider, location, size),
        )
        row = c.execute("SELECT * FROM models WHERE id = ?", (model_id.strip(),)).fetchone()
    return dict(row) if row else {}


def delete_model(model_id: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM models WHERE id = ?", (model_id,)).rowcount > 0


def clear_messages(conversation_id: str = "default") -> None:
    with connect() as c:
        c.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))


def live_telemetry_counts() -> dict:
    with connect() as c:
        audit_total = c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        audit_ok = c.execute("SELECT COUNT(*) FROM audit_log WHERE ok = 1").fetchone()[0]
        msg_total = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        mem_total = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        job_total = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    return {
        "audit_total": audit_total,
        "audit_ok": audit_ok,
        "messages": msg_total,
        "memories": mem_total,
        "jobs": job_total,
    }


def get_model(model_id: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
    return dict(row) if row else None


def _card(key: str, label: str, value: float, fmt: str, icon: str, color: str) -> dict:
    return {"id": key, "label": label, "value": round(float(value), 2), "format": fmt, "icon": icon, "color": color}


def dashboard_stats() -> dict:
    """JSON payload for the usage cards and the 'Your business, in focus' cards (base currency USD)."""
    f = get_finance()
    net = f["monthly_income"] - f["monthly_expenses"]
    progress_pct = (f["revenue"] / f["revenue_target"] * 100) if f["revenue_target"] else 0

    tabs: dict[str, list[dict]] = {
        "Overview": [
            _card("cash_on_hand", "Cash on hand", f["cash_on_hand"], "currency", "fas fa-wallet", "green"),
            _card("monthly_income", "Monthly income", f["monthly_income"], "currency", "fas fa-chart-line", "blue"),
            _card("revenue_target", "Revenue target", f["revenue_target"], "currency", "fas fa-bullseye", "purple"),
        ],
        "Finances": [
            _card("revenue", "Revenue (month to date)", f["revenue"], "currency", "fas fa-sack-dollar", "amber"),
            _card("monthly_expenses", "Monthly expenses", f["monthly_expenses"], "currency", "fas fa-receipt", "red"),
            _card("net_profit", "Net monthly profit", net, "currency", "fas fa-scale-balanced", "green"),
        ],
        "Progress": [
            _card("target_progress", "Revenue target progress", progress_pct, "percent", "fas fa-flag-checkered", "purple"),
        ],
        "Audience": [],
    }
    for m in get_metrics():
        tabs.setdefault(m["tab"], []).append(
            _card(m["key"], m["label"], m["value"], m["format"], m["icon"], m["color"])
        )

    return {
        "user": get_user(),
        "base_currency": f.get("base_currency", "USD"),
        "updated_at": f.get("updated_at"),
        "usage": get_usage(),
        "finance": {k: f[k] for k in ("cash_on_hand", "monthly_income", "monthly_expenses", "revenue", "revenue_target")},
        "tabs": tabs,
    }


# --- Conversations ----------------------------------------------------------

def add_message(conversation_id: str, role: str, content: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
                  (conversation_id, role, content))


def recent_messages(conversation_id: str, limit: int) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT role, content, created_at FROM messages WHERE conversation_id = ? "
                         "ORDER BY id DESC LIMIT ?", (conversation_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


# --- Memory -----------------------------------------------------------------

def add_memory(text: str, tags: str, source: str, embedding: bytes | None) -> int:
    with connect() as c:
        cur = c.execute("INSERT INTO memories (text, tags, source, embedding) VALUES (?,?,?,?)",
                        (text, tags, source, embedding))
        return int(cur.lastrowid)


def all_memories() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM memories ORDER BY id DESC").fetchall()]


def delete_memory(memory_id: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0


# --- Jobs -------------------------------------------------------------------

def add_job(name: str, prompt: str, role: str, kind: str, value: str, next_run: str | None) -> int:
    with connect() as c:
        cur = c.execute("INSERT INTO jobs (name, prompt, role, kind, value, next_run) VALUES (?,?,?,?,?,?)",
                        (name, prompt, role, kind, value, next_run))
        return int(cur.lastrowid)


def list_jobs(include_disabled: bool = False) -> list[dict]:
    q = "SELECT * FROM jobs" + ("" if include_disabled else " WHERE enabled = 1") + " ORDER BY id"
    with connect() as c:
        return [dict(r) for r in c.execute(q).fetchall()]


def due_jobs(now_iso: str) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM jobs WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?",
                         (now_iso,)).fetchall()
    return [dict(r) for r in rows]


def update_job(job_id: int, **fields) -> None:
    allowed = {"next_run", "last_run", "last_status", "enabled"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with connect() as c:
        c.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), job_id))


def delete_job(job_id: int) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount > 0


# --- Inbox ------------------------------------------------------------------

def add_inbox(title: str, body: str, source: str = "agent", job_id: int | None = None) -> dict:
    with connect() as c:
        cur = c.execute("INSERT INTO inbox (title, body, source, job_id) VALUES (?,?,?,?)",
                        (title, body, source, job_id))
        row = c.execute("SELECT * FROM inbox WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_inbox(limit: int = 50) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM inbox ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def mark_inbox_read(item_id: int | None = None) -> None:
    with connect() as c:
        if item_id is None:
            c.execute("UPDATE inbox SET read = 1")
        else:
            c.execute("UPDATE inbox SET read = 1 WHERE id = ?", (item_id,))


# --- Audit ------------------------------------------------------------------

def add_audit(actor: str, origin: str, role: str, tool: str, args: str, risk: str,
              decision: str, ok: bool, result: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO audit_log (actor, origin, role, tool, args, risk, decision, ok, result) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (actor, origin, role, tool, args, risk, decision, int(ok), result))


def list_audit(limit: int = 100) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
