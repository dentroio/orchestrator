#!/usr/bin/env python3
"""
Active Directory Connector for Clarion Lab Orchestrator

Handles LDAP connections and user queries from Active Directory.
"""

import logging
from typing import List, Dict, Optional, Tuple
import random

try:
    from ldap3 import Server, Connection, ALL, SUBTREE
    from ldap3.core.exceptions import LDAPException
    LDAP3_AVAILABLE = True
except ImportError:
    LDAP3_AVAILABLE = False
    logging.warning("ldap3 library not available. Install with: pip3 install ldap3")

logger = logging.getLogger(__name__)


class ADConnector:
    """Manages Active Directory connections and queries."""
    
    def __init__(self):
        """Initialize the ADConnector."""
        self.connection = None
        self.server = None
    
    def test_connection(self, server_address: str, port: int, use_ssl: bool, 
                       bind_dn: str, password: str) -> Tuple[bool, str]:
        """
        Test connection to Active Directory.
        
        Args:
            server_address: IP address or hostname of the AD server.
            port: LDAP port (e.g., 389 for plain, 636 for SSL).
            use_ssl: Whether to connect via SSL.
            bind_dn: Distinguished name (or UPN) to bind with.
            password: Password for the bind user.
        
        Returns:
            Tuple of (success: bool, message: str)
        """
        if not LDAP3_AVAILABLE:
            return False, "ldap3 library not installed. Run: pip3 install ldap3"
        
        try:
            # Create server object
            self.server = Server(
                server_address,
                port=port,
                use_ssl=use_ssl,
                get_info=ALL
            )
            
            # Attempt to bind
            self.connection = Connection(
                self.server,
                user=bind_dn,
                password=password,
                auto_bind=True
            )
            
            logger.info(f"Successfully connected to AD server: {server_address}")
            return True, f"Successfully connected to {server_address}"
            
        except LDAPException as e:
            logger.error(f"LDAP connection failed: {e}")
            return False, f"Connection failed: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error during AD connection: {e}")
            return False, f"Error: {str(e)}"
    
    def get_organizational_units(self, base_dn: str) -> List[Dict[str, str]]:
        """
        Retrieve list of organizational units.
        
        Args:
            base_dn: Base distinguished name to search from (e.g., "DC=example,DC=com")
        
        Returns:
            List of OUs with 'dn' and 'name' keys
        """
        if not self.connection:
            logger.error("No active AD connection")
            return []
        
        try:
            # Search for organizational units
            self.connection.search(
                search_base=base_dn,
                search_filter='(objectClass=organizationalUnit)',
                search_scope=SUBTREE,
                attributes=['ou', 'distinguishedName']
            )
            
            ous = []
            for entry in self.connection.entries:
                ou_name = str(entry.ou) if hasattr(entry, 'ou') and entry.ou else "Unknown"
                ous.append({
                    'dn': str(entry.distinguishedName) if hasattr(entry, 'distinguishedName') and entry.distinguishedName else "",
                    'name': ou_name
                })
            
            logger.info(f"Found {len(ous)} organizational units")
            return ous
            
        except LDAPException as e:
            logger.error(f"Failed to query OUs: {e}")
            return []
    
    def get_users_from_ou(self, ou_dn: str, active_only: bool = True) -> List[Dict[str, str]]:
        """
        Query users from a specific organizational unit.
        
        Args:
            ou_dn: Distinguished name of the OU
            active_only: If True, only return active (enabled) accounts
        
        Returns:
            List of user dictionaries with AD attributes
        """
        if not self.connection:
            logger.error("No active AD connection")
            return []
        
        try:
            # Build search filter
            search_filter = '(&(objectClass=user)(objectCategory=person)'
            if active_only:
                # userAccountControl flag 2 = ACCOUNTDISABLE
                # We want accounts where this flag is NOT set
                search_filter += '(!(userAccountControl:1.2.840.113556.1.4.803:=2))'
            search_filter += ')'
            
            # Search for users
            self.connection.search(
                search_base=ou_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=[
                    'sAMAccountName',
                    'displayName',
                    'givenName',
                    'sn',
                    'department',
                    'mail',
                    'userAccountControl'
                ]
            )
            
            users = []
            for entry in self.connection.entries:
                # Extract attributes safely
                username = str(entry.sAMAccountName) if hasattr(entry, 'sAMAccountName') and entry.sAMAccountName else None
                if not username:
                    continue
                
                display_name = str(entry.displayName) if hasattr(entry, 'displayName') and entry.displayName else username
                department = str(entry.department) if hasattr(entry, 'department') and entry.department else ""
                email = str(entry.mail) if hasattr(entry, 'mail') and entry.mail else ""
                
                users.append({
                    'username': username,
                    'display_name': display_name,
                    'department': department,
                    'email': email
                })
            
            logger.info(f"Found {len(users)} users in {ou_dn}")
            return users
            
        except LDAPException as e:
            logger.error(f"Failed to query users: {e}")
            return []
    
    def disconnect(self):
        """Close the LDAP connection."""
        if self.connection:
            self.connection.unbind()
            self.connection = None
            logger.info("Disconnected from AD")


