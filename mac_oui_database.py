#!/usr/bin/env python3
"""
MAC Address OUI Database for Clarion Lab

Provides Organizationally Unique Identifiers (OUIs) for realistic MAC address generation
based on device manufacturers and types.
"""

import random
from typing import List, Dict, Optional, Tuple

# User Device Manufacturers with their OUIs
USER_DEVICE_MANUFACTURERS = {
    "Dell": ["00:14:22", "18:03:73", "D4:AE:52", "F8:BC:12", "B8:2A:72"],
    "HP": ["00:1F:29", "3C:D9:2B", "70:5A:0F", "D8:9E:F3", "98:E7:F4"],
    "Lenovo": ["00:21:CC", "54:EE:75", "C8:5B:76", "E4:54:E8", "00:0A:CD"],
    "Apple": ["00:1B:63", "A4:5E:60", "F0:18:98", "BC:D0:74", "AC:87:A3"],
    "Microsoft Surface": ["00:15:5D", "98:5F:D3", "7C:1E:52"],
    "Asus": ["00:1F:C6", "04:D4:C4", "2C:56:DC", "1C:B7:2C"],
    "Acer": ["00:21:85", "B8:EE:65", "E0:94:67", "00:26:B6"],
    "Samsung": ["00:12:FB", "34:AA:8B", "E8:50:8B", "C8:14:79"],
    "Toshiba": ["00:00:39", "00:26:66", "B8:AE:ED", "00:1D:E0"],
    "Generic PC": ["00:50:56", "52:54:00", "00:0C:29"]  # VMware, QEMU
}

# IoT Device Manufacturers organized by device type/persona
IOT_DEVICE_MANUFACTURERS = {
    "Camera": {
        "Axis": ["00:40:8C", "AC:CC:8E", "B8:A4:4F"],
        "Hikvision": ["44:19:B6", "BC:AD:28", "28:57:BE"],
        "Dahua": ["00:12:16", "08:57:00", "C0:56:E3"],
        "Hanwha": ["00:09:18", "00:D0:D8"]
    },
    "Printer": {
        "HP": ["00:1F:29", "3C:D9:2B", "70:5A:0F"],
        "Canon": ["00:00:85", "C4:73:1E", "F8:D0:BD"],
        "Epson": ["00:00:48", "64:EB:8C", "00:26:AB"],
        "Brother": ["00:80:77", "30:05:5C", "00:1B:A9"]
    },
    "Badge Reader": {
        "HID": ["00:06:8E", "00:12:D3"],
        "Honeywell": ["00:D0:2D", "00:E0:63"],
        "Suprema": ["00:16:6C"]
    },
    "Environmental Sensor": {
        "Nest": ["18:B4:30", "64:16:66"],
        "Ecobee": ["44:61:32", "24:FD:52"],
        "Honeywell": ["00:D0:2D"]
    },
    "HVAC Controller": {
        "Johnson Controls": ["00:21:D2", "00:1E:8C"],
        "Honeywell": ["00:D0:2D", "00:E0:63"],
        "Trane": ["00:1E:C0", "00:50:C2"]
    },
    "Door Lock": {
        "Schlage": ["00:1D:C0", "00:21:5C"],
        "Yale": ["00:0D:6F", "00:1E:8F"],
        "August": ["70:88:6B"]
    },
    "Display": {
        "Samsung": ["00:12:FB", "34:AA:8B", "E8:50:8B"],
        "LG": ["00:1C:62", "B8:5E:7B", "64:BC:0C"],
        "Sony": ["00:1D:BA", "54:42:49"]
    },
    "VoIP Phone": {
        "Cisco": ["00:0A:B7", "00:1E:BD", "F8:66:F2", "70:CA:9B"],
        "Polycom": ["00:04:F2", "64:16:7F", "00:90:7A"],
        "Yealink": ["00:15:65", "80:5E:C0", "00:1F:E1"]
    },
    "Robot": {
        "iRobot": ["50:14:79", "80:C5:F2"],
        "Boston Dynamics": ["00:1A:A0"],
        "Universal Robots": ["00:1F:16"]
    },
    "Medical Device": {
        "Philips Healthcare": ["00:25:1B", "1C:5A:6B"],   # Philips CareServant / Philips Electronics Nederland
        "GE Healthcare": ["44:4B:5D"],                    # GE Healthcare (verified IEEE OUI)
        "Medtronic": ["54:FA:89", "DC:16:A2"],            # Medtronic CRM / Medtronic Diabetes
        "Baxter": ["58:46:E1", "58:42:E4"],               # Baxter International infusion pumps
        "Draeger Medical": ["00:10:5D", "00:30:E6"],      # Draeger ventilators / anesthesia
        "Mindray": ["00:0F:14", "38:0B:26"],              # Mindray patient monitors
    }
}

