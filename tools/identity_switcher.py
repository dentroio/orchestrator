#!/usr/bin/env python3
"""
Clarion Lab Identity Switcher

Rotates 802.1x identity on a Linux device (Raspberry Pi).
1. Reads users from identities.json
2. Updates /etc/wpa_supplicant/wpa_supplicant.conf
3. Bounces the network interface to force re-authentication.

Usage:
  sudo python3 identity_switcher.py --interface wlan0 --random
  sudo python3 identity_switcher.py --interface eth0 --user user_morning
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
import logging

# Name of the dedicated connection profile for the lab
LAB_PROFILE_NAME = "clarion-lab-auth"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("IdentitySwitcher")

WPA_TEMPLATE = """ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=US

network={{
    ssid="{ssid}"
    key_mgmt=WPA-EAP
    eap=PEAP
    identity="{username}"
    password="{password}"
    phase2="auth=MSCHAPV2"
}}
"""

WPA_WIRED_TEMPLATE = """ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
ap_scan=0
network={{
    key_mgmt=IEEE8021X
    eap=PEAP
    identity="{username}"
    password="{password}"
    phase2="auth=MSCHAPV2"
    eapol_flags=0
}}
"""

def load_identities(path):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load identities from {path}: {e}")
        sys.exit(1)

def is_networkmanager_managed(interface):
    """Check if NetworkManager is managing this interface.
    
    Returns True if NM is running AND knows about this interface, regardless of
    whether the link is up. NM reports 'unavailable' when there is no carrier but
    it still manages the interface — we must use NM paths (not wpa_supplicant/
    dhclient directly) in that case to avoid conflicts.
    """
    try:
        # Check if NetworkManager is running
        result = subprocess.run(["systemctl", "is-active", "NetworkManager"], 
                              capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return False
        
        # Check if NM knows about this interface in any state
        # (connected, disconnected, unavailable — all mean NM manages it)
        result = subprocess.run(["nmcli", "-t", "-f", "DEVICE,STATE", "device", "status"],
                              capture_output=True, text=True, check=False)
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                parts = line.split(':')
                if len(parts) >= 2 and parts[0].strip() == interface:
                    state = parts[1].strip().lower()
                    # "unmanaged" means NM explicitly does not manage it
                    if state != "unmanaged":
                        return True
        return False
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.debug(f"Error checking NetworkManager: {e}")
        return False

def get_nm_connection_name(interface):
    """Get the NetworkManager connection name for an interface."""
    try:
        # Get device status
        result = subprocess.run(["nmcli", "-t", "-f", "DEVICE,CONNECTION", "device", "status"],
                              capture_output=True, text=True, check=True)
        for line in result.stdout.split('\n'):
            if line.startswith(interface + ':'):
                parts = line.split(':')
                if len(parts) >= 2 and parts[1]:
                    return parts[1]
        
        # If not connected, try to find any connection for this interface
        result = subprocess.run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"],
                              capture_output=True, text=True, check=True)
        for line in result.stdout.split('\n'):
            if ':' in line:
                name, device = line.split(':', 1)
                if device.strip() == interface:
                    return name.strip()
        
        # Fallback: try to find connection by interface name pattern
        result = subprocess.run(["nmcli", "-t", "-f", "NAME", "connection", "show"],
                              capture_output=True, text=True, check=True)
        for line in result.stdout.split('\n'):
            conn_name = line.strip()
            if conn_name and interface in conn_name.lower() and 'netplan' not in conn_name.lower():
                return conn_name
        
        return None
    except Exception as e:
        logger.warning(f"Error getting NetworkManager connection name: {e}")
        return None


def deactivate_other_connections_on_interface(interface, keep_connection_name=LAB_PROFILE_NAME):
    """Bring down any NM connection currently active on this interface that is not our lab profile.
    Ensures netplan-eth0 (or other default) does not stay active so clarion-lab-auth can take over."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            capture_output=True, text=True, check=False
        )
        if result.returncode != 0 or not result.stdout:
            return
        for line in result.stdout.splitlines():
            if ":" not in line:
                continue
            name, device = line.split(":", 1)
            name, device = name.strip(), device.strip()
            if device == interface and name != keep_connection_name:
                logger.info("Deactivating %s on %s so %s can be used", name, interface, keep_connection_name)
                subprocess.run(["nmcli", "connection", "down", name], check=False, capture_output=True)
                time.sleep(0.5)
    except Exception as e:
        logger.warning("Could not deactivate other connections on %s: %s", interface, e)


def apply_mab_connection(interface, dhcp_hostname=None, cloned_mac=None, vendor_class=None, ssid=None):
    """Configure connection for MAB (MAC Authentication Bypass): no 802.1x, just MAC + optional DHCP hostname."""
    if is_networkmanager_managed(interface):
        conn_name = LAB_PROFILE_NAME
        # Delete existing if any, then create a fresh MAB profile.
        # autoconnect=no prevents NM from racing to bring the connection up
        # before we have finished setting the cloned MAC — if it fires early,
        # the Pi hardware MAC would be used instead of the identity MAC.
        subprocess.run(["nmcli", "connection", "delete", conn_name], check=False, stderr=subprocess.DEVNULL)
        is_wired = interface.startswith("eth") or interface.startswith("en")
        type_flag = "ethernet" if is_wired else "wifi"
        cmd = ["nmcli", "connection", "add", "type", type_flag, "con-name", conn_name, "ifname", interface,
               "connection.autoconnect", "no"]
        if not is_wired:
            mab_ssid = ssid if ssid else "netlab_iot"
            cmd.extend(["ssid", mab_ssid])
        subprocess.run(cmd, check=False)
        try:
            if dhcp_hostname and dhcp_hostname.strip():
                subprocess.run(["nmcli", "connection", "modify", conn_name,
                              "ipv4.dhcp-hostname", dhcp_hostname.strip(),
                              "ipv6.dhcp-hostname", dhcp_hostname.strip()], check=True)
            if vendor_class and vendor_class.strip():
                subprocess.run(["nmcli", "connection", "modify", conn_name,
                              "ipv4.dhcp-vendor-class-identifier", vendor_class.strip()], check=True)
                logger.info("MAB: Set DHCP Vendor Class ID to %s", vendor_class.strip())
            # Set identity MAC so ISE sees the correct device (MAB = MAC-based auth)
            if cloned_mac and cloned_mac.strip():
                is_wired = interface.startswith("eth") or interface.startswith("en")
                mac_prop = "802-3-ethernet.cloned-mac-address" if is_wired else "802-11-wireless.cloned-mac-address"
                subprocess.run(["nmcli", "connection", "modify", conn_name, mac_prop, cloned_mac.strip()], check=True)
                logger.info("MAB: Set cloned MAC to %s on %s", cloned_mac.strip(), conn_name)
            is_wired = interface.startswith("eth") or interface.startswith("en")
            # Wired ethernet profiles have no 802-1x section; clearing EAP props errors on NM.
            if not is_wired:
                subprocess.run(["nmcli", "connection", "modify", conn_name,
                              "802-1x.eap", "",
                              "802-1x.identity", "",
                              "802-1x.password", "",
                              "802-1x.phase2-auth", "",
                              "802-1x.anonymous-identity", "",
                              "802-1x.ca-cert", ""], check=False)
            # ISE MAB + profiling can exceed NM's default 45s DHCP timeout on wired ports.
            subprocess.run(["nmcli", "connection", "modify", conn_name,
                          "ipv4.dhcp-timeout", "120",
                          "ipv4.dhcp-client-id", "mac",
                          "ipv6.method", "ignore"], check=False)
            logger.info("MAB: Cleared 802.1x on %s, DHCP hostname=%s", conn_name, dhcp_hostname or "(none)")
        except Exception as e:
            logger.warning("MAB: Could not configure connection: %s", e)
    else:
        logger.info("MAB: Non-NM path; bounce will use link-up + DHCP only (no wpa_supplicant)")


