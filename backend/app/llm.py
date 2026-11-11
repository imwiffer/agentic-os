"""LLM + embedding client: Remote VPS Ollama primary -> Local Ollama / AirLLM failsafe -> Cloud fallback."""
from __future__ import annotations

import asyncio
import logging
import time
from array import array

import httpx

from . import config, db

log = logging.getLogger("jarvis.llm")

_TAGS_CACHE: dict[str, tuple[float, list[str]]] = {}
_TAGS_TTL = 30.0


class LLMError(RuntimeError):
    pass


async def _installed_ollama_models(base_url: str) -> list[str]:
    now = time.monotonic()
    cached = _TAGS_CACHE.get(base_url)
    if cached and (now - cached[0]) < _TAGS_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{base_url.rstrip('/')}/api/tags")
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
            _TAGS_CACHE[base_url] = (now, names)
            return names
    except Exception as exc:
        log.debug("Could not fetch Ollama tags from %s: %s", base_url, exc)
        return []


def resolve_model(model_id: str | None) -> dict:
    model_id = (model_id or config.DEFAULT_MODEL).strip()
    # Map legacy default model name to the model pulled on the VPS
    if model_id in ("qwen2.5:7b-instruct", "qwen2.5:7b"):
        model_id = config.DEFAULT_MODEL
    found = db.get_model(model_id)
    if found:
        return found
    location = "cloud" if "/" in model_id and not model_id.startswith("criscar22/") else "local"
    provider = "airllm" if model_id.startswith("criscar22/") else ("ollama" if location == "local" else "openai-compatible")
    return {"id": model_id, "name": model_id, "provider": provider, "location": location}


async def _call_ollama(base_url: str, model_id: str, messages: list[dict], json_mode: bool) -> tuple[str, int, str]:
    installed = await _installed_ollama_models(base_url)
    target_model = model_id
    # If the target is the CPU-only VPS and a fast CPU model (0.5b / 1.5b) is installed, prefer it unless 7b was explicitly requested and no small model exists
    if installed:
        if target_model not in installed:
            for small in ("qwen2.5:0.5b", "qwen2.5-coder:1.5b", config.DEFAULT_MODEL):
                if small in installed:
                    target_model = small
                    break
            else:
                target_model = installed[0]
            log.info("Model %s not installed on %s; auto-routing to installed model %s", model_id, base_url, target_model)
        elif ("147.189.168.95" in base_url or "100.120.101.8" in base_url) and target_model in ("qwen2.5-coder:7b", "qwen2.5:7b-instruct"):
            # If CPU-only VPS has qwen2.5:0.5b or 1.5b installed, auto-select it to prevent CPU OOM/stall
            for fast_cpu in ("qwen2.5:0.5b", "qwen2.5-coder:1.5b"):
                if fast_cpu in installed:
                    log.info("Auto-selecting CPU-optimized model %s on VPS %s (instead of %s)", fast_cpu, base_url, target_model)
                    target_model = fast_cpu
                    break

    is_small_cpu = any(tag in target_model for tag in ("0.5b", "1.5b", "1b", "3b"))
    timeout = httpx.Timeout(config.LLM_TIMEOUT, connect=6.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        body: dict = {
            "model": target_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_ctx": 1536 if is_small_cpu else 2048,
                "num_predict": 220 if is_small_cpu else 300,
            },
            "keep_alive": "24h",
        }
        if json_mode:
            body["format"] = "json"
        r = await client.post(f"{base_url.rstrip('/')}/api/chat", json=body)
        r.raise_for_status()
        d = r.json()
        tokens = int(d.get("eval_count", 0)) + int(d.get("prompt_eval_count", 0))
        return d["message"]["content"], tokens, target_model


