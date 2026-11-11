"""Optional "pro route" voice engines that run on the backend.

STT: faster-whisper or Vosk (fully offline). The browser streams 16 kHz mono PCM16 over the WebSocket.
TTS: ElevenLabs (streamed MP3) or Coqui TTS (local WAV).
All heavy imports are lazy, so the server starts even when none of these packages are installed.
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import logging
import threading
import wave
from typing import AsyncIterator

import httpx

from . import config

log = logging.getLogger("jarvis.voice")


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def available_stt_engines() -> list[str]:
    engines = ["browser"]
    if _has("faster_whisper"):
        engines.append("whisper")
    if _has("vosk") and config.VOSK_MODEL_PATH:
        engines.append("vosk")
    return engines


def available_tts_engines() -> list[str]:
    engines = ["browser"]
    if config.FISH_AUDIO_API_KEY:
        engines.append("fishaudio")
    if config.ELEVENLABS_API_KEY:
        engines.append("elevenlabs")
    if _has("TTS"):
        engines.append("coqui")
    return engines


class STT:
    def __init__(self, engine: str):
        if engine not in available_stt_engines():
            if engine != "browser":
                log.warning("STT engine '%s' unavailable (package/model missing) - falling back to browser", engine)
            engine = "browser"
        self.engine = engine
        self._model = None
        self._lock = threading.Lock()

    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        if self.engine == "browser":
            raise RuntimeError("Server-side STT is disabled (STT_ENGINE=browser)")
        if len(pcm16) < sample_rate // 5:  # < ~100 ms of audio
            return ""
        return await asyncio.to_thread(self._transcribe_sync, pcm16, sample_rate)

    def _transcribe_sync(self, pcm16: bytes, sample_rate: int) -> str:
        with self._lock:
            if self.engine == "whisper":
                import numpy as np
                from faster_whisper import WhisperModel

                if self._model is None:
                    log.info("Loading Whisper model '%s' ...", config.WHISPER_MODEL)
                    self._model = WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE, compute_type="int8")
                audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
                if sample_rate != 16000:  # simple linear resample
                    n = int(len(audio) * 16000 / sample_rate)
                    audio = np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio)
                    audio = audio.astype(np.float32)
                segments, _ = self._model.transcribe(audio, language="en", beam_size=1, vad_filter=True)
                return " ".join(s.text.strip() for s in segments).strip()

            if self.engine == "vosk":
                from vosk import KaldiRecognizer, Model

                if self._model is None:
                    log.info("Loading Vosk model from %s ...", config.VOSK_MODEL_PATH)
                    self._model = Model(config.VOSK_MODEL_PATH)
                rec = KaldiRecognizer(self._model, sample_rate)
                rec.AcceptWaveform(pcm16)
                return json.loads(rec.FinalResult()).get("text", "").strip()

        raise RuntimeError(f"Unknown STT engine {self.engine}")


class TTS:
    def __init__(self, engine: str):
        if engine not in available_tts_engines():
            if engine != "browser":
                log.warning("TTS engine '%s' unavailable (key/package missing) - falling back to browser", engine)
            engine = "browser"
        self.engine = engine
        self._coqui = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.engine != "browser"

    @property
    def mime(self) -> str:
        return "audio/mpeg" if self.engine in ("fishaudio", "elevenlabs") else "audio/wav"

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield audio chunks as soon as they are produced."""
        if self.engine == "fishaudio":
            url = f"{config.FISH_AUDIO_BASE_URL}/v1/tts"
            payload: dict = {
                "text": text,
                "format": "mp3",
                "latency": "normal",
                "streaming": True,
            }
            if config.FISH_AUDIO_VOICE_ID:
                payload["reference_id"] = config.FISH_AUDIO_VOICE_ID
            headers = {
                "Authorization": f"Bearer {config.FISH_AUDIO_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
                "model": config.FISH_AUDIO_MODEL,
            }
            async with httpx.AsyncClient(timeout=30) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as r:
                    r.raise_for_status()
                    async for chunk in r.aiter_bytes(16384):
                        yield chunk
        elif self.engine == "elevenlabs":
            url = (f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}/stream"
                   "?output_format=mp3_44100_128")
            async with httpx.AsyncClient(timeout=30) as client:
                async with client.stream(
                    "POST", url,
                    headers={"xi-api-key": config.ELEVENLABS_API_KEY, "accept": "audio/mpeg"},
                    json={"text": text, "model_id": config.ELEVENLABS_MODEL,
                          "voice_settings": {"stability": 0.55, "similarity_boost": 0.75}},
                ) as r:
                    r.raise_for_status()
                    async for chunk in r.aiter_bytes(16384):
                        yield chunk
        elif self.engine == "coqui":
            yield await asyncio.to_thread(self._coqui_wav, text)

    def _coqui_wav(self, text: str) -> bytes:
        import numpy as np

        with self._lock:
            if self._coqui is None:
                from TTS.api import TTS as CoquiTTS

                log.info("Loading Coqui model '%s' ...", config.COQUI_MODEL)
                self._coqui = CoquiTTS(config.COQUI_MODEL)
            samples = np.asarray(self._coqui.tts(text), dtype=np.float32)
            rate = self._coqui.synthesizer.output_sample_rate
        pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        return buf.getvalue()
