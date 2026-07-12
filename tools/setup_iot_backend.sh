#!/bin/bash
# setup_iot_backend.sh - Deploy to 192.168.31.2 (iotdev Pi, dev network 192.168.31.0/24)
# Purpose: Host IoT persona-specific HTTP endpoints (ports 9001-9010)
# Usage: ./setup_iot_backend.sh
#
# 192.168.31.2 = Pi that hosts the iotdev site (nothing else on it currently).
# Run as your normal user (e.g. steve) on that Pi. Script uses $USER and $HOME.

set -e

RUN_USER="${RUN_USER:-$USER}"
RUN_HOME="${RUN_HOME:-$HOME}"
CLARION_DIR="$RUN_HOME/clarion"
LAB_DIR="$CLARION_DIR/lab"

echo "=========================================="
echo "Clarion Lab - IoT Backend Setup"
echo "=========================================="
echo "User: $RUN_USER"
echo "Home: $RUN_HOME"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
   echo "Please run as regular user (e.g. steve or pi), not root"
   exit 1
fi

# Parse args
SKIP_GIT=false
OFFLINE=false

for arg in "$@"; do
    case $arg in
        --skip-git) SKIP_GIT=true ;;
        --offline) OFFLINE=true; SKIP_GIT=true ;;
    esac
done

if [ "$OFFLINE" = true ]; then
    echo "!!! OFFLINE MODE ENABLED !!!"
    echo "Skipping apt/pip installs."
else
    # Update system
    echo "[1/7] Updating system packages..."
    sudo apt update
    sudo apt upgrade -y

    # Install system dependencies (python3-flask via apt avoids PEP 668 / externally-managed-environment)
    echo "[2/7] Installing system dependencies..."
    sudo apt install -y python3-flask ufw git curl
fi

# Clone repository if not exists
# Sparse Checkout (Lab only)
if [ "$SKIP_GIT" = true ]; then
    echo "[3/7] Skipping git update (--skip-git passed)..."
else
    echo "[3/7] Setting up repository..."
    
    # Check connectivity
    if ! curl -s --head --request GET https://github.com --max-time 3 > /dev/null; then
        echo "Warning: GitHub not reachable. Skipping git update."
    else
        mkdir -p "$CLARION_DIR"
        cd "$CLARION_DIR"

        if [ ! -d ".git" ]; then
            git init
            git remote add origin https://github.com/dentroio/clarion.git
            git config core.sparseCheckout true
            echo "lab/" >> .git/info/sparse-checkout
            git pull --depth=1 origin main
        else
            echo "Repository exists, updating..."
            git config core.sparseCheckout true
            echo "lab/" > .git/info/sparse-checkout
            git pull --depth=1 origin main
        fi
    fi
fi

# Install Python dependencies (apt, not pip - works on modern Debian/Raspberry Pi OS with PEP 668)
if [ "$OFFLINE" = true ]; then
    echo "[4/7] Skipping Python packages (Offline Mode)..."
else
    echo "[4/7] Ensuring Python packages (Flask via apt)..."
    sudo apt install -y python3-flask || true
fi

# Create working directories
echo "[5/7] Creating working directories..."
sudo mkdir -p "$RUN_HOME/clarion_lab/iot_backend/{logs,data}"
sudo chown -R "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab"
sudo mkdir -p /var/log/clarion_lab

# Set permissions
sudo chown -R "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab"
sudo chown -R "$RUN_USER:$RUN_USER" /var/log/clarion_lab

# Configure firewall
echo "[6/7] Configuring firewall..."
sudo ufw allow 9001:9010/tcp comment "IoT backend endpoints"
sudo ufw allow 22/tcp comment "SSH"
sudo ufw allow 1883/tcp comment "MQTT (optional)"
echo "y" | sudo ufw enable || true

# Create systemd service (use current user and paths)
echo "[7/7] Creating systemd service..."
sudo tee /etc/systemd/system/iot_backend_mock.service > /dev/null << SERVICEEOF
[Unit]
Description=Clarion Lab IoT Backend Mock Server
After=network.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$LAB_DIR
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/bin/python3 $LAB_DIR/iot_backend_mock.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/clarion_lab/iot_backend.log
StandardError=append:/var/log/clarion_lab/iot_backend_error.log

[Install]
WantedBy=multi-user.target
SERVICEEOF

# Reload systemd
sudo systemctl daemon-reload

# Enable and start service
sudo systemctl enable iot_backend_mock.service
sudo systemctl start iot_backend_mock.service

# Wait a moment for service to start
sleep 3

echo ""
echo "=========================================="
echo "✓ IoT Backend setup complete!"
echo "=========================================="
echo ""
echo "Hostname: $(hostname)"
echo "Service status:"
sudo systemctl status iot_backend_mock.service --no-pager || true
echo ""
echo "Firewall status:"
sudo ufw status numbered
echo ""
echo "Testing endpoints..."
echo ""

# Test all 10 endpoints
ENDPOINTS=(
    "9001:badge/events:POST"
    "9002:camera/stream:POST"
    "9003:print/jobs:GET"
    "9004:telemetry:POST"
    "9005:hvac/status:POST"
    "9006:lock/events:POST"
    "9007:display/feed:GET"
    "9008:voip/register:GET"
    "9009:robot/telemetry:POST"
    "9010:medical/vitals:POST"
)

for endpoint in "${ENDPOINTS[@]}"; do
    IFS=':' read -r port path method <<< "$endpoint"
    
    if [ "$method" == "POST" ]; then
        RESPONSE=$(curl -s -X POST "http://localhost:$port/$path" -H "Content-Type: application/json" -d '{"test": true}' || echo "FAILED")
    else
        RESPONSE=$(curl -s "http://localhost:$port/$path" || echo "FAILED")
    fi
    
    if echo "$RESPONSE" | grep -q '"status"'; then
        echo "✓ Port $port ($path) - OK"
    else
        echo "✗ Port $port ($path) - FAILED"
    fi
done

echo ""
echo "=========================================="
echo "Service Management Commands:"
echo "=========================================="
echo "Check status:  sudo systemctl status iot_backend_mock"
echo "View logs:     sudo journalctl -u iot_backend_mock -f"
echo "Restart:       sudo systemctl restart iot_backend_mock"
echo "Stop:          sudo systemctl stop iot_backend_mock"
echo "Disable:       sudo systemctl disable iot_backend_mock"
echo ""
echo "Test from another machine:"
echo "  curl http://$(hostname -I | awk '{print $1}'):9001/badge/events -X POST"
echo ""
echo "Optional: Install MQTT broker (Mosquitto)"
echo "  sudo apt install mosquitto mosquitto-clients"
echo "  sudo systemctl enable mosquitto"
echo "  sudo systemctl start mosquitto"
echo ""
