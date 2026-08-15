#!/usr/bin/env python3
import sqlite3
import json
import pexpect
import sys
import time
import random
import string

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

    print(f"Loaded {len(identities)} identities. Generating realistic CMDB fields...")

    enriched_identities = []
    # Seed random for deterministic generation (or keep it dynamic)
    random.seed(42)
    
    for i, ident in enumerate(identities):
        auth = (ident.get("auth") or "dot1x").lower()
        dept = ident.get("department") or "IoT"
        persona = ident.get("persona") or "Workstation"
        mfg = ident.get("manufacturer") or "Generic"
        
        asset_tag = f"CMDB-{i+1:04d}"
        
        # Unique Serial
        chars = string.ascii_uppercase + string.digits
        serial_number = "".join(random.choices(chars, k=10))
        
        # OS
        os_val = ident.get("os")
        if not os_val:
            if auth == "mab":
                if persona in ["PLC", "HMI", "RTU", "DCS Controller", "Safety Controller"]:
                    os_val = "VxWorks"
                elif persona in ["Camera", "Badge Reader", "Printer", "Environmental Sensor", "HVAC Controller", "Display", "VoIP Phone", "Industrial Gateway", "Field Device"]:
                    os_val = "Embedded Linux"
                else:
                    os_val = "Embedded OS"
            else:
                os_val = "Windows"

        # Model
        model = "Generic"
        if persona == "PLC":
            if "siemens" in mfg.lower(): model = "Simatic S7-1500"
            elif "rockwell" in mfg.lower(): model = "ControlLogix 5580"
            elif "schneider" in mfg.lower(): model = "Modicon M580"
            else: model = "Industrial PLC v2"
        elif persona == "HMI":
            if "siemens" in mfg.lower(): model = "Simatic Comfort Panel"
            elif "rockwell" in mfg.lower(): model = "PanelView Plus 7"
            else: model = "Touchscreen HMI"
        elif persona == "SCADA Server":
            model = "Ignition SCADA Server"
        elif persona == "Camera":
            model = "Dome Network Camera"
        elif persona == "Printer":
            model = "LaserJet Pro MFP"
        elif persona == "Badge Reader":
            model = "Signo Reader 40"
        elif persona == "VoIP Phone":
            model = "IP Phone 8841"
        elif persona == "Workstation":
            if "apple" in mfg.lower(): model = "MacBook Pro"
            else: model = "OptiPlex 7090"
        else:
            model = f"{persona} Pro"

        # VLAN
        if auth == "mab":
            if dept == "OT":
                vlan = "12"
            else:
                vlan = "15"
        else:
            if dept == "Engineering":
                vlan = "40"
            else:
                vlan = "30"

        # Location
        if auth == "mab":
            if dept == "OT":
                location = "Plant 1 - Assembly Floor"
            else:
                location = "HQ - Facilities / Security"
        else:
            location = f"HQ - Floor {random.choice(['1', '2', '3'])} - {dept} Area"

        # Device Profile
        if auth == "mab":
            device_profile = f"Simulated {persona}"
        else:
            device_profile = "Corporate Workstation"

        # Merge fields
        enriched = {
            "mac": ident.get("mac"),
            "asset_tag": asset_tag,
            "serial_number": serial_number,
            "model": model,
            "vlan": vlan,
            "location": location,
            "device_profile": device_profile,
            "os": os_val
        }
        enriched_identities.append(enriched)

    # Write export JSON
    try:
        with open("/home/admin/identities_fields.json", "w") as f:
            json.dump(enriched_identities, f, indent=2)
        print("Wrote enriched fields to /home/admin/identities_fields.json")
    except Exception as e:
        print(f"ERROR: Failed to write export file: {e}", file=sys.stderr)
        sys.exit(1)

    # Importer code that runs on the CMDB server (192.168.30.2)
    importer_code = """#!/usr/bin/env python3
import sqlite3
import json
import sys
import time

CMDB_DB_PATH = "/home/steve/cmdb/backend/cmdb.db"
EXPORT_JSON_PATH = "/tmp/identities_fields.json"

def normalize_mac(mac):
    import re
    raw = re.sub(r"[^0-9A-Fa-f]", "", str(mac or ""))
    if len(raw) != 12:
        return None
    raw = raw.lower()
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))

def main():
    print("CMDB Enricher: Loading exported fields...")
    try:
        with open(EXPORT_JSON_PATH, "r") as f:
            fields = json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to read JSON export: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        conn = sqlite3.connect(CMDB_DB_PATH)
        cursor = conn.cursor()
    except Exception as e:
        print(f"ERROR: Failed to connect to CMDB DB: {e}", file=sys.stderr)
        sys.exit(1)

    now = int(time.time())
    updated = 0

    for item in fields:
        mac = normalize_mac(item.get("mac"))
        if not mac:
            continue

        cursor.execute('''
            UPDATE devices
            SET
                asset_tag = ?,
                serial_number = ?,
                model = ?,
                vlan = ?,
                location = ?,
                device_profile = ?,
                os = ?,
                updated_at = ?
            WHERE mac_address = ?
        ''', (
            item["asset_tag"],
            item["serial_number"],
            item["model"],
            item["vlan"],
            item["location"],
            item["device_profile"],
            item["os"],
            now,
            mac
        ))
        updated += 1

    conn.commit()
    conn.close()
    print(f"CMDB Enricher: Completed successfully. Enriched {updated} devices.")

if __name__ == "__main__":
    main()
"""

    try:
        with open("/home/admin/cmdb_enricher.py", "w") as f:
            f.write(importer_code)
        print("Wrote enricher script to /home/admin/cmdb_enricher.py")
    except Exception as e:
        print(f"ERROR: Failed to write enricher script: {e}", file=sys.stderr)
        sys.exit(1)

    # Transfer and execute
    try:
        print("Copying export JSON to 192.168.30.2...")
        child = pexpect.spawn("scp -o StrictHostKeyChecking=no /home/admin/identities_fields.json steve@192.168.30.2:/tmp/", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(pexpect.EOF)

        print("Copying enricher script to 192.168.30.2...")
        child = pexpect.spawn("scp -o StrictHostKeyChecking=no /home/admin/cmdb_enricher.py steve@192.168.30.2:/tmp/", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(pexpect.EOF)

        print("Executing enricher script on 192.168.30.2...")
        child = pexpect.spawn("ssh -o StrictHostKeyChecking=no steve@192.168.30.2", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(r"steve@")

        child.sendline("python3 /tmp/cmdb_enricher.py")
        child.expect(r"steve@")
        print(child.before)

        # Cleanup remote
        child.sendline("rm /tmp/cmdb_enricher.py /tmp/identities_fields.json")
        child.expect(r"steve@")
        print("Cleaned up files on 192.168.30.2")

        # Cleanup local
        import os
        os.remove("/home/admin/identities_fields.json")
        os.remove("/home/admin/cmdb_enricher.py")
        print("Cleaned up local temporary files.")

        print("CMDB FIELDS POPULATED SUCCESSFULLY!")
    except Exception as e:
        print(f"ERROR: Enrichment process failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
