#!/usr/bin/env python3
"""
Assign application cohorts orthogonal to persona, and per-persona traffic cadence.

Cohort assignment is deterministic (SHA-256 of MAC or username), so repeated
runs produce identical labels and previously logged ground truth stays valid.

Usage:
    python3 tools/apply_cohorts.py --dry-run
    python3 tools/apply_cohorts.py --apply
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from collections import defaultdict

API = "http://localhost:5000/api"

# persona -> [(cohort, cumulative_percent), ...]; last entry must be 100
COHORT_SPLITS = {
    "Camera":                   [("app-physec", 60), ("app-clinical", 85), ("app-ot-line1", 100)],
    "Badge Reader":             [("app-physec", 80), ("app-clinical", 100)],
    "Door Lock":                [("app-physec", 70), ("app-bms", 100)],
    "Display":                  [("app-bms", 40), ("app-voice", 70), ("app-clinical", 100)],
    "Environmental Sensor":     [("app-bms", 60), ("app-ot-line2", 100)],
    "HVAC Controller":          [("app-bms", 85), ("app-clinical", 100)],
    "Printer":                  [("app-itops", 55), ("app-clinical", 100)],
    "VoIP Phone":               [("app-voice", 90), ("app-clinical", 100)],
    "Medical Device":           [("app-clinical", 100)],
    "Robot":                    [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "PLC":                      [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "RTU":                      [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "HMI":                      [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "Historian":                [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "SCADA Server":             [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "DCS Controller":           [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "Safety Controller":        [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "Field Device":             [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "Industrial Gateway":       [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "Engineering Workstation":  [("app-devtools", 40), ("app-ot-line1", 100)],
    "Sales":                    [("app-crm", 70), ("app-erp", 100)],
    "Finance":                  [("app-erp", 65), ("app-crm", 100)],
    "Engineering":              [("app-devtools", 70), ("app-ot-line1", 100)],
    "IT":                       [("app-itops", 50), ("app-devtools", 75), ("app-erp", 100)],
    "Smart Thermostat":         [("app-bms", 75), ("app-itops", 100)],
    "Power Meter":              [("app-bms", 70), ("app-ot-line2", 100)],
    "Smart PDU":                [("app-itops", 70), ("app-bms", 100)],
}

# persona -> (traffic_min_sleep, traffic_max_sleep)
CADENCE = {
    "Camera": (1, 5),
    "PLC": (1, 3), "RTU": (1, 3), "Safety Controller": (1, 3),
    "Historian": (5, 15), "SCADA Server": (5, 15), "DCS Controller": (5, 15),
    "Medical Device": (10, 30),
    "Robot": (5, 20), "Industrial Gateway": (5, 20),
    "Field Device": (5, 20), "HMI": (5, 20),
    "Environmental Sensor": (30, 60),
    "Display": (30, 120),
    "HVAC Controller": (60, 300),
    "VoIP Phone": (60, 120),
    "Badge Reader": (60, 600),
    "Door Lock": (120, 900),
    "Printer": (300, 1800),
    "Engineering Workstation": (3, 20),
    "Smart Thermostat": (600, 1800),
    "Power Meter": (10, 30),
    "Smart PDU": (30, 60),
}

SHARED = "infra-shared"
ALL_COHORTS = {c for splits in COHORT_SPLITS.values() for c, _ in splits}


def get(path):
    with urllib.request.urlopen(f"{API}/{path}", timeout=15) as r:
        return json.loads(r.read().decode())


def post(path, payload):
    req = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def bucket(identity):
    """Stable 0-99 bucket from MAC, falling back to username or device_name."""
    key = (identity.get("mac") or identity.get("username")
           or identity.get("device_name") or "").strip().lower()
    if not key:
        return None
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 100


def cohort_for(identity):
    persona = (identity.get("persona") or identity.get("department") or "").strip()
    splits = COHORT_SPLITS.get(persona)
    if not splits:
        return None, persona
    b = bucket(identity)
    if b is None:
        return None, persona
    for cohort, ceiling in splits:
        if b < ceiling:
            return cohort, persona
    return splits[-1][0], persona


def check_invariants(assignments):
    """assignments: list of (persona, cohort). Returns list of failure strings."""
    by_cohort, by_persona = defaultdict(set), defaultdict(set)
    for persona, cohort in assignments:
        by_cohort[cohort].add(persona)
        by_persona[persona].add(cohort)

    failures = []
    for cohort, personas in sorted(by_cohort.items()):
        if len(personas) < 2:
            failures.append(f"cohort {cohort!r} has only persona(s) {sorted(personas)}")
    for persona, cohorts in sorted(by_persona.items()):
        if persona == "Medical Device":
            continue
        if len(cohorts) < 2:
            failures.append(f"persona {persona!r} sits in only cohort(s) {sorted(cohorts)}")
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="POST changes (default is dry run)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    policies = get("connectivity")
    leaked = [k for k in policies if k not in ALL_COHORTS and k != SHARED]
    if leaked:
        print(f"ERROR: connectivity_policies still contains non-cohort keys: {leaked}")
        print("Persona keys must be removed first or device type stays correlated.")
        return 1

    identities = get("identities")
    print(f"Loaded {len(identities)} identities")

    assignments, skipped = [], []
    for ident in identities:
        cohort, persona = cohort_for(ident)
        if not cohort:
            skipped.append(ident.get("device_name") or ident.get("username") or "?")
            continue
        ident["application_cohort"] = cohort
        groups = [g for g in (ident.get("groups") or []) if g not in ALL_COHORTS and g != SHARED]
        groups.extend([cohort, SHARED])
        ident["groups"] = groups
        if persona in CADENCE:
            ident["traffic_min_sleep"], ident["traffic_max_sleep"] = CADENCE[persona]
        assignments.append((persona, cohort))

    counts = defaultdict(int)
    for persona, cohort in assignments:
        counts[(persona, cohort)] += 1
    print("\npersona -> cohort distribution:")
    for (persona, cohort), n in sorted(counts.items()):
        print(f"  {persona:26} {cohort:16} {n:4}")

    if skipped:
        print(f"\nWARNING: {len(skipped)} identities had no persona mapping: {skipped[:10]}")

    failures = check_invariants(assignments)
    if failures:
        print("\nINVARIANT FAILURES — the experiment will not be able to separate the two dimensions:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nInvariants OK: every cohort spans >=2 personas, every persona spans >=2 cohorts.")

    if not args.apply:
        print("\nDry run. Re-run with --apply to POST.")
        return 0

    print(post("identities", identities))
    print("Applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
