#!/usr/bin/env python3
import urllib.request
import json
import subprocess
import sys

API_STOP = "http://localhost:5000/api/stop"
API_GET_CONFIG = "http://localhost:5000/api/config"

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=8",
    "-o", "BatchMode=yes"
]

def main():
    print("1. Stopping the orchestrator loop...")
    try:
        req = urllib.request.Request(API_STOP, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode())
            print(f"Orchestrator stopped response: {res}")
    except Exception as e:
        print(f"WARNING: Failed to stop orchestrator: {e}")

    print("\n2. Fetching runner configurations...")
    try:
        with urllib.request.urlopen(API_GET_CONFIG, timeout=10) as resp:
            config = json.loads(resp.read().decode())
            runners = config.get("runners", [])
    except Exception as e:
        print(f"ERROR: Failed to fetch config: {e}")
        sys.exit(1)

    print(f"Found {len(runners)} runners.")

    for runner in runners:
        name = runner.get("name")
        host = runner.get("host")
        user = runner.get("user", "admin")
        runner_type = runner.get("runner_type", "pi")

        # win-runner check or missing host
        if not host or runner_type == "windows" or "win-runner" in name:
            print(f"Skipping {name} (type: {runner_type}, host: {host})")
            continue

        lab_iface = runner.get("interface", "eth0")
        print(f"\n--- Stopping Pi Runner: {name} ({user}@{host}) ---")

        # 1. Stop clarion-runner service
        stop_service_cmd = ["ssh"] + SSH_OPTS + [f"{user}@{host}", "sudo systemctl stop clarion-runner"]
        print(f"Executing: {' '.join(stop_service_cmd)}")
        res_service = subprocess.run(stop_service_cmd, capture_output=True, text=True)
        if res_service.stdout.strip():
            print(f"Service stop stdout: {res_service.stdout.strip()}")
        if res_service.stderr.strip():
            print(f"Service stop stderr: {res_service.stderr.strip()}")

        # 2. Tear down lab interface
        teardown_cmd_str = (
            f"sudo nmcli connection down clarion-lab-auth 2>/dev/null; "
            f"sudo nmcli connection down clarion-lab-wifi 2>/dev/null; "
            f"sudo nmcli device disconnect {lab_iface} 2>/dev/null; "
            f"sudo ip link set {lab_iface} down"
        )
        teardown_cmd = ["ssh"] + SSH_OPTS + [f"{user}@{host}", teardown_cmd_str]
        print(f"Executing: {' '.join(teardown_cmd)}")
        res_teardown = subprocess.run(teardown_cmd, capture_output=True, text=True)
        if res_teardown.stdout.strip():
            print(f"Teardown stdout: {res_teardown.stdout.strip()}")
        if res_teardown.stderr.strip():
            print(f"Teardown stderr: {res_teardown.stderr.strip()}")

    print("\nALL LAB INTERFACES DEACTIVATED AND DOWN.")

if __name__ == "__main__":
    main()
