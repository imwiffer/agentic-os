# Starts the Agentic OS Jarvis brain + dashboard locally on http://127.0.0.1:8000 (connected to VPS Ollama 100.120.101.8:11434)
param(
    [switch]$Reload
)
$ErrorActionPreference = "Stop"
$env:OLLAMA_URL = "http://100.120.101.8:11434"
$env:JARVIS_DEFAULT_MODEL = "qwen2.5:0.5b"
$env:JARVIS_FALLBACK_MODEL = "criscar22/Qwen2.5-Coder-7B-Instruct-abliterated"
$env:JARVIS_TOOL_MODE = "auto"

Set-Location "$PSScriptRoot\backend"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    .\.venv\Scripts\python -m pip install -r requirements.txt
}
$argsList = @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000")
if ($Reload) { $argsList += "--reload" }
.\.venv\Scripts\python @argsList
