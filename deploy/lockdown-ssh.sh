#!/usr/bin/env bash
# Run ONLY after confirming you can open a second session over Tailscale (`ssh jarvis@jarvis-vps`).
# Removes public TCP/22 from UFW so the VPS has zero public TCP ports.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/lockdown-ssh.sh" >&2
  exit 1
fi

if ! tailscale status >/dev/null 2>&1; then
  echo "ABORT: tailscaled is not online. Refusing to remove public SSH." >&2
  exit 1
fi

ufw delete limit 22/tcp || ufw delete allow 22/tcp || true
systemctl disable --now fail2ban 2>/dev/null || true
ufw status verbose
echo ""
echo "Public TCP/22 closed in UFW. Now also delete the temporary TCP/22 rule in the ZAP-Hosting Cloud Firewall panel."
