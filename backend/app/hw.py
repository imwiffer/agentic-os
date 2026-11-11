# language: Python 3.11+, file: backend/app/hw.py, target: Windows 11 / Ubuntu Server + Main PC Auto-Hardware Fabric
"""Hardware Specification Detector & Automatic Model/Compute Selector.

Detects GPU (NVIDIA CUDA / VRAM), CPU cores, and RAM on the local host and remote nodes,
and automatically selects the optimal model tier:
  - CPU-only nodes (e.g. Ubuntu Server 147.189.168.95) -> ultra-light CPU models (`qwen2.5:0.5b`, `qwen2.5-coder:1.5b`)
  - Remote GPU nodes (e.g. Cloud/RunPod/Vast/vLLM) -> heavy models (`qwen2.5-coder:7b`, 70B abliterated)
  - Local GPU nodes (e.g. Main PC RTX 5070) -> GPU models (`qwen2.5-coder:7b`, AirLLM 7B)
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from typing import Any

import httpx
import psutil

from . import config, db

log = logging.getLogger("jarvis.hw")

LAST_FABRIC_REPORT: dict[str, Any] = {}


def detect_local_specs() -> dict[str, Any]:
    """Detect local CPU, RAM, and GPU (name + VRAM MB) without external heavy dependencies."""
    cpu_cores = psutil.cpu_count(logical=False) or 2
    cpu_threads = psutil.cpu_count(logical=True) or 4
    ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    cpu_name = platform.processor() or "x86_64 CPU"

    gpus: list[dict[str, Any]] = []
    # 1. Try nvidia-smi for accurate VRAM reporting
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            proc = subprocess.run(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                for line in proc.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2:
                        vram_mb = int(float(parts[1]))
                        gpus.append({"name": parts[0], "vram_mb": vram_mb, "vram_gb": round(vram_mb / 1024, 1), "vendor": "nvidia"})
        except Exception as exc:
            log.debug("nvidia-smi probe failed: %s", exc)

    # 2. Fallback to PowerShell / CIM on Windows if nvidia-smi not found
    if not gpus and os.name == "nt":
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -Property Name,AdapterRAM | ConvertTo-Json -Compress"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                import json
                raw = json.loads(proc.stdout.strip())
                items = raw if isinstance(raw, list) else [raw]
                for it in items:
                    name = str(it.get("Name") or "")
                    if any(k in name.lower() for k in ("microsoft basic", "parsec", "rdp", "virtual")):
                        continue
                    ram_bytes = int(it.get("AdapterRAM") or 0)
                    vram_mb = max(0, ram_bytes // (1024 * 1024))
                    is_discrete = any(k in name.lower() for k in ("nvidia", "geforce", "rtx", "gtx", "radeon rx", "arc"))
                    if is_discrete and vram_mb <= 4096 and "5070" in name:
                        vram_mb = 12288  # Win32_VideoController 32-bit AdapterRAM cap workaround
                    gpus.append({"name": name, "vram_mb": vram_mb, "vram_gb": round(vram_mb / 1024, 1), "vendor": "discrete" if is_discrete else "integrated"})
        except Exception as exc:
            log.debug("WMI GPU probe failed: %s", exc)

    has_discrete_gpu = any(g.get("vendor") in ("nvidia", "discrete") and g.get("vram_mb", 0) >= 4000 for g in gpus)
    max_vram_gb = max((g.get("vram_gb", 0.0) for g in gpus), default=0.0)

    if has_discrete_gpu and max_vram_gb >= 10.0:
        tier = "gpu_high"
        recommended_model = "qwen2.5-coder:7b"
        cpu_fallback_model = "qwen2.5-coder:1.5b"
    elif has_discrete_gpu and max_vram_gb >= 4.0:
        tier = "gpu_mid"
        recommended_model = "qwen2.5-coder:1.5b"
        cpu_fallback_model = "qwen2.5:0.5b"
    elif cpu_threads >= 4 and ram_gb >= 8.0:
        tier = "cpu_only_standard"
        recommended_model = "qwen2.5:0.5b"
        cpu_fallback_model = "qwen2.5:0.5b"
    else:
        tier = "cpu_only_ultralight"
        recommended_model = "qwen2.5:0.5b"
        cpu_fallback_model = "qwen2.5:0.5b"

    return {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu_name": cpu_name,
        "cpu_cores": cpu_cores,
        "cpu_threads": cpu_threads,
        "ram_gb": ram_gb,
        "gpus": gpus,
        "has_gpu": has_discrete_gpu,
        "max_vram_gb": max_vram_gb,
        "tier": tier,
        "recommended_model": recommended_model,
        "cpu_fallback_model": cpu_fallback_model,
    }


def pick_best_cpu_model(installed: list[str]) -> str:
    """Choose the fastest CPU-capable model from an Ollama node's installed models."""
    preferred_order = [
        "qwen2.5:0.5b",
        "qwen2.5-coder:1.5b",
        "qwen2.5:1.5b",
        "llama3.2:1b",
        "qwen2.5:3b",
        "llama3.2:3b",
        "qwen2.5-coder:7b",
    ]
    for candidate in preferred_order:
        if candidate in installed:
            return candidate
    return installed[0] if installed else "qwen2.5:0.5b"


