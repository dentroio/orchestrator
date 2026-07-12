#!/usr/bin/env python3
"""
Update a runner's interface and management_interface in the orchestrator DB.

The orchestrator uses the database (clarion_lab.db), not JSON files. Use this
script on the orchestrator host to fix a runner's lab/management interfaces
(e.g. pi-runner-6: lab=eth0, management=wlan0).

Usage (on orchestrator host 192.168.20.95):
  cd ~/clarion/lab
  python3 set_runner_interface.py --runner pi-runner-6 --interface eth0 --management-interface wlan0

After running, restart the orchestrator web process so the in-memory config picks up the change.
"""

import argparse
import os
import sys

LAB_DIR = os.path.dirname(os.path.abspath(__file__))
if LAB_DIR not in sys.path:
    sys.path.insert(0, LAB_DIR)

import db


def main():
    parser = argparse.ArgumentParser(
        description="Set a runner's interface and management_interface in the orchestrator DB."
    )
    parser.add_argument("--runner", required=True, help="Runner name (e.g. pi-runner-6)")
    parser.add_argument("--interface", required=True, help="Lab interface (e.g. eth0)")
    parser.add_argument("--management-interface", required=True, dest="management_interface",
                        help="Management interface (e.g. wlan0)")
    parser.add_argument("--db", default=None, help="Path to clarion_lab.db (default: lab/clarion_lab.db)")
    parser.add_argument("--show", action="store_true", help="Only show current runners config, do not change")
    args = parser.parse_args()

    db_path = args.db or os.path.join(LAB_DIR, "clarion_lab.db")
    if not os.path.exists(db_path):
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    runners = db.get_config("runners", db_path)
    if not runners:
        print("No runners in DB.", file=sys.stderr)
        sys.exit(1)

    if args.show:
        for r in runners:
            print(f"  {r.get('name')}: interface={r.get('interface')}, management_interface={r.get('management_interface')}")
        return

    found = False
    for r in runners:
        if r.get("name") == args.runner:
            r["interface"] = args.interface
            r["management_interface"] = args.management_interface
            found = True
            break

    if not found:
        print(f"Runner '{args.runner}' not found. Existing: {[x.get('name') for x in runners]}", file=sys.stderr)
        sys.exit(1)

    db.set_config("runners", runners, db_path)
    print(f"Updated {args.runner}: interface={args.interface}, management_interface={args.management_interface}")
    print("Restart the orchestrator web process so the change takes effect.")


if __name__ == "__main__":
    main()
