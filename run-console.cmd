@echo off
title Agentic OS - Console Application (VPS Ollama + Local AirLLM Failsafe)
cd /d "C:\Users\wiff\Documents\AgenticAi"
if exist "backend\.venv\Scripts\python.exe" (
    "backend\.venv\Scripts\python.exe" console_app.py %*
) else (
    python console_app.py %*
)