def set_dhcp_hostname(interface, hostname):
    """Set hostname sent in DHCP (option 12) and update the system hostname.

    Always calls hostnamectl set-hostname so the Pi's hostname matches the
    simulated identity. Also updates /etc/hosts so sudo doesn't fail with
    'unable to resolve host'. For NM-managed interfaces, additionally sets
    ipv4/ipv6.dhcp-hostname on the connection profile so the DHCP request
    carries the correct option 12.
    """
    if not hostname or not hostname.strip():
        return
    hostname = hostname.strip()
    try:
        # Always set the system hostname so the Pi appears as the identity on the network
        subprocess.run(["hostnamectl", "set-hostname", hostname], check=True)
        logger.info("Set system hostname to %s", hostname)

        # Keep /etc/hosts in sync so sudo and local resolution work
        try:
            with open("/etc/hosts", "r") as f:
                lines = f.readlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("127.0.1.1"):
                    new_lines.append(f"127.0.1.1\t{hostname}\n")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"127.0.1.1\t{hostname}\n")
            with open("/etc/hosts", "w") as f:
                f.writelines(new_lines)
            logger.info("Updated /etc/hosts: 127.0.1.1 -> %s", hostname)
        except Exception as e:
            logger.warning("Could not update /etc/hosts: %s", e)

        # For NM-managed interfaces also set dhcp-hostname in the connection
        # profile so the DHCP DISCOVER/REQUEST carries the right option 12.
        if is_networkmanager_managed(interface):
            conn_name = get_nm_connection_name(interface)
            if conn_name:
                subprocess.run(["nmcli", "connection", "modify", conn_name,
                              "ipv4.dhcp-hostname", hostname,
                              "ipv6.dhcp-hostname", hostname], check=True)
                logger.info("Set DHCP hostname to %s on NM connection %s", hostname, conn_name)
    except Exception as e:
        logger.warning("Could not set hostname %s: %s", hostname, e)


def update_networkmanager_config(interface, ssid, username, password, dhcp_hostname=None, cloned_mac=None, vendor_class=None):
    """Update NetworkManager connection with new 802.1X credentials.
    
    cloned_mac: If set, use this MAC for the connection (identity-specific MAC).
                Otherwise use 'permanent' (hardware MAC).
    """
    logger.info("NetworkManager detected. Updating connection via nmcli...")
    
    # Get connection name
    # Use dedicated profile instead of trying to guess existing one
    conn_name = LAB_PROFILE_NAME
    
    # Delete existing profiles with this name that are bound to our lab interface.
    # We include DEVICE in the query so we never touch profiles bound to the management
    # interface (wlan0/eth0) even if they happen to share the same profile name.
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,UUID,DEVICE", "connection", "show"],
            capture_output=True, text=True, check=True
        )
        for line in result.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 2:
                continue
            pname = parts[0].strip()
            puuid = parts[1].strip()
            pdev = parts[2].strip() if len(parts) > 2 else ""
            # Only delete profiles for this lab interface (or unbound profiles with our name).
            if pname == conn_name and (not pdev or pdev == "--" or pdev == interface):
                subprocess.run(["nmcli", "connection", "delete", puuid],
                               capture_output=True, check=False)
                logger.info("Deleted existing profile: %s (%s) on %s", conn_name, puuid, pdev or "unbound")
    except Exception:
        pass

    # Disable autoconnect on ALL other profiles bound to this interface so they can't
    # steal the interface back when clarion-lab-auth is activated (e.g. netplan-wlan0-nettime).
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"],
            capture_output=True, text=True, check=True
        )
        for line in result.stdout.splitlines():
            if ":" in line:
                cname, dev = line.split(":", 1)
                cname, dev = cname.strip(), dev.strip()
                if dev == interface and cname != conn_name:
                    subprocess.run(
                        ["nmcli", "connection", "modify", cname, "connection.autoconnect", "no"],
                        check=False, capture_output=True
                    )
                    logger.info("Disabled autoconnect on competing profile: %s (%s)", cname, dev)
    except Exception as ex:
        logger.warning("Could not disable competing profiles: %s", ex)

    logger.info(f"Creating dedicated profile: {conn_name}")
    try:
        # Determine if wired or wireless
        is_wired = interface.startswith("eth") or interface.startswith("en")
        type_flag = "ethernet" if is_wired else "wifi"

        # autoconnect=no prevents NM from racing to bring the connection up
        # before we finish setting all properties (credentials, cloned MAC, etc.).
        # autoconnect is re-enabled in the modify call below once everything is set.
        cmd = ["nmcli", "connection", "add", "type", type_flag, "con-name", conn_name, "ifname", interface,
               "connection.autoconnect", "no"]
        if not is_wired:
            cmd.extend(["ssid", ssid])

        subprocess.run(cmd, check=True)
        
        # Set basic 802.1X settings (common to wired and wireless)
        subprocess.run(["nmcli", "connection", "modify", conn_name,
                      "802-1x.eap", "peap",
                      "802-1x.phase2-auth", "mschapv2",
                      "802-1x.identity", username,
                      "802-1x.password", password,
                      "ipv6.method", "ignore",
                      "connection.autoconnect", "yes",
                      "connection.autoconnect-priority", "100"], check=True)
        # Wireless-specific (ethernet doesn't have these)
        if not is_wired:
            mac_arg = cloned_mac.strip() if cloned_mac and cloned_mac.strip() else "permanent"
            subprocess.run(["nmcli", "connection", "modify", conn_name,
                          "802-11-wireless-security.key-mgmt", "wpa-eap",
                          "802-11-wireless.cloned-mac-address", mac_arg,
                          "802-11-wireless.mac-address-randomization", "never"], check=True)
        
        if vendor_class and vendor_class.strip():
            subprocess.run(["nmcli", "connection", "modify", conn_name,
                          "ipv4.dhcp-vendor-class-identifier", vendor_class.strip()], check=True)
            logger.info("Dot1x: Set DHCP Vendor Class ID to %s on %s", vendor_class.strip(), conn_name)
            
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create connection profile: {e}")
        return False

    logger.info(f"Using NetworkManager connection: {conn_name}")
    
    try:
        # Set DHCP hostname so DHCP/DNS and Dot1x can see this device
        if dhcp_hostname and dhcp_hostname.strip():
            subprocess.run(["nmcli", "connection", "modify", conn_name,
                          "ipv4.dhcp-hostname", dhcp_hostname.strip(),
                          "ipv6.dhcp-hostname", dhcp_hostname.strip()], check=False)
            logger.info("Set DHCP hostname to %s for Dot1x/DNS", dhcp_hostname.strip())
        
        # Determine if wired or wireless
        is_wired = interface.startswith("eth") or interface.startswith("en")
        
        if is_wired:
            # Wired 802.1X: set the identity MAC explicitly in the NM profile so NM
            # applies it directly when bringing the connection up.  Using "preserve"
            # was unreliable — the Pi's smsc95xx/lan78xx USB Ethernet driver can reset
            # to the hardware MAC on a link cycle before NM takes the snapshot, causing
            # the Pi's real OUI to appear in ISE/Clarion instead of the identity MAC.
            # Fall back to "preserve" only when no MAC is provided.
            mac_val = cloned_mac.strip() if cloned_mac and cloned_mac.strip() else "preserve"
            subprocess.run(["nmcli", "connection", "modify", conn_name,
                          "802-3-ethernet.cloned-mac-address", mac_val], check=True)
            if cloned_mac and cloned_mac.strip():
                logger.info(f"Wired 802.1X config for user: {username} — NM will apply MAC {mac_val}")
            subprocess.run(["nmcli", "connection", "modify", conn_name,
                          "802-1x.eap", "peap",
                          "802-1x.identity", username,
                          "802-1x.password", password,
                          "802-1x.phase2-auth", "mschapv2",
                          "802-1x.anonymous-identity", username.split('@')[0] if '@' in username else username,
                          "802-1x.ca-cert", ""], check=True)
        else:
            # Wireless 802.1X configuration (cloned-mac already set above for wifi)
            if cloned_mac and cloned_mac.strip():
                subprocess.run(["nmcli", "connection", "modify", conn_name,
                              "802-11-wireless.cloned-mac-address", cloned_mac.strip()], check=True)
            logger.info(f"Updating wireless 802.1X config for SSID '{ssid}', user: {username}" +
                       (f" (MAC: {cloned_mac.strip()})" if cloned_mac and cloned_mac.strip() else ""))
            # Update SSID if needed
            subprocess.run(["nmcli", "connection", "modify", conn_name,
                          "802-11-wireless.ssid", ssid], check=False)
            # Update 802.1X settings
            subprocess.run(["nmcli", "connection", "modify", conn_name,
                          "802-1x.eap", "peap",
                          "802-1x.identity", username,
                          "802-1x.password", password,
                          "802-1x.phase2-auth", "mschapv2",
                          "802-1x.anonymous-identity", username.split('@')[0] if '@' in username else username,
                          "802-1x.ca-cert", ""], check=True)
        
        logger.info("NetworkManager connection updated successfully")
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to update NetworkManager connection: {e}")
        return False
    except Exception as e:
        logger.error(f"Error updating NetworkManager: {e}")
        return False