def pick_best_gpu_model(installed: list[str]) -> str:
    """Choose the strongest GPU model from an Ollama node's installed models."""
    preferred_order = [
        "qwen2.5-coder:14b",
        "qwen2.5-coder:7b",
        "qwen2.5:7b-instruct",
        "llama3.1:8b",
        "qwen2.5-coder:1.5b",
        "qwen2.5:0.5b",
    ]
    for candidate in preferred_order:
        if candidate in installed:
            return candidate
    return installed[0] if installed else "qwen2.5-coder:7b"


async def probe_ollama_node(base_url: str) -> dict[str, Any]:
    """Inspect an Ollama endpoint's installed tags and whether it is running on GPU or CPU."""
    base_url = base_url.rstrip("/")
    info: dict[str, Any] = {
        "url": base_url,
        "online": False,
        "installed": [],
        "loaded": [],
        "gpu_accelerated": False,
        "recommended_model": "qwen2.5:0.5b",
    }
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            r_tags = await client.get(f"{base_url}/api/tags")
            r_tags.raise_for_status()
            models = r_tags.json().get("models", [])
            installed = [m.get("name", "") for m in models if m.get("name")]
            info["online"] = True
            info["installed"] = installed

            r_ps = await client.get(f"{base_url}/api/ps")
            if r_ps.status_code == 200:
                running = r_ps.json().get("models", [])
                info["loaded"] = [m.get("name", "") for m in running if m.get("name")]
                info["gpu_accelerated"] = any(int(m.get("size_vram") or 0) > 0 for m in running)

            # If the node has no VRAM usage or matches Ubuntu/VPS IP, treat as CPU-only node
            is_vps_cpu = ("147.189.168.95" in base_url or "100.120.101.8" in base_url) and not info["gpu_accelerated"]
            if is_vps_cpu or not info["gpu_accelerated"]:
                info["mode"] = "cpu_only"
                info["recommended_model"] = pick_best_cpu_model(installed)
            else:
                info["mode"] = "gpu"
                info["recommended_model"] = pick_best_gpu_model(installed)
    except Exception as exc:
        info["error"] = str(exc)
    return info


async def ensure_vps_cpu_model(base_url: str, installed: list[str]) -> str:
    """If the CPU-only VPS only has a heavy 7B+ model, automatically pull qwen2.5:0.5b (397MB) for fast CPU inference."""
    small_cpu_models = ("qwen2.5:0.5b", "qwen2.5-coder:1.5b", "qwen2.5:1.5b", "llama3.2:1b")
    for m in small_cpu_models:
        if m in installed:
            return m
    target = "qwen2.5:0.5b"
    try:
        log.info("VPS (%s) is CPU-only and lacks a small model; auto-pulling %s (397MB)...", base_url, target)
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{base_url.rstrip('/')}/api/pull", json={"name": target, "stream": False})
            if r.status_code == 200:
                # Unload heavy 7B model from VPS RAM if loaded
                await client.post(f"{base_url.rstrip('/')}/api/generate", json={"model": "qwen2.5-coder:7b", "keep_alive": 0})
                log.info("Auto-pulled %s onto CPU-only VPS %s.", target, base_url)
                return target
    except Exception as exc:
        log.warning("Could not auto-pull %s onto %s: %s", target, base_url, exc)
    return pick_best_cpu_model(installed)


