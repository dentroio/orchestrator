#!/usr/bin/env python3
"""
One-shot migration: Fix existing user identities that have missing personas/OS
or are using Raspberry Pi OUIs, updating them to Dell/HP/Lenovo/Apple OUI MACs.
"""

import argparse
import os
import sys

# Setup sys.path to find db module in parent directory
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import db

def main() -> None:
    p = argparse.ArgumentParser(description="Fix user identities using Pi OUIs in lab SQLite DB.")
    p.add_argument(
        "--db",
        default=os.path.join(REPO_ROOT, "clarion_lab.db"),
        help="Path to clarion_lab.db",
    )
    args = p.parse_args()
    
    db_path = args.db
    if not os.path.exists(db_path):
        # Fallback to standard remote lab path if local does not exist and running on remote
        fallback_path = os.path.expanduser("~/clarion/lab/clarion_lab.db")
        if os.path.exists(fallback_path):
            db_path = fallback_path
        else:
            fallback_path_2 = "/home/admin/clarion/lab/clarion_lab.db"
            if os.path.exists(fallback_path_2):
                db_path = fallback_path_2
                
    print(f"Using database: {db_path}")
    if not os.path.exists(db_path):
        print(f"ERROR: Database path not found: {db_path}", file=sys.stderr)
        sys.exit(1)
        
    n = db.fix_existing_user_oui_and_personas(db_path)
    print(f"Successfully updated {n} user identity profile(s) with correct PC OUIs and personas.")
    if n:
        print("Please restart clarion-orchestrator if it is running so the server reloads the updated database records.")

if __name__ == "__main__":
    main()