def update_wpa_config(interface, ssid, username, password, dhcp_hostname=None, cloned_mac=None, vendor_class=None):
    """Update 802.1X configuration. Supports both NetworkManager and wpa_supplicant.
    
    cloned_mac: Optional. If set, use this MAC for the connection (identity-specific MAC).
    """
    # Check if NetworkManager is managing this interface
    if is_networkmanager_managed(interface):
        return update_networkmanager_config(interface, ssid, username, password, dhcp_hostname=dhcp_hostname, cloned_mac=cloned_mac, vendor_class=vendor_class)
    
    # Fall back to wpa_supplicant configuration
    logger.info("Using wpa_supplicant configuration (NetworkManager not managing this interface)")
    
    # Determine which template to use
    if interface.startswith("eth") or interface.startswith("en"):
        conf_content = WPA_WIRED_TEMPLATE.format(username=username, password=password)
        logger.info(f"Generating Wired 802.1x config for user: {username}")
    else:
        conf_content = WPA_TEMPLATE.format(ssid=ssid, username=username, password=password)
        logger.info(f"Generating Wireless 802.1x config for SSID '{ssid}', user: {username}")
    
    # Backup existing config
    conf_path = "/etc/wpa_supplicant/wpa_supplicant.conf"
    if os.path.exists(conf_path):
        subprocess.run(["cp", conf_path, f"{conf_path}.bak"], check=False)
    
    # Write new config
    try:
        with open(conf_path, 'w') as f:
            f.write(conf_content)
        logger.info("Updated wpa_supplicant.conf")
        return True
    except PermissionError:
        logger.error("Permission denied writing to /etc/wpa_supplicant/wpa_supplicant.conf. Run with sudo?")
        sys.exit(1)