def generate_device_name(username: str, existing_names: List[str]) -> str:
    """
    Generate a unique device name from username.
    
    Args:
        username: AD username (e.g., "alice.johnson")
        existing_names: List of existing device names to avoid duplicates
    
    Returns:
        Unique device name (e.g., "ajohnson-wst")
    """
    # Extract first letter of first name and full last name
    parts = username.split('.')
    if len(parts) >= 2:
        device_name = f"{parts[0][0]}{parts[1]}-wst"
    else:
        device_name = f"{username}-wst"
    
    # Ensure uniqueness
    base_name = device_name
    counter = 1
    while device_name in existing_names:
        device_name = f"{base_name}-{counter}"
        counter += 1
    
    return device_name


def generate_mac_address(existing_macs: List[str]) -> str:
    """
    Generate a unique MAC address.
    
    Args:
        existing_macs: List of existing MAC addresses to avoid duplicates
    
    Returns:
        Unique MAC address in format "dc:a6:32:xx:xx:xx"
    """
    # Use consistent vendor prefix
    vendor_prefix = "dc:a6:32"
    
    while True:
        # Generate random last 3 octets
        mac = f"{vendor_prefix}:{random.randint(0, 255):02x}:{random.randint(0, 255):02x}:{random.randint(0, 255):02x}"
        
        if mac not in existing_macs:
            return mac


def convert_ad_users_to_identities(ad_users: List[Dict[str, str]], 
                                   existing_identities: List[Dict],
                                   default_password: str = "C!sco#123",
                                   default_ssid: str = "netlab_employee") -> List[Dict]:
    """
    Convert AD users to Clarion identity format.
    
    Args:
        ad_users: List of AD user dictionaries
        existing_identities: Existing identities to avoid duplicates
        default_password: Default password for imported users
        default_ssid: Default SSID for imported users
    
    Returns:
        List of identity dictionaries ready for import
    """
    existing_names = [i.get('device_name', '') for i in existing_identities]
    existing_macs = [i.get('mac', '') for i in existing_identities]
    existing_usernames = [i.get('username', '') for i in existing_identities]
    
    new_identities = []
    
    for user in ad_users:
        username = user['username']
        
        # Skip if username already exists
        if username in existing_usernames:
            logger.warning(f"Skipping {username} - already exists")
            continue
        
        # Generate unique device name and MAC
        device_name = generate_device_name(username, existing_names)
        mac = generate_mac_address(existing_macs)
        
        # Add to tracking lists
        existing_names.append(device_name)
        existing_macs.append(mac)
        existing_usernames.append(username)
        
        # Create identity
        identity = {
            'username': username,
            'display_name': user.get('display_name', username),
            'device_name': device_name,
            'department': user.get('department', 'Imported'),
            'description': f"Imported from AD: {user.get('email', '')}",
            'password': default_password,
            'mac': mac,
            'ssid': default_ssid
        }
        
        new_identities.append(identity)
    
    logger.info(f"Converted {len(new_identities)} AD users to identities")
    return new_identities
