# language: Python 3.11+, file: desktop_app.py, target: Windows 11 Native Desktop Console (.exe entrypoint)
"""Agentic OS — Native Desktop Console Application Launcher.

When launched on the Main PC (or via AgenticOS.exe):
  1. Auto-detects Main PC GPU (NVIDIA RTX 5070) and starts the Local GPU Bridge (:7777) if available.
  2. Starts the Agentic OS FastAPI + WebSocket backend (:8000) which auto-detects VPS CPU vs Local GPU specs
     and selects the optimal model (`qwen2.5:0.5b` for CPU-only, `7B` for GPU).
  3. Opens the native frameless/app window connected to http://127.0.0.1:8000.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
VANTA_IDE_DIR = Path(r"C:\Users\wiff\Documents\Opencode\Projects\vanta-ide")
AIRLLM_DIR = Path(r"C:\Users\wiff\Documents\Opencode\Projects\airllm")
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

HOST = "127.0.0.1"
PORT = int(os.getenv("JARVIS_PORT", "8000"))
URL = f"http://{HOST}:{PORT}"


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def start_gpu_bridge_if_needed() -> None:
    """Ensure the Main PC RTX GPU bridge (Vanta server on :7777) is running so the remote VPS and local OS can use the GPU."""
    if is_port_open("127.0.0.1", 7777):
        return
    python_candidates = [
        VANTA_IDE_DIR / ".venv" / "Scripts" / "python.exe",
        AIRLLM_DIR / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    server_candidates = [
        (VANTA_IDE_DIR / "server.py", VANTA_IDE_DIR),
        (AIRLLM_DIR / "ide" / "server.py", AIRLLM_DIR),
    ]
    py_exec = next((p for p in python_candidates if p.exists()), None)
    for server_script, work_dir in server_candidates:
        if py_exec and server_script.exists():
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                [str(py_exec), str(server_script), "--host", "0.0.0.0", "--port", "7777"],
                cwd=str(work_dir),
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            break


def start_backend_thread() -> None:
    start_gpu_bridge_if_needed()
    if is_port_open(HOST, PORT):
        return

    def _run() -> None:
        import uvicorn
        from app.main import app
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"{URL}/api/health", timeout=1.0) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.2)


class WindowAPI:
    def __init__(self) -> None:
        self._win = None

    def attach(self, win) -> None:
        self._win = win

    def close(self) -> None:
        if self._win:
            self._win.destroy()

    def minimize(self) -> None:
        if self._win:
            self._win.minimize()

    def toggle_fullscreen(self) -> None:
        if self._win:
            self._win.toggle_fullscreen()


def launch_window() -> None:
    start_backend_thread()
    try:
        import webview  # type: ignore
        api = WindowAPI()
        win = webview.create_window(
            "Agentic OS · Console",
            URL,
            width=1480,
            height=920,
            min_size=(1024, 680),
            background_color="#090a0f",
            js_api=api,
        )
        api.attach(win)
        webview.start(debug=False)
        return
    except ImportError:
        pass

    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("msedge"),
        shutil.which("chrome"),
    ]
    browser = next((c for c in candidates if c and Path(c).exists()), None)
    if browser:
        proc = subprocess.Popen([browser, f"--app={URL}", "--window-size=1480,920"])
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
    else:
        import webbrowser
        webbrowser.open(URL)
        print(f"Agentic OS running at {URL} — Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    if "--bridge-only" in sys.argv:
        start_gpu_bridge_if_needed()
    else:
        launch_window()
