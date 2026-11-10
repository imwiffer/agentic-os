#!/usr/bin/env bash
# Layer-2 host firewall (UFW) for the Agentic OS VPS.
# Layer-1 is the ZAP-Hosting Cloud Firewall (see deploy/README.md).
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/firewall.sh" >&2
  exit 1
fi

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw default deny routed

# Trust all traffic arriving inside the encrypted Tailscale tunnel (HTTPS 443 via tailscale serve, Tailscale SSH, etc.)
ufw allow in on tailscale0 comment 'tailnet traffic'

# Allow direct WireGuard UDP so the PC and VPS establish a low-latency peer-to-peer link instead of relayed DERP
ufw allow 41641/udp comment 'tailscale direct wireguard'

# TEMPORARY bootstrap SSH rule. Remove with deploy/lockdown-ssh.sh once `tailscale ssh` is confirmed working.
ufw limit 22/tcp comment 'bootstrap ssh - REMOVE AFTER TAILSCALE UP'

ufw logging low
ufw --force enable
ufw status verbose
