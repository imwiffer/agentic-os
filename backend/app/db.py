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
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            _seed(c)


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
    c.executemany(
        "INSERT INTO models (id, name, provider, location, size, sort) VALUES (?,?,?,?,?,?)",
        [
            ("qwen3.8:27b", "Qwen 3.8 27B", "ollama", "local", "27B", 1),
            ("llama3:8b", "Llama 3 8B", "ollama", "local", "8B", 2),
            ("mistral:7b", "Mistral 7B", "ollama", "local", "7B", 3),
            ("meta-llama/llama-3.1-70b-instruct", "Llama 3.1 70B", "openai-compatible", "cloud", "70B", 4),
            ("qwen/qwen-2.5-72b-instruct", "Qwen 2.5 72B", "openai-compatible", "cloud", "72B", 5),
            ("mistralai/mistral-large", "Mistral Large", "openai-compatible", "cloud", "123B", 6),
        ],
    )


# --- Queries ----------------------------------------------------------------

def get_user(user_id: int = 1) -> dict:
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else {}


def get_finance(user_id: int = 1) -> dict:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM finance WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
    return dict(row) if row else {}


def get_metrics() -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM metrics ORDER BY tab, sort").fetchall()
    return [dict(r) for r in rows]


def get_usage() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM usage ORDER BY id").fetchall()]


def list_models() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM models ORDER BY sort").fetchall()]


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
