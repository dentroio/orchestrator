#!/bin/bash
# setup_pi_runner.sh - One-time setup for a Pi runner (run ON the Pi, not from your laptop)
# Usage: Run on the Pi after SSH:
#   ssh admin@192.168.1.187
#   cd ~/clarion/lab && ./setup_pi_runner.sh [OPTIONS]
# Options: --skip-git | --offline | --lab-interface=eth0|wlan0  (default: eth0)

set -e

echo "=========================================="
echo "Clarion Lab - Pi Runner Setup"
echo "=========================================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
   echo "Please run as regular user (pi), not root"
   exit 1
fi

# Parse args
SKIP_GIT=false
OFFLINE=false
LAB_INTERFACE=eth0

for arg in "$@"; do
    case $arg in
        --skip-git) SKIP_GIT=true ;;
        --offline) OFFLINE=true; SKIP_GIT=true ;;
        --lab-interface=eth0) LAB_INTERFACE=eth0 ;;
        --lab-interface=wlan0) LAB_INTERFACE=wlan0 ;;
        --lab-interface) ;;   # value taken from next arg in loop below
        *)
            if [[ "$arg" =~ @ ]]; then
                echo "Usage: Run this script ON the Pi (after ssh admin@<pi-ip>), not from your laptop."
                echo "  ssh admin@192.168.1.187   # then on the Pi:"
                echo "  cd ~/clarion/lab && ./setup_pi_runner.sh [OPTIONS]"
                echo "Options: --skip-git | --offline | --lab-interface=eth0|wlan0  (default: eth0)"
                exit 1
            fi
            ;;
    esac
done

# Support --lab-interface wlan0 (space-separated)
prev=""
for arg in "$@"; do
    if [ "$prev" = "--lab-interface" ]; then
        LAB_INTERFACE="$arg"
        prev=""
        continue
    fi
    prev="$arg"
done

if [ "$LAB_INTERFACE" != "eth0" ] && [ "$LAB_INTERFACE" != "wlan0" ]; then
    echo "Invalid --lab-interface value. Use eth0 or wlan0."
    exit 1
fi
echo "Lab interface: $LAB_INTERFACE"

# User/Home detection
RUN_USER=${SUDO_USER:-$USER}
RUN_HOME=$(getent passwd "$RUN_USER" | cut -d: -f6)

if [ "$OFFLINE" = true ]; then
    echo "!!! OFFLINE MODE ENABLED !!!"
    echo "Skipping apt/pip installs."
else
    # Update system
    echo "[1/11] Updating system packages..."
    sudo apt update
    sudo apt upgrade -y

    # Install system dependencies
    echo "[2/11] Installing system dependencies..."
    sudo apt install -y python3-pip wpasupplicant network-manager git curl
fi

# Sparse Checkout (Lab only)
if [ "$SKIP_GIT" = true ]; then
    echo "[3/11] Skipping git update (--skip-git passed)..."
else
    echo "[3/11] Setting up repository..."
    
    # Check connectivity first
    if ! curl -s --head --request GET https://github.com --max-time 3 > /dev/null; then
        echo "Warning: GitHub not reachable. Skipping git update."
    else
        mkdir -p "$RUN_HOME/clarion"
        cd "$RUN_HOME/clarion"

        if [ ! -d ".git" ]; then
            echo "Initializing new repository with sparse checkout..."
            git init
            git remote add origin https://github.com/dentroio/clarion.git
            git config core.sparseCheckout true
            echo "lab/" >> .git/info/sparse-checkout
            git pull --depth=1 origin main
        else
            echo "Repository already exists, updating with sparse checkout..."
            git config core.sparseCheckout true
            # Ensure 'lab/' is in sparse-checkout, overwrite if necessary to be clean
            echo "lab/" > .git/info/sparse-checkout
            git pull --depth=1 origin main
        fi
    fi
fi

