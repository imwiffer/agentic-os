#!/usr/bin/env bash
# Automated verification of the VPS security posture and 24/7 service health.
set -euo pipefail

PASS=0
FAIL=0
check() {
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  [PASS] ${desc}"; PASS=$((PASS + 1))
  else
    echo "  [FAIL] ${desc}"; FAIL=$((FAIL + 1))
  fi
}

echo "=== Agentic OS VPS Verification ==="
check "jarvis.service is active" systemctl is-active --quiet jarvis
check "ollama.service is active" systemctl is-active --quiet ollama
check "tailscaled.service is active" systemctl is-active --quiet tailscaled
check "jarvis-backup.timer is active" systemctl is-active --quiet jarvis-backup.timer
check "/api/health responds 200 on loopback" curl -fsS http://127.0.0.1:8000/api/health
check "Port 8000 bound to 127.0.0.1 only" bash -c "ss -tlnH '( sport = :8000 )' | awk '{print \$4}' | grep -vx '127.0.0.1:8000' && exit 1 || exit 0"
check "Port 11434 bound to 127.0.0.1 only" bash -c "ss -tlnH '( sport = :11434 )' | awk '{print \$4}' | grep -vx '127.0.0.1:11434' && exit 1 || exit 0"
check "UFW is active with default deny incoming" bash -c "ufw status verbose | grep -q 'Status: active' && ufw status verbose | grep -q 'deny (incoming)'"
check "UFW allows tailscale0 + 41641/udp" bash -c "ufw status | grep -q 'tailscale0' && ufw status | grep -q '41641/udp'"
check "sshd disables password & root login" bash -c "sshd -T | grep -iq 'passwordauthentication no' && sshd -T | grep -iq 'permitrootlogin no'"

echo ""
echo "=== Self-heal restart check ==="
systemctl kill -s SIGKILL jarvis
sleep 5
check "jarvis.service auto-restarted within 5s after SIGKILL" systemctl is-active --quiet jarvis

echo ""
echo "=== Tailscale tunnel check ==="
tailscale netcheck || true

echo ""
echo "Summary: ${PASS} passed, ${FAIL} failed."
[[ "${FAIL}" -eq 0 ]]