# OT (Operational Technology) Device Manufacturers organized by device type/persona
OT_DEVICE_MANUFACTURERS = {
    "PLC": {
        "Siemens": ["00:0E:8C", "00:1B:1B"],
        "Rockwell Automation": ["00:00:BC", "00:1D:9C"],
        "Schneider Electric": ["00:80:F4"],               # 00:80:F4 → TELEMECANIQUE ELECTRIQUE (Schneider legacy)
        "Mitsubishi Electric": ["58:52:8A", "10:4B:46"],  # verified Mitsubishi Electric Corporation OUIs
        "ABB": ["00:0C:62", "54:F8:76", "00:1B:45"],     # ABB AB Cewe-Control / ABB AG / ABB AS Automation
        "Beckhoff": ["00:01:05"],
        "WAGO": ["00:30:DE"],                             # WAGO Kontakttechnik GmbH (verified)
        "FANUC Robotics": ["00:E0:E4"],                   # FANUC ROBOTICS NORTH AMERICA (verified)
    },
    "HMI": {
        "Siemens": ["00:0E:8C", "00:1B:1B"],
        "Rockwell Automation": ["00:00:BC"],
        "Schneider Electric": ["00:80:F4"],
        "ABB": ["00:0C:62"],
        "Beckhoff": ["00:01:05"],
        "B&R Industrial Automation": ["00:60:65"],        # B&R Industrial Automation GmbH (ABB subsidiary, verified)
    },
    "SCADA Server": {
        "Siemens": ["00:0E:8C"],
        "Rockwell Automation": ["00:00:BC"],
        "GE": ["00:90:D0"],
        "Dell": ["00:14:22", "18:03:73"],
    },
    "Engineering Workstation": {
        "Siemens": ["00:0E:8C"],
        "Rockwell Automation": ["00:00:BC"],
        "GE": ["00:90:D0"],
        "Dell": ["00:14:22", "18:03:73"],
        "HP": ["00:1F:29", "18:03:73"],
    },
    "Historian": {
        "Honeywell": ["00:D0:2D"],
        "GE": ["00:90:D0"],
        "Dell": ["00:14:22", "18:03:73"],
    },
    "DCS Controller": {
        "Honeywell": ["00:D0:2D", "00:E0:63"],
        "Emerson": ["00:00:4F", "00:12:97"],
        "ABB": ["00:30:11"],
        "Yokogawa": ["00:01:0E", "00:0A:5E"],
        "Siemens": ["00:0E:8C"],
    },
    "Safety Controller": {
        "Emerson": ["00:00:4F"],
        "Honeywell": ["00:D0:2D"],
        "HIMA": ["00:0B:D3"],
        "Pilz": ["00:0B:D0"],
        "ABB": ["00:30:11"],
    },
    "Field Device": {
        "Endress+Hauser": ["00:0B:F1"],
        "Emerson": ["00:00:4F", "00:12:97"],
        "Siemens": ["00:0E:8C"],
        "SICK": ["00:30:91"],
        "VEGA": ["00:0B:79"],
        "ABB": ["00:30:11"],
    },
    "Industrial Gateway": {
        "Moxa": ["00:90:E8"],
        "HMS": ["00:D0:CF"],
        "Red Lion": ["00:01:CB"],
        "Siemens": ["00:0E:8C"],
    },
    "RTU": {
        "Emerson": ["00:00:4F"],
        "ABB": ["00:30:11"],
        "GE": ["00:90:D0"],
        "Honeywell": ["00:D0:2D"],
    },
}


