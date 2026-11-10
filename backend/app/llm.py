"""LLM + embedding client: Ollama first, OpenAI-compatible cloud as automatic fallback."""
from __future__ import annotations

import logging
from array import array

import httpx

from . import config, db

log = logging.getLogger("jarvis.llm")


class LLMError(RuntimeError):
    pass


def resolve_model(model_id: str | None) -> dict:
    model_id = model_id or config.DEFAULT_MODEL
    found = db.get_model(model_id)
    if found:
        return found
    location = "cloud" if "/" in model_id else "local"  # provider/model ids are cloud (OpenRouter style)
    return {"id": model_id, "name": model_id, "provider": "ollama" if location == "local" else "openai-compatible",
            "location": location}


async def _call(model: dict, messages: list[dict], json_mode: bool) -> tuple[str, int]:
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as client:
        if model["location"] == "local":
            body = {"model": model["id"], "messages": messages, "stream": False,
                    "options": {"temperature": 0.1, "num_ctx": 8192}, "keep_alive": "30m"}
            if json_mode:
                body["format"] = "json"
            r = await client.post(f"{config.OLLAMA_URL}/api/chat", json=body)
            r.raise_for_status()
            d = r.json()
            return d["message"]["content"], int(d.get("eval_count", 0)) + int(d.get("prompt_eval_count", 0))

        if not config.OPENAI_API_KEY:
            raise LLMError("OPENAI_API_KEY not set for cloud models")
        body = {"model": model["id"], "messages": messages, "temperature": 0.1}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = await client.post(f"{config.OPENAI_BASE_URL}/chat/completions",
                              headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"}, json=body)
        r.raise_for_status()
        d = r.json()
        return d["choices"][0]["message"]["content"], int(d.get("usage", {}).get("total_tokens", 0))


async def chat(model_id: str | None, messages: list[dict], json_mode: bool = True) -> tuple[str, int, str]:
    """Returns (content, tokens, model_id_used). Raises LLMError if every option failed."""
    primary = resolve_model(model_id)
    candidates = [primary]
    if (primary["location"] == "local" and config.OPENAI_API_KEY and config.FALLBACK_MODEL
            and config.FALLBACK_MODEL != primary["id"]):
        candidates.append(resolve_model(config.FALLBACK_MODEL))

    errors = []
    for model in candidates:
        try:
            content, tokens = await _call(model, messages, json_mode)
            return content, tokens, model["id"]
        except Exception as exc:  # timeout, connection refused, HTTP error, bad payload
            errors.append(f"{model['id']}: {type(exc).__name__}")
            log.info("LLM %s failed: %s", model["id"], exc)
    raise LLMError("; ".join(errors))


# --- Embeddings -------------------------------------------------------------

async def embed(text: str) -> bytes | None:
    """float32 vector as bytes, or None when the embedding model is unavailable."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{config.OLLAMA_URL}/api/embed",
                                  json={"model": config.EMBED_MODEL, "input": text[:4000]})
            r.raise_for_status()
            vec = r.json()["embeddings"][0]
            return array("f", vec).tobytes()
    except Exception as exc:
        log.debug("Embedding unavailable: %s", exc)
        return None
