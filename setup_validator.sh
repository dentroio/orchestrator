#!/bin/bash
# setup_validator.sh - Deploy to Pi-Rebuild-4 (Validation & Scoring Node)
# Usage: ./setup_validator.sh

set -e

echo "=========================================="
echo "Clarion Lab - Validator Setup"
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
    echo "[1/7] Updating system packages..."
    sudo apt update
    sudo apt upgrade -y

    # Install system dependencies
    echo "[2/7] Installing system dependencies..."
    sudo apt install -y python3-pip git rsync curl jq
fi

# Sparse Checkout (Lab only)
if [ "$SKIP_GIT" = true ]; then
    echo "[3/7] Skipping git update (--skip-git passed)..."
else
    echo "[3/7] Setting up repository..."
    
    # Check connectivity
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
    echo "[4/7] Skipping Python packages (Offline Mode)..."
else
    echo "[4/7] Installing Python packages..."
    pip3 install --upgrade requests pandas matplotlib
fi

# Create working directories
echo "[5/7] Creating working directories..."
sudo mkdir -p "$RUN_HOME/clarion_lab/validation"
sudo mkdir -p "$RUN_HOME/clarion_lab/ground_truth"
sudo mkdir -p "$RUN_HOME/clarion_lab/logs"
sudo mkdir -p "$RUN_HOME/reports"
sudo chown -R "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab"
sudo chown -R "$RUN_USER:$RUN_USER" "$RUN_HOME/reports"
sudo mkdir -p /var/log/clarion_lab

# Set permissions
sudo chown -R "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab"
sudo chown -R "$RUN_USER:$RUN_USER" "$RUN_HOME/reports"
sudo chown -R "$RUN_USER:$RUN_USER" /var/log/clarion_lab

# Create validation config
echo "[6/7] Creating validation config..."
sudo tee "$RUN_HOME/clarion/lab/validation_config.json" > /dev/null << EOF
{
  "clarion_api": "http://192.168.30.2:5000/api",
  "ground_truth_source": "pi-orchestrator:$RUN_HOME/clarion_lab/ground_truth/ground_truth_log.csv",
  "output_dir": "$RUN_HOME/clarion_lab/validation",
  "thresholds": {
    "correlation_coverage": 0.90,
    "grouping_purity": 0.85,
    "backend_exclusivity": 0.90,
    "false_merges": 0.10,
    "anomaly_detection": 0.75
  }
}
EOF
sudo chown "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion/lab/validation_config.json"

# Create helper script to sync ground truth from orchestrator
echo "[7/7] Creating sync helper..."
# Assuming we will configure specific IP later or use a default
ORCHESTRATOR_IP="192.168.20.95" # Default as per LAB_MASTER_PLAN

sudo tee "$RUN_HOME/clarion_lab/sync_ground_truth.sh" > /dev/null << EOF
#!/bin/bash
# Sync ground truth log from orchestrator
# Usage: ./sync_ground_truth.sh

ORCHESTRATOR="$RUN_USER@$ORCHESTRATOR_IP"
REMOTE_PATH="$RUN_HOME/clarion_lab/ground_truth/ground_truth_log.csv"
LOCAL_PATH="$RUN_HOME/clarion_lab/ground_truth/ground_truth_log.csv"

echo "Syncing ground truth from orchestrator ($ORCHESTRATOR_IP)..."
rsync -avz "\$ORCHESTRATOR:\$REMOTE_PATH" "\$LOCAL_PATH" || {
    echo "ERROR: Failed to sync ground truth"
    echo "Make sure SSH access to orchestrator is configured"
    echo "SSH Command: ssh-copy-id $RUN_USER@$ORCHESTRATOR_IP"
    exit 1
}

echo "✓ Ground truth synced successfully"
echo "Lines: \$(wc -l < \$LOCAL_PATH)"
EOF

sudo chmod +x "$RUN_HOME/clarion_lab/sync_ground_truth.sh"
sudo chown "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab/sync_ground_truth.sh"

# Create validation runner script
sudo tee "$RUN_HOME/clarion_lab/run_validation.sh" > /dev/null << EOF
#!/bin/bash
# Run complete validation workflow
# Usage: ./run_validation.sh

set -e

SCRIPT_DIR="$RUN_HOME/clarion/lab"
OUTPUT_DIR="$RUN_HOME/clarion_lab/validation"
TIMESTAMP=\$(date +%Y-%m-%d_%H-%M-%S)

echo "=========================================="
echo "Clarion Validation Run - \$TIMESTAMP"
echo "=========================================="

# Step 1: Sync ground truth
echo "[1/3] Syncing ground truth from orchestrator..."
$RUN_HOME/clarion_lab/sync_ground_truth.sh

# Step 2: Run validation
echo "[2/3] Running validation..."
python3 "\$SCRIPT_DIR/validate_clarion_grouping.py" \
    --ground-truth $RUN_HOME/clarion_lab/ground_truth/ground_truth_log.csv \
    --clarion-api "http://192.168.30.2:5000/api" \
    --output "\$OUTPUT_DIR/validation_report.json"

# Step 3: Archive report
echo "[3/3] Archiving report..."
cp "\$OUTPUT_DIR/validation_report.json" "$RUN_HOME/reports/validation_\$TIMESTAMP.json"

# Display summary
echo ""
echo "=========================================="
echo "Validation Complete"
echo "=========================================="
echo "Latest report: \$OUTPUT_DIR/validation_report.json"
echo "Archived to: $RUN_HOME/reports/validation_\$TIMESTAMP.json"
echo ""
if [ -f "\$OUTPUT_DIR/validation_report.json" ]; then
    cat "\$OUTPUT_DIR/validation_report.json" | python3 -m json.tool | grep -E '"(pass|correlation_coverage|grouping_purity_avg|backend_exclusivity_avg|false_merges|anomaly_detection)"'
else
    echo "No report generated."
fi
EOF

sudo chmod +x "$RUN_HOME/clarion_lab/run_validation.sh"
sudo chown "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab/run_validation.sh"

echo ""
echo "=========================================="
echo "✓ Validator setup complete!"
echo "=========================================="
echo ""
echo "Hostname: $(hostname)"
echo ""
echo "Next steps:"
echo "1. Verify Orchestrator IP in $RUN_HOME/clarion_lab/sync_ground_truth.sh (Default: $ORCHESTRATOR_IP)"
echo "2. Setup SSH access to Orchestrator:"
echo "   ssh-copy-id $RUN_USER@$ORCHESTRATOR_IP"
echo "3. Run validation:"
echo "   $RUN_HOME/clarion_lab/run_validation.sh"
echo ""
