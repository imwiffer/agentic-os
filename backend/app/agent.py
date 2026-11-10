"""Jarvis agent: turns an utterance into validated tool calls + a short spoken reply.

Flow:  text -> LLM (JSON function-calling prompt) -> plan -> validate/execute -> AgentResult
If no LLM is reachable (Ollama not running, no cloud key) a deterministic rule-based
intent parser produces the plan instead, so the dashboard is always controllable.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field

import httpx

from . import config, db

log = logging.getLogger("jarvis.agent")

SIDEBAR_TABS = ["Dashboard", "Inbox", "Chat", "Calendar", "Memory", "Design", "Website", "Agents", "Settings"]
BUSINESS_TABS = ["Overview", "Finances", "Progress", "Audience"]
ALL_TABS = SIDEBAR_TABS + BUSINESS_TABS

TAB_ALIASES = {
    "home": "Dashboard", "main": "Dashboard", "dash": "Dashboard",
    "finance": "Finances", "financials": "Finances", "money": "Finances", "budget": "Finances",
    "email": "Inbox", "emails": "Inbox", "mail": "Inbox", "messages": "Inbox",
    "schedule": "Calendar", "agenda": "Calendar", "meetings": "Calendar",
    "memories": "Memory", "knowledge": "Memory",
    "stats": "Overview", "summary": "Overview",
    "goals": "Progress", "growth": "Progress", "milestones": "Progress",
    "fans": "Audience", "followers": "Audience", "subscribers": "Audience",
    "preferences": "Settings", "config": "Settings", "configuration": "Settings",
    "site": "Website", "web": "Website", "bots": "Agents", "agent": "Agents", "designs": "Design",
}

CURRENCY_ALIASES = {
    "usd": "USD", "dollar": "USD", "dollars": "USD", "us dollars": "USD",
    "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "sterling": "GBP",
    "jpy": "JPY", "yen": "JPY",
    "cad": "CAD", "canadian": "CAD", "canadian dollars": "CAD",
}

TOOL_SPECS = [
    {"name": "open_tab", "description": "Open a sidebar section or a business tab.",
     "args": {"target": f"one of {ALL_TABS}"}},
    {"name": "check_balance", "description": "Read the current cash on hand.", "args": {}},
    {"name": "get_income", "description": "Read monthly income, revenue and revenue target.", "args": {}},
    {"name": "set_currency", "description": "Change the display currency of all money values.",
     "args": {"currency": f"one of {list(config.CURRENCY_RATES)}"}},
    {"name": "scroll_page", "description": "Scroll the main view.", "args": {"direction": "up | down | top | bottom"}},
    {"name": "select_model", "description": "Switch the active LLM.",
     "args": {"model_id": "an id from state.available_models"}},
    {"name": "speak_text", "description": "Just say something, no UI change.", "args": {"text": "what to say"}},
]

TOOL_ALIASES = {
    "open_tab": "open_tab", "open_dashboard": "open_tab", "navigate": "open_tab", "open": "open_tab",
    "check_balance": "check_balance", "get_balance": "check_balance", "balance": "check_balance",
    "get_income": "get_income", "check_income": "get_income", "income": "get_income",
    "set_currency": "set_currency", "change_currency": "set_currency",
    "scroll_page": "scroll_page", "scroll": "scroll_page",
    "select_model": "select_model", "switch_model": "select_model", "set_model": "select_model",
    "speak_text": "speak_text", "speak": "speak_text", "say": "speak_text",
}

SYSTEM_PROMPT = """You are JARVIS, the OS Controller for "Agentic OS", a voice-controlled dashboard owned by {user_name}.
You have access to these tools:
{tools}

Current UI state:
{state}

Reply to every user message with ONE valid JSON object and nothing else:
{{"actions": [{{"tool": "<tool name>", "args": {{}}}}], "response": "<short spoken reply>"}}

Rules:
- Use only the tools listed above. Use an empty "actions" list for small talk.
- "response" is read aloud: one or two short sentences, no markdown, no emojis. Address the user as sir occasionally.
- Never invent financial numbers. Call check_balance or get_income and put these placeholders in "response":
  {{cash_on_hand}}, {{monthly_income}}, {{monthly_expenses}}, {{revenue}}, {{revenue_target}}, {{net_profit}}.
- You may chain several actions, e.g. set_currency then check_balance.

