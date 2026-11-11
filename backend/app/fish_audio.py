# language: Python 3.11+, file: backend/app/fish_audio.py, target: Fish Audio Agents & TTS Bridge
"""Fish Audio Integration for Agentic OS.

Supports:
1. Fish Audio Conversational Voice Agents API:
   - GET   /v1/agent/agents
   - POST  /v1/agent/agents
   - PATCH /v1/agent/agents/{agent_id}/config
   - POST  /v1/agent/agents/{agent_id}/publish
   - POST  /v1/agent/sessions
2. Fish Audio High-Fidelity Real-Time Text-to-Speech (s2.1-pro / drama-3):
   - POST  https://api.fish.audio/v1/tts
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import httpx

from . import config

log = logging.getLogger("jarvis.fish_audio")


def _headers(api_key: str | None = None) -> dict[str, str]:
    key = api_key or config.FISH_AUDIO_API_KEY
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def validate_api_key(api_key: str | None = None) -> dict[str, Any]:
    """Test Fish Audio API key authentication against /v1/agent/agents or /model."""
    key = api_key or config.FISH_AUDIO_API_KEY
    if not key:
        return {"ok": False, "error": "No API key provided."}

    url = f"{config.FISH_AUDIO_BASE_URL}/v1/agent/agents"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url, headers=_headers(key))
            if res.status_code == 200:
                data = res.json()
                items = data.get("agents") or data.get("items") or []
                return {"ok": True, "agents_count": len(items), "data": data}
            if res.status_code in (401, 403):
                return {"ok": False, "status_code": res.status_code, "error": "Invalid API key or unauthorized."}
            # Fallback probe to /model if agent permissions differ
            r_model = await client.get(f"{config.FISH_AUDIO_BASE_URL}/model", headers=_headers(key))
            if r_model.status_code == 200:
                return {"ok": True, "models_count": len(r_model.json().get("items", []))}
            return {"ok": False, "status_code": res.status_code, "error": res.text}
    except Exception as exc:
        log.warning("Fish Audio validation error: %s", exc)
        return {"ok": False, "error": str(exc)}


async def list_agents(api_key: str | None = None) -> list[dict[str, Any]]:
    """List all conversational voice agents configured on Fish Audio."""
    url = f"{config.FISH_AUDIO_BASE_URL}/v1/agent/agents"
    async with httpx.AsyncClient(timeout=12.0) as client:
        res = await client.get(url, headers=_headers(api_key))
        res.raise_for_status()
        data = res.json()
        return data.get("agents") or data.get("items") or [] if isinstance(data, dict) else data


async def create_agent(
    name: str = "JARVIS Voice Agent",
    system_prompt: str = "You are JARVIS, an autonomous agentic operating system assistant.",
    first_message: str = "Hello dj, JARVIS voice link is established.",
    tts_model: str = "s2.1-pro",
    voice_id: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Create and configure a new voice agent on Fish Audio."""
    url = f"{config.FISH_AUDIO_BASE_URL}/v1/agent/agents"
    payload = {
        "name": name,
        "prompt": {
            "system_prompt": system_prompt,
            "first_message": first_message,
        },
        "voice": {
            "tts_model": tts_model or config.FISH_AUDIO_MODEL,
        },
    }
    if voice_id:
        payload["voice"]["voice_id"] = voice_id

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(url, headers=_headers(api_key), json=payload)
        res.raise_for_status()
        return res.json()


async def update_agent_config(
    agent_id: str,
    prompt: dict[str, Any] | None = None,
    voice: dict[str, Any] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Update draft settings for a voice agent."""
    url = f"{config.FISH_AUDIO_BASE_URL}/v1/agent/agents/{agent_id}/config"
    payload: dict[str, Any] = {}
    if prompt:
        payload["prompt"] = prompt
    if voice:
        payload["voice"] = voice

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.patch(url, headers=_headers(api_key), json=payload)
        res.raise_for_status()
        return res.json()


async def publish_agent(
    agent_id: str,
    version_title: str = "Production Build",
    version_description: str = "Agentic OS Voice Bridge",
    api_key: str | None = None,
) -> dict[str, Any]:
    """Publish a draft agent into an active production version."""
    url = f"{config.FISH_AUDIO_BASE_URL}/v1/agent/agents/{agent_id}/publish"
    payload = {
        "version_title": version_title,
        "version_description": version_description,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(url, headers=_headers(api_key), json=payload)
        res.raise_for_status()
        return res.json()


async def create_session(agent_id: str, api_key: str | None = None) -> dict[str, Any]:
    """Create an interactive conversation session and return WebRTC / join credentials."""
    url = f"{config.FISH_AUDIO_BASE_URL}/v1/agent/sessions"
    payload = {"agent_id": agent_id}
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(url, headers=_headers(api_key), json=payload)
        res.raise_for_status()
        return res.json()


async def stream_tts(
    text: str,
    reference_id: str | None = None,
    latency: str = "low",
    output_format: str = "mp3",
    api_key: str | None = None,
) -> AsyncIterator[bytes]:
    """Stream real-time high-fidelity speech from Fish Audio /v1/tts."""
    key = api_key or config.FISH_AUDIO_API_KEY
    if not key:
        raise ValueError("Fish Audio API key not configured.")

    url = f"{config.FISH_AUDIO_BASE_URL}/v1/tts"
    payload = {
        "text": text,
        "format": output_format,
        "latency": latency,
        "streaming": True,
    }
    ref = reference_id or config.FISH_AUDIO_VOICE_ID
    if ref:
        payload["reference_id"] = ref

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": f"audio/{output_format}",
        "model": config.FISH_AUDIO_MODEL,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes(8192):
                yield chunk
