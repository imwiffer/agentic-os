"""Web search + fetch, with an SSRF guard so the agent cannot reach localhost, Ollama, or tailnet hosts."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import httpx

from .. import config
from . import ToolContext, ToolError, tool

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 JarvisOS"
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale addresses live here
_TS_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def _ip_forbidden(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast
            or addr.is_unspecified or addr in (_CGNAT if addr.version == 4 else _TS_V6))


async def assert_public_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ToolError("Only http(s) URLs are allowed.")
    host = p.hostname
    if host.endswith((".ts.net", ".local", ".internal", "localhost")):
        raise ToolError("Internal hosts are off-limits.")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ToolError(f"Could not resolve {host}.") from exc
    if any(_ip_forbidden(info[4][0]) for info in infos):
        raise ToolError("That address points to a private or internal network.")


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


async def fetch_text(url: str, limit: int | None = None) -> str:
    limit = limit or config.TOOL_OUTPUT_LIMIT
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers={"User-Agent": UA}) as client:
        for _ in range(4):  # follow redirects manually so every hop is re-validated
            await assert_public_url(url)
            r = await client.get(url)
            if r.is_redirect and "location" in r.headers:
                url = urljoin(url, r.headers["location"])
                continue
            break
        else:
            raise ToolError("Too many redirects.")
    if r.status_code >= 400:
        raise ToolError(f"HTTP {r.status_code} from {url}")
    ctype = r.headers.get("content-type", "")
    if not any(t in ctype for t in ("text", "json", "xml")):
        raise ToolError(f"Unsupported content type: {ctype or 'unknown'}")
    body = r.text[:500_000]
    text = html_to_text(body) if "html" in ctype else body
    return text[:limit]


async def _search_brave(query: str, n: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": n},
                             headers={"X-Subscription-Token": config.BRAVE_API_KEY, "Accept": "application/json"})
        r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": html_to_text(x.get("description", ""))}
            for x in r.json().get("web", {}).get("results", [])[:n]]


async def _search_ddg(query: str, n: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}) as client:
        r = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
        r.raise_for_status()
    results = []
    blocks = re.findall(r'(?s)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
                        r.text)
    for href, title, rest in blocks:
        href = html.unescape(href)
        if "uddg=" in href:
            href = parse_qs(urlparse(href).query).get("uddg", [href])[0]
        if "duckduckgo.com/y.js" in href:  # ads
            continue
        snip = re.search(r'(?s)class="result__snippet"[^>]*>(.*?)</a>', rest)
        results.append({"title": html_to_text(title), "url": href, "snippet": html_to_text(snip.group(1)) if snip else ""})
        if len(results) >= n:
            break
    return results


@tool("web_search", "Search the web. Returns titles, URLs and snippets.", {"query": "search terms"})
async def web_search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query") or args.get("q") or "").strip()
    if not query:
        raise ToolError("Empty search query.")
    try:
        results = await (_search_brave(query, 5) if config.BRAVE_API_KEY else _search_ddg(query, 5))
    except httpx.HTTPError as exc:
        raise ToolError(f"Search failed ({type(exc).__name__}).") from exc
    if not results:
        return f"No results for {query!r}. (https://duckduckgo.com/?q={quote_plus(query)})"
    return "\n".join(f"{i}. {r['title']} - {r['url']}\n   {r['snippet'][:300]}" for i, r in enumerate(results, 1))


@tool("web_fetch", "Download a public web page and return its readable text.", {"url": "https://..."})
async def web_fetch(args: dict, ctx: ToolContext) -> str:
    url = str(args.get("url") or args.get("target") or "").strip()
    try:
        return await fetch_text(url)
    except httpx.HTTPError as exc:
        raise ToolError(f"Could not fetch {url} ({type(exc).__name__}).") from exc
