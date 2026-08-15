#!/usr/bin/env python3
import sqlite3
import json
import urllib.request
import pexpect
import sys
import os

def main():
    # 1. Fetch active status from orchestrator
    print("Fetching active status from orchestrator...")
    try:
        with urllib.request.urlopen("http://localhost:5000/api/status") as resp:
            status = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Error fetching status: {e}")
        return

    # 2. Extract active MAC -> IP mappings
    mappings = []
    runners = status.get("runners", {})
    for name, r in runners.items():
        telemetry = r.get("telemetry")
        if telemetry:
            mac = telemetry.get("network", {}).get("mac")
            ip = telemetry.get("network", {}).get("ip")
            if mac and ip and ip != "unknown":
                mappings.append({"mac": mac.strip().lower(), "ip": ip.strip()})

    print(f"Found {len(mappings)} active MAC-to-IP mappings.")
    if not mappings:
        print("No active runner IPs to sync.")
        return

    # Write mappings to JSON
    with open("/home/admin/active_ips.json", "w") as f:
        json.dump(mappings, f)

    # Importer code to run on 192.168.30.2
    importer_code = """#!/usr/bin/env python3
import sqlite3
import json
import time

conn = sqlite3.connect("/home/steve/cmdb/backend/cmdb.db")
cursor = conn.cursor()

with open("/tmp/active_ips.json", "r") as f:
    mappings = json.load(f)

now = int(time.time())
updated = 0
for m in mappings:
    mac = m["mac"]
    ip = m["ip"]
    cursor.execute("UPDATE devices SET ip_address = ?, last_seen = ?, updated_at = ? WHERE mac_address = ?", (ip, now, now, mac))
    updated += cursor.rowcount

conn.commit()
conn.close()
print(f"Updated {updated} active devices in CMDB.")
"""
    with open("/home/admin/cmdb_ip_sync.py", "w") as f:
        f.write(importer_code)

    # Copy and run
    try:
        child = pexpect.spawn("scp /home/admin/active_ips.json steve@192.168.30.2:/tmp/", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(pexpect.EOF)

        child = pexpect.spawn("scp /home/admin/cmdb_ip_sync.py steve@192.168.30.2:/tmp/", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(pexpect.EOF)

        child = pexpect.spawn("ssh steve@192.168.30.2 python3 /tmp/cmdb_ip_sync.py", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(pexpect.EOF)
        print(child.before)

        # Cleanup remote
        child = pexpect.spawn("ssh steve@192.168.30.2 rm /tmp/active_ips.json /tmp/cmdb_ip_sync.py", encoding="utf-8")
        child.expect("password:")
        child.sendline("C!sco#123")
        child.expect(pexpect.EOF)

        # Cleanup local
        os.remove("/home/admin/active_ips.json")
        os.remove("/home/admin/cmdb_ip_sync.py")
        print("Sync completed successfully.")
    except Exception as e:
        print(f"Error during sync: {e}")

if __name__ == "__main__":
    main()
