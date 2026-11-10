# Starts the Jarvis brain + dashboard locally on http://127.0.0.1:8000
param(
    [switch]$Reload
)
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\backend"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    .\.venv\Scripts\python -m pip install -r requirements.txt
}
$argsList = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")
if ($Reload) { $argsList += "--reload" }
.\.venv\Scripts\python @argsList
