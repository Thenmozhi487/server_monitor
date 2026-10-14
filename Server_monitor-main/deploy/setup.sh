#!/usr/bin/env bash
# ==============================================================================
# Automated Production Deployment Setup Script for Server Health Dashboard
# Tested on Ubuntu 20.04/22.04/24.04 and Debian 11/12
# ==============================================================================

set -euo pipefail

APP_DIR="/opt/server-health-dashboard"
SERVICE_NAME="server-health-dashboard"
LOG_DIR="/var/log/server-health-dashboard"
HTPASSWD_FILE="/etc/nginx/.htpasswd"
NGINX_CONF_AVAILABLE="/etc/nginx/sites-available/$SERVICE_NAME"
NGINX_CONF_ENABLED="/etc/nginx/sites-enabled/$SERVICE_NAME"

echo "=================================================================="
echo " Starting Server Health Dashboard Production Setup"
echo "=================================================================="

# 1. Require Root / Sudo
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run this setup script as root or with sudo:"
    echo "  sudo ./deploy/setup.sh"
    exit 1
fi

# 2. Install Linux System Dependencies
echo "[1/7] Installing system dependencies (Python, Nginx, Apache2-utils, stress-ng)..."
apt-get update -y
apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    nginx \
    apache2-utils \
    stress-ng \
    curl

# 3. Create Application Directory & Copy Files
echo "[2/7] Deploying application codebase to $APP_DIR..."
mkdir -p "$APP_DIR"
mkdir -p "$LOG_DIR"

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Copy files
cp -r "$SOURCE_DIR/app.py" "$APP_DIR/"
cp -r "$SOURCE_DIR/collector.py" "$APP_DIR/"
cp -r "$SOURCE_DIR/alerts.py" "$APP_DIR/"
cp -r "$SOURCE_DIR/db.py" "$APP_DIR/"
cp -r "$SOURCE_DIR/config.yaml" "$APP_DIR/"
cp -r "$SOURCE_DIR/requirements.txt" "$APP_DIR/"
cp -r "$SOURCE_DIR/templates" "$APP_DIR/"
cp -r "$SOURCE_DIR/static" "$APP_DIR/"
cp -r "$SOURCE_DIR/scripts" "$APP_DIR/"
cp -r "$SOURCE_DIR/deploy" "$APP_DIR/"

# 4. Create Dedicated Virtualenv and Install Python Requirements
echo "[3/7] Setting up Python virtual environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip setuptools wheel
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# 5. Set Permissions
echo "[4/7] Configuring file ownership and permissions for www-data..."
chown -R www-data:www-data "$APP_DIR"
chown -R www-data:www-data "$LOG_DIR"
chmod 750 "$APP_DIR"
chmod -R 755 "$APP_DIR/static"

# 6. Configure HTTP Basic Authentication
echo "[5/7] Configuring HTTP Basic Authentication in $HTPASSWD_FILE..."
if [ ! -f "$HTPASSWD_FILE" ]; then
    read -r -p "Enter desired dashboard admin username [default: admin]: " AUTH_USER
    AUTH_USER="${AUTH_USER:-admin}"
    echo "Generating credentials for user: $AUTH_USER"
    htpasswd -c "$HTPASSWD_FILE" "$AUTH_USER"
    chmod 640 "$HTPASSWD_FILE"
    chown root:www-data "$HTPASSWD_FILE"
    echo "Basic authentication configured successfully."
else
    echo "Existing $HTPASSWD_FILE detected. Keeping existing credentials."
fi

# 7. Configure Systemd Service
echo "[6/7] Installing systemd unit file..."
cp "$APP_DIR/deploy/server-health-dashboard.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# 8. Configure Nginx Reverse Proxy
echo "[7/7] Configuring Nginx reverse proxy..."
cp "$APP_DIR/deploy/nginx.conf" "$NGINX_CONF_AVAILABLE"

# Remove default site if present
if [ -L "/etc/nginx/sites-enabled/default" ]; then
    rm -f "/etc/nginx/sites-enabled/default"
fi

ln -sf "$NGINX_CONF_AVAILABLE" "$NGINX_CONF_ENABLED"
nginx -t
systemctl reload nginx

echo "=================================================================="
echo " Server Health Dashboard Deployment Complete!"
echo "=================================================================="
echo "Status Checks:"
echo "  - Systemd Service Status: systemctl status $SERVICE_NAME"
echo "  - Service Logs:           journalctl -u $SERVICE_NAME -f"
echo "  - Gunicorn Logs:          tail -f $LOG_DIR/error.log"
echo "  - Nginx Status:           systemctl status nginx"
echo ""
echo "Access Dashboard:"
echo "  http://$(hostname -I | awk '{print $1}')/"
echo ""
echo "To enable HTTPS with Let's Encrypt (Certbot), run:"
echo "  sudo certbot --nginx -d your-domain.com"
echo "=================================================================="
