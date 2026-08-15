#!/usr/bin/env python3
"""
Seed Active Directory (Samba DC) with 500 users across 4 department OUs
and sync them with the local orchestrator database.

Usage:
    python3 tools/seed_ad_users.py --dry-run
    python3 tools/seed_ad_users.py --apply
"""

import argparse
import json
import random
import sys
import urllib.request
from collections import defaultdict

# Active Directory LDAPS connection settings
AD_SERVER = "192.168.100.10"
AD_PORT = 636
AD_USER = "Administrator@netlab.net"
AD_PASSWORD = "C!sco#123"
BASE_DN = "DC=netlab,DC=net"

# Orchestrator API
API = "http://localhost:5000/api"

# Name pools
FIRST_NAMES = [
    "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda",
    "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph",
    "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Christopher", "Nancy",
    "Daniel", "Lisa", "Matthew", "Betty", "Anthony", "Margaret", "Mark", "Sandra",
    "Paul", "Ashley", "Steven", "Dorothy", "Andrew", "Kimberly", "Kenneth", "Emily",
    "Joshua", "Donna", "Kevin", "Michelle", "Brian", "Carol", "George", "Amanda",
    "Edward", "Dorothy", "Ronald", "Melissa"
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill",
    "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell",
    "Mitchell", "Carter", "Roberts"
]

DEPARTMENTS = {
    "Sales": ("OU=Sales,DC=netlab,DC=net", 0.35),
    "Finance": ("OU=Finance,DC=netlab,DC=net", 0.25),
    "Engineering": ("OU=Engineering,DC=netlab,DC=net", 0.25),
    "IT": ("OU=IT,DC=netlab,DC=net", 0.15)
}


def generate_unique_users(count=500):
    users = []
    generated = set()
    
    # Deterministic random seed so runs are reproducible
    rng = random.Random(42)
    
    deps = list(DEPARTMENTS.keys())
    weights = [DEPARTMENTS[d][1] for d in deps]
    
    while len(users) < count:
        fn = rng.choice(FIRST_NAMES)
        ln = rng.choice(LAST_NAMES)
        username = f"{fn.lower()}.{ln.lower()}"
        
        # Handle collision
        suffix = 1
        orig_username = username
        while username in generated:
            username = f"{orig_username}{suffix}"
            suffix += 1
            
        generated.add(username)
        
        dep = rng.choices(deps, weights=weights)[0]
        ou_dn = DEPARTMENTS[dep][0]
        
        users.append({
            "username": username,
            "display_name": f"{fn} {ln}",
            "given_name": fn,
            "sn": ln,
            "department": dep,
            "ou_dn": ou_dn,
            "email": f"{username}@netlab.net"
        })
        
    return users


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="POST changes to AD and Orchestrator API")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    
    if not args.apply and not args.dry_run:
        print("Please specify --apply or --dry-run")
        return 1

    users = generate_unique_users(500)
    
    # Calculate stats
    stats = defaultdict(int)
    for u in users:
        stats[u["department"]] += 1
        
    print(f"Generated {len(users)} unique user objects:")
    for dep, cnt in stats.items():
        print(f"  - {dep:15} {cnt:4} users")

    if args.dry_run:
        print("\nDry run completed successfully. No changes made.")
        return 0

    # Execute LDAPS connection and user writes
    try:
        from ldap3 import Server, Connection, MODIFY_REPLACE
    except ImportError:
        print("Error: ldap3 library is required. Install it using 'pip3 install ldap3'.")
        return 1

    print("\nConnecting to AD server via secure LDAPS...")
    try:
        server = Server(AD_SERVER, port=AD_PORT, use_ssl=True)
        conn = Connection(server, user=AD_USER, password=AD_PASSWORD, auto_bind=True)
        print("✓ Connected and bound successfully to AD!")
    except Exception as e:
        print(f"✗ Failed to connect to Active Directory DC: {e}")
        return 1

    print("Seeding users into Active Directory OUs...")
    created_users = []
    
    # Normal user Account Control flag
    # 512 = NORMAL_ACCOUNT
    pw_utf = ('"%s"' % AD_PASSWORD).encode('utf-16-le')
    
    for i, u in enumerate(users):
        dn = f"CN={u['display_name']},{u['ou_dn']}"
        
        # Check if already exists, recreate if needed
        try:
            conn.delete(dn)
        except:
            pass
            
        res = conn.add(dn,
            object_class=['top', 'person', 'organizationalPerson', 'user'],
            attributes={
                'sAMAccountName': u['username'],
                'userPrincipalName': f"{u['username']}@netlab.net",
                'givenName': u['given_name'],
                'sn': u['sn'],
                'displayName': u['display_name'],
                'department': u['department'],
                'mail': u['email']
            }
        )
        
        if res:
            # Set password and enable account
            conn.modify(dn, {
                'unicodePwd': [(MODIFY_REPLACE, [pw_utf])],
                'userAccountControl': [(MODIFY_REPLACE, [512])]
            })
            created_users.append(u)
            if (len(created_users)) % 50 == 0:
                print(f"  ... created {len(created_users)} / 500 users")
        else:
            print(f"✗ Failed to create user {u['username']} ({conn.result.get('description')})")

    print(f"✓ Created {len(created_users)} users in Active Directory.")
    
    # Import into orchestrator
    if created_users:
        print("\nImporting users into Clarion Orchestrator database...")
        payload = {
            "users": [
                {
                    "username": u["username"],
                    "display_name": u["display_name"],
                    "department": u["department"],
                    "email": u["email"]
                } for u in created_users
            ],
            "default_password": AD_PASSWORD
        }
        
        try:
            req = urllib.request.Request(
                f"{API}/ad/import-users",
                data=json.dumps(payload).encode(),
                method="POST",
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read().decode())
                print(f"✓ Orchestrator imported {resp.get('imported_count')} users successfully!")
        except Exception as e:
            print(f"✗ Failed to sync users to Orchestrator API: {e}")
            return 1
            
    print("\nSeeding of AD users completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
