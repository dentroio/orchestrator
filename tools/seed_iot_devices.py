#!/usr/bin/env python3
"""
Seed 150 new specialized OT/IoT devices into the orchestrator database.

Usage:
    python3 tools/seed_iot_devices.py --dry-run
    python3 tools/seed_iot_devices.py --apply
"""

import argparse
import json
import random
import sys
import urllib.request
from collections import defaultdict

API = "http://localhost:5000/api"

NEW_PERSONAS = {
    "Smart Thermostat": {
        "manufacturer": "Honeywell",
        "oui": "00:1C:2B",
        "prefix": "thermostat-bms-",
        "count": 50
    },
    "Power Meter": {
        "manufacturer": "Schneider Electric",
        "oui": "00:80:67",
        "prefix": "pmeter-bms-",
        "count": 50
    },
    "Smart PDU": {
        "manufacturer": "APC / Schneider",
        "oui": "00:0E:3C",
        "prefix": "pdu-itops-",
        "count": 50
    }
}


def get(path):
    with urllib.request.urlopen(f"{API}/{path}", timeout=15) as r:
        return json.loads(r.read().decode())


def post(path, payload):
    req = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def generate_mac(oui, rng):
    """Generate a unique MAC address with the specified OUI."""
    suffix = [f"{rng.randint(0, 255):02X}" for _ in range(3)]
    return f"{oui}:{':'.join(suffix)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="POST updates to the Orchestrator database")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.apply and not args.dry_run:
        print("Please specify --apply or --dry-run")
        return 1

    # Fetch current identities from the API
    if args.apply:
        try:
            current_identities = get("identities")
            print(f"Loaded {len(current_identities)} existing identities from Orchestrator API")
        except Exception as e:
            print(f"Error connecting to Orchestrator API: {e}")
            return 1
    else:
        current_identities = []
        print("Dry run: Skipping connection to Orchestrator API")

    existing_macs = {i.get("mac").upper() for i in current_identities if i.get("mac")}
    rng = random.Random(88)

    new_devices = []
    for persona, conf in NEW_PERSONAS.items():
        count = conf["count"]
        for idx in range(1, count + 1):
            mac = generate_mac(conf["oui"], rng)
            while mac.upper() in existing_macs:
                mac = generate_mac(conf["oui"], rng)
            
            existing_macs.add(mac.upper())
            name = f"{conf['prefix']}{idx:02d}"
            
            # Format according to orchestrator identity schema
            device = {
                "mac": mac,
                "device_name": name,
                "display_name": f"{persona} {idx:02d}",
                "manufacturer": conf["manufacturer"],
                "os": "Linux",
                "username": "",
                "password": "C!sco#123",
                "ssid": "netlab_iot",
                "persona": persona,
                "department": persona  # apply_cohorts fallback key
            }
            new_devices.append(device)

    print(f"\nGenerated {len(new_devices)} new OT/IoT devices:")
    counts = defaultdict(int)
    for d in new_devices:
        counts[d["persona"]] += 1
    for persona, count in counts.items():
        print(f"  - {persona:20} {count:4} devices")

    if args.dry_run:
        print("\nDry run completed successfully. No changes made.")
        return 0

    # Append and upload to orchestrator
    all_identities = current_identities + new_devices
    print(f"\nUploading {len(all_identities)} total identities to Orchestrator...")
    try:
        resp = post("identities", all_identities)
        print(f"✓ Orchestrator successfully updated: {resp}")
    except Exception as e:
        print(f"✗ Failed to upload identities to API: {e}")
        return 1

    print("\nOT/IoT Device Seeding Completed Successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
