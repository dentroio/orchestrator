#!/usr/bin/env python3
"""One-shot: user identities *-ws -> *-wst in clarion_lab.db. See db.migrate_user_device_names_ws_to_wst."""
import argparse
import os
import sys

# Allow running from repo root or lab/
LAB_DIR = os.path.dirname(os.path.abspath(__file__))
if LAB_DIR not in sys.path:
    sys.path.insert(0, LAB_DIR)

import db  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Rename user device_name *-ws to *-wst in lab SQLite DB.")
    p.add_argument(
        "--db",
        default=os.path.join(LAB_DIR, "clarion_lab.db"),
        help="Path to clarion_lab.db (default: lab/clarion_lab.db next to this script)",
    )
    args = p.parse_args()
    n = db.migrate_user_device_names_ws_to_wst(args.db)
    print(f"Updated {n} user device_name value(s) (*-ws -> *-wst). DB={args.db}")
    if n:
        print("Restart clarion-orchestrator if it is running so the in-memory copy reloads from DB.")


if __name__ == "__main__":
    main()