def get_user_manufacturers() -> List[str]:
    """Get list of available user device manufacturers."""
    return sorted(USER_DEVICE_MANUFACTURERS.keys())


def get_iot_personas() -> List[str]:
    """Get list of available IoT device types/personas (includes OT personas)."""
    return sorted(set(IOT_DEVICE_MANUFACTURERS.keys()) | set(OT_DEVICE_MANUFACTURERS.keys()))


def get_ot_personas() -> List[str]:
    """Get list of available OT device types/personas."""
    return sorted(OT_DEVICE_MANUFACTURERS.keys())


def get_manufacturers_for_persona(persona: str) -> List[str]:
    """
    Get available manufacturers for a specific IoT or OT device persona.

    Args:
        persona: Device type (e.g., "Camera", "PLC")

    Returns:
        List of manufacturer names
    """
    if persona in IOT_DEVICE_MANUFACTURERS:
        return sorted(IOT_DEVICE_MANUFACTURERS[persona].keys())
    if persona in OT_DEVICE_MANUFACTURERS:
        return sorted(OT_DEVICE_MANUFACTURERS[persona].keys())
    return []


def get_oui_for_manufacturer(manufacturer: str, persona: Optional[str] = None) -> Optional[str]:
    """
    Get a random OUI for a manufacturer.
    
    Args:
        manufacturer: Manufacturer name
        persona: Optional IoT device persona (required for IoT devices)
    
    Returns:
        OUI string (e.g., "00:14:22") or None if not found
    """
    # Check user device manufacturers
    if manufacturer in USER_DEVICE_MANUFACTURERS:
        return random.choice(USER_DEVICE_MANUFACTURERS[manufacturer])

    # Check IoT device manufacturers
    if persona and persona in IOT_DEVICE_MANUFACTURERS:
        if manufacturer in IOT_DEVICE_MANUFACTURERS[persona]:
            return random.choice(IOT_DEVICE_MANUFACTURERS[persona][manufacturer])

    # Check OT device manufacturers
    if persona and persona in OT_DEVICE_MANUFACTURERS:
        if manufacturer in OT_DEVICE_MANUFACTURERS[persona]:
            return random.choice(OT_DEVICE_MANUFACTURERS[persona][manufacturer])

    # Manufacturer found across any OT persona (when no persona given)
    if not persona:
        for persona_map in OT_DEVICE_MANUFACTURERS.values():
            if manufacturer in persona_map:
                return random.choice(persona_map[manufacturer])

    return None


def mac_has_manufacturer_oui(mac: Optional[str], manufacturer: str) -> bool:
    """Return True if the MAC address uses an OUI for the given manufacturer."""
    if not mac or not isinstance(mac, str):
        return False
    normalized = mac.strip().replace(":", "").replace("-", "").upper()
    if len(normalized) < 6:
        return False
    oui_hex = normalized[:6]
    ouis = set()
    if manufacturer == "Apple" and manufacturer in USER_DEVICE_MANUFACTURERS:
        ouis = set(o.replace(":", "").upper() for o in USER_DEVICE_MANUFACTURERS["Apple"])
    else:
        oui = get_oui_for_manufacturer(manufacturer, None)
        if oui:
            ouis = {oui.replace(":", "").upper()}
    return oui_hex in ouis


