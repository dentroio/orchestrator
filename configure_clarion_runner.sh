#!/bin/bash
# Configure clarion-runner.service override (no interactive editor needed).
# Run on the Pi: sudo ./configure_clarion_runner.sh
# Set ORCHESTRATOR_URL and RUNNER_ID (env or edit below). Installs base unit if missing.

ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://192.168.20.95:5000}"
RUNNER_ID="${RUNNER_ID:-pi-runner-1}"
LAB_DIR="${LAB_DIR:-/home/admin/clarion/lab}"
AGENT_DIR="${AGENT_DIR:-$LAB_DIR/orchestrator/app}"

# Install base unit if missing (so "configure" is enough even if user skipped the cp step)
if [ ! -f /etc/systemd/system/clarion-runner.service ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$LAB_DIR/clarion-runner.service" ]; then
        cp "$LAB_DIR/clarion-runner.service" /etc/systemd/system/clarion-runner.service
        echo "Installed /etc/systemd/system/clarion-runner.service from $LAB_DIR"
    elif [ -f "$SCRIPT_DIR/clarion-runner.service" ]; then
        cp "$SCRIPT_DIR/clarion-runner.service" /etc/systemd/system/clarion-runner.service
        echo "Installed /etc/systemd/system/clarion-runner.service from $SCRIPT_DIR"
    else
        echo "ERROR: Base unit not found. Copy it first: sudo cp $LAB_DIR/clarion-runner.service /etc/systemd/system/"
        exit 1
    fi
fi

# Override Environment, WorkingDirectory, and ExecStart so the service follows AGENT_DIR.
# Reset ExecStart first; systemd requires an empty assignment before replacement.
mkdir -p /etc/systemd/system/clarion-runner.service.d
cat > /etc/systemd/system/clarion-runner.service.d/override.conf << EOF
[Service]
Environment="ORCHESTRATOR_URL=$ORCHESTRATOR_URL"
Environment="RUNNER_ID=$RUNNER_ID"
WorkingDirectory=$AGENT_DIR
ExecStart=
ExecStart=/usr/bin/python3 $AGENT_DIR/runner_agent.py --orchestrator-url \${ORCHESTRATOR_URL} --runner-id \${RUNNER_ID}
EOF

echo "Override written to /etc/systemd/system/clarion-runner.service.d/override.conf"
systemctl daemon-reload
echo "Done. Start with: sudo systemctl enable clarion-runner && sudo systemctl start clarion-runner"
