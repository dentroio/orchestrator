#!/bin/bash
# setup_fault_injector.sh - Deploy to Pi-Rebuild-5 (Fault Injector)
# Usage: ./setup_fault_injector.sh

set -e

echo "=========================================="
echo "Clarion Lab - Fault Injector Setup"
echo "=========================================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
   echo "Please run as regular user (pi), not root"
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

# User/Home detection
RUN_USER=${SUDO_USER:-$USER}
RUN_HOME=$(getent passwd "$RUN_USER" | cut -d: -f6)

if [ "$OFFLINE" = true ]; then
    echo "!!! OFFLINE MODE ENABLED !!!"
    echo "Skipping apt/pip installs."
else
    # Update system
    echo "[1/6] Updating system..."
    sudo apt update
    sudo apt upgrade -y

    # Install dependencies
    echo "[2/6] Installing dependencies..."
    sudo apt install -y python3-pip git nmap network-manager
fi

# Sparse Checkout (Lab only)
if [ "$SKIP_GIT" = true ]; then
    echo "[3/6] Skipping git update (--skip-git passed)..."
else
    echo "[3/6] Setting up repository..."
    
    # Check connectivity
    if ! curl -s --head --request GET https://github.com --max-time 3 > /dev/null; then
        echo "Warning: GitHub not reachable. Skipping git update."
    else
        TARGET_DIR="$RUN_HOME/clarion"
        mkdir -p "$TARGET_DIR"
        cd "$TARGET_DIR"

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

# Install Python libs
if [ "$OFFLINE" = true ]; then
    echo "[4/6] Skipping Python packages (Offline Mode)..."
else
    echo "[4/6] Installing Python packages..."
    pip3 install --upgrade requests scapy
fi

# Setup working dir
echo "[5/6] Creating workspace..."
sudo mkdir -p "$RUN_HOME/clarion_lab/anomalies"
sudo chown -R "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab"
sudo mkdir -p /var/log/clarion_lab
sudo chown "$RUN_USER:$RUN_USER" /var/log/clarion_lab

# Create runner script
echo "[6/6] Creating runner script..."
sudo tee "$RUN_HOME/clarion_lab/run_anomaly.sh" > /dev/null << EOF
#!/bin/bash
# Run specific anomaly scenarios
# Usage: ./run_anomaly.sh [scenario_name]

SCENARIO=\$1
SCRIPT_DIR="$RUN_HOME/clarion/lab"

if [ -z "\$SCENARIO" ]; then
    echo "Usage: \$0 [violation-01|violation-02|portscan]"
    exit 1
fi

echo "Running anomaly scenario: \$SCENARIO"

case \$SCENARIO in
    "violation-01")
        # Sales user accessing IoT Dev
        sudo python3 \$SCRIPT_DIR/auto_lab_runner.py --identity "alice.johnson.violation" --one-shot --session-duration 60
        ;;
    "violation-02")
        # Camera accessing Finance
        sudo python3 \$SCRIPT_DIR/auto_lab_runner.py --identity "camera-01-violation" --one-shot --session-duration 60
        ;;
    "portscan")
        echo "Running Nmap scan..."
        nmap -F 192.168.30.0/24 > $RUN_HOME/clarion_lab/anomalies/scan_\$(date +%s).log
        ;;
    *)
        echo "Unknown scenario: \$SCENARIO"
        exit 1
        ;;
esac
EOF

sudo chmod +x "$RUN_HOME/clarion_lab/run_anomaly.sh"
sudo chown "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab/run_anomaly.sh"

echo ""
echo "=========================================="
echo "✓ Fault Injector setup complete!"
echo "=========================================="
echo "Run anomalies using:"
echo "  $RUN_HOME/clarion_lab/run_anomaly.sh violation-01"
