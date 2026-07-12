#!/bin/bash
# setup_orchestrator.sh - Deploy to Pi-Rebuild-3 (Orchestration Controller)
# Usage: ./setup_orchestrator.sh
#
# Version: 2026-02-17 (client/server, no SSH to runners; config in DB)

set -e
SCRIPT_VERSION="2026-02-17"

echo "=========================================="
echo "Clarion Lab - Orchestrator Setup"
echo "Script version: $SCRIPT_VERSION"
echo "=========================================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
   echo "Please run as regular user (e.g. pi or admin), not root"
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

# Detect User and Home
RUN_USER=$USER
RUN_HOME=$HOME

echo "Installing as user: $RUN_USER"
echo "Home directory: $RUN_HOME"

if [ "$OFFLINE" = true ]; then
    echo "!!! OFFLINE MODE ENABLED !!!"
    echo "Skipping apt update, package install, and pip install."
else
    # Update system
    echo "[1/7] Updating system packages..."
    sudo apt update
    sudo apt upgrade -y

    # Install system dependencies
    echo "[2/7] Installing system dependencies..."
    sudo apt install -y python3-pip openssh-client git curl rsync jq
fi

# Clone/Update repository
if [ "$SKIP_GIT" = true ]; then
    echo "[3/8] Skipping git update (--skip-git passed)..."
else
    echo "[3/8] Setting up repository..."
    # ... git logic ...
    # (Checking connectivity logic remains, but we can simplify since we replaced the block)
    # Re-inserting the git logic for non-offline scenarios
    if ! curl -s --head --request GET https://github.com --max-time 3 > /dev/null; then
        echo "Warning: GitHub not reachable. Skipping git update."
    else
        mkdir -p "$RUN_HOME/clarion"
        cd "$RUN_HOME/clarion"

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

# Install Python dependencies
if [ "$OFFLINE" = true ]; then
    echo "[4/8] Skipping Python packages (Offline Mode)..."
else
    echo "[4/8] Installing Python packages..."
    pip3 install --break-system-packages --upgrade fabric paramiko requests flask ldap3
fi

# Create working directories (all under ~/clarion/lab; no separate clarion_lab folder)
echo "[5/8] Creating working directories..."
mkdir -p "$RUN_HOME/clarion/lab/ground_truth"
mkdir -p "$RUN_HOME/clarion/lab/logs"
sudo mkdir -p /var/log/clarion_lab

# Set permissions
sudo chown -R "$RUN_USER:$RUN_USER" /var/log/clarion_lab

# Generate SSH key
echo "[6/7] Setting up SSH keys..."
if [ ! -f "$RUN_HOME/.ssh/id_rsa" ]; then
    ssh-keygen -t rsa -b 4096 -f "$RUN_HOME/.ssh/id_rsa" -N ""
    echo "SSH key generated"
else
    echo "SSH key already exists"
fi

# Create orchestrator config
echo "[7/7] Configuring orchestrator..."

CONFIG_SRC="$RUN_HOME/clarion/lab/orchestrator_config.json"
CONFIG_DEST="$RUN_HOME/clarion/lab/orchestrator_config.json"

if [ -f "$CONFIG_SRC" ] && grep -q "runners" "$CONFIG_SRC"; then
    echo "Found existing orchestrator_config.json in repo, using it."
else
    echo "No config found, generating template..."
    cat > "$CONFIG_DEST" << EOF
{
  "ground_truth_log": "$RUN_HOME/clarion/lab/ground_truth/ground_truth_log.csv",
  "identities_file": "$RUN_HOME/clarion/lab/identities1.json",
  "runners": [
    {
      "name": "pi-runner-1",
      "host": "192.168.1.187",
      "user": "$RUN_USER",
      "interface": "eth0",
      "persona_set": ["Sales"],
      "session_duration": 600
    },
    {
      "name": "pi-runner-2",
      "host": "192.168.1.193",
      "user": "$RUN_USER",
      "interface": "eth0",
      "persona_set": ["Finance"],
      "session_duration": 600
    },
    {
      "name": "pi-runner-3",
      "host": "192.168.1.188",
      "user": "$RUN_USER",
      "interface": "eth0",
      "persona_set": ["Engineering"],
      "session_duration": 600
    },
    {
      "name": "pi-runner-4",
      "host": "192.168.1.189",
      "user": "$RUN_USER",
      "interface": "eth0",
      "persona_set": ["IT"],
      "session_duration": 600
    },
    {
      "name": "pi-runner-5",
      "host": "192.168.20.91",
      "user": "$RUN_USER",
      "interface": "wlan0",
      "persona_set": ["Badge Reader", "Camera", "Printer", "Environmental Sensor", "HVAC Controller"],
      "session_duration": 1800
    },
    {
      "name": "pi-runner-6",
      "host": "192.168.20.90",
      "user": "$RUN_USER",
      "interface": "wlan0",
      "persona_set": ["Door Lock", "Display", "VoIP Phone", "Robot", "Medical Device"],
      "session_duration": 1800
    }
  ]
}
EOF
fi

# Create SSH config template
# Note: Remote users are assumed to be 'pi' unless specified in Host config
cat > "$RUN_HOME/.ssh/config" << EOF
# Clarion Lab Pi Runners

# Pi-Runner-1
Host pi-runner-1
    HostName 192.168.1.187
    User $RUN_USER
    IdentityFile ~/.ssh/id_rsa
    StrictHostKeyChecking no

# Pi-Runner-2
Host pi-runner-2
    HostName 192.168.1.193
    User $RUN_USER
    IdentityFile ~/.ssh/id_rsa
    StrictHostKeyChecking no

