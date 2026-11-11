@echo off
title Agentic OS - Desktop Console App
cd /d "C:\Users\wiff\Documents\AgenticAi"
if exist "backend\.venv\Scripts\python.exe" (
    "backend\.venv\Scripts\python.exe" desktop_app.py %*
) else (
    python desktop_app.py %*
)
