# language: Python 3.11+, file: backend/app/extensions.py, target: OpenVSX .vsix + Native Agentic Extension Engine
"""Extension Manager for Agentic OS & Vanta Monaco IDE.

Supports:
  1. Searching OpenVSX Marketplace (`https://open-vsx.org/api/-/search`)
  2. Downloading & extracting `.vsix` packages (`extension/package.json`, themes, snippets, grammars)
  3. Loading native Agentic OS plugins (`manifest.json` + `tools.py`) into `app.tools.REGISTRY`
"""
from __future__ import annotations

import importlib.util
import io
import json
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

import httpx

from . import config

log = logging.getLogger("jarvis.extensions")

EXTENSIONS_DIR = config.DATA_DIR / "extensions"

BUILTIN_EXTENSIONS: list[dict[str, Any]] = [
    {
        "id": "vanta.monaco-workbench",
        "name": "Vanta Monaco Workbench Bridge",
        "publisher": "vanta",
        "version": "1.2.0",
        "description": "Fuses the Vanta Monaco multi-tab code editor, Git diff viewer, symbol-slice context engine, and persistent PowerShell PTY into Agentic OS.",
        "kind": "native",
        "enabled": True,
        "builtin": True,
        "contributes": {"tabs": ["IDE"], "themes": [], "snippets": [], "tools": []},
    },
    {
        "id": "vanta.token-optimizer",
        "name": "5-Layer Token Optimizer",
        "publisher": "vanta",
        "version": "1.0.0",
        "description": "Intent-filtered tool injection, Tier-0 zero-token fast path, history compaction, KV-cache pinning, and AST symbol-slice code context.",
        "kind": "native",
        "enabled": True,
        "builtin": True,
        "contributes": {"tabs": [], "themes": [], "snippets": [], "tools": []},
    },
]


def ensure_extensions_dir() -> Path:
    EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return EXTENSIONS_DIR


def _strip_json_comments(text: str) -> str:
    """Strip // and /* */ comments and trailing commas from VS Code theme/snippet JSONC files."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    lines = []
    for line in text.splitlines():
        # Remove // comments not inside quotes
        in_str = False
        escaped = False
        out = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and not escaped:
                escaped = True
                out.append(ch)
                i += 1
                continue
            if ch == '"' and not escaped:
                in_str = not in_str
            if not in_str and line[i:i + 2] == "//":
                break
            escaped = False
            out.append(ch)
            i += 1
        lines.append("".join(out))
    cleaned = "\n".join(lines)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned


def _inspect_installed_dir(ext_dir: Path) -> dict[str, Any] | None:
    pkg_file = ext_dir / "extension" / "package.json"
    if not pkg_file.exists():
        pkg_file = ext_dir / "package.json"
    if not pkg_file.exists():
        pkg_file = ext_dir / "manifest.json"
    if not pkg_file.exists():
        return None

    try:
        pkg = json.loads(_strip_json_comments(pkg_file.read_text(encoding="utf-8", errors="replace")))
    except Exception as exc:
        log.warning("Failed to parse %s: %s", pkg_file, exc)
        return None

    base_dir = pkg_file.parent
    publisher = pkg.get("publisher") or ext_dir.name.split(".")[0]
    name = pkg.get("name") or ext_dir.name
    ext_id = f"{publisher}.{name}" if "." not in name else name
    display_name = pkg.get("displayName") or name
    if display_name.startswith("%") and (base_dir / "package.nls.json").exists():
        try:
            nls = json.loads((base_dir / "package.nls.json").read_text(encoding="utf-8", errors="replace"))
            display_name = nls.get(display_name.strip("%"), display_name)
        except Exception:
            pass

    contributes = pkg.get("contributes") or {}
    themes = []
    for th in contributes.get("themes", []):
        rel_path = th.get("path", "")
        th_file = (base_dir / rel_path).resolve()
        themes.append({
            "label": th.get("label") or th.get("id") or "Theme",
            "uiTheme": th.get("uiTheme", "vs-dark"),
            "path": str(th_file.relative_to(ext_dir)).replace("\\", "/") if th_file.exists() else rel_path,
        })

    snippets = []
    for sn in contributes.get("snippets", []):
        snippets.append({"language": sn.get("language", "plaintext"), "path": sn.get("path", "")})

    grammars = []
    for gr in contributes.get("grammars", []):
        grammars.append({"language": gr.get("language", ""), "scopeName": gr.get("scopeName", ""), "path": gr.get("path", "")})

    return {
        "id": ext_id,
        "folder": ext_dir.name,
        "name": display_name,
        "publisher": publisher,
        "version": pkg.get("version", "1.0.0"),
        "description": pkg.get("description", "Installed .vsix extension"),
        "kind": "vsix" if (ext_dir / "extension").exists() else "native",
        "enabled": True,
        "builtin": False,
        "contributes": {
            "themes": themes,
            "snippets": snippets,
            "grammars": grammars,
            "languages": [l.get("id") for l in contributes.get("languages", []) if l.get("id")],
        },
    }


def list_installed() -> list[dict[str, Any]]:
    ensure_extensions_dir()
    items = list(BUILTIN_EXTENSIONS)
    for d in sorted(EXTENSIONS_DIR.iterdir()):
        if not d.is_dir():
            continue
        info = _inspect_installed_dir(d)
        if info:
            items.append(info)
    return items


async def search_openvsx(query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Search OpenVSX registry for .vsix extensions."""
    url = "https://open-vsx.org/api/-/search"
    params = {"query": query.strip(), "size": min(max(limit, 1), 24)}
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    results = []
    for ext in data.get("extensions", []):
        ns = ext.get("namespace", "")
        name = ext.get("name", "")
        files = ext.get("files") or {}
        results.append({
            "id": f"{ns}.{name}",
            "namespace": ns,
            "name": name,
            "displayName": ext.get("displayName") or name,
            "version": ext.get("version", ""),
            "description": ext.get("description", ""),
            "downloadCount": ext.get("downloadCount", 0),
            "downloadUrl": files.get("download", ""),
            "iconUrl": files.get("icon", ""),
        })
    return results


