#!/usr/bin/env bash
# =============================================================================
# NKON stock monitor — Google Cloud VM setup script
# =============================================================================
# Run once as the default VM user (ubuntu on GCP) with sudo access:
#
#   bash setup.sh
#
# What it does:
#   1. Installs Python 3 and all system libs needed by Camoufox / Firefox
#   2. Creates a virtualenv at ~/nkon-monitor/.venv
#   3. Clones the repo into ~/nkon-monitor (prompts for URL if not set)
#   4. Installs Python deps and downloads the Camoufox Firefox binary
#   5. Writes .env from interactive prompts (skips if .env already exists)
#   6. Installs a systemd user service + timer (every 15 min)
#   7. Runs a Telegram test and a first real stock check
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
# 1. System packages
# =============================================================================
step "Updating apt and installing system packages"
sudo apt-get update -qq

# Detect Ubuntu version to handle libasound2 rename (Ubuntu 24.04 → libasound2t64)
UBUNTU_VER=$(lsb_release -rs 2>/dev/null || echo "22.04")
UBUNTU_MAJOR=$(echo "$UBUNTU_VER" | cut -d. -f1)

ALSA_PKG="libasound2"
if [[ "$UBUNTU_MAJOR" -ge 24 ]]; then
    ALSA_PKG="libasound2t64"
fi

sudo apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    git curl ca-certificates \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libx11-xcb1 libxcb-shm0 \
    libxtst6 libxrandr2 libxcomposite1 libxcursor1 libxdamage1 libxfixes3 \
    libxi6 \
    "$ALSA_PKG" \
    libpangocairo-1.0-0 libpango-1.0-0 libpangoft2-1.0-0 \
    libatk1.0-0 libatk-bridge2.0-0 \
    libcairo2 libcairo-gobject2 \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libxss1 libgbm1 \
    libnss3 libnspr4 \
    fonts-liberation fonts-noto-core fonts-freefont-ttf \
    xvfb

green "  System packages installed (Ubuntu $UBUNTU_VER, alsa=$ALSA_PKG)"

# =============================================================================
# 2. Clone or update repo
# =============================================================================
step "Setting up repo in $INSTALL_DIR"
if [[ -z "$REPO_URL" ]]; then
    read -rp "  GitHub repo HTTPS URL (e.g. https://github.com/you/nkon.git): " REPO_URL
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
green "  Python deps installed"

# =============================================================================
# 4. Download Camoufox Firefox binary
# =============================================================================
step "Downloading Camoufox Firefox binary (~200 MB, may take 1-2 min)"
"$VENV/bin/python" -m camoufox fetch
green "  Camoufox binary ready"

# =============================================================================
# 5. Write .env (skip if already exists)
# =============================================================================
ENV_FILE="$INSTALL_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    yellow "  .env already exists — skipping (edit $ENV_FILE manually if needed)"
else
    step "Creating .env with credentials"
    read -rp  "  NKON_EMAIL: "          nkon_email
    read -rsp "  NKON_PASSWORD: "       nkon_pass;  echo
    read -rp  "  TELEGRAM_BOT_TOKEN: "  tg_token
    read -rp  "  TELEGRAM_CHAT_ID: "    tg_chat
    cat > "$ENV_FILE" <<ENVEOF
NKON_EMAIL=${nkon_email}
NKON_PASSWORD=${nkon_pass}
TELEGRAM_BOT_TOKEN=${tg_token}
TELEGRAM_CHAT_ID=${tg_chat}
ENVEOF
    chmod 600 "$ENV_FILE"
    green "  .env written (chmod 600 — readable only by you)"
fi

# =============================================================================
# 6. systemd user service + timer
# =============================================================================
step "Installing systemd user service and timer"

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/nkon-monitor.service" <<SVCEOF
[Unit]
Description=NKON stock monitor (Camoufox)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV}/bin/python ${INSTALL_DIR}/stock_monitor.py
StandardOutput=journal
StandardError=journal
# 3 retries × ~3 min each + margin = 15 min hard cap
TimeoutStartSec=900

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

# loginctl enable-linger keeps user units alive after logout
loginctl enable-linger "$SERVICE_USER" 2>/dev/null || true

systemctl --user daemon-reload
systemctl --user enable --now nkon-monitor.timer
green "  Timer enabled: $(systemctl --user is-active nkon-monitor.timer)"

# =============================================================================
# 7. Sanity checks
# =============================================================================
step "Running first stock check against nkon.nl"
"$VENV/bin/python" "$INSTALL_DIR/stock_monitor.py" --check-only

echo
green "════════════════════════════════════════════════════"
green " Setup complete! Monitor runs every 15 minutes."
green "════════════════════════════════════════════════════"
echo
echo "Useful commands on this VM:"
echo "  systemctl --user status  nkon-monitor.timer     # next scheduled run"
echo "  systemctl --user status  nkon-monitor.service   # last run result"
echo "  journalctl --user -u nkon-monitor.service -f    # live log stream"
echo "  journalctl --user -u nkon-monitor.service -n 50 # last 50 lines"
echo "  systemctl --user start   nkon-monitor.service   # run right now"
echo "  cat $ENV_FILE                                    # check credentials"
