#!/usr/bin/env python3
import urllib.request
import json
import random
import sys
import os

# Append orchestrator app path to sys.path to import mac_oui_database
sys.path.append("/home/admin/clarion/lab/orchestrator/app")

try:
    import mac_oui_database
except ImportError as e:
    print(f"ERROR: Could not import mac_oui_database: {e}", file=sys.stderr)
    sys.exit(1)

API_URL = "http://localhost:5000/api/identities"

# Categories and their personas
IOT_PERSONAS = [
    'Camera', 'Printer', 'Badge Reader', 'Environmental Sensor', 
    'HVAC Controller', 'Door Lock', 'Display', 'VoIP Phone', 
    'Robot', 'Medical Device'
]

OT_PERSONAS = [
    'PLC', 'HMI', 'SCADA Server', 'Historian', 'Engineering Workstation', 
    'DCS Controller', 'Safety Controller', 'RTU', 'Industrial Gateway', 
    'Field Device'
]

def main():
    print("Fetching current identities...")
    try:
        req = urllib.request.Request(API_URL)
        with urllib.request.urlopen(req, timeout=10) as resp:
            current_identities = json.loads(resp.read().decode())
    except Exception as e:
        print(f"ERROR: Failed to fetch current identities: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(current_identities)} existing identities.")

    # Build tracking sets to prevent duplicate MACs and names
    existing_macs = {i.get("mac", "").lower() for i in current_identities if i.get("mac")}
    existing_names = {i.get("device_name", "").lower() for i in current_identities if i.get("device_name")}

    new_identities = []
    
    # We want 250 total devices: 125 IoT and 125 OT
    iot_count = 125
    ot_count = 125
    
    # Track counts per persona to increment names (e.g. extra-camera-01, extra-camera-02)
    persona_counters = {}

    def get_unique_name(persona, category):
        prefix = f"extra-{category.lower()}-{persona.lower().replace(' ', '-')}"
        if prefix not in persona_counters:
            persona_counters[prefix] = 1
        else:
            persona_counters[prefix] += 1
        
        while True:
            name = f"{prefix}-{persona_counters[prefix]:02d}"
            if name.lower() not in existing_names:
                existing_names.add(name.lower())
                return name
            persona_counters[prefix] += 1

    # Generate IoT Devices
    print("Generating 125 IoT devices...")
    for _ in range(iot_count):
        persona = random.choice(IOT_PERSONAS)
        manufacturers = mac_oui_database.get_manufacturers_for_persona(persona)
        if not manufacturers:
            manufacturers = ["Generic IoT"]
        manufacturer = random.choice(manufacturers)
        
        mac = mac_oui_database.generate_mac_for_manufacturer(manufacturer, existing_macs, persona)
        existing_macs.add(mac.lower())
        
        name = get_unique_name(persona, "iot")
        
        device = {
            "auth": "mab",
            "persona": persona,
            "device_name": name,
            "department": "IoT",
            "manufacturer": manufacturer,
            "description": f"Clarion Extra IoT {persona} - {manufacturer} (Simulated)",
            "mac": mac,
            "ssid": "netlab_iot",
            "urls": [f"http://192.168.31.2/extra/iot/{persona.lower().replace(' ', '-')}/{name}"]
        }
        new_identities.append(device)

    # Generate OT Devices
    print("Generating 125 OT devices...")
    for _ in range(ot_count):
        persona = random.choice(OT_PERSONAS)
        manufacturers = mac_oui_database.get_manufacturers_for_persona(persona)
        if not manufacturers:
            manufacturers = ["Generic OT"]
        manufacturer = random.choice(manufacturers)
        
        mac = mac_oui_database.generate_mac_for_manufacturer(manufacturer, existing_macs, persona)
        existing_macs.add(mac.lower())
        
        name = get_unique_name(persona, "ot")
        
        device = {
            "auth": "mab",
            "persona": persona,
            "device_name": name,
            "department": "OT",
            "manufacturer": manufacturer,
            "description": f"Clarion Extra OT {persona} - {manufacturer} (Simulated)",
            "mac": mac,
            "ssid": "netlab_iot",
            "urls": [f"http://192.168.31.2/extra/ot/{persona.lower().replace(' ', '-')}/{name}"]
        }
        new_identities.append(device)

    print(f"Generated {len(new_identities)} new MAB devices.")
    
    # Merge and POST
    merged = current_identities + new_identities
    print(f"Posting {len(merged)} total identities back to orchestrator...")
    
    try:
        data = json.dumps(merged).encode('utf-8')
        req = urllib.request.Request(
            API_URL, 
            data=data, 
            method="POST", 
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            print(f"SUCCESS: Server response: {result}")
    except Exception as e:
        print(f"ERROR: Failed to save identities to orchestrator: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
