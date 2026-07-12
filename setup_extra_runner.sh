#!/bin/bash
# setup_extra_runner.sh - Deploy to Pi-Rebuild-2 (Additional Endpoint Generator)
# Usage: ./setup_extra_runner.sh

set -e

echo "=========================================="
echo "Clarion Lab - Additional Endpoint Setup"
echo "=========================================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
   echo "Please run as regular user (pi), not root"
   exit 1
fi

# 1. Run standard runner setup (installs libs, keys, directories)
echo "Running base runner setup..."
if [ -f "./setup_pi_runner.sh" ]; then
    # Pass all arguments (like --skip-git) to the base script
    ./setup_pi_runner.sh "$@"
else
    echo "Error: setup_pi_runner.sh not found. Please pull the repo."
    exit 1
fi

# User/Home detection
RUN_USER=${SUDO_USER:-$USER}
RUN_HOME=$(getent passwd "$RUN_USER" | cut -d: -f6)

# 2. Configure specifically for "Extra Endpoint" role
echo "Applying custom configuration for Pi-Rebuild-2..."
sudo tee "$RUN_HOME/clarion_lab/runner_config.json" > /dev/null << EOF
{
  "runner_id": "pi-rebuild-2",
  "interface": "eth0",
  "persona_set": ["Engineering", "IT", "Sales", "Finance"],
  "session_duration": 300,
  "notes": "Rotates through all user personas to generate background noise/volume"
}
EOF
sudo chown "$RUN_USER:$RUN_USER" "$RUN_HOME/clarion_lab/runner_config.json"

echo ""
echo "=========================================="
echo "✓ Additional Endpoint setup complete!"
echo "=========================================="
echo "This node is configured to rotate through all personas."
echo "Auto-start command:"
echo "  sudo python3 $RUN_HOME/clarion/lab/auto_lab_runner.py --interface eth0 --session-duration 300"
