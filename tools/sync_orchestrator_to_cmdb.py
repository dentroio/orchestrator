#!/usr/bin/env python3
import sqlite3
import json
import pexpect
import sys
import time

DB_PATH = "/home/admin/clarion/lab/clarion_lab.db"
IMPORTER_SCRIPT_PATH = "/tmp/cmdb_importer.py"
EXPORT_JSON_PATH = "/tmp/identities_export.json"

def main():
    print("Loading identities from orchestrator database...")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT data FROM identities").fetchall()
        identities = [json.loads(r["data"]) for r in rows]
        conn.close()
    except Exception as e:
        print(f"ERROR: Failed to read from orchestrator DB: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(identities)} identities.")

    # Write identities to a temporary JSON file
    try:
        with open("/home/admin/identities_export.json", "w") as f:
            json.dump(identities, f, indent=2)
        print("Wrote identities to /home/admin/identities_export.json")
    except Exception as e:
        print(f"ERROR: Failed to write export file: {e}", file=sys.stderr)
        sys.exit(1)

    # Importer script code that will run on the CMDB server (192.168.30.2)
    importer_code = """#!/usr/bin/env python3
import sqlite3
import json
import sys
import time

CMDB_DB_PATH = "/home/steve/cmdb/backend/cmdb.db"
EXPORT_JSON_PATH = "/tmp/identities_export.json"

def normalize_mac(mac):
    import re
    raw = re.sub(r"[^0-9A-Fa-f]", "", str(mac or ""))
    if len(raw) != 12:
        return None
    raw = raw.lower()
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))

def main():
    print("CMDB Importer: Loading exported JSON...")
    try:
        with open(EXPORT_JSON_PATH, "r") as f:
            identities = json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to read JSON export: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"CMDB Importer: Loaded {len(identities)} identities.")

    try:
        conn = sqlite3.connect(CMDB_DB_PATH)
        cursor = conn.cursor()
    except Exception as e:
        print(f"ERROR: Failed to connect to CMDB DB: {e}", file=sys.stderr)
        sys.exit(1)

    now = int(time.time())
    inserted = 0
    updated = 0

    for ident in identities:
        mac = normalize_mac(ident.get("mac"))
        if not mac:
            continue

        auth = (ident.get("auth") or "dot1x").lower()
        if auth == "mab":
            auth_method = "MAB"
            device_type = ident.get("persona") or "MAB Device"
            assignment_group = ident.get("department") or "IoT"
            name = (ident.get("device_name") or mac).strip()
            hostname = (ident.get("device_name") or "").strip()
            os_val = None
        else:
            auth_method = "802.1X"
            device_type = "Workstation"
            assignment_group = ident.get("department") or "Sales"
            name = (ident.get("device_name") or ident.get("username") or mac).strip()
            hostname = (ident.get("device_name") or "").strip()
            os_val = ident.get("os") or "Windows"

        manufacturer = ident.get("manufacturer")
        status = "Active"

        # Check if MAC exists
        cursor.execute("SELECT id FROM devices WHERE mac_address = ?", (mac,))
        row = cursor.fetchone()

        if row:
            # Update existing device
            cursor.execute('''
                UPDATE devices
                SET
                    name = ?,
                    manufacturer = COALESCE(?, manufacturer),
                    hostname = ?,
                    os = COALESCE(?, os),
                    type = ?,
                    auth_method = ?,
                    assignment_group = ?,
                    status = ?,
                    updated_at = ?
                WHERE mac_address = ?
            ''', (name, manufacturer, hostname, os_val, device_type, auth_method, assignment_group, status, now, mac))
            updated += 1
        else:
            # Insert new device
            cursor.execute('''
                INSERT INTO devices (
                    mac_address, name, manufacturer, hostname, os, type, 
                    auth_method, assignment_group, status, 
                    created_at, updated_at, ise_sync_enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ''', (mac, name, manufacturer, hostname, os_val, device_type, auth_method, assignment_group, status, now, now))
            inserted += 1

    conn.commit()
    conn.close()
    print(f"CMDB Importer: Completed successfully. Inserted: {inserted}, Updated: {updated}")

if __name__ == "__main__":
    main()
"""

    try:
        with open("/home/admin/cmdb_importer.py", "w") as f:
            f.write(importer_code)
        print("Wrote importer script to /home/admin/cmdb_importer.py")
    except Exception as e:
        print(f"ERROR: Failed to write importer script: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        print("Copying export JSON to 192.168.30.2...")
        child = pexpect.spawn("scp -o StrictHostKeyChecking=no /home/admin/identities_export.json steve@192.168.30.2:/tmp/", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(pexpect.EOF)

        print("Copying importer script to 192.168.30.2...")
        child = pexpect.spawn("scp -o StrictHostKeyChecking=no /home/admin/cmdb_importer.py steve@192.168.30.2:/tmp/", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(pexpect.EOF)

        print("Executing importer script on 192.168.30.2...")
        child = pexpect.spawn("ssh -o StrictHostKeyChecking=no steve@192.168.30.2", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(r"steve@")

        child.sendline("python3 /tmp/cmdb_importer.py")
        child.expect(r"steve@")
        print(child.before)

        # Cleanup remote
        child.sendline("rm /tmp/cmdb_importer.py /tmp/identities_export.json")
        child.expect(r"steve@")
        print("Cleaned up files on 192.168.30.2")

        # Cleanup local
        import os
        os.remove("/home/admin/identities_export.json")
        os.remove("/home/admin/cmdb_importer.py")
        print("Cleaned up local temporary files.")

        print("SYNC COMPLETED SUCCESSFULLY!")
    except Exception as e:
        print(f"ERROR: Sync process failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
