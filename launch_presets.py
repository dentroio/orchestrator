"""
Named launch campaigns for grouped Clarion grouping tests.

Group campaigns run one persona (Sales, Finance, …) at a time so ground truth
and Clarion can be compared per department. Population campaigns build endpoint
volume toward a target count.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from launch_profile import DEFAULT_USER_PERSONAS

# One Windows lab PC per department (override via runner primary_persona / windows_persona).
DEFAULT_WINDOWS_BY_PERSONA = {
    "Sales": "win-runner-1",
    "Finance": "win-runner-2",
    "Engineering": "win-runner-3",
    "IT": "win-runner-4",
}

# Pi runners whose persona_set is only user departments (wired user churn)
def _is_user_pi(runner: Dict[str, Any], user_personas: List[str]) -> bool:
    ps = runner.get("persona_set") or []
    if not ps:
        return False
    return all(p in user_personas for p in ps)


def _is_iot_pi(runner: Dict[str, Any], user_personas: List[str]) -> bool:
    ps = runner.get("persona_set") or []
    if not ps:
        return False
    return any(p not in user_personas for p in ps)


def _runners_for_persona(
    runners: List[Dict[str, Any]],
    persona: str,
    *,
    user_personas: List[str],
    include_pi: bool = True,
    include_windows: bool = True,
) -> List[str]:
    names: List[str] = []
    for runner in runners:
        name = runner.get("name") or ""
        rtype = str(runner.get("runner_type") or "pi").strip().lower()
        ps = set(runner.get("persona_set") or [])
        fallback = (runner.get("fallback_persona") or "").strip()
        if persona not in ps and fallback != persona:
            continue
        if rtype == "windows":
            if not include_windows:
                continue
            primary = (runner.get("primary_persona") or runner.get("windows_persona") or "").strip()
            mapped = DEFAULT_WINDOWS_BY_PERSONA.get(persona)
            if mapped and name != mapped:
                continue
            if primary and primary != persona:
                continue
            if fallback and fallback != persona:
                continue
            names.append(name)
        elif rtype == "pi" and include_pi:
            if persona in ps:
                names.append(name)
    return names


def _user_pi_runners(runners: List[Dict[str, Any]], user_personas: List[str]) -> List[str]:
    return [
        r["name"]
        for r in runners
        if str(r.get("runner_type") or "pi").lower() == "pi"
        and _is_user_pi(r, user_personas)
        and r.get("name")
    ]


def _iot_pi_runners(runners: List[Dict[str, Any]], user_personas: List[str]) -> List[str]:
    return [
        r["name"]
        for r in runners
        if str(r.get("runner_type") or "pi").lower() == "pi"
        and _is_iot_pi(r, user_personas)
        and r.get("name")
    ]


def _windows_runners(runners: List[Dict[str, Any]]) -> List[str]:
    return [
        r["name"]
        for r in runners
        if str(r.get("runner_type") or "pi").strip().lower() == "windows" and r.get("name")
    ]


def list_presets() -> List[Dict[str, Any]]:
    """Static preset catalog (resolved runner lists filled at apply time)."""
    quick_start = [
        {
            "id": "lab-start-all",
            "label": "Quick start: All runners (Pi + Windows)",
            "campaign_type": "quick",
            "description": (
                "Start orchestration with every configured runner: all Pi hosts (users + IoT) "
                "and all Windows PCs. Best for a full lab session after preflight audit passes."
            ),
            "verify_persona": None,
            "target_endpoints": 0,
        },
    ]
    groups = []
    for persona in DEFAULT_USER_PERSONAS:
        groups.append(
            {
                "id": f"group-{persona.lower()}",
                "label": f"Group: {persona}",
                "campaign_type": "group",
                "description": (
                    f"Run only {persona} users on matching Pi + Windows hosts. "
                    "Verify Clarion groups these endpoints under {persona} behavior."
                ).format(persona=persona),
                "verify_persona": persona,
                "target_endpoints": 0,
            }
        )
    return quick_start + groups + [
        {
            "id": "anchor-windows",
            "label": "Anchors: Windows only (4 PCs)",
            "campaign_type": "anchor",
            "description": "Four stable Windows hosts with logged-in users. Small population; good for baseline purity, not for ~50 endpoint scale.",
            "verify_persona": None,
            "target_endpoints": 4,
        },
        {
            "id": "population-users",
            "label": "Population: All users (Pi rotation)",
            "campaign_type": "population",
            "description": "All user Pi runners cycle 802.1X identities once. Builds endpoint count in Clarion for clustering scale.",
            "verify_persona": None,
            "target_endpoints": 50,
        },
        {
            "id": "population-iot",
            "label": "Population: IoT / MAB (wlan Pis)",
            "campaign_type": "population",
            "description": "IoT Pi runners (wlan lab) cycle MAB device personas. Separates IoT clusters from user traffic.",
            "verify_persona": None,
            "target_endpoints": 30,
        },
        {
            "id": "grouping-scale",
            "label": "Grouping scale: Users then full mix",
            "campaign_type": "population",
            "description": "All user Pis, cycle once, moderate concurrency. Aim for 50+ endpoints in Clarion before running bootstrap clustering.",
            "verify_persona": None,
            "target_endpoints": 50,
        },
    ]


def resolve_preset(
    preset_id: str,
    runners: List[Dict[str, Any]],
    *,
    user_personas: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a launch_profile dict from preset id and configured runners."""
    user_personas = list(user_personas or DEFAULT_USER_PERSONAS)
    preset_id = (preset_id or "").strip()
    meta = next((p for p in list_presets() if p["id"] == preset_id), None)

    profile: Dict[str, Any] = {
        "preset_id": preset_id,
        "campaign_type": (meta or {}).get("campaign_type", "custom"),
        "campaign_label": (meta or {}).get("label", preset_id),
        "verify_persona": (meta or {}).get("verify_persona"),
        "target_endpoints": int((meta or {}).get("target_endpoints") or 0),
        "auto_start_runners": True,
    }

    if preset_id == "lab-start-all":
        profile.update(
            {
                "identity_kinds": ["users", "iot"],
                "personas": [],
                "runner_names": [
                    r["name"]
                    for r in runners
                    if r.get("name")
                ],
                "include_windows": True,
                "max_concurrent": 6,
                "cycle_once": False,
            }
        )
        return profile

    if preset_id.startswith("group-"):
        slug = preset_id[6:]
        persona = slug.replace("-", " ").title()
        for p in user_personas:
            if p.lower().replace(" ", "-") == slug:
                persona = p
                break
        profile.update(
            {
                "identity_kinds": ["users"],
                "personas": [persona],
                "runner_names": _runners_for_persona(
                    runners, persona, user_personas=user_personas
                ),
                "include_windows": True,
                "max_concurrent": 2,
                "cycle_once": False,
            }
        )
        return profile

    if preset_id == "anchor-windows":
        profile.update(
            {
                "identity_kinds": ["users"],
                "personas": list(user_personas),
                "runner_names": _windows_runners(runners),
                "include_windows": True,
                "max_concurrent": 0,
                "cycle_once": False,
            }
        )
        return profile

    if preset_id == "population-users":
        profile.update(
            {
                "identity_kinds": ["users"],
                "personas": [],
                "runner_names": _user_pi_runners(runners, user_personas),
                "include_windows": False,
                "max_concurrent": 4,
                "cycle_once": True,
            }
        )
        return profile

    if preset_id == "population-iot":
        profile.update(
            {
                "identity_kinds": ["iot"],
                "personas": [],
                "runner_names": _iot_pi_runners(runners, user_personas),
                "include_windows": False,
                "max_concurrent": 2,
                "cycle_once": True,
            }
        )
        return profile

    if preset_id == "grouping-scale":
        profile.update(
            {
                "identity_kinds": ["users"],
                "personas": [],
                "runner_names": _user_pi_runners(runners, user_personas),
                "include_windows": False,
                "max_concurrent": 6,
                "cycle_once": True,
            }
        )
        return profile

    raise ValueError(f"Unknown launch preset: {preset_id}")
