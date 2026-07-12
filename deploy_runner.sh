#!/bin/bash
# deploy_runner.sh - Push lab code to a Pi runner (no apt, no setup script)
# Use this for quick code updates (e.g. identity_switcher.py) without touching package manager.
# Usage: ./deploy_runner.sh [user@host]
# Example: ./deploy_runner.sh admin@192.168.1.187
#
# Version: 2026-02-17 (client/server; runners are agents only)

SCRIPT_VERSION="2026-02-17"
TARGET=${1:-admin@192.168.1.187}
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=5"

echo "=========================================="
echo "Pushing lab code to $TARGET (code only, no apt)..."
echo "Script version: $SCRIPT_VERSION"
echo "=========================================="

if [ -d "lab" ]; then
    SRC_DIR="$(pwd)/lab/"
elif [ -f "deploy_runner.sh" ]; then
    SRC_DIR="$(pwd)/"
else
    echo "Error: Run from repo root or lab/ directory."
    exit 1
fi

ssh $SSH_OPTS $TARGET "mkdir -p ~/clarion/lab" || { echo "Error: Could not connect to $TARGET"; exit 1; }

# --delete: remove files on remote that are no longer in source (clean up legacy/removed files)
rsync -avz --delete \
    --exclude '__pycache__' --exclude '*.pyc' --exclude '.git' \
    --exclude 'orchestrator_config.json' \
    -e "ssh $SSH_OPTS" \
    "$SRC_DIR" $TARGET:~/clarion/lab/

echo "=========================================="
echo "Done. Code at ~/clarion/lab/ on $TARGET"
echo "=========================================="