async def _call_vanta_airllm(model_id: str, messages: list[dict]) -> tuple[str, int]:
    """Bridge to the local Vanta IDE AirLLM worker on 127.0.0.1:7777 as failsafe."""
    base = config.VANTA_AIRLLM_URL
    timeout = httpx.Timeout(config.LLM_TIMEOUT, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        poll0 = await client.get(f"{base}/api/ai/poll", params={"cursor": 0})
        poll0.raise_for_status()
        cursor = int(poll0.json().get("cursor", 0))

        chat_res = await client.post(
            f"{base}/api/ai/chat",
            json={
                "provider": "local",
                "mode": "chat",
                "localModel": model_id or config.FALLBACK_MODEL,
                "messages": [m for m in messages if m.get("role") in ("user", "assistant")],
                "maxTokens": 512,
                "temperature": 0.2,
                "agentTools": False,
            },
        )
        chat_res.raise_for_status()

        for _ in range(180):
            await asyncio.sleep(0.5)
            pr = await client.get(f"{base}/api/ai/poll", params={"cursor": cursor})
            pr.raise_for_status()
            pdata = pr.json()
            cursor = int(pdata.get("cursor", cursor))
            for ev in pdata.get("events", []):
                if ev.get("type") == "reply":
                    txt = str(ev.get("text") or "")
                    return txt, max(16, len(txt.split()) * 2)
                if ev.get("type") == "error":
                    raise LLMError(str(ev.get("msg") or "AirLLM worker error"))
        raise LLMError("AirLLM failsafe timed out")


async def _call_cloud(model_id: str, messages: list[dict], json_mode: bool) -> tuple[str, int]:
    if not config.OPENAI_API_KEY:
        raise LLMError("OPENAI_API_KEY not set for cloud models")
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as client:
        body: dict = {"model": model_id, "messages": messages, "temperature": 0.1}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = await client.post(
            f"{config.OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            json=body,
        )
        r.raise_for_status()
        d = r.json()
        return d["choices"][0]["message"]["content"], int(d.get("usage", {}).get("total_tokens", 0))


async def chat(model_id: str | None, messages: list[dict], json_mode: bool = True) -> tuple[str, int, str]:
    """Returns (content, tokens, model_id_used). Automatic failsafe from VPS Ollama -> Local -> Cloud."""
    primary = resolve_model(model_id)
    errors: list[str] = []

    # 1. If user explicitly selected AirLLM failsafe model
    if primary.get("provider") == "airllm":
        try:
            content, tokens = await _call_vanta_airllm(primary["id"], messages)
            return content, tokens, f"{primary['id']} (airllm)"
        except Exception as exc:
            errors.append(f"airllm:{type(exc).__name__}")

    # 2. If primary is local/VPS Ollama
    if primary["location"] == "local" and primary.get("provider") != "airllm":
        try:
            content, tokens, actual_model = await _call_ollama(config.OLLAMA_URL, primary["id"], messages, json_mode)
            return content, tokens, actual_model
        except Exception as exc:
            errors.append(f"vps-ollama({config.OLLAMA_URL}/{primary['id']}):{type(exc).__name__}:{exc}")
            log.warning("Primary VPS Ollama (%s) failed for %s: %r", config.OLLAMA_URL, primary["id"], exc)

        # Failsafe Chain when VPS Ollama is unreachable
        if config.FAILSAFE_ENABLED:
            if config.FAILSAFE_OLLAMA_URL and config.FAILSAFE_OLLAMA_URL != config.OLLAMA_URL:
                try:
                    content, tokens, actual_model = await _call_ollama(config.FAILSAFE_OLLAMA_URL, primary["id"], messages, json_mode)
                    return content, tokens, f"{actual_model} (local-failsafe)"
                except Exception as exc:
                    errors.append(f"local-ollama:{type(exc).__name__}")

            try:
                content, tokens = await _call_vanta_airllm(config.FALLBACK_MODEL, messages)
                return content, tokens, f"{config.FALLBACK_MODEL} (airllm-failsafe)"
            except Exception as exc:
                errors.append(f"airllm-failsafe:{type(exc).__name__}")

    # 3. Cloud model (either selected directly or as final fallback)
    if primary["location"] == "cloud" or (config.FAILSAFE_ENABLED and config.OPENAI_API_KEY):
        cloud_id = primary["id"] if primary["location"] == "cloud" else "qwen/qwen-2.5-72b-instruct"
        try:
            content, tokens = await _call_cloud(cloud_id, messages, json_mode)
            return content, tokens, cloud_id
        except Exception as exc:
            errors.append(f"cloud({cloud_id}):{type(exc).__name__}")

    raise LLMError("; ".join(errors) or "no LLM available")


# --- Embeddings -------------------------------------------------------------

async def embed(text: str) -> bytes | None:
    """float32 vector as bytes, or None when the embedding model is unavailable."""
    for base in (config.OLLAMA_URL, config.FAILSAFE_OLLAMA_URL):
        if not base:
            continue
        installed = await _installed_ollama_models(base)
        if not any(m.startswith(config.EMBED_MODEL) for m in installed):
            continue
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.post(
                    f"{base.rstrip('/')}/api/embed",
                    json={"model": config.EMBED_MODEL, "input": text[:4000]},
                )
                r.raise_for_status()
                vec = r.json()["embeddings"][0]
                return array("f", vec).tobytes()
        except Exception as exc:
            log.debug("Embedding unavailable on %s: %s", base, exc)
    return None
