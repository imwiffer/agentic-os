# Deploying Agentic OS on a ZAP-Hosting Ubuntu VPS (24/7 via Tailscale)

This directory contains everything needed to run the Jarvis brain 24/7 on an Ubuntu 22.04/24.04 CPU VPS with **zero public TCP ports** exposed to the internet.

---

## 1. Layer 1 — ZAP-Hosting Cloud Firewall (Web Panel)

Configure these rules in the ZAP-Hosting firewall panel **before** or right after provisioning the VPS:

| # | Direction | Protocol | Port | Source | Action | Purpose |
|---|---|---|---|---|---|---|
| 1 | Inbound | UDP | `41641` | Any (`0.0.0.0/0`) | **Allow** | Direct WireGuard peer-to-peer tunnel (avoids DERP relay latency) |
| 2 | Inbound | TCP | `22` | **Your home IP `/32` only** | **Allow** | **Temporary** bootstrap SSH — delete in step 5 |
| 3 | Inbound | ICMP | — | Any | **Allow** | Path MTU discovery & ping |
| 4 | Inbound | All | All | Any | **Deny** | Default drop for everything else |
| 5 | Outbound | All | All | Any | **Allow** | Required for Tailscale control/STUN (`TCP 443`, `UDP 3478`), `apt`, Ollama, OpenRouter |

> **ZAP DDoS protection note:** Leave the DDoS profile on **Standard / Automatic**. Aggressive game-filter profiles can rate-limit UDP `41641`; if `tailscale ping jarvis-vps` shows `via DERP` instead of a direct IP, whitelist UDP `41641` in the DDoS panel.

---

## 2. Tailscale Admin Console Prep (1 minute)

In [https://login.tailscale.com/admin](https://login.tailscale.com/admin):
1. **DNS (`admin/dns`):** Enable **MagicDNS** and **HTTPS Certificates** (required so `tailscale serve` provisions a valid TLS cert for browser microphone access).
2. **Access Controls (`admin/acls/file`):** Optionally paste [`tailscale-acl.hujson`](./tailscale-acl.hujson) so only your devices can reach the VPS on ports `22` and `443`.

---

## 3. Bootstrap the VPS (Layer 2 — UFW + systemd + Ollama)

SSH into your Ubuntu VPS and run:

```bash
git clone <your-repo-url> ~/AgenticAi
cd ~/AgenticAi
sudo bash deploy/bootstrap.sh
```

`bootstrap.sh` is idempotent and will:
- Enable `unattended-upgrades`, a 4 GB swapfile, kernel `sysctl` hardening, and SSH key-only config (`99-hardening.conf`).
- Create the unprivileged `jarvis` service user and `/srv/jarvis/{app,data,workspace,backups}`.
- Generate a 64-hex `JARVIS_AUTH_TOKEN` in `/etc/jarvis/jarvis.env` (`chmod 640`).
- Install Ollama bound strictly to `127.0.0.1:11434` and pull `qwen2.5:7b-instruct` + `nomic-embed-text`.
- Install Tailscale (`tailscaled` with `--auto-update`) and apply [`firewall.sh`](./firewall.sh) (`ufw default deny incoming`, allow `tailscale0`, allow `41641/udp`, temporary `limit 22/tcp`).
- Enable `jarvis.service` (`127.0.0.1:8000`) and `jarvis-backup.timer` (daily SQLite online backup at 03:30, 7-day retention).

---

## 4. Connect Tailscale & Enable HTTPS

On the VPS:

```bash
sudo tailscale up --ssh --hostname=jarvis-vps
sudo tailscale serve --bg --https=443 http://127.0.0.1:8000
```

Then in [https://login.tailscale.com/admin/machines](https://login.tailscale.com/admin/machines), click `jarvis-vps` → **Disable key expiry** so the tunnel stays up 24/7 without re-authentication.

Retrieve your dashboard login token (or set `JARVIS_TAILSCALE_USERS=you@github` in `/etc/jarvis/jarvis.env` for automatic Tailscale SSO):

```bash
sudo grep JARVIS_AUTH_TOKEN /etc/jarvis/jarvis.env
```

---

## 5. Lock Down Public SSH (Zero Public TCP Ports)

1. Keep your current SSH window open.
2. From your Windows PC, run `.\deploy\connect.ps1` and verify a **second** terminal can connect via Tailscale (`ssh root@jarvis-vps`).
3. On the VPS, remove public port 22 from UFW:
   ```bash
   sudo bash deploy/lockdown-ssh.sh
   ```
4. Delete temporary Rule #2 (`TCP 22`) in the **ZAP-Hosting Cloud Firewall** panel.
5. Run the automated verification suite:
   ```bash
   sudo bash deploy/verify.sh
   ```
