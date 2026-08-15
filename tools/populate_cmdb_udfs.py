#!/usr/bin/env python3
import sqlite3
import json
import pexpect
import sys
import time
import random

# Use same DB path
DB_PATH = "/home/admin/clarion/lab/clarion_lab.db"

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

    print(f"Loaded {len(identities)} identities. Generating realistic CMDB Customer UDF values...")

    udf_values = []
    # Seed random for deterministic generation
    random.seed(42)

    for i, ident in enumerate(identities):
        mac = ident.get("mac")
        if not mac:
            continue
        mac = mac.strip().lower()

        auth = (ident.get("auth") or "dot1x").lower()
        dept = ident.get("department") or "IoT"
        persona = ident.get("persona") or "Workstation"
        mfg = ident.get("manufacturer") or "Generic"
        username = ident.get("username")

        # 1. Owner
        if auth == "mab":
            if dept == "OT":
                owner = "OT Operations Team"
            elif persona == "Camera":
                owner = "Physical Security Team"
            elif persona == "Badge Reader":
                owner = "Facilities Team"
            else:
                owner = "IT Infrastructure Operations"
        else:
            if username:
                # Remove domain prefix if any
                owner = username.split("\\")[-1].replace(".", " ").title()
            else:
                owner = "Corporate User"

        # 2. Business Criticality
        if persona in ["PLC", "HMI", "SCADA Server", "Historian", "DCS Controller", "Safety Controller"]:
            criticality = "High"
        elif persona in ["Workstation", "Badge Reader", "VoIP Phone", "RTU", "Industrial Gateway"]:
            criticality = "Medium"
        else:
            criticality = "Low"

        # 3. Support Vendor
        support_vendor = f"{mfg} Enterprise Support"

        # 4. Contract Number
        contract_number = f"CON-{100000 + (i * 143) % 900000}"

        # 5. SLA Level
        if criticality == "High":
            sla_level = "24x7 2-Hour Response"
        elif criticality == "Medium":
            sla_level = "8x5 Next Business Day"
        else:
            sla_level = "Best Effort"

        # Add to values
        udf_values.append({"device_mac": mac, "field_name": "owner", "value_text": owner})
        udf_values.append({"device_mac": mac, "field_name": "business_criticality", "value_text": criticality})
        udf_values.append({"device_mac": mac, "field_name": "support_vendor", "value_text": support_vendor})
        udf_values.append({"device_mac": mac, "field_name": "contract_number", "value_text": contract_number})
        udf_values.append({"device_mac": mac, "field_name": "sla_level", "value_text": sla_level})

    # Write export JSON
    try:
        with open("/home/admin/udf_values_export.json", "w") as f:
            json.dump(udf_values, f, indent=2)
        print("Wrote enriched UDF values to /home/admin/udf_values_export.json")
    except Exception as e:
        print(f"ERROR: Failed to write export file: {e}", file=sys.stderr)
        sys.exit(1)

    # Importer code that runs on the CMDB server (192.168.30.2)
    importer_code = """#!/usr/bin/env python3
import sqlite3
import json
import sys

CMDB_DB_PATH = "/home/steve/cmdb/backend/cmdb.db"
EXPORT_JSON_PATH = "/tmp/udf_values_export.json"

def main():
    print("CMDB UDF Importer: Loading exported values...")
    try:
        with open(EXPORT_JSON_PATH, "r") as f:
            values = json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to read JSON export: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        conn = sqlite3.connect(CMDB_DB_PATH)
        cursor = conn.cursor()
    except Exception as e:
        print(f"ERROR: Failed to connect to CMDB DB: {e}", file=sys.stderr)
        sys.exit(1)

    inserted = 0
    updated = 0

    for item in values:
        mac = item["device_mac"]
        field = item["field_name"]
        val = item["value_text"]

        cursor.execute("SELECT 1 FROM device_udf_values WHERE device_mac = ? AND field_name = ?", (mac, field))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE device_udf_values SET value_text = ? WHERE device_mac = ? AND field_name = ?", (val, mac, field))
            updated += 1
        else:
            cursor.execute("INSERT INTO device_udf_values (device_mac, field_name, value_text) VALUES (?, ?, ?)", (mac, field, val))
            inserted += 1

    conn.commit()
    conn.close()
    print(f"CMDB UDF Importer: Completed successfully. Inserted: {inserted}, Updated: {updated}")

if __name__ == "__main__":
    main()
"""

    try:
        with open("/home/admin/cmdb_udf_importer.py", "w") as f:
            f.write(importer_code)
        print("Wrote UDF importer script to /home/admin/cmdb_udf_importer.py")
    except Exception as e:
        print(f"ERROR: Failed to write UDF importer script: {e}", file=sys.stderr)
        sys.exit(1)

    # Transfer and execute
    try:
        print("Copying export JSON to 192.168.30.2...")
        child = pexpect.spawn("scp -o StrictHostKeyChecking=no /home/admin/udf_values_export.json steve@192.168.30.2:/tmp/", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(pexpect.EOF)

        print("Copying UDF importer script to 192.168.30.2...")
        child = pexpect.spawn("scp -o StrictHostKeyChecking=no /home/admin/cmdb_udf_importer.py steve@192.168.30.2:/tmp/", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(pexpect.EOF)

        print("Executing UDF importer script on 192.168.30.2...")
        child = pexpect.spawn("ssh -o StrictHostKeyChecking=no steve@192.168.30.2", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(r"steve@")

        child.sendline("python3 /tmp/cmdb_udf_importer.py")
        child.expect(r"steve@")
        print(child.before)

        # Cleanup remote
        child.sendline("rm /tmp/cmdb_udf_importer.py /tmp/udf_values_export.json")
        child.expect(r"steve@")
        print("Cleaned up files on 192.168.30.2")

        # Cleanup local
        import os
        os.remove("/home/admin/udf_values_export.json")
        os.remove("/home/admin/cmdb_udf_importer.py")
        print("Cleaned up local temporary files.")

        print("CMDB UDF VALUES POPULATED SUCCESSFULLY!")
    except Exception as e:
        print(f"ERROR: UDF enrichment process failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