Examples:
User: open finances
{{"actions": [{{"tool": "open_tab", "args": {{"target": "Finances"}}}}], "response": "Opening Finances."}}
User: how much cash do I have in euros?
{{"actions": [{{"tool": "set_currency", "args": {{"currency": "EUR"}}}}, {{"tool": "check_balance", "args": {{}}}}], "response": "You have {{cash_on_hand}} on hand, sir."}}
"""


@dataclass
class AgentResult:
    response: str
    actions: list[dict] = field(default_factory=list)  # frontend commands: {"action", "target"?, "args"?}
    engine: str = "rules"
    model: str | None = None
    tokens: int = 0
    latency_ms: int = 0
    raw_plan: str = ""
    error: str | None = None
    steps: list[dict] = field(default_factory=list)  # tool-call trace (runtime.py)
    role: str = "jarvis"

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw_plan")
        return d


# --- Normalisers ------------------------------------------------------------

def normalize_tab(name) -> str | None:
    if not name:
        return None
    key = str(name).strip().lower().removesuffix(" tab").strip()
    for tab in ALL_TABS:
        if tab.lower() == key:
            return tab
    if key in TAB_ALIASES:
        return TAB_ALIASES[key]
    for tab in ALL_TABS:
        if key.startswith(tab.lower()) or (len(key) >= 4 and tab.lower().startswith(key)):
            return tab
    return None


def normalize_currency(value) -> str | None:
    if not value:
        return None
    key = str(value).strip().lower()
    if key.upper() in config.CURRENCY_RATES:
        return key.upper()
    return CURRENCY_ALIASES.get(key)


def normalize_tool(name) -> str | None:
    if not name:
        return None
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", str(name).strip()).lower().replace(" ", "_").replace("-", "_")
    snake = re.sub(r"_+", "_", snake)
    return TOOL_ALIASES.get(snake)


def money(usd_value: float, currency: str) -> str:
    value = usd_value * config.CURRENCY_RATES.get(currency, 1.0)
    return f"{config.CURRENCY_SYMBOLS.get(currency, currency + ' ')}{value:,.0f}"


def finance_facts(currency: str) -> dict[str, str]:
    f = db.get_finance()
    f["net_profit"] = f["monthly_income"] - f["monthly_expenses"]
    keys = ("cash_on_hand", "monthly_income", "monthly_expenses", "revenue", "revenue_target", "net_profit")
    return {k: money(f[k], currency) for k in keys}


def match_model(query: str, models: list[dict]) -> dict | None:
    q = (query or "").lower()
    if not q:
        return None
    for m in models:
        if m["id"].lower() == q or m["name"].lower() == q:
            return m
    want_cloud = "cloud" in q
    best, best_score = None, 0
    for m in models:
        tokens = re.findall(r"[a-z]+|\d+(?:\.\d+)?b?", m["name"].lower())
        if not tokens or tokens[0] not in q:  # family name (llama/qwen/mistral) must be mentioned
            continue
        score = sum(1 for t in tokens if t in q) + (0.5 if (m["location"] == "cloud") == want_cloud else 0)
        if score > best_score:
            best, best_score = m, score
    return best


# --- Models -----------------------------------------------------------------

async def _ollama_tags() -> list[str] | None:
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"{config.OLLAMA_URL}/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return None


def _tag_matches(model_id: str, tags: list[str]) -> bool:
    return model_id in tags or f"{model_id}:latest" in tags


async def list_models() -> dict:
    models = db.list_models()
    tags = await _ollama_tags()
    cloud_ready = bool(config.OPENAI_API_KEY)
    known = {m["id"] for m in models}
    for m in models:
        if m["location"] == "local":
            m["available"] = bool(tags) and _tag_matches(m["id"], tags)
            m["status"] = "ready" if m["available"] else ("not pulled" if tags is not None else "ollama offline")
        else:
            m["available"] = cloud_ready
            m["status"] = "ready" if cloud_ready else "no API key"
    for tag in tags or []:
        base = tag.removesuffix(":latest")
        if tag not in known and base not in known:
            models.append({"id": tag, "name": base.replace(":", " ").title(), "provider": "ollama",
                           "location": "local", "size": "", "available": True, "status": "ready",
                           "discovered": True})
    return {"models": models, "default": config.DEFAULT_MODEL,
            "ollama_online": tags is not None, "cloud_ready": cloud_ready}


def resolve_model(model_id: str | None) -> dict:
    model_id = model_id or config.DEFAULT_MODEL
    return db.get_model(model_id) or {"id": model_id, "name": model_id, "provider": "ollama", "location": "local"}


async def _call_llm(model: dict, messages: list[dict]) -> tuple[str, int]:
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as client:
        if model["location"] == "local":
            r = await client.post(f"{config.OLLAMA_URL}/api/chat", json={
                "model": model["id"], "messages": messages, "stream": False, "format": "json",
                "options": {"temperature": 0.1},
            })
            r.raise_for_status()
            d = r.json()
            return d["message"]["content"], int(d.get("eval_count", 0)) + int(d.get("prompt_eval_count", 0))

        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set for cloud models")
        r = await client.post(
            f"{config.OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            json={"model": model["id"], "messages": messages, "temperature": 0.1,
                  "response_format": {"type": "json_object"}},
        )
        r.raise_for_status()
        d = r.json()
        return d["choices"][0]["message"]["content"], int(d.get("usage", {}).get("total_tokens", 0))


def _extract_json(raw: str) -> dict | None:
    if not raw:
        return None
    s = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "actions" not in obj and "action" in obj:  # shorthand {"action": "openTab", "target": "Finances"}
        obj = {"actions": [{"tool": obj["action"], "args": obj.get("args") or {"target": obj.get("target")}}],
               "response": obj.get("response", "")}
    actions = obj.get("actions") or []
    if not isinstance(actions, list):
        actions = [actions]
    return {"actions": [a for a in actions if isinstance(a, dict)], "response": str(obj.get("response") or "")}


# --- Rule-based fallback ----------------------------------------------------

def _find_tab(text: str) -> str | None:
    for tab in ALL_TABS:
        if re.search(rf"\b{tab.lower()}\b", text):
            return tab
    for alias, tab in TAB_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return tab
    return None


def rule_based_plan(text: str, models: list[dict]) -> dict:
    t = text.lower().strip()
    actions: list[dict] = []
    response = ""

    currency = next((v for k, v in sorted(CURRENCY_ALIASES.items(), key=lambda kv: -len(kv[0]))
                     if re.search(rf"\b{re.escape(k)}\b", t)), None)
    if currency and re.search(r"\b(currency|switch|change|set|show|convert|display|in|to)\b", t):
        actions.append({"tool": "set_currency", "args": {"currency": currency}})

    if re.search(r"\b(use|switch|select|load|change|activate)\b", t) and re.search(r"\b(model|llama|qwen|mistral)\b", t):
        m = match_model(t, models)
        if m:
            actions.append({"tool": "select_model", "args": {"model_id": m["id"]}})

    if "scroll" in t or re.search(r"\b(go|jump) to the (top|bottom)\b", t):
        direction = next((d for d in ("top", "bottom", "up", "down") if re.search(rf"\b{d}\b", t)), "down")
        actions.append({"tool": "scroll_page", "args": {"direction": direction}})

    tab = _find_tab(t)
    nav_verb = re.search(r"\b(open|show|go to|goto|switch to|navigate to|take me to|display|bring up|view|pull up)\b", t)
    if tab and (nav_verb or len(t.split()) <= 2) and not any(a["tool"] == "select_model" for a in actions):
        actions.append({"tool": "open_tab", "args": {"target": tab}})

    if re.search(r"\b(balance|cash|how much money|in the bank)\b", t):
        actions.append({"tool": "check_balance", "args": {}})
    if re.search(r"\b(income|revenue|earn(ed|ing|ings)?|sales|making)\b", t):
        actions.append({"tool": "get_income", "args": {}})

    if not actions:
        if re.search(r"\b(hello|hi|hey|morning|evening|afternoon)\b", t):
            response = "Good to see you, sir. All systems are online."
        elif re.search(r"\b(who are you|what can you do|help)\b", t):
            response = ("I'm Jarvis. I can open any section, read your balance or income, "
                        "change currency, scroll, and switch models.")
        elif re.search(r"\b(thank|thanks)\b", t):
            response = "Always a pleasure, sir."
        else:
            response = "I don't have a tool for that yet. Try 'open finances' or 'what's my balance'."
    return {"actions": actions, "response": response}


# --- Execution --------------------------------------------------------------

def execute_plan(plan: dict, state: dict, models: list[dict]) -> tuple[str, list[dict]]:
    currency = normalize_currency(state.get("currency")) or "USD"
    commands: list[dict] = []
    info_notes: list[str] = []   # spoken when the LLM gave no response
    data_notes: list[str] = []   # spoken facts (appended if the response contains no number)
    errors: list[str] = []
    response = (plan.get("response") or "").strip()

    for call in plan.get("actions", []):
        tool = normalize_tool(call.get("tool") or call.get("name"))
        args = call.get("args") or call.get("arguments") or {}
        if not isinstance(args, dict):
            args = {"target": args}

        if tool == "open_tab":
            tab = normalize_tab(args.get("target") or args.get("tab_name") or args.get("tab") or args.get("name"))
            if tab:
                commands.append({"action": "openTab", "target": tab})
                info_notes.append(f"Opening {tab}.")
            else:
                errors.append("I couldn't find that section.")
        elif tool == "check_balance":
            facts = finance_facts(currency)
            commands.append({"action": "checkBalance", "target": "cash_on_hand"})
            data_notes.append(f"You have {facts['cash_on_hand']} in cash on hand.")
        elif tool == "get_income":
            facts = finance_facts(currency)
            commands.append({"action": "getIncome", "target": "monthly_income"})
            data_notes.append(f"Monthly income is {facts['monthly_income']}, with {facts['revenue']} revenue "
                              f"this month against a {facts['revenue_target']} target.")
        elif tool == "set_currency":
            cur = normalize_currency(args.get("currency") or args.get("target"))
            if cur:
                currency = cur
                commands.append({"action": "setCurrency", "target": cur})
                info_notes.append(f"Displaying values in {cur}.")
            else:
                errors.append("That currency isn't supported.")
        elif tool == "scroll_page":
            direction = str(args.get("direction") or args.get("target") or "down").lower()
            direction = direction if direction in ("up", "down", "top", "bottom") else "down"
            commands.append({"action": "scrollPage", "target": direction})
            info_notes.append(f"Scrolling {direction}.")
        elif tool == "select_model":
            m = match_model(str(args.get("model_id") or args.get("model") or args.get("target") or ""), models)
            if m:
                commands.append({"action": "selectModel", "target": m["id"]})
                info_notes.append(f"Switching to {m['name']}.")
            else:
                errors.append("I couldn't find that model.")
        elif tool == "speak_text":
            response = response or str(args.get("text") or "")
        else:
            log.warning("Ignoring unknown tool call: %s", call)

    facts = finance_facts(currency)
    response = re.sub(r"\{(\w+)\}", lambda m: facts.get(m.group(1), m.group(0)), response)

    if not response:
        response = " ".join(info_notes + data_notes) or "Done."
    elif data_notes and not re.search(r"\d", response):
        response = f"{response} {' '.join(data_notes)}"
    if errors:
        response = f"{response} {' '.join(errors)}".strip()
    return response, commands


async def run(text: str, state: dict | None = None, history: list[dict] | None = None) -> AgentResult:
    started = time.perf_counter()
    state = dict(state or {})
    history = history or []
    models = db.list_models()
    model = resolve_model(state.get("model"))

    public_state = {
        "current_tab": state.get("currentTab", "Dashboard"),
        "business_tab": state.get("businessTab", "Overview"),
        "currency": state.get("currency", "USD"),
        "active_model": model["id"],
        "available_models": [m["id"] for m in models],
    }
    system = SYSTEM_PROMPT.format(
        user_name=db.get_user().get("name", "the user"),
        tools=json.dumps(TOOL_SPECS, indent=1),
        state=json.dumps(public_state),
    )
    messages = [{"role": "system", "content": system}, *history[-config.HISTORY_TURNS * 2:],
                {"role": "user", "content": text}]

    plan, engine, tokens, error, raw = None, "rules", 0, None, ""
    try:
        raw, tokens = await _call_llm(model, messages)
        plan = _extract_json(raw)
        if plan is None:
            error = "LLM returned invalid JSON; used rule engine"
        else:
            engine = "llm"
    except Exception as exc:  # LLM offline / misconfigured -> deterministic fallback
        error = f"LLM unavailable ({type(exc).__name__}); used rule engine"
        log.info("%s: %s", error, exc)

    if plan is None:
        plan = rule_based_plan(text, models)
        raw = json.dumps(plan)
        tokens = tokens or len(text.split()) + 12

    response, commands = execute_plan(plan, state, models)
    return AgentResult(
        response=response, actions=commands, engine=engine, model=model["id"], tokens=tokens,
        latency_ms=int((time.perf_counter() - started) * 1000), raw_plan=raw, error=error,
    )
