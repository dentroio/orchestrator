"""
Clarion scale status and per-launch verification against ground truth.
"""

from __future__ import annotations

import csv
import os
import urllib.error
import urllib.request
import json
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple


def _fetch_json(url: str, timeout: int = 8) -> Tuple[Optional[Any], Optional[str]]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode()), None
    except Exception as exc:
        return None, str(exc)


def clarion_endpoint_stats(api_base: str) -> Dict[str, Any]:
    """
    Query Clarion GET /devices for inventory scale.

    api_base: e.g. http://192.168.30.2:5000/api
    """
    base = (api_base or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "clarion_api_url not configured", "endpoint_count": 0}

    url = f"{base}/devices?limit=1&offset=0&infrastructure=exclude&validation=trusted"
    payload, err = _fetch_json(url)
    if err:
        return {"ok": False, "error": err, "endpoint_count": 0, "api_url": base}

    total = 0
    if isinstance(payload, dict):
        total = int(payload.get("total") or payload.get("count") or 0)
        if not total and isinstance(payload.get("devices"), list):
            total = len(payload["devices"])
    elif isinstance(payload, list):
        total = len(payload)

    return {
        "ok": True,
        "endpoint_count": total,
        "api_url": base,
    }


def load_ground_truth_sessions(
    ground_truth_path: str,
    launch_id: Optional[str] = None,
) -> List[Dict[str, str]]:
    path = os.path.expanduser(ground_truth_path or "")
    if not path or not os.path.isfile(path):
        return []
    rows: List[Dict[str, str]] = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if launch_id and (row.get("launch_id") or "") != launch_id:
                continue
            if (row.get("status") or "").strip().lower() != "started":
                continue
            rows.append(dict(row))
    return rows


def verify_launch_against_clarion(
    ground_truth_path: str,
    api_base: str,
    launch_id: str,
    *,
    expected_persona: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compare ground-truth sessions for launch_id to Clarion device inventory.

    Matching is by MAC (normalized) then device_name / username.
    """
    sessions = load_ground_truth_sessions(ground_truth_path, launch_id)
    stats = clarion_endpoint_stats(api_base)
    out: Dict[str, Any] = {
        "launch_id": launch_id,
        "ground_truth_sessions": len(sessions),
        "expected_persona": expected_persona,
        "clarion": stats,
    }
    if not sessions:
        out["ok"] = False
        out["message"] = "No ground-truth sessions for this launch_id"
        return out

    if not stats.get("ok"):
        out["ok"] = False
        out["message"] = "Could not reach Clarion API"
        return out

    devices_url = f"{stats['api_url'].rstrip('/')}/devices?limit=1000&offset=0&infrastructure=exclude"
    payload, err = _fetch_json(devices_url)
    if err:
        out["ok"] = False
        out["message"] = err
        return out

    devices: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        devices = payload.get("devices") or payload.get("endpoints") or []
    elif isinstance(payload, list):
        devices = payload

    by_mac: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    for dev in devices:
        mac = (dev.get("mac_address") or dev.get("mac") or "").strip().lower().replace(":", "")
        name = (dev.get("device_name") or dev.get("hostname") or dev.get("name") or "").strip().lower()
        if mac:
            by_mac[mac] = dev
        if name:
            by_name[name] = dev

    matched = 0
    persona_hits = 0
    details: List[Dict[str, Any]] = []

    for row in sessions:
        mac = (row.get("device_mac") or "").strip().lower().replace(":", "")
        name = (row.get("device_name") or row.get("username") or "").strip().lower()
        expected = (row.get("persona") or expected_persona or "").strip()
        dev = by_mac.get(mac) if mac and mac != "unknown" else None
        if not dev and name:
            dev = by_name.get(name)

        entry = {
            "device_mac": row.get("device_mac"),
            "device_name": row.get("device_name"),
            "expected_persona": expected,
            "found_in_clarion": bool(dev),
            "clarion_cluster": None,
            "clarion_device_type": None,
            "persona_match": False,
        }
        if dev:
            matched += 1
            cluster = (
                dev.get("cluster_label")
                or dev.get("assigned_group")
                or dev.get("persona")
                or dev.get("department")
                or ""
            )
            dtype = dev.get("device_type") or dev.get("device_profile") or ""
            entry["clarion_cluster"] = cluster
            entry["clarion_device_type"] = dtype
            if expected and (
                expected.lower() in str(cluster).lower()
                or expected.lower() in str(dtype).lower()
            ):
                persona_hits += 1
                entry["persona_match"] = True
        details.append(entry)

    out["devices_matched"] = matched
    out["persona_aligned"] = persona_hits
    out["match_rate"] = round(matched / len(sessions), 3) if sessions else 0
    out["persona_rate"] = round(persona_hits / len(sessions), 3) if sessions else 0
    out["details"] = details[:50]
    out["ok"] = matched > 0
    out["message"] = (
        f"{matched}/{len(sessions)} sessions seen in Clarion; "
        f"{persona_hits}/{len(sessions)} persona-aligned"
    )
    return out
