#!/usr/bin/env bash
# =============================================================================
# NKON stock monitor — Google Cloud VM setup script
# Run once as the default VM user (ubuntu) with sudo access:
#   bash setup.sh
# =============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-}"
INSTALL_DIR="$HOME/nkon-monitor"
VENV="$INSTALL_DIR/.venv"
SERVICE_USER="$(id -un)"

green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
step()   { echo; green "▶ $*"; }

# =============================================================================
# 1. System packages (minimal — no browser needed)
# =============================================================================
step "Updating apt and installing system packages"
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    git curl ca-certificates
green "  System packages installed"

# =============================================================================
# 2. Clone or update repo
# =============================================================================
step "Setting up repo in $INSTALL_DIR"
if [[ -z "$REPO_URL" ]]; then
    read -rp "  GitHub repo HTTPS URL: " REPO_URL
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
    yellow "  Repo already exists — pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# =============================================================================
# 3. Python virtualenv + deps
# =============================================================================
step "Creating Python virtualenv and installing dependencies"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
green "  Python deps installed (curl_cffi, certifi)"

# =============================================================================
# 4. Write .env (skip if already exists)
# =============================================================================
ENV_FILE="$INSTALL_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    yellow "  .env already exists — skipping"
else
    step "Creating .env with Telegram credentials"
    read -rp  "  TELEGRAM_BOT_TOKEN: "  tg_token
    read -rp  "  TELEGRAM_CHAT_ID: "    tg_chat
    cat > "$ENV_FILE" <<ENVEOF
TELEGRAM_BOT_TOKEN=${tg_token}
TELEGRAM_CHAT_ID=${tg_chat}
ENVEOF
    chmod 600 "$ENV_FILE"
    green "  .env written (chmod 600)"
fi

# =============================================================================
# 5. systemd user service + timer
# =============================================================================
step "Installing systemd user service and timer"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/nkon-monitor.service" <<SVCEOF
[Unit]
Description=NKON stock monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV}/bin/python ${INSTALL_DIR}/stock_monitor.py
StandardOutput=journal
StandardError=journal
TimeoutStartSec=120

[Install]
WantedBy=default.target
SVCEOF

cat > "$UNIT_DIR/nkon-monitor.timer" <<TMREOF
[Unit]
Description=Run NKON stock monitor every 15 minutes
Requires=nkon-monitor.service

[Timer]
OnCalendar=*:0/15
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
TMREOF

loginctl enable-linger "$SERVICE_USER" 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now nkon-monitor.timer
green "  Timer enabled: $(systemctl --user is-active nkon-monitor.timer)"

# =============================================================================
# 6. First stock check
# =============================================================================
step "Running first stock check against nkon.nl"
"$VENV/bin/python" "$INSTALL_DIR/stock_monitor.py" --check-only

echo
green "════════════════════════════════════════════════════"
green " Setup complete! Monitor runs every 15 minutes."
green "════════════════════════════════════════════════════"
echo
echo "Useful commands:"
echo "  systemctl --user status  nkon-monitor.timer"
echo "  journalctl --user -u nkon-monitor.service -f"
echo "  systemctl --user start   nkon-monitor.service"
