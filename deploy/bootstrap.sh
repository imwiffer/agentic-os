#!/usr/bin/env bash
# Idempotent Ubuntu 22.04 / 24.04 LTS bootstrap for Agentic OS on a CPU VPS.
# Run as root from the repository root:  sudo bash deploy/bootstrap.sh
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/bootstrap.sh" >&2
  exit 1
fi

REPO_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ROOT="/srv/jarvis"

echo "=== [1/8] System packages & unattended security updates ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get -y full-upgrade
apt-get -y install ufw fail2ban unattended-upgrades python3 python3-venv python3-pip sqlite3 git curl ca-certificates
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "=== [2/8] 4 GB swapfile (prevents OOM kills when loading 7B-8B quantized models) ==="
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "=== [3/8] Kernel & SSH hardening ==="
install -m 0644 "${REPO_SRC}/deploy/99-jarvis-sysctl.conf" /etc/sysctl.d/99-jarvis-sysctl.conf
sysctl --system >/dev/null
install -m 0644 "${REPO_SRC}/deploy/99-hardening.conf" /etc/ssh/sshd_config.d/99-hardening.conf
sshd -t && systemctl reload ssh || systemctl reload sshd || true
systemctl enable --now fail2ban

echo "=== [4/8] Service user & directory layout ==="
id -u jarvis >/dev/null 2>&1 || useradd --system --home-dir "${APP_ROOT}" --shell /usr/sbin/nologin jarvis
mkdir -p "${APP_ROOT}"/{app,data,workspace,backups} /etc/jarvis
rsync -a --delete --exclude '.venv' --exclude '*.db' "${REPO_SRC}/" "${APP_ROOT}/app/" 2>/dev/null \
  || cp -a "${REPO_SRC}/." "${APP_ROOT}/app/"
chown -R jarvis:jarvis "${APP_ROOT}"/{data,workspace,backups}

echo "=== [5/8] Python virtualenv & dependencies ==="
if [[ ! -x "${APP_ROOT}/.venv/bin/python" ]]; then
  python3 -m venv "${APP_ROOT}/.venv"
fi
"${APP_ROOT}/.venv/bin/pip" install --upgrade pip -q
"${APP_ROOT}/.venv/bin/pip" install -r "${APP_ROOT}/app/backend/requirements.txt" -q

if [[ ! -f /etc/jarvis/jarvis.env ]]; then
  install -m 0640 -o root -g jarvis "${REPO_SRC}/deploy/jarvis.env.example" /etc/jarvis/jarvis.env
  TOKEN="$(openssl rand -hex 32)"
  sed -i "s|^JARVIS_AUTH_TOKEN=.*|JARVIS_AUTH_TOKEN=${TOKEN}|" /etc/jarvis/jarvis.env
  echo "Generated JARVIS_AUTH_TOKEN in /etc/jarvis/jarvis.env (retrieve with: sudo grep JARVIS_AUTH_TOKEN /etc/jarvis/jarvis.env)"
fi

echo "=== [6/8] Ollama (bound strictly to 127.0.0.1:11434) ==="
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
mkdir -p /etc/systemd/system/ollama.service.d
cat >/etc/systemd/system/ollama.service.d/loopback.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=30m"
EOF
systemctl daemon-reload
systemctl enable --now ollama
ollama pull qwen2.5:7b-instruct || true
ollama pull nomic-embed-text || true

echo "=== [7/8] Tailscale & UFW firewall ==="
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled
tailscale set --auto-update || true
bash "${REPO_SRC}/deploy/firewall.sh"

echo "=== [8/8] Jarvis systemd service & daily SQLite backups ==="
install -m 0644 "${REPO_SRC}/deploy/jarvis.service" /etc/systemd/system/jarvis.service
install -m 0644 "${REPO_SRC}/deploy/jarvis-backup.service" /etc/systemd/system/jarvis-backup.service
install -m 0644 "${REPO_SRC}/deploy/jarvis-backup.timer" /etc/systemd/system/jarvis-backup.timer
systemctl daemon-reload
systemctl enable --now jarvis.service jarvis-backup.timer

echo ""
echo "Bootstrap complete. Next steps:"
echo "  1. Authenticate Tailscale:  sudo tailscale up --ssh --hostname=jarvis-vps"
echo "  2. Enable HTTPS proxy:      sudo tailscale serve --bg --https=443 http://127.0.0.1:8000"
echo "  3. Disable key expiry for jarvis-vps in https://login.tailscale.com/admin/machines"
echo "  4. Verify Tailscale SSH from your PC, then close public TCP/22:  sudo bash deploy/lockdown-ssh.sh"
