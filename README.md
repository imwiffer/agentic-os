# Agentic OS · J.A.R.V.I.S.

[![License: MIT](https://img.shields.io/badge/License-MIT-purple.svg)](./LICENSE)
[![Platform: Windows 11 / Ubuntu](https://img.shields.io/badge/Platform-Windows%2011%20%7C%20Ubuntu-blue.svg)](https://github.com/imwiffer/agentic-os)
[![Voice: Fish Audio](https://img.shields.io/badge/Voice-Fish%20Audio%20Neural-pink.svg)](https://fish.audio)
[![Engine: Ollama + AirLLM](https://img.shields.io/badge/Compute-RTX%205070%20%2B%20Ubuntu%20VPS-emerald.svg)](https://github.com/imwiffer/agentic-os)

An autonomous Agentic Operating System featuring a hybrid compute fabric (Local NVIDIA RTX 5070 GPU + Remote Ubuntu VPS), real-time J.A.R.V.I.S. neural voice synthesis with Fish Audio personas, an embedded Monaco IDE workbench, OpenVSX extension support, and a 5-layer token optimization engine.

---

## Key Capabilities

### 1. Hybrid Compute Fabric
* **Local GPU Node (Main PC):** Direct acceleration via NVIDIA GeForce RTX 5070 (12GB VRAM) and the local Vanta AirLLM bridge on `:7777` for zero-latency tool execution and code completion.
* **Remote 24/7 VPS Node (Ubuntu):** Always-on background scheduler, automations, and ReAct agent loop powered by `qwen2.5:0.5b` (397MB) on CPU (`147.189.168.95:11434`).
* **Remote GPU Cloud Failsafe:** Dynamic routing support for external GPU instances (RunPod, Vast.ai, vLLM clusters) via `/api/remote_gpu/configure`.

### 2. J.A.R.V.I.S. Neural Voice Studio
* **Fish Audio Real-Time Streaming:** Powered by `s2.1-pro-free` with natural conversational inflection.
* **Curated Voice Personas:**
  * `J.A.R.V.I.S. (Classic British)` — Refined, calm, authentic British butler (`14129c3e...`)
  * `J.A.R.V.I.S. (Cinematic HD)` — High-definition studio baritone (`88b183bf...`)
  * `J.A.R.V.I.S. (Tactical)` — Low-latency military operator tone (`05b36da8...`)
  * `J.A.R.V.I.S. (Deep Assistant)` — Warm, deep conversational tone (`e13fa398...`)
  * `Announcer (Epic / Powerful)` — Smash Bros.-style thunderous announcer (`90e65eaa...`)
* **Instant Master Mute:** One-click instant audio cutoff across header, floating HUD, and Voice Studio tab.
* **Anti-Ramble Speech Clamp:** Strips code blocks, URLs, and logs, restricting spoken responses to 1-2 punchy sentences.

### 3. Monaco IDE Code Workbench
* Embedded VS Code-style Monaco editor accessible under the `Monaco IDE` tab.
* Direct connection to local Python/C++/Rust workspace with popout support and terminal integration.

### 4. OpenVSX Extensions Marketplace
* Browse, search, and install themes, snippets, and grammars directly from the [OpenVSX Registry](https://open-vsx.org).
* Extensible plugin loader for custom Python tool extensions.

### 5. 5-Layer Token Optimization Engine
* **Layer 1: Intent-Filtered Tool Injection:** Scores prompts against tool keywords (0ms) and injects only relevant tool definitions, cutting prompt tokens by ~70%.
* **Layer 2: Tier-0 Zero-Token Fast Path:** Resolves deterministic UI navigation, financial checks, and currency changes in `<5ms` with `0 LLM tokens`.
* **Layer 3: History Compaction:** Compresses older dialogue turns while keeping recent context verbatim.
* **Layer 4: Observation Pruning:** Collapses repetitive tool outputs and logs.
* **Layer 5: Response Caching:** TTL caching for repeated system queries.

---

## Architecture Overview

```
frontend/                 (Glass UI served by FastAPI at /)
  index.html              Dashboard, Voice Studio, Monaco IDE, Extensions, Inbox, Scheduler
  css/jarvis.css          Glass styling, arc-reactor orb, HUD, data ribbons
  js/store.js             Global reactive state (models, connectors, latency, telemetry)
  js/ui.js                Dynamic renderers (system cards, audit log, usage gauges)
  js/voice.js             Fish Audio TTS streaming + Web Speech fallback + Master Mute
  js/api.js               REST client for backend endpoints
  js/main.js              The Listen -> ReAct -> Approve -> Act -> Speak loop

backend/
  app/main.py             FastAPI application routes, WebSocket /ws/jarvis, telemetry
  app/runtime.py          Multi-step ReAct agent runtime + policy enforcement
  app/hw.py               Automatic hardware detector (RTX 5070 vs CPU VPS model selector)
  app/fish_audio.py       Fish Audio API connector (agents, sessions, neural TTS streaming)
  app/token_opt.py        5-layer token optimizer & telemetry accounting
  app/extensions.py       OpenVSX registry search & VSIX package manager
  app/voice.py            Multi-engine backend voice coordinator
  app/roles.py            Specialist agent personas (jarvis, researcher, ops, finance)
  app/tools/              Filesystem, shell, web search, system monitoring, memory tools
  app/db.py               Append-only SQLite WAL database & audit logging

launchers/
  AgenticOS.exe           Native Windows Desktop GUI window
  AgenticOS-Console.exe   Interactive CLI / Terminal Console
```

---

## Quick Start

### 1. Launch on Windows
Double-click the native launcher or run via terminal:

```powershell
# Launch Desktop GUI App
.\AgenticOS.exe

# Or Launch Console TUI
.\AgenticOS-Console.exe
```

### 2. Manual Dev Server Startup

```powershell
cd backend
.\.venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open your browser to `http://127.0.0.1:8000`.

---

## License
Released under the [MIT License](./LICENSE).