def bounce_interface(interface, shutdown_duration=60, dhcp_hostname=None, mab=False, vendor_class=None, dhcp_req_list=None):
    """Re-authenticate the interface with new credentials.

    For wired interfaces (eth0) managed by NetworkManager, credentials are
    updated in-place and the NM connection is restarted WITHOUT taking the
    physical link down. This avoids link-flap err-disable on the upstream
    switch, which trips when the port repeatedly loses carrier.

    For wireless interfaces or the non-NM fallback path, a brief shutdown is
    still used (shutdown_duration seconds) to force re-association.

    Args:
        interface: Network interface name (e.g., eth0, wlan0)
        shutdown_duration: Seconds to keep interface down (wireless / fallback only)
        dhcp_hostname: Optional hostname to send in DHCP
        mab: If True, MAB — no 802.1x; just bring up and DHCP
        vendor_class: Vendor class option 60 text.
        dhcp_req_list: Comma-separated Option 55 parameter request numbers.
    """
    is_wired = interface.startswith("eth") or interface.startswith("en")

    # --- Wired + NetworkManager: re-auth without dropping the physical link ---
    # NEVER fall through to the manual bounce for wired interfaces — that causes
    # link-flap which err-disables the switch port.
    if is_wired and is_networkmanager_managed(interface):
        conn_name = LAB_PROFILE_NAME
        logger.info(f"Wired NM re-auth on {interface} (no link-down, avoids switch err-disable)...")
        try:
            set_dhcp_hostname(interface, dhcp_hostname or "")
            # Deactivate existing connection (ignore errors — may not be active yet)
            subprocess.run(["nmcli", "connection", "down", conn_name], check=False,
                           capture_output=True)
            time.sleep(0.5)

            # Kill any stale dhclient processes on this interface — they conflict
            # with NM's own DHCP client and prevent IP acquisition.
            subprocess.run(["sudo", "pkill", "-f", f"dhclient.*{interface}"],
                           check=False, capture_output=True)
            subprocess.run(["sudo", "pkill", "-f", f"dhclient {interface}"],
                           check=False, capture_output=True)

            # Deactivate any other connection on this interface (e.g. netplan-eth0) so
            # clarion-lab-auth is the one that gets activated.
            deactivate_other_connections_on_interface(interface, keep_connection_name=conn_name)

            # Restore carrier: NM marks the device "unavailable" when the interface
            # is brought down (no carrier). nmcli connection up will not activate on
            # an unavailable device — bring the link up first so NM sees carrier and
            # moves eth0 from unavailable → disconnected before we activate.
            subprocess.run(["ip", "link", "set", "dev", interface, "up"],
                           check=False, capture_output=True)
            # Switch ports often take a few seconds to establish a link.
            # Wait up to 10 seconds for the carrier to be detected by NM.
            for _ in range(10):
                result = subprocess.run(["ip", "link", "show", interface], capture_output=True, text=True)
                if "LOWER_UP" in result.stdout:
                    break
                time.sleep(1)
            time.sleep(2)  # Give NM extra time to process the carrier-changed event

            # MAB: give the switch time to complete MAC auth and put the port in the
            # data VLAN before we start DHCP. Otherwise DHCP can run while the port
            # is still in a pre-auth state and never get a reply.
            if mab:
                wait_s = 12
                logger.info(f"MAB: waiting {wait_s}s for switch/ISE to complete auth before DHCP...")
                time.sleep(wait_s)

            # Fire connection up. By not passing --wait 0, nmcli blocks until
            # connection is fully established or fails (default wait is 90s).
            # This is safer than returning instantly while device may not have carrier.
            result = subprocess.run(["nmcli", "connection", "up", conn_name],
                                    check=False, capture_output=True, text=True)
            logger.info(f"NM re-auth command completed for {conn_name} (returncode {result.returncode}), polling for IP on {interface}...")
            # MAB + ISE profiling can be slow; poll longer so we don't give up before DHCP succeeds.
            ip_wait_seconds = 120 if mab else 30
            for i in range(ip_wait_seconds):
                time.sleep(1)
                result = subprocess.run(["ip", "-4", "addr", "show", interface],
                                        capture_output=True, text=True)
                if "inet " in result.stdout:
                    logger.info(f"NM re-auth succeeded — {interface} has IP (after {i + 1}s)")
                    set_default_route_priority(interface)
                    return
                if mab and i == 35:
                    # After ~36s without IP, try one more connection up to force a fresh DHCP attempt
                    # (port may not have been in data VLAN on first attempt).
                    logger.info("MAB: no IP yet — retrying connection up to trigger DHCP again...")
                    subprocess.run(["nmcli", "connection", "down", conn_name], check=False, capture_output=True)
                    time.sleep(2)
                    subprocess.run(["nmcli", "connection", "up", conn_name], check=False, capture_output=True, text=True)
            logger.warning(f"NM re-auth: no IP on {interface} after {ip_wait_seconds}s — auth may still be in progress")
            # Do NOT fall back to manual bounce; the link is still up and NM may
            # complete auth shortly. Returning here avoids the link-flap.
            return
        except Exception as e:
            logger.warning(f"NM re-auth error: {e} — returning without bounce to preserve link")
            return

    logger.info(f"Bouncing interface {interface} (shutdown_duration={shutdown_duration}s)...")
    
    # Check if NetworkManager is managing this interface
    if is_networkmanager_managed(interface):
        # Use our dedicated profile
        conn_name = LAB_PROFILE_NAME
        # Ensure it exists (might not if update_networkmanager_config failed or wasn't called yet)
        # But usually update_config is called before bounce.
        # Fallback to detection if dedicated profile not present
        result = subprocess.run(["nmcli", "connection", "show", conn_name],
                          capture_output=True, text=True, check=False)
        if result.returncode != 0:
             conn_name = get_nm_connection_name(interface)
        
        if conn_name:
            try:
                # Set DHCP hostname on connection so next DHCP request sends it (Dot1x/DNS)
                set_dhcp_hostname(interface, dhcp_hostname or "")
                # Deactivate any other connection on this interface (e.g. netplan-eth0) first
                deactivate_other_connections_on_interface(interface, keep_connection_name=conn_name)
                logger.info(f"Disconnecting NetworkManager connection: {conn_name}")
                # Disconnect connection
                subprocess.run(["nmcli", "connection", "down", conn_name], check=False)
                
                # Clean up all routes for this interface while it's down
                logger.info("Cleaning up routes for disconnected interface...")
                cleanup_interface_routes(interface)
                
                # Shutdown period for cleanup
                logger.info(f"Interface {interface} will be down for {shutdown_duration} seconds for cleanup...")
                time.sleep(shutdown_duration)
                
                # Unblock wifi in case power management soft-blocked it
                subprocess.run(["sudo", "rfkill", "unblock", "wifi"], check=False)
                subprocess.run(["sudo", "nmcli", "radio", "wifi", "on"], check=False)
                subprocess.run(["sudo", "ip", "link", "set", interface, "up"], check=False)
                time.sleep(2)
                # Reconnect with new settings — retry up to 3x for transient DHCP timing issues
                logger.info(f"Reconnecting NetworkManager connection: {conn_name}")
                connected = False
                for attempt in range(1, 4):
                    result = subprocess.run(["nmcli", "connection", "up", conn_name],
                                            capture_output=True, text=True)
                    if result.returncode == 0:
                        connected = True
                        break
                    logger.warning("NM connect attempt %d/3 failed (rc=%d): %s",
                                   attempt, result.returncode, result.stderr.strip())
                    time.sleep(5)
                if connected:
                    logger.info("NetworkManager connection restarted with new credentials")

                # Set route metric and DNS priority — only when connected.
                # Using 'nmcli connection reapply' (not 'up') to apply in-place without
                # triggering a new DHCP cycle; 'connection up' on an active connection
                # causes an immediate down+up which tears down the just-acquired lease.
                if connected:
                    try:
                        logger.info(f"Setting route metric to 100 for {conn_name} (preferred)")
                        subprocess.run(["nmcli", "connection", "modify", conn_name,
                                      "ipv4.route-metric", "100"], check=True)

                        # Set other active connections to higher metric
                        result = subprocess.run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
                                              capture_output=True, text=True, check=True)
                        for line in result.stdout.split('\n'):
                            if ':' in line:
                                other_conn, other_dev = line.split(':', 1)
                                other_conn = other_conn.strip()
                                other_dev = other_dev.strip()
                                # Skip loopback and the current connection
                                if other_conn and other_conn != conn_name and other_dev and not other_dev.startswith('lo'):
                                    logger.info(f"Setting route metric to 2000 for {other_conn} (lower priority)")
                                    subprocess.run(["nmcli", "connection", "modify", other_conn,
                                                  "ipv4.route-metric", "2000"], check=False)

                        # Reapply settings in-place — no DHCP restart
                        subprocess.run(["nmcli", "connection", "reapply", conn_name], check=False)

                        # Set DNS to follow this connection: lower dns-priority = preferred for DNS
                        try:
                            logger.info(f"Setting DNS to follow {conn_name} (eth0's DHCP DNS)")
                            subprocess.run(["nmcli", "connection", "modify", conn_name,
                                          "ipv4.dns-priority", "-100"], check=True)
                            result = subprocess.run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
                                                  capture_output=True, text=True, check=True)
                            for line in result.stdout.split('\n'):
                                if ':' in line:
                                    other_conn, other_dev = line.split(':', 1)
                                    other_conn = other_conn.strip()
                                    other_dev = other_dev.strip()
                                    if other_conn and other_conn != conn_name and other_dev and not other_dev.startswith('lo'):
                                        subprocess.run(["nmcli", "connection", "modify", other_conn,
                                                      "ipv4.dns-priority", "100"], check=False)
                            # Reapply DNS settings in-place — no DHCP restart
                            subprocess.run(["nmcli", "connection", "reapply", conn_name], check=False)
                            time.sleep(1)
                            # Restart systemd-resolved so it picks up the preferred DNS from NM
                            subprocess.run(["systemctl", "restart", "systemd-resolved"], check=False)
                        except Exception as e:
                            logger.warning(f"Could not set DNS priority: {e}")
                    except Exception as e:
                        logger.warning(f"Could not set route metric via NetworkManager: {e}")
                        # Fall back to manual route management
                        time.sleep(3)
                        set_default_route_priority(interface)
                    else:
                        time.sleep(2)
                        # Verify routes
                        result = subprocess.run(["ip", "route", "show", "default"],
                                              capture_output=True, text=True, check=False)
                        logger.info("Current default routes:")
                        for line in result.stdout.split('\n'):
                            if line.strip():
                                logger.info(f"  {line.strip()}")
                
                return
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to restart NetworkManager connection: {e}")
                # Fall through to manual interface bounce
    
    # Manual interface bounce (for wpa_supplicant or fallback)
    try:
        # 1. Clean up routes before bringing down
        logger.info("Cleaning up routes before interface shutdown...")
        cleanup_interface_routes(interface)
        
        # 2. Bring down
        logger.info(f"Bringing interface {interface} down...")
        subprocess.run(["ifconfig", interface, "down"], check=False)
        
        # 3. Shutdown period for cleanup
        logger.info(f"Interface {interface} will be down for {shutdown_duration} seconds for cleanup...")
        time.sleep(shutdown_duration)
        
        # 4. Reconfigure Authentication (skip for MAB - no 802.1x)
        if mab:
            logger.info("MAB: Skipping 802.1x; interface will use link-up + DHCP only.")
            # Important: Kill any existing wpa_supplicant so it doesn't keep trying dot1x
            subprocess.run(f"pkill -f 'wpa_supplicant.*{interface}'", shell=True, check=False)
        elif interface.startswith("eth") or interface.startswith("en"):
             # Wired 802.1x often requires restarting wpa_supplicant with -Dwired
             logger.info("Wired interface detected. Restarting wpa_supplicant with wired driver...")
             
             # Kill existing wpa_supplicant for this interface
             subprocess.run(f"pkill -f 'wpa_supplicant.*{interface}'", shell=True, check=False)
             time.sleep(1)
             
             # Start new wpa_supplicant
             # -Dwired = Wired driver
             # -i = Interface
             # -c = Config file
             # -B = Background
             cmd = ["wpa_supplicant", "-Dwired", "-i", interface, "-c", "/etc/wpa_supplicant/wpa_supplicant.conf", "-B"]
             subprocess.run(cmd, check=False)
             
        else:
            # Wireless - need to restart wpa_supplicant to pick up new config
            logger.info("Wireless interface detected. Restarting wpa_supplicant to load new config...")
            
            # Kill existing wpa_supplicant processes for this interface
            # Try multiple methods to ensure it's stopped
            subprocess.run(f"pkill -f 'wpa_supplicant.*{interface}'", shell=True, check=False)
            subprocess.run(["pkill", "-f", f"wpa_supplicant.*-i.*{interface}"], check=False)
            # Also try stopping systemd service if it exists
            subprocess.run(["systemctl", "stop", "wpa_supplicant@{}".format(interface)], check=False)
            time.sleep(2)
            
            # Start new wpa_supplicant with updated config
            # -B = Background daemon
            # -i = Interface
            # -c = Config file
            # -D = Driver (nl80211 for most modern wireless, webrt for some)
            cmd = ["wpa_supplicant", "-B", "-i", interface, "-c", "/etc/wpa_supplicant/wpa_supplicant.conf"]
            result = subprocess.run(cmd, check=False, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.warning(f"wpa_supplicant start returned {result.returncode}, trying with nl80211 driver...")
                # Try with explicit driver
                cmd = ["wpa_supplicant", "-B", "-i", interface, "-c", "/etc/wpa_supplicant/wpa_supplicant.conf", "-D", "nl80211"]
                subprocess.run(cmd, check=False)
            
            logger.info("wpa_supplicant restarted with new configuration")
        
        # 5. Bring up
        logger.info(f"Bringing interface {interface} up...")
        subprocess.run(["sudo", "rfkill", "unblock", "wifi"], check=False)
        subprocess.run(["ifconfig", interface, "up"], check=False)
        time.sleep(2)
        
        # 5b. Set hostname so DHCP sends it (Dot1x/DNS can see device)
        set_dhcp_hostname(interface, dhcp_hostname or "")
        
        # 6. Request DHCP
        logger.info("Requesting DHCP...")
        subprocess.run(["dhclient", "-r", interface], check=False) # Release
        time.sleep(1)
        
        # Ensure we write a custom dhclient.conf if profiling variables are passed
        dhclient_conf_args = []
        if vendor_class or dhcp_req_list:
            custom_conf = f"/tmp/dhclient_custom_{interface}.conf"
            try:
                with open(custom_conf, "w") as f:
                    if vendor_class:
                        f.write(f'send vendor-class-identifier "{vendor_class}";\n')
                    if dhcp_req_list:
                        # Translate the numbers into standard option names accepted by dhclient
                        # Or dhclient might require a different approach depending on the version.
                        # For simplicity, dhclient allows 'request subnet-mask, broadcast-address, time-offset...'
                        # Or raw option injection. Due to varying dhclient support for arbitrary request lists, 
                        # sometimes we rely entirely on NM for the rich option 55 sets, but we can set up NM spoof.
                        f.write(f'# Raw dhcp_req_list={dhcp_req_list} typically better managed by NM or raw packet crafter\n')
                dhclient_conf_args = ["-cf", custom_conf]
                logger.info("Using custom dhclient config with vendor class injection")
            except Exception as e:
                logger.warning(f"Could not write custom dhclient config: {e}")
        
        subprocess.run(["dhclient", interface] + dhclient_conf_args, check=False)       # Renew
        
        # Wait for DHCP to assign IP and set routes
        logger.info("Waiting for DHCP to assign IP and routes...")
        time.sleep(5)
        
        # 7. Set default route priority to ensure traffic goes over this interface
        set_default_route_priority(interface)
        
        # 8. Set DNS from this interface's DHCP (so DNS follows eth0 when using eth0)
        apply_dns_from_interface(interface)
        
        # 9. Verify and enforce route priority one more time after a short delay
        time.sleep(2)
        logger.info("Final route priority enforcement...")
        enforce_route_priority(interface)
        
        logger.info(f"Interface {interface} restarted.")
        
    except Exception as e:
        logger.error(f"Error bouncing interface: {e}")

# Raspberry Pi OUIs (Organizationally Unique Identifiers)
PI_OUIS = [
    "b8:27:eb", # Raspberry Pi Foundation
    "dc:a6:32", # Raspberry Pi Trading Ltd
    "e4:5f:01", # Raspberry Pi Trading Ltd
    "28:cd:c1"  # Raspberry Pi Trading Ltd
]

# Vendor OUIs and DHCP/HTTP Fingerprints per Persona/OS.
# This is the single source of truth consumed by:
#   • identity_switcher.py  (OUI / vendor_class → NM cloned-mac, nmcli VCI)
#   • dhcp_fingerprint_inject.py  (option_55 / option_60 / option_43 → scapy raw DHCP)
#   • http_persona_spoofer.py  (http_server_header → ISE active HTTP probe)
PERSONA_PROFILES = {
    # ── IoT Personas ────────────────────────────────────────────────────────
    "Badge Reader": {
        "oui": "00:04:5A",                   # Zebra Technologies
        "vendor_class": "Zebra Technologies",
        "dhcp_req_list": "1,3,6,12,15",
        "dhcp_option_43": "",
        "http_server_header": "Zebra-Technologies/1.0",
    },
    "Camera": {
        "oui": "54:C4:15",                   # Hikvision
        "vendor_class": "Hikvision_Camera",
        "dhcp_req_list": "1,3,6,12",
        "dhcp_option_43": "",
        "http_server_header": "App-webs/",
    },
    "Printer": {
        "oui": "00:30:6E",                   # HP
        "vendor_class": "Hewlett-Packard.HP",
        "dhcp_req_list": "1,3,6,9,15",
        "dhcp_option_43": "",
        "http_server_header": "HP HTTP Server; HP LaserJet Pro MFP M227",
    },
    "Environmental Sensor": {
        "oui": "00:1E:58",                   # Honeywell
        "vendor_class": "Honeywell Sensor",
        "dhcp_req_list": "1,3,6,12",
        "dhcp_option_43": "",
        "http_server_header": "Honeywell-Web/1.0",
    },
    "HVAC Controller": {
        "oui": "00:26:55",                   # Honeywell
        "vendor_class": "Honeywell HVAC",
        "dhcp_req_list": "1,3,6,12",
        "dhcp_option_43": "",
        "http_server_header": "Honeywell-Web/2.0",
    },
    "Door Lock": {
        "oui": "00:0D:67",                   # HID
        "vendor_class": "HID Door Access",
        "dhcp_req_list": "1,3,6,12",
        "dhcp_option_43": "",
        "http_server_header": "HID-EDGE-Solo/1.0",
    },
    "Display": {
        "oui": "00:12:47",                   # Samsung
        "vendor_class": "Samsung Display",
        "dhcp_req_list": "1,3,6,12",
        "dhcp_option_43": "",
        "http_server_header": "Samsung-MagicInfo/1.0",
    },
    "VoIP Phone": {
        "oui": "00:1E:7A",                   # Cisco
        "vendor_class": "Cisco Systems, Inc. IP Phone 7941G",
        "dhcp_req_list": "1,3,6,15,42",
        # sub-option 150 = TFTP server (0.0.0.0 placeholder)
        "dhcp_option_43": "\x96\x04\x00\x00\x00\x00",
        "http_server_header": "Cisco-IOS-HTTP-Server",
    },
    "Robot": {
        "oui": "00:1E:67",                   # Fanuc
        "vendor_class": "Fanuc Robot Controller",
        "dhcp_req_list": "1,3,6,12",
        "dhcp_option_43": "",
        "http_server_header": "FanucCNC/7.0",
    },
    "Medical Device": {
        "oui": "00:50:7F",                   # Philips
        "vendor_class": "Philips Medical Patient Monitor",
        "dhcp_req_list": "1,3,6,12,15",
        "dhcp_option_43": "",
        "http_server_header": "Philips-Medical/3.0",
    },
    "SICK Scanner": {
        "oui": "00:30:91",                   # SICK AG
        "vendor_class": "SICK-AG",
        "dhcp_req_list": "1,3,6,12",
        "dhcp_option_43": "",
        "http_server_header": "SICK-Sensor/1.0",
    },

    # ── User / Workstation OS types ──────────────────────────────────────────
    "Windows": {
        "oui": "F8:B1:56",                   # Dell
        "vendor_class": "MSFT 5.0",
        "dhcp_req_list": "1,3,6,15,31,33,43,44,46,47,119,121,249,252",
        "dhcp_option_43": "",
        "http_server_header": "Microsoft-IIS/10.0",
    },
    "Mac": {
        "oui": "00:1C:B3",                   # Apple
        "vendor_class": "AAPLBSDPC",
        "dhcp_req_list": "1,3,6,15,119,95,252,44,46,47",
        "dhcp_option_43": "",
        "http_server_header": "AirTunes/220.4",
    },
    "Linux": {
        "oui": "00:25:90",                   # Supermicro
        "vendor_class": "dhcpcd-9.4.1:Linux-6.1.21-v8+:aarch64:BCM2835",
        "dhcp_req_list": "1,28,2,3,15,6",
        "dhcp_option_43": "",
        "http_server_header": "nginx/1.18.0 (Ubuntu)",
    },
}

def get_persona_profile(persona):
    """Return the profile dict for an IoT persona or User OS type."""
    if not persona:
        return None
    return PERSONA_PROFILES.get((persona or "").strip())

def generate_persona_mac(persona):
    """Generate a random MAC address matching the vendor OUI for the given persona/OS."""
    prof = get_persona_profile(persona)
    oui = prof["oui"] if prof and "oui" in prof else random.choice(PI_OUIS)
    # Generate 3 random bytes
    suffix = [random.randint(0, 255) for _ in range(3)]
    return f"{oui}:{suffix[0]:02x}:{suffix[1]:02x}:{suffix[2]:02x}"

def generate_pi_mac():
    """Generate a random MAC address that looks like a Raspberry Pi."""
    oui = random.choice(PI_OUIS)
    # Generate 3 random bytes
    suffix = [random.randint(0, 255) for _ in range(3)]
    return f"{oui}:{suffix[0]:02x}:{suffix[1]:02x}:{suffix[2]:02x}"

def _get_dns_from_dhclient_lease(interface):
    """Get DNS servers from dhclient lease file for the interface. Returns list of IP strings or []."""
    dns_servers = []
    lease_files = [
        "/var/lib/dhcp/dhclient.leases",
        "/var/lib/dhcp/dhclient.{}.leases".format(interface),
        "/run/network/dhclient.{}.lease".format(interface),
    ]
    for path in lease_files:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r") as f:
                content = f.read()
        except Exception:
            continue
        # Parse lease blocks; take the most recent one that matches our interface
        in_lease = False
        current_interface = None
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("lease ") and "{" in line:
                in_lease = True
                current_interface = None
                continue
            if in_lease and "interface" in line and '"' in line:
                # interface "eth0";
                current_interface = line.split('"')[1].strip()
                continue
            if in_lease and "option domain-name-servers" in line:
                # Only use this block if it's for our interface (or file is interface-specific)
                if current_interface == interface or (current_interface is None and interface in path):
                    # option domain-name-servers 192.168.1.1, 192.168.1.2;
                    part = line.split("domain-name-servers", 1)[-1].strip().rstrip(";")
                    for token in part.replace(",", " ").split():
                        token = token.strip()
                        if token and all(c in "0123456789." for c in token):
                            dns_servers.append(token)
            if in_lease and line == "}":
                in_lease = False
        if dns_servers:
            return list(dict.fromkeys(dns_servers))  # unique order-preserving
    return dns_servers


def apply_dns_from_interface(interface):
    """Ensure DNS follows the active interface (e.g. use eth0's DHCP DNS when using eth0)."""
    logger.info(f"Setting DNS from interface {interface}...")
    dns_servers = _get_dns_from_dhclient_lease(interface)
    if not dns_servers:
        logger.warning("Could not get DNS servers from DHCP lease for %s; DNS may still use other interface", interface)
        return
    logger.info("Using DNS from %s: %s", interface, ", ".join(dns_servers))
    try:
        # Prefer resolvectl (systemd-resolved) if available
        result = subprocess.run(["which", "resolvectl"], capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            subprocess.run(["resolvectl", "dns", interface] + dns_servers, check=False)
            subprocess.run(["resolvectl", "default-route", interface, "true"], check=False)
            subprocess.run(["systemctl", "restart", "systemd-resolved"], check=False)
            logger.info("DNS set via resolvectl for %s", interface)
            return
        # Fallback: write /etc/resolv.conf (backup first)
        resolv = "/etc/resolv.conf"
        if os.path.exists(resolv):
            subprocess.run(["cp", resolv, resolv + ".bak"], check=False)
        with open(resolv, "w") as f:
            for s in dns_servers:
                f.write("nameserver %s\n" % s)
        logger.info("DNS written to %s", resolv)
    except Exception as e:
        logger.warning("Could not set DNS from interface: %s", e)


def cleanup_interface_routes(interface):
    """Remove all routes (default and interface-specific) for an interface."""
    logger.info(f"Cleaning up all routes for {interface}...")
    
    try:
        # Remove all default routes for this interface
        max_attempts = 10
        for attempt in range(max_attempts):
            result = subprocess.run(["ip", "route", "show", "default", "dev", interface],
                                  capture_output=True, text=True, check=False)
            if not result.stdout.strip():
                break
            
            for line in result.stdout.split('\n'):
                if 'default' in line and interface in line:
                    parts = line.split()
                    gateway = None
                    if 'via' in parts:
                        idx = parts.index('via')
                        if idx + 1 < len(parts):
                            gateway = parts[idx + 1]
                    
                    if gateway:
                        subprocess.run(["ip", "route", "del", "default", "via", gateway, "dev", interface], check=False)
                    else:
                        subprocess.run(["ip", "route", "del", "default", "dev", interface], check=False)
            
            time.sleep(0.5)
        
        # Remove interface-specific routes (but keep link-local)
        result = subprocess.run(["ip", "route", "show", "dev", interface],
                              capture_output=True, text=True, check=False)
        for line in result.stdout.split('\n'):
            if line.strip() and 'default' not in line and 'scope link' not in line:
                # Try to delete non-link-local routes
                parts = line.split()
                if 'via' in parts:
                    idx = parts.index('via')
                    if idx + 1 < len(parts):
                        gateway = parts[idx + 1]
                        # Reconstruct route to delete
                        route_parts = ['ip', 'route', 'del']
                        route_parts.extend(parts[:parts.index('dev')])
                        route_parts.extend(['dev', interface])
                        subprocess.run(route_parts, check=False)
        
        logger.info(f"Route cleanup complete for {interface}")
        
    except Exception as e:
        logger.warning(f"Error during route cleanup: {e}")

def set_default_route_priority(interface):
    """Set the default route priority so the specified interface is preferred.
    
    Sets the active interface to metric 100 (preferred) and other interfaces
    to metric 2000+ (lower priority) to ensure traffic goes over the correct interface.
    """
    logger.info(f"Setting default route priority for {interface}...")
    
    try:
        # Wait for DHCP to finish and routes to stabilize
        time.sleep(3)
        
        # Get list of all network interfaces (excluding loopback)
        result = subprocess.run(["ip", "link", "show"], capture_output=True, text=True, check=True)
        all_interfaces = []
        for line in result.stdout.split('\n'):
            if ': ' in line and 'state' in line.lower():
                # Extract interface name (format: "2: eth0: <BROADCAST...")
                parts = line.split(':')
                if len(parts) >= 2:
                    iface = parts[1].strip().split()[0]  # Get interface name before any spaces
                    # Filter out loopback and virtual interfaces
                    if iface and not iface.startswith('lo') and not iface.startswith('docker') and not iface.startswith('br-'):
                        all_interfaces.append(iface)
        
        # Clean up existing default routes for the active interface so we can re-add with correct metric.
        # Cap iterations — NM may keep re-adding; if so, routes are likely fine and we must not block.
        logger.info(f"Cleaning up duplicate default routes for {interface}...")
        max_cleanup_iters = 5
        for _ in range(max_cleanup_iters):
            result = subprocess.run(["ip", "route", "show", "default", "dev", interface],
                                  capture_output=True, text=True, check=False)
            if not result.stdout.strip():
                break
            # Delete all default routes for this interface
            for line in result.stdout.split('\n'):
                if 'default' in line and interface in line:
                    # Extract gateway if present
                    parts = line.split()
                    gateway = None
                    if 'via' in parts:
                        idx = parts.index('via')
                        if idx + 1 < len(parts):
                            gateway = parts[idx + 1]
                    
                    # Delete the route
                    if gateway:
                        subprocess.run(["ip", "route", "del", "default", "via", gateway, "dev", interface], check=False)
                    else:
                        subprocess.run(["ip", "route", "del", "default", "dev", interface], check=False)
            time.sleep(0.5)
        
        # Wait for DHCP to potentially add a new route
        time.sleep(2)
        
        # Get the gateway for this interface (from DHCP or existing routes)
        result = subprocess.run(["ip", "route", "show", "dev", interface], 
                              capture_output=True, text=True, check=False)
        gateway = None
        for line in result.stdout.split('\n'):
            if 'default via' in line:
                parts = line.split()
                if 'via' in parts:
                    idx = parts.index('via')
                    if idx + 1 < len(parts):
                        gateway = parts[idx + 1]
                        break
        
        if not gateway:
            # Try to get gateway from non-default routes
            for line in result.stdout.split('\n'):
                if 'via' in line and 'scope link' not in line:
                    parts = line.split()
                    if 'via' in parts:
                        idx = parts.index('via')
                        if idx + 1 < len(parts):
                            potential_gw = parts[idx + 1]
                            # Check if it looks like an IP address
                            if '.' in potential_gw:
                                gateway = potential_gw
                                break
        
        if gateway:
            # Add default route with low metric for active interface
            subprocess.run(["ip", "route", "add", "default", "via", gateway, 
                          "dev", interface, "metric", "100"], check=False)
            logger.info(f"Added default route via {gateway} on {interface} with metric 100")
        else:
            logger.warning(f"Could not determine gateway for {interface}, route may be set by DHCP")
        
        # Set other interfaces to high metric (lower priority)
        for iface in all_interfaces:
            if iface != interface:
                # Check if this interface has a default route
                result = subprocess.run(["ip", "route", "show", "default", "dev", iface],
                                      capture_output=True, text=True, check=False)
                if result.returncode == 0 and result.stdout.strip():
                    logger.info(f"Setting {iface} to metric 2000 (lower priority)")
                    # Delete all existing default routes for this interface
                    for line in result.stdout.split('\n'):
                        gateway_other = None
                        if 'default' in line and iface in line:
                            parts = line.split()
                            if 'via' in parts:
                                idx = parts.index('via')
                                if idx + 1 < len(parts):
                                    gateway_other = parts[idx + 1]
                            
                            if gateway_other:
                                subprocess.run(["ip", "route", "del", "default", "via", gateway_other, "dev", iface], check=False)
                            else:
                                subprocess.run(["ip", "route", "del", "default", "dev", iface], check=False)
                    
                    time.sleep(1)
                    
                    # Re-add with high metric if we found a gateway
                    if gateway_other:
                        subprocess.run(["ip", "route", "add", "default", "via", gateway_other,
                                      "dev", iface, "metric", "2000"], check=False)
        
        # Final cleanup: remove any remaining duplicate routes for the active interface
        result = subprocess.run(["ip", "route", "show", "default", "dev", interface],
                              capture_output=True, text=True, check=False)
        routes = [line for line in result.stdout.split('\n') if line.strip()]
        if len(routes) > 1:
            logger.info(f"Found {len(routes)} default routes for {interface}, keeping only the one with metric 100")
            for line in routes:
                if 'metric 100' not in line:
                    # Delete this duplicate route
                    parts = line.split()
                    if 'via' in parts:
                        idx = parts.index('via')
                        if idx + 1 < len(parts):
                            gw = parts[idx + 1]
                            subprocess.run(["ip", "route", "del", "default", "via", gw, "dev", interface], check=False)
        
        # Verify the route table
        result = subprocess.run(["ip", "route", "show", "default"], 
                              capture_output=True, text=True, check=False)
        logger.info("Current default routes:")
        for line in result.stdout.split('\n'):
            if line.strip():
                logger.info(f"  {line.strip()}")
        
    except Exception as e:
        logger.warning(f"Error setting route priority: {e}")
        logger.info("Route metrics may need to be set manually or via NetworkManager")

def enforce_route_priority(interface):
    """Enforce route priority by ensuring only the active interface has low metric."""
    logger.info(f"Enforcing route priority for {interface}...")
    
    try:
        # Get all default routes
        result = subprocess.run(["ip", "route", "show", "default"],
                              capture_output=True, text=True, check=False)
        
        active_route_found = False
        other_routes = []
        
        for line in result.stdout.split('\n'):
            if not line.strip():
                continue
            
            if interface in line:
                # Check if this route has metric 100
                if 'metric 100' in line:
                    active_route_found = True
                    logger.info(f"Active route found: {line.strip()}")
                else:
                    # This is a duplicate route for our interface with wrong metric
                    logger.info(f"Removing duplicate route with wrong metric: {line.strip()}")
                    parts = line.split()
                    gateway = None
                    if 'via' in parts:
                        idx = parts.index('via')
                        if idx + 1 < len(parts):
                            gateway = parts[idx + 1]
                    
                    if gateway:
                        subprocess.run(["ip", "route", "del", "default", "via", gateway, "dev", interface], check=False)
                    else:
                        subprocess.run(["ip", "route", "del", "default", "dev", interface], check=False)
            else:
                # This is a route for another interface
                other_routes.append(line.strip())
        
        # If we don't have a route with metric 100, create one
        if not active_route_found:
            logger.warning(f"No route with metric 100 found for {interface}, creating one...")
            # Get gateway from interface routes
            result = subprocess.run(["ip", "route", "show", "dev", interface],
                                  capture_output=True, text=True, check=False)
            gateway = None
            for line in result.stdout.split('\n'):
                if 'default via' in line or ('via' in line and 'scope link' not in line):
                    parts = line.split()
                    if 'via' in parts:
                        idx = parts.index('via')
                        if idx + 1 < len(parts):
                            gateway = parts[idx + 1]
                            break
            
            if gateway:
                subprocess.run(["ip", "route", "add", "default", "via", gateway,
                              "dev", interface, "metric", "100"], check=False)
                logger.info(f"Created default route via {gateway} on {interface} with metric 100")
        
        # Ensure other interfaces have high metrics
        for route_line in other_routes:
            # Extract interface name from route
            parts = route_line.split()
            if 'dev' in parts:
                idx = parts.index('dev')
                if idx + 1 < len(parts):
                    other_iface = parts[idx + 1]
                    if other_iface != interface and not other_iface.startswith('lo'):
                        # Check current metric
                        if 'metric' in parts:
                            metric_idx = parts.index('metric')
                            if metric_idx + 1 < len(parts):
                                current_metric = int(parts[metric_idx + 1])
                                if current_metric < 1000:
                                    logger.info(f"Adjusting metric for {other_iface} from {current_metric} to 2000")
                                    # Delete and recreate with higher metric
                                    gateway = None
                                    if 'via' in parts:
                                        via_idx = parts.index('via')
                                        if via_idx + 1 < len(parts):
                                            gateway = parts[via_idx + 1]
                                    
                                    if gateway:
                                        subprocess.run(["ip", "route", "del", "default", "via", gateway, "dev", other_iface], check=False)
                                        subprocess.run(["ip", "route", "add", "default", "via", gateway,
                                                      "dev", other_iface, "metric", "2000"], check=False)
        
        # Final verification
        result = subprocess.run(["ip", "route", "show", "default"],
                              capture_output=True, text=True, check=False)
        logger.info("Final default routes:")
        for line in result.stdout.split('\n'):
            if line.strip():
                logger.info(f"  {line.strip()}")
        
    except Exception as e:
        logger.warning(f"Error enforcing route priority: {e}")

def change_mac_address(interface, new_mac):
    """Change the MAC address of the interface.

    For wired interfaces managed by NetworkManager the MAC is applied via
    802-3-ethernet.cloned-mac-address in the NM profile (set by
    update_networkmanager_config). We do NOT take the interface down manually
    here because doing so generates a link-down event that the upstream switch
    counts as a link-flap, eventually triggering err-disable.

    For wireless interfaces or non-NM paths we still do the traditional
    ifconfig-down → ip link set address → leave-down approach.
    """
    is_wired = interface.startswith("eth") or interface.startswith("en")
    if is_networkmanager_managed(interface):
        if not is_wired:
            logger.info(f"Skipping manual MAC change for wireless NM interface {interface}; NM handles cloned-mac natively upon connection.")
            return
            
        # For wired NM interfaces we set the MAC via ip link while the interface is
        # down. NM may still hold the device after connection down — explicitly
        # bring the link down and wait so the kernel/NM fully release it (avoids
        # "Device or resource busy" on the next session switch).
        # We must securely disconnect the device, else NM might auto-connect another profile.
        logger.info(f"Changing MAC address of {interface} to {new_mac} (ip link while down, no NM cloned-mac flap)...")
        try:
            subprocess.run(["nmcli", "device", "disconnect", interface],
                           check=False, capture_output=True)
            subprocess.run(["ip", "link", "set", "dev", interface, "down"],
                           check=False, capture_output=True)
            time.sleep(2)  # let kernel/NM fully release the interface
            subprocess.run(["ip", "link", "set", "dev", interface, "address", new_mac], check=True)
            logger.info(f"MAC set to {new_mac} on {interface} while interface was down")
        except Exception as e:
            logger.warning(f"Could not set MAC on {interface}: {e}")
        return

    logger.info(f"Changing MAC address of {interface} to {new_mac}...")
    try:
        subprocess.run(["ifconfig", interface, "down"], check=False)
        time.sleep(1)
        subprocess.run(["ip", "link", "set", "dev", interface, "address", new_mac], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to change MAC address: {e}")
    except Exception as e:
        logger.error(f"Error changing MAC: {e}")

# ── Post-bounce fingerprint helpers ─────────────────────────────────────────

def _script_dir() -> str:
    """Return the directory that contains identity_switcher.py."""
    return os.path.dirname(os.path.abspath(__file__))


def launch_fingerprint_tools(interface: str, mac: str, persona: str, hostname: str) -> None:
    """Launch dhcp_fingerprint_inject and http_persona_spoofer after a bounce.

    Both processes are started as non-blocking subprocesses so they don't
    delay the caller.  If scapy or the scripts are missing the failure is
    logged but does not abort the identity switch.

    Args:
        interface : Network interface the identity is on (e.g. wlan0, eth0).
        mac       : The identity's MAC address string (e.g. '54:C4:15:aa:11:01').
        persona   : Persona/OS type key (e.g. 'Camera', 'Windows').
                    Pass None or '' to skip DHCP fingerprint injection.
        hostname  : DHCP hostname (option 12) to send (e.g. 'camera-01').
    """
    base = _script_dir()
    dhcp_script   = os.path.join(base, "dhcp_fingerprint_inject.py")
    http_script   = os.path.join(base, "http_persona_spoofer.py")

    # ── 0. Stop any existing HTTP spoofer so the new one can bind to port 80 ──
    if os.path.exists(http_script):
        try:
            subprocess.run(
                [sys.executable, http_script, "--stop"],
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            pass

    # ── 1. HTTP persona spoofer (start/restart — it's instant) ────────────────
    if os.path.exists(http_script) and persona:
        try:
            logger.info("[fingerprint] Starting HTTP spoofer — persona='%s'", persona)
            subprocess.Popen(
                [sys.executable, http_script,
                 "--persona", persona,
                 "--port", "80",
                 "--daemonize"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.warning("[fingerprint] Could not start HTTP spoofer: %s", exc)
    else:
        if not persona:
            logger.debug("[fingerprint] No persona set — skipping HTTP spoofer.")
        else:
            logger.warning("[fingerprint] http_persona_spoofer.py not found at %s", http_script)

    # ── 2. DHCP fingerprint injector (scapy raw DHCP) ─────────────────────
    # Give NM a couple of seconds to finish DHCP before we inject.
    if os.path.exists(dhcp_script) and persona and mac:
        try:
            # Capture DHCP injector stdout/stderr for observability.
            # This script can fail quickly (e.g., missing scapy). Redirecting to
            # log files makes failures actionable.
            ts = int(time.time())
            default_log_dir = "/var/log/clarion_lab/fingerprint"
            log_dir = default_log_dir
            try:
                os.makedirs(log_dir, exist_ok=True)
            except Exception:
                log_dir = "/tmp/clarion_lab_fingerprint"
                os.makedirs(log_dir, exist_ok=True)

            dhcp_out_path = os.path.join(
                log_dir, f"dhcp_fingerprint_inject_{interface}_{persona}_{ts}.log"
            )
            dhcp_err_path = os.path.join(
                log_dir, f"dhcp_fingerprint_inject_{interface}_{persona}_{ts}.err.log"
            )

            logger.info("[fingerprint] Injecting DHCP fingerprint — persona='%s' mac=%s iface=%s",
                         persona, mac, interface)
            # Run in a separate process; --force-apply is intentionally omitted so
            # the script won't displace NM's lease if one already exists.
            with open(dhcp_out_path, "a") as out, open(dhcp_err_path, "a") as err:
                proc = subprocess.Popen(
                    [sys.executable, dhcp_script,
                     "--interface", interface,
                     "--mac", mac,
                     "--persona", persona,
                     "--hostname", hostname or "",
                     "--timeout", "10"],
                    stdout=out,
                    stderr=err,
                )
                logger.info("[fingerprint] DHCP injector started (pid=%s) log=%s", proc.pid, dhcp_out_path)
        except Exception as exc:
            logger.warning("[fingerprint] Could not launch DHCP fingerprint injector: %s", exc)
    else:
        if not persona:
            logger.debug("[fingerprint] No persona set — skipping DHCP fingerprint injection.")
        elif not mac:
            logger.debug("[fingerprint] No MAC set — skipping DHCP fingerprint injection.")
        else:
            logger.warning("[fingerprint] dhcp_fingerprint_inject.py not found at %s", dhcp_script)


def main():
    parser = argparse.ArgumentParser(description="Identity Switcher")
    parser.add_argument("--interface", default="wlan0", help="Network interface (wlan0, eth0)")
    parser.add_argument("--ssid", default="ClarionLab", help="SSID for wireless connection")
    parser.add_argument("--identities", default="identities.json", help="Path to identities JSON file")
    parser.add_argument("--user", help="Specific username to switch to")
    parser.add_argument("--random", action="store_true", help="Select random user from list")
    parser.add_argument("--random-mac", action="store_true", help="Randomize MAC address (keeping Pi OUI)")
    parser.add_argument("--shutdown-duration", type=int, default=60, help="Seconds to keep interface down for cleanup (default: 60)")
    parser.add_argument("--dhcp-hostname", default=None, help="Hostname to send in DHCP (option 12) so Dot1x/DNS can see the device. Can also set network.dhcp_hostname in lab_config.json")
    
    args = parser.parse_args()
    
    # DHCP hostname: CLI overrides config
    dhcp_hostname = args.dhcp_hostname
    if dhcp_hostname is None:
        try:
            lab_config_path = os.path.join(os.path.dirname(__file__) or ".", "lab_config.json")
            if os.path.exists(lab_config_path):
                with open(lab_config_path, "r") as f:
                    lab_config = json.load(f)
                dhcp_hostname = lab_config.get("network", {}).get("dhcp_hostname") or None
        except Exception:
            pass
    
    # Check root
    if os.geteuid() != 0:
        logger.warning("This script probably needs root privileges to edit /etc/wpa_supplicant/wpa_supplicant.conf and control interfaces.")
    
    # 1. MAC Randomization (do this first while interface is likely up, or bring it down)
    if args.random_mac:
        new_mac = generate_pi_mac()
        change_mac_address(args.interface, new_mac)
    
    identities = load_identities(args.identities)
    
    target_identity = None
    
    if args.user:
        for ident in identities:
            if ident.get("username") == args.user or ident.get("device_name") == args.user:
                target_identity = ident
                break
        if not target_identity:
            logger.error(f"User or device '{args.user}' not found in {args.identities}")
            sys.exit(1)
    elif args.random:
        target_identity = random.choice(identities)
    else:
        # Default to first one or interactive?
        logger.info("No user specified. Available users/devices:")
        for ident in identities:
            name = ident.get("username") or ident.get("device_name") or "(no name)"
            print(f" - {name} ({ident.get('description', '')})")
        logger.info("Use --user [username or device_name] or --random to switch.")
        sys.exit(0)
        
    # Resolve DHCP/device name: identity device_name > display_name > config/CLI
    resolved_dhcp_hostname = (
        target_identity.get("device_name")
        or target_identity.get("display_name")
        or dhcp_hostname
    )
    is_mab = (target_identity.get("auth") or "dot1x").lower() == "mab"
    cloned_mac = target_identity.get("mac")
    # persona field drives both DHCP (option 55/60/43) and HTTP fingerprinting.
    # For user personas the identity JSON may set "persona" explicitly; fall back
    # to "department" (e.g. "Windows", "Linux") so workstation OS is still spoofed.
    persona = (
        target_identity.get("persona")
        or target_identity.get("os_type")
        or target_identity.get("department")
        or ""
    )
    # Resolve persona profile for vendor_class / dhcp_req_list (NM and bounce).
    persona_profile = get_persona_profile(persona)
    vendor_class = (persona_profile or {}).get("vendor_class") or ""
    dhcp_req_list = (persona_profile or {}).get("dhcp_req_list") if persona_profile else None

    if is_mab:
        logger.info("MAB identity: no 802.1x; using MAC + device name only.")
        apply_mab_connection(args.interface, dhcp_hostname=resolved_dhcp_hostname, cloned_mac=cloned_mac, vendor_class=vendor_class or None)
        bounce_interface(args.interface, shutdown_duration=args.shutdown_duration,
                        dhcp_hostname=resolved_dhcp_hostname, mab=True, vendor_class=vendor_class or None, dhcp_req_list=dhcp_req_list)
    else:
        logger.info(f"Switching identity to: {target_identity['username']}")
        update_wpa_config(args.interface, args.ssid, target_identity["username"], target_identity["password"],
                          dhcp_hostname=resolved_dhcp_hostname, cloned_mac=cloned_mac, vendor_class=vendor_class or None)
        bounce_interface(args.interface, shutdown_duration=args.shutdown_duration,
                         dhcp_hostname=resolved_dhcp_hostname, mab=False, vendor_class=vendor_class or None, dhcp_req_list=dhcp_req_list)

    # Launch DHCP fingerprint injector + HTTP persona spoofer so ISE profiles
    # this device correctly as the target persona/OS rather than generic Linux.
    launch_fingerprint_tools(
        interface=args.interface,
        mac=cloned_mac or "",
        persona=persona,
        hostname=resolved_dhcp_hostname or "",
    )

    logger.info("Identity switch complete. Check connectivity.")

if __name__ == "__main__":
    main()
