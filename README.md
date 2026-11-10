# Agentic OS · Jarvis

A voice-controlled "Iron Man" Agentic AI OS: a FastAPI multi-step ReAct brain (specialist agents, real tools, vector/keyword memory, 24/7 scheduler, approval gate, and append-only audit log) served over Tailscale HTTPS from a hardened Ubuntu VPS.

```
frontend/                 (served by FastAPI at /)
  index.html              dashboard, Inbox, Scheduler, Memory, Agents, Settings, Login & Approval modals
  css/jarvis.css          glass UI, mic pulse, data ribbon, thinking overlay, arc-reactor orb
  js/store.js             appState + updateUI(newState)        – global reactive state
  js/ui.js                renderers: state -> DOM (cards, inbox, jobs, memory, roles, step trace, audit)
  js/tools.js             browser UI tools registry (openTab, getIncome, setCurrency…)
  js/socket.js            auto-reconnecting WebSocket
  js/voice.js             STT (Web Speech / PCM streaming), VAD, TTS
  js/pcm-worklet.js       AudioWorklet mic capture
  js/main.js              the Listen → Send → ReAct Loop → Approve → Action → Speak loop
backend/
  app/main.py             FastAPI routes + /ws/jarvis + auth & security headers
  app/runtime.py          multi-step ReAct loop + tool risk gating + audit logging
  app/roles.py            specialist agent personas (jarvis, researcher, ops, planner, finance)
  app/llm.py              Ollama primary + automatic OpenRouter cloud fallback + embeddings
  app/memory.py           semantic (Ollama embeddings) + keyword long-term memory
  app/scheduler.py        24/7 background job scheduler (interval, daily, one-off) -> Inbox
  app/auth.py             Tailscale identity SSO, signed session cookies, bearer tokens, rate limiter
  app/tools/              ui, system, web (SSRF-guarded), files (sandboxed), shell (3-tier), memory, orchestration
  tests/test_jarvis.py    pytest suite (ReAct loop, path sandbox, shell classifier, SSRF, auth, audit)
deploy/
  bootstrap.sh            idempotent Ubuntu 22.04/24.04 setup (Ollama, Tailscale, UFW, systemd, backups)
  firewall.sh             UFW policy: deny all inbound except tailscale0 and UDP 41641
  lockdown-ssh.sh         removes temporary public TCP/22 once Tailscale SSH is verified
  verify.sh               automated security & self-heal check on the VPS
  connect.ps1             Windows client helper (installs Tailscale, checks direct path, opens HTTPS URL)
  README.md               ZAP-Hosting Cloud Firewall + UFW 24/7 runbook
```

## Quick Start (Local Dev)

```powershell
.\run.ps1                         # serves http://127.0.0.1:8000
.\backend\.venv\Scripts\pytest backend/tests -v --rootdir=backend --import-mode=importlib -o pythonpath=backend
```

## 24/7 VPS Deployment over Tailscale

See [`deploy/README.md`](./deploy/README.md) for the complete ZAP-Hosting Cloud Firewall + Ubuntu UFW + `tailscale serve` HTTPS guide. Once the VPS is up, connect from Windows with:

```powershell
.\deploy\connect.ps1 -Node jarvis-vps
```