def generate_mac_for_manufacturer(manufacturer: str, existing_macs: List[str], 
                                  persona: Optional[str] = None) -> str:
    """
    Generate a unique MAC address for a specific manufacturer.
    
    Args:
        manufacturer: Manufacturer name
        existing_macs: List of existing MAC addresses to avoid duplicates
        persona: Optional IoT device persona
    
    Returns:
        MAC address string in format "XX:XX:XX:XX:XX:XX"
    """
    oui = get_oui_for_manufacturer(manufacturer, persona)
    
    if not oui:
        # Fallback to generic OUI if manufacturer not found
        oui = "00:50:56"
    
    # Generate unique MAC
    while True:
        # Generate random last 3 octets
        mac = f"{oui}:{random.randint(0, 255):02x}:{random.randint(0, 255):02x}:{random.randint(0, 255):02x}"
        
        if mac.lower() not in [m.lower() for m in existing_macs]:
            return mac


def identify_manufacturer_from_mac(mac: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Attempt to identify manufacturer and device type from MAC address.
    
    Args:
        mac: MAC address string
    
    Returns:
        Tuple of (manufacturer, persona) or (None, None) if not recognized
    """
    if not mac or len(mac) < 8:
        return None, None
    
    # Extract OUI (first 3 octets)
    oui = mac[:8].upper()
    
    # Check user device manufacturers
    for manufacturer, ouis in USER_DEVICE_MANUFACTURERS.items():
        if any(oui.startswith(o.upper()) for o in ouis):
            return manufacturer, "User Device"
    
    # Check IoT device manufacturers
    for persona, manufacturers in IOT_DEVICE_MANUFACTURERS.items():
        for manufacturer, ouis in manufacturers.items():
            if any(oui.startswith(o.upper()) for o in ouis):
                return manufacturer, persona

    # Check OT device manufacturers
    for persona, manufacturers in OT_DEVICE_MANUFACTURERS.items():
        for manufacturer, ouis in manufacturers.items():
            if any(oui.startswith(o.upper()) for o in ouis):
                return manufacturer, persona

    return None, None


def get_default_manufacturer_for_persona(persona: str) -> Optional[str]:
    """
    Get the default/first manufacturer for a given IoT persona.
    
    Args:
        persona: IoT device persona
    
    Returns:
        Default manufacturer name or None
    """
    manufacturers = get_manufacturers_for_persona(persona)
    return manufacturers[0] if manufacturers else None


# Mapping for backward compatibility with existing identities
LEGACY_OUI_MAPPING = {
    "dc:a6:32": "Generic PC",  # Old default OUI
    "b8:27:eb": "Generic PC",  # Raspberry Pi
    "28:cd:c1": "Generic PC",
    "e4:5f:01": "Generic PC"
}


def migrate_legacy_mac(mac: str, is_iot: bool = False, persona: Optional[str] = None) -> Tuple[str, str]:
    """
    Migrate legacy MAC address to manufacturer-specific MAC.
    
    Args:
        mac: Existing MAC address
        is_iot: Whether this is an IoT device
        persona: IoT device persona if applicable
    
    Returns:
        Tuple of (new_mac, manufacturer)
    """
    oui = mac[:8].lower()
    
    # Check if it's a legacy OUI that needs migration
    if oui in LEGACY_OUI_MAPPING:
        if is_iot and persona:
            # Assign default manufacturer for this IoT persona
            manufacturer = get_default_manufacturer_for_persona(persona)
            if manufacturer:
                new_mac = generate_mac_for_manufacturer(manufacturer, [], persona)
                return new_mac, manufacturer
        
        # For users, assign a random common manufacturer
        manufacturer = random.choice(["Dell", "HP", "Lenovo"])
        new_mac = generate_mac_for_manufacturer(manufacturer, [])
        return new_mac, manufacturer
    
    # MAC is already manufacturer-specific, try to identify it
    manufacturer, detected_persona = identify_manufacturer_from_mac(mac)
    if manufacturer:
        return mac, manufacturer
    
    # Unknown MAC, keep as-is with Generic PC
    return mac, "Generic PC"