def ensure_local_device_model(local_specs: dict[str, Any]) -> dict[str, Any]:
    """Ensure a model matching the device's GPU/CPU specs is downloaded locally on disk (~/.cache/huggingface/hub)."""
    from pathlib import Path

    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    has_gpu = bool(local_specs.get("has_gpu"))
    cpu_hf_model = "Qwen/Qwen3-0.6B"
    gpu_hf_model = config.FALLBACK_MODEL  # criscar22/Qwen2.5-Coder-7B-Instruct-abliterated

    target_hf = gpu_hf_model if has_gpu else cpu_hf_model
    folder_name = "models--" + target_hf.replace("/", "--")
    model_dir = hf_cache / folder_name

    # Always register the local CPU and GPU HuggingFace models in SQLite
    db.add_model(cpu_hf_model, "Qwen3 0.6B (Local Device CPU)", "airllm", "local", "1.2GB")
    db.add_model(gpu_hf_model, "Qwen 2.5 Coder 7B (Local Device GPU)", "airllm", "local", "4.7GB")

    if model_dir.exists():
        return {"model": target_hf, "downloaded": True, "path": str(model_dir)}

    # Download automatically in background using the Vanta .venv or current Python
    airllm_py = Path(r"C:\Users\wiff\Documents\Opencode\Projects\airllm\.venv\Scripts\python.exe")
    py_exe = str(airllm_py) if airllm_py.exists() else "python"
    try:
        log.info("Auto-downloading local device model %s to %s...", target_hf, hf_cache)
        subprocess.Popen(
            [
                py_exe,
                "-c",
                f"from huggingface_hub import snapshot_download; snapshot_download({target_hf!r})",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {"model": target_hf, "downloaded": "downloading", "path": str(model_dir)}
    except Exception as exc:
        log.warning("Auto-download of local model %s failed: %s", target_hf, exc)
        return {"model": target_hf, "downloaded": False, "error": str(exc)}


async def auto_configure_compute_fabric() -> dict[str, Any]:
    """Detect local PC GPU specs + remote VPS CPU specs and auto-configure DEFAULT_MODEL and endpoints."""
    global LAST_FABRIC_REPORT
    local_specs = detect_local_specs()
    local_device_model = ensure_local_device_model(local_specs)

    vps_probe = await probe_ollama_node(config.OLLAMA_URL)
    local_ollama_probe = await probe_ollama_node(config.FAILSAFE_OLLAMA_URL)
    remote_gpu_probe = await probe_ollama_node(config.REMOTE_GPU_URL) if config.REMOTE_GPU_URL else {"online": False, "url": ""}

    # Check if local Vanta AirLLM GPU bridge (127.0.0.1:7777) is online on Main PC
    vanta_gpu_online = False
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"{config.VANTA_AIRLLM_URL}/api/ping")
            vanta_gpu_online = r.status_code == 200
    except Exception:
        vanta_gpu_online = False

    # Register small CPU models in SQLite so the UI dropdown always shows them
    db.add_model("qwen2.5:0.5b", "Qwen 2.5 0.5B (Ultra-Fast CPU)", "ollama", "local", "397MB")
    db.add_model("qwen2.5-coder:1.5b", "Qwen 2.5 Coder 1.5B (Fast CPU)", "ollama", "local", "986MB")
    db.add_model("qwen2.5-coder:7b", "Qwen 2.5 Coder 7B (GPU / Heavy)", "ollama", "local", "4.7GB")

    decision_reason = ""
    # Automatic decision logic:
    # 1. If Local Main PC has an RTX GPU AND local Ollama is online with a GPU model, use Main PC GPU as primary or bridge
    if local_specs["has_gpu"] and local_ollama_probe["online"] and local_ollama_probe["installed"]:
        chosen_model = pick_best_gpu_model(local_ollama_probe["installed"])
        config.DEFAULT_MODEL = chosen_model
        decision_reason = (
            f"Detected {local_specs['gpus'][0]['name']} ({local_specs['max_vram_gb']}GB VRAM) with local Ollama online; "
            f"selected GPU model '{chosen_model}'."
        )
    # 2. If VPS Ollama (100.120.101.8) is online (CPU-only VPS), auto-select (and ensure) the lightweight CPU model!
    elif vps_probe["online"]:
        if vps_probe.get("mode") == "cpu_only":
            chosen_model = await ensure_vps_cpu_model(config.OLLAMA_URL, vps_probe["installed"])
        else:
            chosen_model = vps_probe["recommended_model"]
        config.DEFAULT_MODEL = chosen_model
        decision_reason = (
            f"Connected to VPS Ollama ({config.OLLAMA_URL}, mode={vps_probe.get('mode', 'cpu_only')}); "
            f"auto-selected fast CPU model '{chosen_model}' "
            f"(Main PC GPU: {local_specs['gpus'][0]['name'] if local_specs['gpus'] else 'none'})."
        )
    # 3. If Vanta AirLLM GPU bridge is online on Main PC
    elif vanta_gpu_online:
        config.DEFAULT_MODEL = config.FALLBACK_MODEL
        decision_reason = f"Using Main PC GPU via Vanta AirLLM bridge ({config.VANTA_AIRLLM_URL}) with {config.FALLBACK_MODEL}."
    else:
        config.DEFAULT_MODEL = local_specs["cpu_fallback_model"]
        decision_reason = f"Offline fallback; defaulted to '{config.DEFAULT_MODEL}'."

    LAST_FABRIC_REPORT = {
        "local_specs": local_specs,
        "local_device_model": local_device_model,
        "vps_ollama": vps_probe,
        "remote_gpu": remote_gpu_probe,
        "local_ollama": local_ollama_probe,
        "vanta_airllm_online": vanta_gpu_online,
        "selected_default_model": config.DEFAULT_MODEL,
        "decision_reason": decision_reason,
    }
    log.info("Hardware Fabric Auto-Config: %s", decision_reason)
    return LAST_FABRIC_REPORT
