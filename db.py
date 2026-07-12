#!/usr/bin/env python3
"""
Clarion Lab – SQLite-backed config and identities (client/server, no JSON files).

Config (runners, services, connectivity_policies, settings) and identities
live in ``clarion_lab.db`` (see DEFAULT_DB_PATH). That database is the source
of truth at runtime: the dashboard and APIs read/write it. ``identities1.json``
is only imported once when the identities table is empty—editing JSON does not
update an existing DB; use the UI, ``set_identities`` / migration helpers, or
run a one-shot script against the DB file on the orchestrator host.
"""

import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Canonical DB location for lab deployments is under lab root so upgrades and
# service working-directory changes do not split state into multiple DB files.
LAB_ROOT_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "clarion_lab.db")
)
APP_LOCAL_DB_PATH = os.path.join(os.path.dirname(__file__), "clarion_lab.db")
DEFAULT_DB_PATH = os.environ.get("CLARION_LAB_DB_PATH") or (
    LAB_ROOT_DB_PATH if os.path.exists(LAB_ROOT_DB_PATH) else APP_LOCAL_DB_PATH
)

# Default config keys (JSON blobs)
CONFIG_KEYS = (
    "runners",
    "services",
    "connectivity_policies",
    "ground_truth_log",
    "orchestrator_url",
    "clarion_api_url",
    "custom_personas",
    "orchestration_settings",
    "policy_test_log",
    "runner_health_settings",
)

DEFAULT_RUNNERS: List[Dict[str, Any]] = [
    {
        "name": "pi-runner-1",
        "runner_type": "pi",
        "interface": "eth0",
        "management_interface": "wlan0",
        "persona_set": ["Sales"],
        "session_duration": 600,
    },
]

DEFAULT_SERVICES: List[Dict[str, Any]] = []
DEFAULT_CONNECTIVITY: Dict[str, List[str]] = {}
DEFAULT_GROUND_TRUTH_LOG = os.path.expanduser("~/clarion/lab/ground_truth/ground_truth_log.csv")
DEFAULT_ORCHESTRATOR_URL = "http://192.168.20.95:5000"
DEFAULT_ORCHESTRATION_SETTINGS: Dict[str, Any] = {
    "role_behaviors": {
        "sales": {
            "session_min": 900,
            "session_max": 2100,
            "traffic_min_sleep": 4,
            "traffic_max_sleep": 18,
            "target_min": 2,
            "target_max": 3,
            "primary_patterns": ["thehub", "sales", "crm", "customer"],
            "secondary_patterns": ["www.netlab.net", "portal", "finance"],
        },
        "finance": {
            "session_min": 1200,
            "session_max": 2400,
            "traffic_min_sleep": 8,
            "traffic_max_sleep": 30,
            "target_min": 1,
            "target_max": 3,
            "primary_patterns": ["finance", "erp", "payroll", "ledger"],
            "secondary_patterns": ["thehub", "www.netlab.net"],
        },
        "engineering": {
            "session_min": 1500,
            "session_max": 2700,
            "traffic_min_sleep": 3,
            "traffic_max_sleep": 20,
            "target_min": 2,
            "target_max": 4,
            "primary_patterns": ["engineering", "code", "api", "iotdev", "git"],
            "secondary_patterns": ["thehub", "www.netlab.net", "192.168.30.2"],
        },
        "it": {
            "session_min": 600,
            "session_max": 1800,
            "traffic_min_sleep": 2,
            "traffic_max_sleep": 14,
            "target_min": 2,
            "target_max": 5,
            "primary_patterns": ["mab", "code", "engineering", "admin", "cmdb"],
            "secondary_patterns": ["thehub", "finance", "www.netlab.net"],
        },
        "office": {
            "session_min": 900,
            "session_max": 2100,
            "traffic_min_sleep": 5,
            "traffic_max_sleep": 24,
            "target_min": 2,
            "target_max": 3,
            "primary_patterns": ["thehub", "portal", "www.netlab.net"],
            "secondary_patterns": ["finance", "engineering"],
        },
        "iot": {
            "session_min": 1800,
            "session_max": 7200,
            "traffic_min_sleep": 30,
            "traffic_max_sleep": 300,
            "target_min": 1,
            "target_max": 2,
            "primary_patterns": ["telemetry", "camera", "printer", "badge", "lock", "hvac", "sensor", "robot", "medical"],
            "secondary_patterns": [],
        },
    },
    "off_hours_factor": {
        "sales": 0.35,
        "finance": 0.30,
        "engineering": 0.70,
        "it": 0.85,
        "office": 0.50,
        "iot": 1.0,
    },
    "policy_test_settings": {
        "enabled": True,
        "allow_cases_per_session": 3,
        "deny_cases_per_session": 2,
        "max_attempts_per_case": 1,
        "request_timeout_ms": 8000,
        "fallback_to_complement": False,
        "deny_catalog": {},
        "test_matrix": {},
    },
}
DEFAULT_POLICY_TEST_LOG = os.path.expanduser("~/clarion/lab/ground_truth/policy_test_results.jsonl")


def _merge_orchestration_settings(raw: Any) -> Dict[str, Any]:
    """Merge stored orchestration settings with new defaults for backward compatibility."""
    merged = json.loads(json.dumps(DEFAULT_ORCHESTRATION_SETTINGS))
    if not isinstance(raw, dict):
        return merged
    merged["role_behaviors"].update(raw.get("role_behaviors") or {})
    merged["off_hours_factor"].update(raw.get("off_hours_factor") or {})
    merged["policy_test_settings"].update(raw.get("policy_test_settings") or {})
    return merged