# Install Python dependencies (apt avoids PEP 668 / externally-managed-environment)
if [ "$OFFLINE" = true ]; then
    echo "[4/11] Skipping Python packages (Offline Mode)..."
else
    echo "[4/11] Installing Python packages..."
    sudo apt install -y python3-requests python3-flask python3-psutil
    # DHCP fingerprint injection uses scapy (raw DHCP packets).
    # Some Debian/Ubuntu environments enforce PEP 668 (externally-managed Python),
    # so prefer apt, then fall back to pip with --break-system-packages.
    sudo apt install -y python3-scapy || true
    if ! python3 -c "import scapy" >/dev/null 2>&1; then
        sudo pip3 install --break-system-packages scapy
    fi
fi

# Create working directories
# Create working directories with sudo (in case owned by root from prior runs)
echo "[5/11] Creating working directories..."
sudo mkdir -p "$RUN_HOME/clarion_lab/logs"
sudo mkdir -p "$RUN_HOME/clarion_lab/state"
sudo chown -R "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab"
sudo mkdir -p /var/log/clarion_lab

# Set permissions
sudo chown -R "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab"
sudo chown -R "$RUN_USER:$RUN_USER" /var/log/clarion_lab

# Disable IPv6 by default (lab uses IPv4 only; avoids dual-stack surprises)
echo "[6/11] Disabling IPv6 by default..."
CLARION_SYSCTL="/etc/sysctl.d/99-clarion-disable-ipv6.conf"
if [ ! -f "$CLARION_SYSCTL" ]; then
    sudo tee "$CLARION_SYSCTL" > /dev/null << 'SYSCTL_EOF'
# Clarion Lab: disable IPv6 by default (lab traffic and 802.1X use IPv4 only)
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
SYSCTL_EOF
    sudo sysctl -p "$CLARION_SYSCTL" 2>/dev/null || true
    echo "Created $CLARION_SYSCTL (IPv6 disabled)"
else
    echo "Already exists: $CLARION_SYSCTL (skipping)"
fi

# Generate SSH key for orchestrator access
echo "[7/11] Setting up SSH keys..."
if [ ! -f "$RUN_HOME/.ssh/id_rsa" ]; then
    sudo -u "$RUN_USER" ssh-keygen -t rsa -b 4096 -f "$RUN_HOME/.ssh/id_rsa" -N ""
    echo "SSH key generated"
else
    echo "SSH key already exists"
fi

# Ensure lab interface is configured: eth0 via netplan when using ethernet, wlan0 via NetworkManager below
echo "[8/11] Configuring lab interface ($LAB_INTERFACE)..."
if [ "$LAB_INTERFACE" = "eth0" ]; then
    CLARION_ETH0_YAML="/etc/netplan/99-clarion-lab-eth0.yaml"
    if [ ! -f "$CLARION_ETH0_YAML" ]; then
        sudo tee "$CLARION_ETH0_YAML" > /dev/null << 'NETPLAN_EOF'
# Clarion Lab: bring up eth0 with DHCP (lab interface for dot1x). Optional so boot does not wait if cable unplugged.
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      optional: true
NETPLAN_EOF
        sudo chmod 600 "$CLARION_ETH0_YAML"
        echo "Created $CLARION_ETH0_YAML"
        if command -v netplan >/dev/null 2>&1; then
            sudo netplan apply
            echo "Applied netplan."
        else
            echo "Netplan not found; you may need to run 'sudo netplan apply' after installing netplan.io, or configure eth0 via NetworkManager."
        fi
    else
        sudo chmod 600 "$CLARION_ETH0_YAML" 2>/dev/null || true
        echo "Already exists: $CLARION_ETH0_YAML (skipping)"
    fi
else
    echo "Lab interface is wlan0; eth0 netplan skipped (WiFi lab)."
fi

