#!/usr/bin/env bash
# =============================================================================
# NKON monitor — update script (called by GitHub Actions deploy job)
# =============================================================================
# Pulls latest code, reinstalls deps if requirements.txt changed, restarts timer.
# Safe to run while the timer is active — systemd will wait for the current
# oneshot run to finish before starting the next one.
# =============================================================================
set -euo pipefail

INSTALL_DIR="$HOME/nkon-monitor"
VENV="$INSTALL_DIR/.venv"

cd "$INSTALL_DIR"

echo "[update] Pulling latest code…"
git pull --ff-only

echo "[update] Syncing Python dependencies…"
"$VENV/bin/pip" install --quiet -r requirements.txt

echo "[update] Reloading systemd units…"
systemctl --user daemon-reload
systemctl --user restart nkon-monitor.timer

echo "[update] Done. Timer status:"
systemctl --user status nkon-monitor.timer --no-pager