# Pi-Runner-3
Host pi-runner-3
    HostName 192.168.1.188
    User $RUN_USER
    IdentityFile ~/.ssh/id_rsa
    StrictHostKeyChecking no

# Pi-Runner-4
Host pi-runner-4
    HostName 192.168.1.189
    User $RUN_USER
    IdentityFile ~/.ssh/id_rsa
    StrictHostKeyChecking no

# Pi-Runner-5
Host pi-runner-5
    HostName 192.168.20.91
    User $RUN_USER
    IdentityFile ~/.ssh/id_rsa
    StrictHostKeyChecking no

# Pi-Runner-6
Host pi-runner-6
    HostName 192.168.20.90
    User $RUN_USER
    IdentityFile ~/.ssh/id_rsa
    StrictHostKeyChecking no
EOF

chmod 600 "$RUN_HOME/.ssh/config"

# Create helper script for distributing SSH keys (optional; for deploy_runner.sh from this host)
cat > "$RUN_HOME/clarion/lab/distribute_ssh_keys.sh" << 'EOF'
#!/bin/bash
# Distribute SSH public key to all runners
# Usage: ./distribute_ssh_keys.sh

CONFIG_FILE="$HOME/clarion/lab/orchestrator_config.json"
DEFAULT_USER=${USER}

# Check for jq
if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed. Please install it (sudo apt install jq)."
    echo "Falling back to default list..."
    # Hard fallback just in case
    RUNNERS=(
        "192.168.1.187"
        "192.168.1.193"
        "192.168.1.188"
        "192.168.1.189"
        "192.168.20.91"
        "192.168.20.90"
    )
    USER_LIST=()
    for ip in "${RUNNERS[@]}"; do
        USER_LIST+=("$DEFAULT_USER@$ip")
    done
    RUNNERS=("${USER_LIST[@]}")
else
    # Parse runners from config
    echo "Reading runners from $CONFIG_FILE..."
    # We use newline as delimiter for the array
    # Format: "name:user@host"
    IFS=$'\n' read -d '' -r -a ALL_RUNNERS < <(jq -r '.runners[] | "\(.name):\(.user)@\(.host)"' "$CONFIG_FILE")
fi

TARGET_FILTER=$1

# Build list of targets
RUNNERS=()
for entry in "${ALL_RUNNERS[@]}"; do
    NAME="${entry%%:*}"
    CONNECTION="${entry#*:}"
    
    # If filter is provided, check if it matches name or IP
    if [ -n "$TARGET_FILTER" ]; then
        if [[ "$NAME" == *"$TARGET_FILTER"* ]] || [[ "$CONNECTION" == *"$TARGET_FILTER"* ]]; then
            RUNNERS+=("$CONNECTION")
        fi
    else
        # No filter, add everything
        RUNNERS+=("$CONNECTION")
    fi
done

if [ ${#RUNNERS[@]} -eq 0 ]; then
    echo "No runners found matching '$TARGET_FILTER'."
    exit 1
fi

echo "Found ${#RUNNERS[@]} matching runners."
echo "Distributing SSH key..."
echo "NOTE: You may be asked to accept fingerprints (yes) and enter passwords."
echo ""

for runner in "${RUNNERS[@]}"; do
    echo "---------------------------------------------------"
    echo "Target: $runner"
    # Use ssh-copy-id with StrictHostKeyChecking=no to avoid excessive prompts
    
    ssh-copy-id -o StrictHostKeyChecking=no -i ~/.ssh/id_rsa.pub "$runner"
    
    if [ $? -eq 0 ]; then
        echo "✓ Success"
    else
        echo "✗ Failed (Check password or connectivity)"
    fi
done

echo ""
echo "Testing SSH access..."
for runner in "${RUNNERS[@]}"; do
    echo -n "Testing $runner... "
    ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$runner" "echo OK" || echo "FAILED"
done
EOF

chmod +x "$RUN_HOME/clarion/lab/distribute_ssh_keys.sh"

# Setup Systemd Service for Orchestrator UI
echo "[8/8] Configuring Systemd Service..."
SERVICE_FILE="/etc/systemd/system/clarion-orchestrator.service"

sudo bash -c "cat > $SERVICE_FILE" << EOF
[Unit]
Description=Clarion Lab Orchestrator UI
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$RUN_HOME/clarion/lab/orchestrator/app
ExecStart=/usr/bin/python3 $RUN_HOME/clarion/lab/orchestrator/app/orchestrator_web.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd and enabling service..."
sudo systemctl daemon-reload
sudo systemctl enable clarion-orchestrator
# Don't start immediately, let user do it or reboot
# sudo systemctl start clarion-orchestrator

echo ""
echo "=========================================="
echo "✓ Orchestrator setup complete!"
echo "=========================================="
echo ""
echo "Hostname: $(hostname)"
echo "SSH Public Key (add to runners if ssh-copy-id fails):"
cat "$RUN_HOME/.ssh/id_rsa.pub"
echo ""
echo "Next steps:"
echo "1. Start Orchestrator UI:"
echo "   sudo systemctl start clarion-orchestrator"
echo "2. Access UI at http://$(hostname -I | awk '{print $1}'):5000"
echo "3. (Optional) To push code to runners from this host, distribute SSH keys:"
echo "   cd $RUN_HOME/clarion/lab && ./distribute_ssh_keys.sh"
echo ""
echo "All orchestrator files live under $RUN_HOME/clarion/lab (one folder)."
echo ""
