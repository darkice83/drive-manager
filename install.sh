#!/bin/bash
set -e

echo "[1/5] Installing system dependencies..."
sudo apt update && sudo apt install -y smartmontools util-linux sg3-utils sqlite3 python3-pip python3-venv nginx certbot python3-certbot-nginx

echo "[2/5] Setting up project directory in /opt/drive-manager..."
sudo mkdir -p /opt/drive-manager
# If running from cloned repo directory:
sudo cp -r . /opt/drive-manager/
cd /opt/drive-manager

echo "[3/5] Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install fastapi uvicorn sqlalchemy jinja2 pydantic passlib bcrypt

echo "[4/5] Configuring systemd service..."
sudo cp deploy/drive-manager.service /etc/systemd/system/drive-manager.service
sudo systemctl daemon-reload
sudo systemctl enable drive-manager
sudo systemctl restart drive-manager

echo "[5/5] Installation complete!"
echo "Service status:"
sudo systemctl status drive-manager --no-pager
