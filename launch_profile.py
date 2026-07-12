"""
Session launch profile: filter identities and runners before orchestration starts.

Used by the dashboard Launch Session modal and POST /api/start.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Set

DEFAULT_USER_PERSONAS = ("Sales", "Finance", "Engineering", "IT")


def normalize_launch_profile(
    raw: Optional[Dict[str, Any]],
    *,
    user_personas: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return a normalized launch profile dict."""
    raw = raw if isinstance(raw, dict) else {}
    kinds = raw.get("identity_kinds")
    if kinds is None:
        # Default: both user (802.1x) and IoT (MAB) so all Pi persona sets can receive work.
        kinds = ["users", "iot"]
    elif isinstance(kinds, str):
        kinds = [kinds]
    kinds = [str(k).strip().lower() for k in kinds if k]
    if not kinds:
        kinds = ["users"]

    personas = raw.get("personas") or []
    if isinstance(personas, str):
        personas = [personas]
    personas = [str(p).strip() for p in personas if p]

    runner_names = raw.get("runner_names") or []
    if isinstance(runner_names, str):
        runner_names = [runner_names]
    runner_names = [str(n).strip() for n in runner_names if n]

    max_concurrent = raw.get("max_concurrent", raw.get("max_concurrent_sessions", 0))
    try:
        max_concurrent = max(0, int(max_concurrent))
    except (TypeError, ValueError):
        max_concurrent = 0

    cycle_once = bool(raw.get("cycle_once", False))
    return {
        "launch_id": (raw.get("launch_id") or "").strip() or str(uuid.uuid4()),
        "preset_id": (raw.get("preset_id") or "").strip(),
        "campaign_type": (raw.get("campaign_type") or "").strip(),
        "campaign_label": (raw.get("campaign_label") or "").strip(),
        "verify_persona": (raw.get("verify_persona") or "").strip() or None,
        "target_endpoints": max(0, int(raw.get("target_endpoints") or 0)),
        "identity_kinds": kinds,
        "personas": personas,
        "runner_names": runner_names,
        "max_concurrent": max_concurrent,
        "include_windows": bool(raw.get("include_windows", True)),
        "auto_start_runners": bool(raw.get("auto_start_runners", True)),
        "cycle_once": cycle_once,
        "user_personas": list(user_personas or DEFAULT_USER_PERSONAS),
    }


def identity_kind(identity: Dict[str, Any]) -> str:
    """Return ``user`` (dot1x) or ``iot`` (MAB)."""
    if (identity.get("auth") or "dot1x").strip().lower() == "mab":
        return "iot"
    return "user"


def identity_persona_keys(identity: Dict[str, Any]) -> Set[str]:
    keys: Set[str] = set()
    for field in ("persona", "department"):
        val = identity.get(field)
        if val:
            keys.add(str(val).strip())
    for g in identity.get("groups") or []:
        if g:
            keys.add(str(g).strip())
    return keys


def kind_allowed(kind: str, profile: Dict[str, Any]) -> bool:
    kinds = profile.get("identity_kinds") or ["users", "iot"]
    if "all" in kinds:
        return True
    if kind == "user":
        return "users" in kinds or "user" in kinds
    if kind == "iot":
        return "iot" in kinds or "iot_devices" in kinds
    return False


def identity_matches_launch_profile(
    identity: Dict[str, Any],
    profile: Dict[str, Any],
    *,
    persona_set: Optional[List[str]] = None,
) -> bool:
    """True if identity passes launch filters and (optionally) runner persona_set."""
    kind = identity_kind(identity)
    if not kind_allowed(kind, profile):
        return False

    keys = identity_persona_keys(identity)
    launch_personas = profile.get("personas") or []
    if launch_personas and not (keys & set(launch_personas)):
        return False

    if persona_set:
        if not (keys & set(persona_set)):
            return False
        user_personas = set(profile.get("user_personas") or DEFAULT_USER_PERSONAS)
        is_user_runner = all(p in user_personas for p in persona_set) if persona_set else True
        is_mab = kind == "iot"
        if is_user_runner and is_mab:
            return False
        if not is_user_runner and not is_mab:
            return False

    return True