# Configure wireless lab connection for runners that use wlan0 as lab (e.g. runner-5, runner-6)
echo "[9/11] Configuring wireless lab connection (wlan0)..."
if ! systemctl is-active --quiet NetworkManager 2>/dev/null; then
    echo "Starting NetworkManager (required for WiFi lab profile)..."
    sudo systemctl enable NetworkManager 2>/dev/null || true
    sudo systemctl start NetworkManager 2>/dev/null || true
    sleep 2
fi
if ! systemctl is-active --quiet NetworkManager 2>/dev/null; then
    echo "Warning: NetworkManager is not running. WiFi-lab runners need it for clarion-lab-wifi."
    echo "  Try: sudo systemctl enable --now NetworkManager"
    echo "  If using dhcpcd, you may need to disable it for wlan0 or switch to NM for WiFi."
else
    if nmcli -t -f NAME connection show 2>/dev/null | grep -q '^clarion-lab-wifi$'; then
        echo "clarion-lab-wifi already exists (skipping)"
    else
        sudo nmcli connection delete clarion-lab-wifi 2>/dev/null || true
        sudo nmcli connection add type wifi ifname wlan0 con-name clarion-lab-wifi \
          wifi.ssid "NETLAB_IOT" \
          ipv4.method auto \
          ipv6.method ignore
        echo "Created clarion-lab-wifi (open SSID NETLAB_IOT)"
    fi
fi

# Create runner config template
echo "[10/11] Creating runner config template..."
sudo tee "$RUN_HOME/clarion_lab/runner_config.json" > /dev/null << EOF
{
  "runner_id": "HOSTNAME",
  "interface": "LAB_IFACE",
  "persona_set": ["Sales"],
  "session_duration": 600,
  "notes": "Edit this file to customize runner behavior"
}
EOF
sudo chown "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab/runner_config.json"

# Replace placeholders
HOSTNAME=$(hostname)
sudo sed -i "s/HOSTNAME/$HOSTNAME/g" "$RUN_HOME/clarion_lab/runner_config.json"
sudo sed -i "s/LAB_IFACE/$LAB_INTERFACE/g" "$RUN_HOME/clarion_lab/runner_config.json"

# Test script
echo "[11/11] Testing installation..."
cd "$RUN_HOME/clarion/lab"
python3 -c "import requests; import flask; import psutil; import scapy; print('✓ Python packages OK (including scapy)')"

echo ""
echo "=========================================="
echo "✓ Pi Runner setup complete!"
echo "=========================================="
echo ""
echo "Hostname: $(hostname)"
echo "SSH Public Key:"
cat "$RUN_HOME/.ssh/id_rsa.pub"
echo ""
echo "Next steps:"
echo "1. From your machine, push code to this Pi: ./lab/deploy_runner.sh user@<this-pi-ip>"
echo "   (Use the SSH key above in authorized_keys on your machine if you deploy via SSH.)"
echo ""
echo "2. On this Pi, install and start the agent service:"
echo "   sudo RUNNER_ID=pi-runner-N ORCHESTRATOR_URL=http://ORCHESTRATOR_IP:5000 $RUN_HOME/clarion/lab/configure_clarion_runner.sh"
echo "   sudo systemctl daemon-reload && sudo systemctl enable clarion-runner && sudo systemctl start clarion-runner"
echo "   (configure_clarion_runner.sh installs the service file if missing. Set RUNNER_ID and ORCHESTRATOR_URL for this Pi.)"
echo ""
echo "3. In the dashboard (Configuration), add a runner with:"
echo "   name = RUNNER_ID, interface = $LAB_INTERFACE, persona set, session duration."
echo ""
if [ "$LAB_INTERFACE" = "eth0" ]; then
echo "4. If eth0 was down: plug in Ethernet, then run: sudo netplan apply (or reboot). Check: ip link show eth0"
else
echo "4. For wlan0: ensure WiFi is connected (e.g. clarion-lab-wifi). Check: ip link show wlan0"
fi
echo ""