def _default_config_value(key: str) -> Any:
    if key == "runners":
        return DEFAULT_RUNNERS
    if key == "services":
        return DEFAULT_SERVICES
    if key == "connectivity_policies":
        return DEFAULT_CONNECTIVITY
    if key == "ground_truth_log":
        return DEFAULT_GROUND_TRUTH_LOG
    if key == "orchestrator_url":
        return DEFAULT_ORCHESTRATOR_URL
    if key == "clarion_api_url":
        return os.environ.get("CLARION_API_URL", "http://192.168.30.2:5000/api").strip()
    if key == "custom_personas":
        return []
    if key == "orchestration_settings":
        return DEFAULT_ORCHESTRATION_SETTINGS
    if key == "policy_test_log":
        return DEFAULT_POLICY_TEST_LOG
    if key == "runner_health_settings":
        from runner_health_controller import DEFAULT_HEALTH_SETTINGS

        return DEFAULT_HEALTH_SETTINGS
    return None


def get_connection(db_path: str = None) -> sqlite3.Connection:
    """Return a connection to the lab DB; create schema if needed."""
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


def get_config(key: str, db_path: str = None) -> Any:
    """Get one config key (JSON-decoded). Returns default if missing."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        if row:
            value = json.loads(row["value"])
            if key == "orchestration_settings":
                return _merge_orchestration_settings(value)
            return value
        default = _default_config_value(key)
        if default is not None:
            set_config(key, default, db_path)
            return default
        return None
    finally:
        conn.close()


def set_config(key: str, value: Any, db_path: str = None) -> None:
    """Set one config key (value will be JSON-encoded)."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
        conn.commit()
    finally:
        conn.close()


def get_full_config(db_path: str = None) -> Dict[str, Any]:
    """Return full config dict including orchestration settings."""
    conn = get_connection(db_path)
    out = {}
    try:
        for key in CONFIG_KEYS:
            row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
            if row:
                value = json.loads(row["value"])
                if key == "orchestration_settings":
                    out[key] = _merge_orchestration_settings(value)
                else:
                    out[key] = value
            else:
                default = _default_config_value(key)
                if default is not None:
                    set_config(key, default, db_path)
                    out[key] = default
                else:
                    out[key] = None
        return out
    finally:
        conn.close()


def save_full_config(config: Dict[str, Any], db_path: str = None) -> None:
    """Save the supported top-level config keys to DB."""
    for key in (
        "runners",
        "services",
        "connectivity_policies",
        "ground_truth_log",
        "orchestrator_url",
        "clarion_api_url",
        "orchestration_settings",
        "policy_test_log",
        "runner_health_settings",
    ):
        if key in config:
            set_config(key, config[key], db_path)


def get_identities(db_path: str = None) -> List[Dict[str, Any]]:
    """Return all identities from the database (source of truth). Each row's data column is JSON-decoded."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT data FROM identities ORDER BY id").fetchall()
        return [json.loads(r["data"]) for r in rows]
    finally:
        conn.close()


def set_identities(identities: List[Dict[str, Any]], db_path: str = None) -> None:
    """Replace all identities in the database (source of truth). Each identity is stored as a JSON blob; all keys (e.g. os, persona, department) are preserved."""
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM identities")
        for ident in identities:
            conn.execute("INSERT INTO identities (data) VALUES (?)", (json.dumps(ident),))
        conn.commit()
    finally:
        conn.close()


def migrate_user_device_names_ws_to_wst(db_path: str = None) -> int:
    """
    User identities only (rows with ``username``): rename DHCP-style hostnames
    ``*-ws`` -> ``*-wst``. IoT / MAB rows (no username) are unchanged.

    Returns the number of identities updated. Idempotent for already-renamed rows.
    """
    identities = get_identities(db_path)
    changed = 0
    for ident in identities:
        if not ident.get("username"):
            continue
        dn = (ident.get("device_name") or "").strip()
        if dn.endswith("-ws"):
            ident["device_name"] = f"{dn}t"
            changed += 1
    if changed:
        set_identities(identities, db_path)
        logger.info("migrate_user_device_names_ws_to_wst: updated %d identities", changed)
    return changed


def migrate_from_json_if_present(db_path: str = None) -> None:
    """One-time: if DB has no identities and identities1.json exists, import it. Same for config."""
    path = db_path or DEFAULT_DB_PATH
    lab_dir = os.path.dirname(path)
    identities_file = os.path.join(lab_dir, "identities1.json")
    config_file = os.path.join(lab_dir, "orchestrator_config.json")

    conn = get_connection(db_path)
    try:
        # Identities
        count = conn.execute("SELECT COUNT(*) FROM identities").fetchone()[0]
        if count == 0 and os.path.exists(identities_file):
            with open(identities_file, "r") as f:
                identities = json.load(f)
            set_identities(identities, db_path)
            logger.info("Migrated identities from identities1.json into DB")

        # Config: if no runners in DB but orchestrator_config.json exists, import
        row = conn.execute("SELECT value FROM config WHERE key = 'runners'").fetchone()
        if not row and os.path.exists(config_file):
            with open(config_file, "r") as f:
                config = json.load(f)
            for key in CONFIG_KEYS:
                if key in config:
                    set_config(key, config[key], db_path)
            logger.info("Migrated config from orchestrator_config.json into DB")
    finally:
        conn.close()
