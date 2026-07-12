#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$ROOT_DIR/.." && pwd)"
OUT_DIR="$ROOT_DIR/dist/windows-runner-installer"

mkdir -p "$OUT_DIR"
cp "$ROOT_DIR/install_windows_runner.ps1" "$OUT_DIR/"
cp "$ROOT_DIR/install_windows_runner.cmd" "$OUT_DIR/"
cp "$APP_DIR/windows_runner_agent.ps1" "$OUT_DIR/"
cp "$APP_DIR/windows_runner_watchdog.ps1" "$OUT_DIR/"

if command -v zip >/dev/null 2>&1; then
  (cd "$ROOT_DIR/dist" && zip -r "windows-runner-installer.zip" "windows-runner-installer" >/dev/null)
  echo "Built: $ROOT_DIR/dist/windows-runner-installer.zip"
else
  echo "Built directory: $OUT_DIR"
  echo "Install zip utility to produce a zip archive."
fi
