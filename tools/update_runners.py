#!/usr/bin/env python3
import urllib.request
import json
import sys

API_GET_CONFIG = "http://localhost:5000/api/config"
API_POST_RUNNERS = "http://localhost:5000/api/config/runners"

def main():
    print("Fetching current config...")
    try:
        req = urllib.request.Request(API_GET_CONFIG)
        with urllib.request.urlopen(req, timeout=10) as resp:
            config = json.loads(resp.read().decode())
    except Exception as e:
        print(f"ERROR: Failed to fetch current config: {e}", file=sys.stderr)
        sys.exit(1)

    runners = config.get("runners", [])
    print(f"Loaded {len(runners)} runner configurations.")

    updated_runners = []
    for r in runners:
        r_copy = dict(r)
        name = r_copy.get("name")
        if name == "pi-runner-3":
            print("Updating pi-runner-3: adding IT to persona_set")
            r_copy["persona_set"] = ["Engineering", "IT"]
        elif name == "pi-runner-4":
            print("Updating pi-runner-4: changing to OT MAB (session_duration=120)")
            r_copy["persona_set"] = [
                "PLC", "HMI", "SCADA Server", "Historian", "Engineering Workstation", 
                "DCS Controller", "Safety Controller", "RTU", "Industrial Gateway"
            ]
            r_copy["session_duration"] = 120
        elif name == "pi-runner-5":
            print("Updating pi-runner-5: changing session_duration to 120")
            r_copy["session_duration"] = 120
        elif name == "pi-runner-6":
            print("Updating pi-runner-6: changing session_duration to 120")
            r_copy["session_duration"] = 120
        
        updated_runners.append(r_copy)

    print("Posting updated runners back to orchestrator...")
    try:
        data = json.dumps(updated_runners).encode('utf-8')
        req = urllib.request.Request(
            API_POST_RUNNERS, 
            data=data, 
            method="POST", 
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            print(f"SUCCESS: Server response: {result}")
    except Exception as e:
        print(f"ERROR: Failed to save updated runners: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