async def install_vsix(ext_id: str, download_url: str = "") -> dict[str, Any]:
    """Download and extract a .vsix package from OpenVSX into extensions/<ext_id>/."""
    ensure_extensions_dir()
    ext_id = ext_id.strip()
    if not download_url:
        if "." not in ext_id:
            raise ValueError("Extension ID must be 'publisher.name' (e.g. 'dracula-theme.theme-dracula').")
        ns, name = ext_id.split(".", 1)
        meta_url = f"https://open-vsx.org/api/{ns}/{name}"
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            mr = await client.get(meta_url)
            mr.raise_for_status()
            mdata = mr.json()
            download_url = (mdata.get("files") or {}).get("download", "")
            if not download_url:
                raise ValueError(f"No .vsix download URL found on OpenVSX for {ext_id}")

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(download_url)
        resp.raise_for_status()
        raw_bytes = resp.content

    safe_folder = re.sub(r"[^a-zA-Z0-9._-]", "_", ext_id)
    dest = EXTENSIONS_DIR / safe_folder
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        zf.extractall(dest)

    # If the extension contains a native tools.py, load it dynamically
    tools_py = dest / "tools.py"
    if tools_py.exists():
        try:
            spec = importlib.util.spec_from_file_location(f"ext_{safe_folder}", tools_py)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
        except Exception as exc:
            log.warning("Failed to load tools.py from %s: %s", safe_folder, exc)

    info = _inspect_installed_dir(dest)
    if not info:
        raise ValueError("Downloaded archive did not contain a valid package.json / manifest.json")
    return info


def uninstall_extension(ext_id: str) -> bool:
    ensure_extensions_dir()
    for d in EXTENSIONS_DIR.iterdir():
        if not d.is_dir():
            continue
        info = _inspect_installed_dir(d)
        if d.name == ext_id or (info and info["id"].lower() == ext_id.lower()):
            shutil.rmtree(d, ignore_errors=True)
            return True
    return False


def load_theme_payload(ext_id: str, theme_label: str = "") -> dict[str, Any]:
    """Load and convert a .vsix VS Code color theme JSON into a Monaco-compatible theme + CSS variables."""
    ensure_extensions_dir()
    for d in EXTENSIONS_DIR.iterdir():
        if not d.is_dir():
            continue
        info = _inspect_installed_dir(d)
        if not info or (d.name != ext_id and info["id"].lower() != ext_id.lower()):
            continue
        themes = info.get("contributes", {}).get("themes", [])
        if not themes:
            raise ValueError(f"Extension {ext_id} does not contribute any themes.")
        chosen = next((t for t in themes if not theme_label or t["label"] == theme_label), themes[0])
        theme_file = d / chosen["path"]
        if not theme_file.exists():
            theme_file = d / "extension" / chosen["path"]
        raw = json.loads(_strip_json_comments(theme_file.read_text(encoding="utf-8", errors="replace")))
        colors = raw.get("colors") or {}
        token_colors = raw.get("tokenColors") or []
        rules = []
        for tc in token_colors:
            scopes = tc.get("scope")
            settings = tc.get("settings") or {}
            if not scopes or not settings:
                continue
            if isinstance(scopes, str):
                scopes = [s.strip() for s in scopes.split(",")]
            for sc in scopes:
                rule: dict[str, Any] = {"token": sc}
                if settings.get("foreground"):
                    rule["foreground"] = settings["foreground"].lstrip("#")
                if settings.get("fontStyle"):
                    rule["fontStyle"] = settings["fontStyle"]
                rules.append(rule)
        return {
            "id": ext_id,
            "label": chosen["label"],
            "base": "vs-dark" if "light" not in chosen.get("uiTheme", "").lower() else "vs",
            "inherit": True,
            "rules": rules[:300],
            "colors": colors,
        }
    raise ValueError(f"Extension {ext_id} not found")
