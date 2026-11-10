"""Dashboard (browser) tools: they emit UI commands that frontend/js/tools.js executes."""
from __future__ import annotations

from .. import agent, config, db
from . import ToolContext, ToolError, tool


def _currency(ctx: ToolContext) -> str:
    return agent.normalize_currency(ctx.state.get("currency")) or "USD"


@tool("open_tab", "Open a dashboard section or business tab.", {"target": f"one of {agent.ALL_TABS}"},
      observe=False, kind="ui")
async def open_tab(args: dict, ctx: ToolContext) -> str:
    tab = agent.normalize_tab(args.get("target") or args.get("tab") or args.get("tab_name") or args.get("name"))
    if not tab:
        raise ToolError("I couldn't find that section.")
    ctx.commands.append({"action": "openTab", "target": tab})
    ctx.state["businessTab" if tab in agent.BUSINESS_TABS else "currentTab"] = tab
    return f"Opening {tab}."


@tool("check_balance", "Read the current cash on hand.", kind="ui")
async def check_balance(args: dict, ctx: ToolContext) -> str:
    facts = agent.finance_facts(_currency(ctx))
    ctx.commands.append({"action": "checkBalance", "target": "cash_on_hand"})
    return f"You have {facts['cash_on_hand']} in cash on hand."


@tool("get_income", "Read monthly income, expenses, revenue and revenue target.", kind="ui")
async def get_income(args: dict, ctx: ToolContext) -> str:
    f = agent.finance_facts(_currency(ctx))
    ctx.commands.append({"action": "getIncome", "target": "monthly_income"})
    return (f"Monthly income is {f['monthly_income']}, expenses {f['monthly_expenses']}, net profit {f['net_profit']}, "
            f"with {f['revenue']} revenue this month against a {f['revenue_target']} target.")


@tool("set_currency", "Change the display currency of all money values.",
      {"currency": f"one of {list(config.CURRENCY_RATES)}"}, observe=False, kind="ui")
async def set_currency(args: dict, ctx: ToolContext) -> str:
    cur = agent.normalize_currency(args.get("currency") or args.get("target"))
    if not cur:
        raise ToolError("That currency isn't supported.")
    ctx.commands.append({"action": "setCurrency", "target": cur})
    ctx.state["currency"] = cur
    return f"Displaying values in {cur}."


@tool("scroll_page", "Scroll the main view.", {"direction": "up | down | top | bottom"}, observe=False, kind="ui")
async def scroll_page(args: dict, ctx: ToolContext) -> str:
    direction = str(args.get("direction") or args.get("target") or "down").lower()
    direction = direction if direction in ("up", "down", "top", "bottom") else "down"
    ctx.commands.append({"action": "scrollPage", "target": direction})
    return f"Scrolling {direction}."


@tool("select_model", "Switch the active LLM.", {"model_id": "an id from the available models"},
      observe=False, kind="ui")
async def select_model(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("model_id") or args.get("model") or args.get("target") or "")
    m = agent.match_model(query, db.list_models())
    if not m:
        raise ToolError("I couldn't find that model.")
    ctx.commands.append({"action": "selectModel", "target": m["id"]})
    ctx.state["model"] = m["id"]
    return f"Switching to {m['name']}."


@tool("speak_text", "Just say something, no UI change.", {"text": "what to say"}, observe=False, kind="ui")
async def speak_text(args: dict, ctx: ToolContext) -> str:
    return str(args.get("text") or "")
