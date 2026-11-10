# Agentic OS · Jarvis

A voice-controlled "Iron Man" dashboard: a FastAPI "brain" (LLM function-calling, SQLite, voice engines),
talking to a reactive frontend over WebSockets.

```
frontend/                 (served by FastAPI at /)
  index.html              dashboard markup (ported from dashboard.html, wired with IDs)
  css/jarvis.css          glass UI, mic pulse, data ribbon, thinking overlay, arc-reactor orb
  js/store.js             appState + updateUI(newState)        – global reactive state
  js/ui.js                renderers: state -> DOM (count-up numbers, sidebar, models, status)
  js/tools.js             tools registry the AI can trigger (openTab, getIncome, setCurrency…)
  js/socket.js            auto-reconnecting WebSocket
  js/voice.js             STT (Web Speech / PCM streaming), VAD, TTS
  js/pcm-worklet.js       AudioWorklet mic capture
  js/main.js              the Listen → Send → Process → Action → React → Speak loop
backend/
  app/main.py             FastAPI routes + /ws/jarvis
  app/agent.py            JSON function-calling agent (Ollama / OpenAI-compatible) + rule fallback
  app/db.py               SQLite schema, seed (Jack Roberts, finance), stats builder
  app/voice.py            optional Whisper/Vosk STT, ElevenLabs/Coqui TTS
  app/config.py           env-driven settings (see .env.example)
```

## Run

```powershell
.\run.ps1        # creates backend\.venv on first run, then serves http://localhost:8000
```

Open http://localhost:8000 in **Chrome or Edge** (Web Speech API). Click **Voice** (or `Ctrl+Space`) and say
"open finances", "what's my balance in euros?", "switch to llama", "scroll down". You can also type in the HUD.

### Give it a real brain
Without an LLM the agent uses a deterministic rule engine, so everything works out of the box. For real AI:

- **Local:** install [Ollama](https://ollama.com), `ollama pull llama3:8b`, then pick it from the model dropdown
  (installed models are discovered automatically; green dot = ready).
- **Cloud:** copy `backend/.env.example` to `backend/.env` and set `OPENAI_API_KEY` (OpenRouter by default).

### Pro voice (optional)
| Feature | Setting | Install |
|---|---|---|
| Offline STT (Whisper) | `STT_ENGINE=whisper` | `pip install faster-whisper numpy` |
| Offline STT (Vosk) | `STT_ENGINE=vosk`, `VOSK_MODEL_PATH=...` | `pip install vosk` + a model from alphacephei.com/vosk/models |
| Jarvis voice (ElevenLabs) | `TTS_ENGINE=elevenlabs`, `ELEVENLABS_API_KEY=...` | – |
| Local TTS (Coqui) | `TTS_ENGINE=coqui` | `pip install TTS` |

In server-STT mode the browser streams 16 kHz PCM16 over the WebSocket; server TTS audio is streamed back as binary frames.

## API
| Route | Purpose |
|---|---|
| `GET /api/dashboard/stats` | numbers for all cards (USD base + FX rates) |
| `GET /api/models` | Local/Cloud model list (+ live Ollama discovery) |
| `GET /api/config` | active STT/TTS engines, currencies |
| `POST /api/jarvis/chat` | `{"input": "open finances"}` → `{response, actions, engine}` |
| `WS /ws/jarvis` | `user_text`, `audio_start` / binary PCM / `audio_end` ⇄ `status`, `action`, `reply`, audio |

Action messages look like `{"type": "action", "action": "openTab", "target": "Finances"}`.
