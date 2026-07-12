#!/bin/bash
# deploy_orchestrator.sh - Push deployment for Orchestrator
# Usage: ./deploy_orchestrator.sh [user@ip]              # full deploy (copy + setup)
#        ./deploy_orchestrator.sh --code-only [user@ip]  # copy files only (like deploy_runner.sh)
# Example: ./deploy_orchestrator.sh admin@192.168.20.95
#          ./deploy_orchestrator.sh --code-only admin@192.168.20.95
#
# Version: 2026-02-17 (client/server, no SSH to runners; config in DB)

SCRIPT_VERSION="2026-02-17"
CODE_ONLY=false
TARGET=""

for arg in "$@"; do
    case "$arg" in
        --code-only) CODE_ONLY=true ;;
        *)           TARGET="$arg" ;;
    esac
done
TARGET="${TARGET:-admin@192.168.20.95}"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=5"

echo "=========================================="
if [ "$CODE_ONLY" = true ]; then
    echo "Pushing orchestrator code to $TARGET (code only, no setup)..."
else
    echo "Deploying Orchestrator to $TARGET (copy + setup)..."
fi
echo "Script version: $SCRIPT_VERSION"
echo "=========================================="

# 1. Create remote directory
echo "[1/$([ "$CODE_ONLY" = true ] && echo 2 || echo 3)] Creating remote directory..."
ssh $SSH_OPTS $TARGET "mkdir -p ~/clarion/lab" || {
    echo "Error: Could not connect to $TARGET"
    exit 1
}

# Determine if we are in 'lab' or root
if [ -d "lab" ]; then
    # We are at repo root
    SRC_DIR="$(pwd)/lab/"
elif [ -f "deploy_orchestrator.sh" ]; then
    # We are inside 'lab' directory
    SRC_DIR="$(pwd)/"
else
    echo "Error: Cannot find lab files. Run from repo root or inside lab/ directory."
    exit 1
fi

echo "[2/$([ "$CODE_ONLY" = true ] && echo 2 || echo 3)] Pushing lab files from $SRC_DIR (remote files not in source are removed)..."
# --delete: remove files on remote that are no longer in source (clean up legacy/removed files)
# Exclude DB and one-time import files. Protect ground_truth/ and logs/ so we never delete remote data.
rsync -avz --delete \
    --exclude '__pycache__' --exclude '*.pyc' --exclude '.git' \
    --exclude 'orchestrator_config.json' \
    --exclude 'identities1.json' \
    --exclude 'clarion_lab.db' \
    --exclude 'ground_truth/' --exclude 'logs/' \
    --filter 'protect ground_truth/' --filter 'protect logs/' \
    -e "ssh $SSH_OPTS" \
    "$SRC_DIR" $TARGET:~/clarion/lab/

if [ "$CODE_ONLY" = true ]; then
    echo "=========================================="
    echo "Done. Code at ~/clarion/lab/ on $TARGET"
    echo "Restart service to pick up changes: ssh $TARGET 'sudo systemctl restart clarion-orchestrator'"
    echo "=========================================="
    exit 0
fi

# 3. Run setup script
echo "[3/3] Running setup script on remote..."
ssh $SSH_OPTS -t $TARGET "chmod +x ~/clarion/lab/setup_orchestrator.sh && ~/clarion/lab/setup_orchestrator.sh --skip-git"

echo "=========================================="
echo "Deployment complete!"
echo "=========================================="
