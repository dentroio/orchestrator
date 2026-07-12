#!/bin/bash
# deploy_server.sh - Remote trigger for server deployment
# Usage: ./deploy_server.sh [user@ip]
# Default: steve@192.168.31.2

TARGET="${1:-steve@192.168.31.2}"

echo "=========================================="
echo "Clarion Lab - Remote Server Deployment"
echo "Target: $TARGET"
echo "=========================================="

echo "Connecting to $TARGET..."

# We execute a sequence of commands over SSH:
# 1. Install git (if missing)
# 2. Clone/Pull repo
# 3. Exec setup_iot_backend.sh

ssh -t "$TARGET" '
    set -e
    echo "[Remote] Checking git..."
    if ! command -v git &> /dev/null; then
        echo "[Remote] Installing git..."
        sudo apt update && sudo apt install -y git
    fi

    echo "[Remote] Setting up repo (Sparse Checkout preference)..."
    mkdir -p ~/clarion
    cd ~/clarion
    if [ ! -d ".git" ]; then
        git init
        git remote add origin https://github.com/dentroio/clarion.git
        git config core.sparseCheckout true
        echo "lab/" >> .git/info/sparse-checkout
        git pull --depth=1 origin main
    else
        echo "[Remote] Updating..."
        git config core.sparseCheckout true
        echo "lab/" > .git/info/sparse-checkout
        git pull --depth=1 origin main
    fi

    echo "[Remote] Running setup..."
    cd ~/clarion/lab
    chmod +x setup_iot_backend.sh
    ./setup_iot_backend.sh
'

echo "=========================================="
echo "Deployment triggered successfully."
echo "=========================================="
