# Starts the Jarvis brain + dashboard on http://localhost:8000
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\backend"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    .\.venv\Scripts\python -m pip install -r requirements.txt
}
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
