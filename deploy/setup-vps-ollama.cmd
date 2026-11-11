@echo off
REM ============================================================================
REM VANTA / AGENTIC OS — Windows VPS Automatic Spec Detector + Persistent Service
REM Run inside Administrator SSH (cmd.exe) on WIN-7HVDMUU9APJ (100.120.101.8)
REM ============================================================================

echo [1/4] Setting Ollama environment variables (User + Machine)...
setx OLLAMA_HOST "0.0.0.0:11434"
setx OLLAMA_HOST "0.0.0.0:11434" /M
setx OLLAMA_ORIGINS "*"
setx OLLAMA_ORIGINS "*" /M
setx OLLAMA_KEEP_ALIVE "24h"
setx OLLAMA_KEEP_ALIVE "24h" /M
setx OLLAMA_FLASH_ATTENTION "1"
setx OLLAMA_FLASH_ATTENTION "1" /M
setx OLLAMA_NUM_PARALLEL "2"
setx OLLAMA_NUM_PARALLEL "2" /M

set OLLAMA_HOST=0.0.0.0:11434
set OLLAMA_ORIGINS=*
set OLLAMA_KEEP_ALIVE=24h
set OLLAMA_FLASH_ATTENTION=1
set OLLAMA_NUM_PARALLEL=2

echo [2/4] Allowing TCP 11434 from Tailscale subnet (100.64.0.0/10)...
netsh advfirewall firewall delete rule name="Ollama Tailscale Inbound" >nul 2>&1
netsh advfirewall firewall add rule name="Ollama Tailscale Inbound" dir=in action=allow protocol=TCP localport=11434 remoteip=100.64.0.0/10 profile=any

echo [3/4] Registering Ollama as a persistent Windows Scheduled Task (survives SSH disconnect)...
taskkill /F /IM "ollama app.exe" >nul 2>&1
taskkill /F /IM "ollama.exe" >nul 2>&1
schtasks /Create /F /RU SYSTEM /SC ONSTART /TN "OllamaVPSService" /TR "cmd.exe /c set OLLAMA_HOST=0.0.0.0:11434&& set OLLAMA_ORIGINS=*&& set OLLAMA_KEEP_ALIVE=24h&& \"%LOCALAPPDATA%\Programs\Ollama\ollama.exe\" serve"
schtasks /Run /TN "OllamaVPSService"
timeout /t 3 /nobreak >nul

echo [4/4] Detecting GPU/CPU specs and pulling optimal model automatically...
nvidia-smi >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [GPU DETECTED] Pulling GPU model qwen2.5-coder:7b...
    ollama pull qwen2.5-coder:7b
) else (
    echo [CPU-ONLY VPS DETECTED] Pulling ultra-fast CPU model qwen2.5:0.5b (397MB)...
    ollama pull qwen2.5:0.5b
    ollama rm qwen2.5-coder:7b >nul 2>&1
)
netstat -ano | findstr :11434
