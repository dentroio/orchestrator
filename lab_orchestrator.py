#!/usr/bin/env python3
"""
Lab Orchestration Controller (client/server, no SSH).

Runners are agents that poll the orchestrator for assignments. Identities and
runner config are the master list on the server (DB); no JSON file copy.
Logs ground truth for validation.
"""

import argparse
import csv
import datetime
import json
import logging
import os
import random
import sys
import time
import uuid
from typing import List, Dict, Any, Optional

import launch_profile as lp
from launch_presets import DEFAULT_WINDOWS_BY_PERSONA
from runner_health_controller import default_health_control, default_runner_health_state

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/clarion_lab/orchestrator.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Default configuration (used when no DB/config provided)
DEFAULT_CONFIG = {
    "ground_truth_log": os.path.expanduser("~/clarion/lab/ground_truth/ground_truth_log.csv"),
    "policy_test_log": os.path.expanduser("~/clarion/lab/ground_truth/policy_test_results.jsonl"),
    "runners": [
        {"name": "pi-runner-1", "runner_type": "pi", "interface": "eth0", "management_interface": "wlan0", "persona_set": ["Sales"], "session_duration": 600},
    ],
}


class LabOrchestrator:
    """Orchestrator: master list of identities and runners; assigns work to agents via API (no SSH)."""

    # Clear stalled assignments after:
    #   session_duration_seconds + ASSIGNMENT_LEASE_GRACE_SECONDS
    # This prevents the lab from getting stuck when a runner never ACKs.
    ASSIGNMENT_LEASE_GRACE_SECONDS = 600

    # Default user personas (dot1x); overridable via config key "user_personas"
    USER_PERSONAS = ("Sales", "Finance", "Engineering", "IT")

    ROLE_BEHAVIORS: Dict[str, Dict[str, Any]] = {
        "sales": {
            "session_min": 900,
            "session_max": 2100,
            "traffic_min_sleep": 4,
            "traffic_max_sleep": 18,
            "target_min": 2,
            "target_max": 3,
            "primary_patterns": ("thehub", "sales", "crm", "customer"),
            "secondary_patterns": ("www.netlab.net", "portal", "finance"),
        },
        "finance": {
            "session_min": 1200,
            "session_max": 2400,
            "traffic_min_sleep": 8,
            "traffic_max_sleep": 30,
            "target_min": 1,
            "target_max": 3,
            "primary_patterns": ("finance", "erp", "payroll", "ledger"),
            "secondary_patterns": ("thehub", "www.netlab.net"),
        },
        "engineering": {
            "session_min": 1500,
            "session_max": 2700,
            "traffic_min_sleep": 3,
            "traffic_max_sleep": 20,
            "target_min": 2,
            "target_max": 4,
            "primary_patterns": ("engineering", "code", "api", "iotdev", "git"),
            "secondary_patterns": ("thehub", "www.netlab.net", "192.168.30.2"),
        },
        "it": {
            "session_min": 600,
            "session_max": 1800,
            "traffic_min_sleep": 2,
            "traffic_max_sleep": 14,
            "target_min": 2,
            "target_max": 5,
            "primary_patterns": ("mab", "code", "engineering", "admin", "cmdb"),
            "secondary_patterns": ("thehub", "finance", "www.netlab.net"),
        },
        "office": {
            "session_min": 900,
            "session_max": 2100,
            "traffic_min_sleep": 5,
            "traffic_max_sleep": 24,
            "target_min": 2,
            "target_max": 3,
            "primary_patterns": ("thehub", "portal", "www.netlab.net"),
            "secondary_patterns": ("finance", "engineering"),
        },
        "iot": {
            "session_min": 1800,
            "session_max": 7200,
            "traffic_min_sleep": 30,
            "traffic_max_sleep": 300,
            "target_min": 1,
            "target_max": 2,
            "primary_patterns": ("telemetry", "camera", "printer", "badge", "lock", "hvac", "sensor", "robot", "medical"),
            "secondary_patterns": (),
        },
    }

    OFF_HOURS_FACTOR: Dict[str, float] = {
        "sales": 0.35,
        "finance": 0.30,
        "engineering": 0.70,
        "it": 0.85,
        "office": 0.50,
        "iot": 1.0,
    }

    @classmethod
    def default_orchestration_settings(cls) -> Dict[str, Any]:
        return {
            "role_behaviors": json.loads(json.dumps(cls.ROLE_BEHAVIORS)),
            "off_hours_factor": dict(cls.OFF_HOURS_FACTOR),
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

    def __init__(self, config: Dict):
        self.config = {}
        self.ground_truth_log = DEFAULT_CONFIG["ground_truth_log"]
        self.policy_test_log = DEFAULT_CONFIG["policy_test_log"]
        self.identities = []
        self.runners = []
        self.running = False
        self.paused = False
        self.runner_states = {}
        self.active_identities = set()
        self.launch_profile = None
        self.launch_id = None
        self._launch_seen_users: set = set()
        self._launch_seen_devices: set = set()
        defaults = self.default_orchestration_settings()
        self.role_behaviors = defaults["role_behaviors"]
        self.off_hours_factor = defaults["off_hours_factor"]
        self.apply_config(config)

    def apply_config(self, config: Dict[str, Any]) -> None:
        self.config = dict(config or {})
        self.ground_truth_log = os.path.expanduser(
            self.config.get("ground_truth_log", DEFAULT_CONFIG["ground_truth_log"])
        )
        self.policy_test_log = os.path.expanduser(
            self.config.get("policy_test_log", DEFAULT_CONFIG["policy_test_log"])
        )
        self.identities = self.config.get("identities") or []
        raw_runners = self.config.get("runners") or DEFAULT_CONFIG["runners"]
        self.runners = [self._normalize_windows_runner(dict(r)) for r in raw_runners]
        os.makedirs(os.path.dirname(self.ground_truth_log), exist_ok=True)

        settings = self.default_orchestration_settings()
        configured = self.config.get("orchestration_settings") or {}
        settings["role_behaviors"].update(configured.get("role_behaviors") or {})
        settings["off_hours_factor"].update(configured.get("off_hours_factor") or {})
        settings["policy_test_settings"].update(configured.get("policy_test_settings") or {})
        self.role_behaviors = settings["role_behaviors"]
        self.off_hours_factor = settings["off_hours_factor"]
        self.policy_test_settings = settings.get("policy_test_settings") or {}

        previous_states = self.runner_states or {}
        self.runner_states = {}
        for runner in self.runners:
            name = runner["name"]
            existing = previous_states.get(name, {})
            self.runner_states[name] = {
                "current_identity": existing.get("current_identity"),
                "next_switch": existing.get("next_switch", 0),
                "status": existing.get("status", "stopped"),
                "pending_assignment": existing.get("pending_assignment"),
                "identity_history": existing.get("identity_history", {}),
                "used_identity_ids": existing.get("used_identity_ids", set()),
                "telemetry": existing.get("telemetry"),
                "last_contact": existing.get("last_contact"),
                "recent_logs": existing.get("recent_logs", []),
                "agent_status": existing.get("agent_status"),
                "windows_context": existing.get("windows_context"),
                "current_plan": existing.get("current_plan"),
                "health": existing.get("health") or default_runner_health_state(),
                "health_control": existing.get("health_control") or default_health_control(),
            }

    def get_user_personas(self) -> List[str]:
        return list(self.config.get("user_personas") or self.USER_PERSONAS)

    def _reset_launch_progress(self) -> None:
        """Clear per-launch unique user/device counts (Pi identity sessions only)."""
        self._launch_seen_users = set()
        self._launch_seen_devices = set()

    def _launch_identity_key(self, identity: Dict[str, Any], kind: str) -> str:
        mac = (identity.get("mac") or "").strip().lower().replace(":", "").replace("-", "")
        if mac and mac != "unknown":
            return f"mac:{mac}"
        if kind == "user":
            username = (identity.get("username") or "").strip().lower()
            if username:
                return f"user:{username}"
        device_name = (identity.get("device_name") or "").strip().lower()
        if device_name:
            return f"device:{device_name}"
        return ""

    def _record_launch_progress(self, identity: Dict[str, Any], runner: Dict[str, Any]) -> None:
        """Track unique users and IoT devices that have started a Pi session this launch."""
        if str(runner.get("runner_type") or "pi").strip().lower() == "windows":
            return
        kind = lp.identity_kind(identity)
        key = self._launch_identity_key(identity, kind)
        if not key:
            return
        if kind == "user":
            self._launch_seen_users.add(key)
        elif kind == "iot":
            self._launch_seen_devices.add(key)

    def get_launch_progress(self) -> Dict[str, int]:
        return {
            "user_count": len(self._launch_seen_users),
            "device_count": len(self._launch_seen_devices),
        }

    def set_launch_profile(self, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Store normalized launch profile for the current orchestration run."""
        profile = lp.normalize_launch_profile(raw, user_personas=self.get_user_personas())
        self.launch_profile = profile
        self.launch_id = profile.get("launch_id") or str(uuid.uuid4())
        self.launch_profile["launch_id"] = self.launch_id
        self._reset_launch_progress()
        return self.launch_profile

    def clear_launch_profile(self) -> None:
        self.launch_profile = None
        self.launch_id = None
        self._reset_launch_progress()

    def _runner_in_launch(self, runner: Dict[str, Any]) -> bool:
        """True if this runner is included in the active launch profile."""
        profile = self.launch_profile
        if not profile:
            return True
        names = profile.get("runner_names") or []
        name = runner.get("name")
        if names and name not in names:
            return False
        rtype = str(runner.get("runner_type") or "pi").strip().lower()
        if (
            rtype == "windows"
            and not profile.get("include_windows", True)
            and not (names and name in names)
        ):
            return False
        return True

    def get_identities_for_persona_set(
        self,
        persona_set: List[str],
        launch_profile: Optional[Dict[str, Any]] = None,
    ) -> List[Dict]:
        """Filter identities by persona/department/groups, launch profile, and active set."""
        profile = launch_profile or self.launch_profile
        user_personas = self.get_user_personas()
        if profile:
            profile = lp.normalize_launch_profile(profile, user_personas=user_personas)

        filtered = []
        for ident in self.identities:
            ident_id = ident.get("username") or ident.get("device_name")
            if ident_id in self.active_identities:
                continue

            if profile:
                if not lp.identity_matches_launch_profile(
                    ident, profile, persona_set=persona_set
                ):
                    continue
            else:
                is_user_runner = all(p in user_personas for p in persona_set) if persona_set else True
                is_mab = (ident.get("auth") or "dot1x").lower() == "mab"
                if is_user_runner and is_mab:
                    continue
                if not is_user_runner and not is_mab:
                    continue
                keys = lp.identity_persona_keys(ident)
                if persona_set and not (keys & set(persona_set)):
                    continue

            filtered.append(ident)
        return filtered

    def preview_launch(self, raw_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Counts for dashboard: matching identities and per-runner pools."""
        profile = lp.normalize_launch_profile(
            raw_profile, user_personas=self.get_user_personas()
        )
        all_matching = []
        for ident in self.identities:
            ident_id = ident.get("username") or ident.get("device_name")
            if ident_id in self.active_identities:
                continue
            if lp.identity_matches_launch_profile(ident, profile):
                all_matching.append(ident)

        runners_out = []
        for runner in self.runners or []:
            name = runner.get("name")
            rtype = str(runner.get("runner_type") or "pi").strip().lower()
            selected = True
            names = profile.get("runner_names") or []
            if names and name not in names:
                selected = False
            if (
                rtype == "windows"
                and not profile.get("include_windows", True)
                and not (names and name in names)
            ):
                selected = False
            pool = (
                self.get_identities_for_persona_set(
                    runner.get("persona_set") or [], profile
                )
                if selected
                else []
            )
            runners_out.append(
                {
                    "name": name,
                    "runner_type": rtype,
                    "persona_set": runner.get("persona_set") or [],
                    "selected": selected,
                    "matching_identities": len(pool),
                    "windows_note": (
                        "Uses logged-in AD user on this PC (no identity rotation)"
                        if rtype == "windows"
                        else None
                    ),
                }
            )

        by_kind = {"user": 0, "iot": 0}
        for ident in all_matching:
            kind = lp.identity_kind(ident)
            if kind in by_kind:
                by_kind[kind] += 1

        return {
            "launch_id": profile.get("launch_id"),
            "profile": profile,
            "total_matching_identities": len(all_matching),
            "by_kind": by_kind,
            "runners": runners_out,
            "max_concurrent": profile.get("max_concurrent", 0),
        }

    def prepare_launch_runners(self, profile: Dict[str, Any]) -> List[str]:
        """Start selected runners; stop runners excluded from this launch."""
        started = []
        stopped = []
        names = profile.get("runner_names") or []
        for runner in self.runners or []:
            name = runner.get("name")
            if not name:
                continue
            rtype = str(runner.get("runner_type") or "pi").strip().lower()
            in_list = (not names) or (name in names)
            # Explicit runner_names wins over include_windows (UI may leave the box unchecked after a Pi-only preset).
            if (
                rtype == "windows"
                and not profile.get("include_windows", True)
                and not (names and name in names)
            ):
                in_list = False
            if in_list and profile.get("auto_start_runners", True):
                if self.start_runner(name):
                    started.append(name)
            elif names and name not in names:
                if self.stop_runner(name):
                    stopped.append(name)
        logger.info(
            "Launch runner control: started=%s stopped=%s (profile runners=%s)",
            started,
            stopped,
            names or "all",
        )
        return started

    def invalidate_windows_plan_cache(self, runner_names: Optional[List[str]] = None) -> None:
        """Drop cached Windows host plans so the next poll sees current orchestration state."""
        names = set(runner_names or [])
        for runner in self.runners or []:
            name = runner.get("name")
            if not name:
                continue
            if names and name not in names:
                continue
            if str(runner.get("runner_type") or "pi").strip().lower() != "windows":
                continue
            state = self.runner_states.get(name)
            if isinstance(state, dict):
                state.pop("windows_plan", None)

    def _identity_id(self, identity: Dict[str, Any]) -> str:
        return str(identity.get("username") or identity.get("device_name") or "").strip()

    def _normalize_username(self, value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        if "\\" in text:
            text = text.split("\\")[-1]
        if "@" in text:
            text = text.split("@", 1)[0]
        return text

    def _find_identity_for_windows_user(self, username: str) -> Dict[str, Any]:
        wanted = self._normalize_username(username)
        if not wanted:
            return {}
        for identity in self.identities:
            candidates = [
                identity.get("username"),
                identity.get("display_name"),
                identity.get("device_name"),
            ]
            if any(self._normalize_username(candidate) == wanted for candidate in candidates):
                return identity
        return {}

    def _persona_from_identity(self, identity: Dict[str, Any], fallback: str = "") -> str:
        """Persona from identity record (logged-in AD user department/persona)."""
        return (
            str(identity.get("persona") or "").strip()
            or str(identity.get("department") or "").strip()
            or str(fallback or "").strip()
            or "Sales"
        )

    def _intended_windows_persona(self, runner: Dict[str, Any]) -> str:
        """Stable lab persona for this Windows host (one department per PC)."""
        primary = str(
            runner.get("primary_persona") or runner.get("windows_persona") or ""
        ).strip()
        if primary:
            return primary
        name = str(runner.get("name") or "").strip()
        for persona, runner_id in DEFAULT_WINDOWS_BY_PERSONA.items():
            if runner_id == name:
                return persona
        return str(runner.get("fallback_persona") or "").strip() or "Sales"

    def _normalize_windows_runner(self, runner: Dict[str, Any]) -> Dict[str, Any]:
        if str(runner.get("runner_type") or "pi").strip().lower() != "windows":
            return runner
        intended = self._intended_windows_persona(runner)
        runner["primary_persona"] = intended
        runner["fallback_persona"] = str(runner.get("fallback_persona") or intended).strip() or intended
        ps = runner.get("persona_set") or []
        if not ps or len(ps) > 1 or ps[0] != intended:
            runner["persona_set"] = [intended]
        return runner

    def _url_looks_external(self, url: str) -> bool:
        low = str(url or "").lower()
        if "netlab.net" in low:
            return False
        if "192.168." in low or "10." in low or "172.16." in low or "172.17." in low:
            return False
        return True

    def _resolve_windows_lookup_username(
        self, username: str, interactive_username: str = ""
    ) -> str:
        """Prefer interactive AD user over machine principal for identity lookup."""
        primary = str(username or "").strip()
        interactive = str(interactive_username or "").strip()
        if interactive and (not primary or primary.endswith("$")):
            return interactive
        if primary and not self._find_identity_for_windows_user(primary) and interactive:
            return interactive
        return primary or interactive

    def _identity_groups(self, identity: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        for value in [identity.get("persona"), identity.get("department"), *(identity.get("groups") or [])]:
            text = str(value or "").strip()
            if text and text not in values:
                values.append(text)
        return values

    def _behavior_key(self, identity: Dict[str, Any]) -> str:
        if (identity.get("auth") or "dot1x").strip().lower() == "mab":
            return "iot"
        department = str(identity.get("department") or "").strip().lower()
        if department in ("sales", "finance", "engineering", "it"):
            return department
        if department in ("iot", "operations", "manufacturing"):
            return "iot"
        return "office"

    def _business_hour_factor(self, behavior_key: str, when_ts: float) -> float:
        hour = datetime.datetime.fromtimestamp(when_ts).hour
        if 7 <= hour < 18:
            return 1.0
        return self.off_hours_factor.get(behavior_key, 0.6)

    def _build_services_dict(self) -> Dict[str, Dict[str, Any]]:
        return {s["id"]: s for s in self.config.get("services", []) if s.get("id")}

    def _service_to_url(self, service: Dict[str, Any]) -> str:
        url = f"{service['protocol']}://{service['target']}:{service['port']}"
        if service.get("path"):
            url += service["path"]
        return url

    def _resolve_identity_urls(self, identity: Dict[str, Any], services_dict: Dict[str, Dict[str, Any]]) -> List[str]:
        direct_urls = identity.get("urls") or []
        if isinstance(direct_urls, str):
            direct_urls = [direct_urls]
        urls: List[str] = [str(u).strip() for u in direct_urls if str(u).strip()]
        if identity.get("customer_url"):
            urls.append(str(identity["customer_url"]).strip())

        seen = set(urls)
        for group in self._identity_groups(identity):
            for srv_id in self.config.get("connectivity_policies", {}).get(group, []):
                service = services_dict.get(srv_id)
                if not service:
                    continue
                url = self._service_to_url(service)
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
        return urls

    def _pick_session_duration(self, runner: Dict[str, Any], behavior_key: str) -> int:
        profile = self.role_behaviors.get(behavior_key, self.role_behaviors["office"])
        configured = int(runner.get("session_duration", 900) or 900)
        floor = int(profile["session_min"])
        ceiling = int(profile["session_max"])
        low = max(floor, int(configured * 0.7))
        high = min(ceiling, int(configured * 1.3))
        if high < low:
            high = low
        return random.randint(low, high)

    def _select_session_urls(self, identity: Dict[str, Any], urls: List[str], behavior_key: str) -> List[str]:
        if not urls:
            return []
        if (identity.get("auth") or "dot1x").strip().lower() == "mab":
            return urls

        profile = self.role_behaviors.get(behavior_key, self.role_behaviors["office"])
        lower_urls = [(u, u.lower()) for u in urls]

        primary = [
            url for url, lowered in lower_urls
            if any(pattern in lowered for pattern in profile.get("primary_patterns", ()))
        ]
        secondary = [
            url for url, lowered in lower_urls
            if url not in primary and any(pattern in lowered for pattern in profile.get("secondary_patterns", ()))
        ]
        remaining = [url for url in urls if url not in primary and url not in secondary]

        chosen: List[str] = []
        target_count = min(len(urls), random.randint(profile["target_min"], profile["target_max"]))
        for pool in (primary, secondary, remaining):
            for url in pool:
                if url not in chosen:
                    chosen.append(url)
                if len(chosen) >= target_count:
                    return chosen
        return chosen or urls[:target_count]

    def _select_windows_session_urls(
        self, urls: List[str], behavior_key: str
    ) -> List[str]:
        """Windows hosts: include internet targets from connectivity policy, not only netlab."""
        if not urls:
            return []
        profile = self.role_behaviors.get(behavior_key, self.role_behaviors["office"])
        external = [u for u in urls if self._url_looks_external(u)]
        internal = [u for u in urls if u not in external]
        target_count = min(len(urls), random.randint(profile["target_min"], profile["target_max"]))
        chosen: List[str] = []
        if external:
            chosen.append(random.choice(external))
        for pool in (internal, external):
            random.shuffle(pool)
            for url in pool:
                if url in chosen:
                    continue
                chosen.append(url)
                if len(chosen) >= target_count:
                    return chosen
        return chosen or urls[:target_count]

    def _enabled_services_for_identity(self, identity: Dict[str, Any]) -> List[Dict[str, Any]]:
        services_dict = self._build_services_dict()
        service_ids: List[str] = []
        for group in self._identity_groups(identity):
            for srv_id in self.config.get("connectivity_policies", {}).get(group, []):
                if srv_id not in service_ids:
                    service_ids.append(srv_id)
        services: List[Dict[str, Any]] = []
        for srv_id in service_ids:
            service = services_dict.get(srv_id)
            if service:
                services.append(service)
        return services

    def _normalize_policy_test_matrix(self, settings: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        """Persona -> service_id -> 'allow' | 'deny'. Merges legacy deny_catalog into deny cells."""
        matrix: Dict[str, Dict[str, str]] = {}
        raw = settings.get("test_matrix") or {}
        if isinstance(raw, dict):
            for persona_key, row in raw.items():
                persona = str(persona_key or "").strip()
                if not persona or not isinstance(row, dict):
                    continue
                normalized_row: Dict[str, str] = {}
                for srv_id, action in row.items():
                    svc_key = str(srv_id or "").strip()
                    act = str(action or "").strip().lower()
                    if svc_key and act in ("allow", "deny"):
                        normalized_row[svc_key] = act
                if normalized_row:
                    matrix[persona] = normalized_row

        catalog = settings.get("deny_catalog") or {}
        if isinstance(catalog, dict):
            for persona_key, values in catalog.items():
                persona = str(persona_key or "").strip()
                if not persona:
                    continue
                if isinstance(values, str):
                    raw_values = [v.strip() for v in values.split(",")]
                elif isinstance(values, list):
                    raw_values = [str(v).strip() for v in values]
                else:
                    raw_values = []
                row = matrix.setdefault(persona, {})
                for srv_id in raw_values:
                    if srv_id and srv_id not in row:
                        row[srv_id] = "deny"
        return matrix

    def _policy_test_matrix_actions(
        self, identity: Dict[str, Any], settings: Dict[str, Any]
    ) -> Dict[str, List[str]]:
        """Resolve explicit allow/deny test service IDs from the TrustSec-style matrix."""
        matrix = self._normalize_policy_test_matrix(settings)
        allow_ids: List[str] = []
        deny_ids: List[str] = []
        for group in self._identity_groups(identity):
            group_name = str(group or "").strip()
            if not group_name:
                continue
            row = matrix.get(group_name) or matrix.get(group_name.lower()) or {}
            for srv_id, action in row.items():
                if action == "allow" and srv_id not in allow_ids:
                    allow_ids.append(srv_id)
                elif action == "deny" and srv_id not in deny_ids:
                    deny_ids.append(srv_id)
        return {"allow": allow_ids, "deny": deny_ids}

    def _build_policy_test_plan(
        self,
        identity: Dict[str, Any],
        session_urls: List[str],
        session_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        settings = self.policy_test_settings or {}
        if not bool(settings.get("enabled", True)):
            return {"enabled": False, "cases": []}

        all_services = [s for s in self.config.get("services", []) if isinstance(s, dict) and s.get("id")]
        all_services_dict = {str(s["id"]): s for s in all_services}
        allowed_services = self._enabled_services_for_identity(identity)
        allowed_ids = {str(s["id"]) for s in allowed_services if s.get("id")}
        matrix_actions = self._policy_test_matrix_actions(identity, settings)
        matrix_allow_ids = matrix_actions.get("allow") or []
        matrix_deny_ids = matrix_actions.get("deny") or []
        has_matrix = bool(matrix_allow_ids or matrix_deny_ids)

        fallback_to_complement = bool(settings.get("fallback_to_complement", False))
        deny_candidates: List[Dict[str, Any]] = []
        allow_candidates: List[Dict[str, Any]] = []
        deny_source = "matrix" if matrix_deny_ids else "none"
        allow_source = "matrix" if matrix_allow_ids else "session"

        for srv_id in matrix_allow_ids:
            service = all_services_dict.get(str(srv_id))
            if service:
                allow_candidates.append(service)
        for srv_id in matrix_deny_ids:
            service = all_services_dict.get(str(srv_id))
            if service:
                deny_candidates.append(service)

        if not deny_candidates and fallback_to_complement and not has_matrix:
            deny_candidates = [s for s in all_services if str(s.get("id")) not in allowed_ids]
            deny_source = "complement"
            random.shuffle(deny_candidates)

        allow_limit = max(0, int(settings.get("allow_cases_per_session", 3) or 0))
        deny_limit = max(0, int(settings.get("deny_cases_per_session", 2) or 0))
        max_attempts = max(1, int(settings.get("max_attempts_per_case", 1) or 1))
        timeout_ms = max(1000, int(settings.get("request_timeout_ms", 8000) or 8000))

        cases: List[Dict[str, Any]] = []
        if allow_candidates:
            random.shuffle(allow_candidates)
            for idx, service in enumerate(allow_candidates[:allow_limit]):
                target_url = self._service_to_url(service)
                if not target_url:
                    continue
                cases.append({
                    "case_id": f"allow-{service.get('id') or idx+1}",
                    "target_url": target_url,
                    "expected_action": "allow",
                    "service_id": str(service.get("id") or ""),
                    "method": session_plan.get("traffic_method") or "GET",
                    "attempts": max_attempts,
                    "timeout_ms": timeout_ms,
                })
        else:
            available_allow_urls = [u for u in session_urls if isinstance(u, str) and u.strip()]
            random.shuffle(available_allow_urls)
            for idx, target_url in enumerate(available_allow_urls[:allow_limit]):
                cases.append({
                    "case_id": f"allow-{idx+1}",
                    "target_url": target_url,
                    "expected_action": "allow",
                    "service_id": "",
                    "method": session_plan.get("traffic_method") or "GET",
                    "attempts": max_attempts,
                    "timeout_ms": timeout_ms,
                })

        for service in deny_candidates[:deny_limit]:
            service_id = str(service.get("id") or "")
            target_url = self._service_to_url(service)
            if not target_url:
                continue
            cases.append({
                "case_id": f"deny-{service_id or len(cases)+1}",
                "target_url": target_url,
                "expected_action": "deny",
                "service_id": service_id,
                "method": session_plan.get("traffic_method") or "GET",
                "attempts": max_attempts,
                "timeout_ms": timeout_ms,
            })

        return {
            "enabled": True,
            "identity": self._identity_id(identity),
            "allow_source": allow_source,
            "deny_source": deny_source,
            "allow_candidate_services": matrix_allow_ids or sorted(list(allowed_ids)),
            "deny_candidate_services": matrix_deny_ids or [str(s.get("id") or "") for s in deny_candidates],
            "matrix_allow_services": matrix_allow_ids,
            "matrix_deny_services": matrix_deny_ids,
            "cases": cases,
        }

    def _build_session_plan(self, identity: Dict[str, Any], runner: Dict[str, Any], services_dict: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        behavior_key = self._behavior_key(identity)
        profile = self.role_behaviors.get(behavior_key, self.role_behaviors["office"])
        urls = self._resolve_identity_urls(identity, services_dict)
        is_windows = str(runner.get("runner_type") or "pi").strip().lower() == "windows"
        if is_windows:
            session_urls = self._select_windows_session_urls(urls, behavior_key)
        else:
            session_urls = self._select_session_urls(identity, urls, behavior_key)
        traffic_method = "POST" if (identity.get("auth") or "").strip().lower() == "mab" else "GET"
        if behavior_key == "iot":
            traffic_method = "POST"
        plan = {
            "behavior_key": behavior_key,
            "session_duration": self._pick_session_duration(runner, behavior_key),
            "access_urls": session_urls,
            "traffic_method": traffic_method,
            "traffic_min_sleep": int(identity.get("traffic_min_sleep") or profile["traffic_min_sleep"]),
            "traffic_max_sleep": int(identity.get("traffic_max_sleep") or profile["traffic_max_sleep"]),
        }
        plan["policy_test_plan"] = self._build_policy_test_plan(identity, session_urls, plan)
        return plan

    def _score_identity(self, identity: Dict[str, Any], runner: Dict[str, Any], state: Dict[str, Any], when_ts: float) -> float:
        ident_id = self._identity_id(identity)
        history = state.setdefault("identity_history", {})
        meta = history.get(ident_id, {})
        behavior_key = self._behavior_key(identity)
        hour_factor = self._business_hour_factor(behavior_key, when_ts)
        sessions = int(meta.get("sessions", 0) or 0)
        last_used = float(meta.get("last_used", 0) or 0)
        age_bonus = min(1800.0, max(0.0, when_ts - last_used)) / 1800.0 if last_used else 1.0
        cooldown_penalty = 3.0 if last_used and (when_ts - last_used) < 3600 else 0.0
        return (10.0 * hour_factor) + (3.0 * age_bonus) - (1.5 * sessions) - cooldown_penalty + random.random()

    def _choose_next_identity(self, available_identities: List[Dict[str, Any]], runner: Dict[str, Any], state: Dict[str, Any], when_ts: float, cycle_once: bool, previous_identity_id: str = None) -> Dict[str, Any]:
        if len(available_identities) == 1:
            return available_identities[0]
        scored = sorted(
            available_identities,
            key=lambda ident: self._score_identity(ident, runner, state, when_ts),
            reverse=True,
        )
        if not cycle_once and previous_identity_id:
            non_prev = [ident for ident in scored if self._identity_id(ident) != previous_identity_id]
            if non_prev:
                scored = non_prev
        top_band = scored[: min(3, len(scored))]
        return random.choice(top_band)

    def _record_identity_use(self, state: Dict[str, Any], identity: Dict[str, Any], when_ts: float) -> None:
        ident_id = self._identity_id(identity)
        if not ident_id:
            return
        history = state.setdefault("identity_history", {})
        meta = history.get(ident_id, {})
        history[ident_id] = {
            "sessions": int(meta.get("sessions", 0) or 0) + 1,
            "last_used": when_ts,
        }

    @staticmethod
    def _normalize_runner_key(value: Any) -> str:
        return str(value or "").strip().lower()

    def resolve_runner(
        self,
        runner_id: str = "",
        remote_ip: str = "",
        hostname: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Resolve a configured runner by orchestrator name, hostname alias, or source IP."""
        rid = self._normalize_runner_key(runner_id)
        host = self._normalize_runner_key(hostname)
        if rid:
            for r in self.runners:
                if self._normalize_runner_key(r.get("name")) == rid:
                    return r
                if self._normalize_runner_key(r.get("hostname")) == rid:
                    return r
        if host:
            for r in self.runners:
                if self._normalize_runner_key(r.get("hostname")) == host:
                    return r
                if self._normalize_runner_key(r.get("name")) == host:
                    return r
        if remote_ip:
            for r in self.runners:
                if r.get("host") == remote_ip:
                    return r
                ips = r.get("telemetry_ips") or []
                if isinstance(ips, list) and remote_ip in ips:
                    return r
        return None

    def update_runner_telemetry(self, runner_id: str, remote_ip: str, data: Dict) -> bool:
        """Update runner state with telemetry data."""
        target_runner = self.resolve_runner(
            runner_id=runner_id,
            remote_ip=remote_ip,
            hostname=str(data.get("hostname") or ""),
        )

        if target_runner:
            name = target_runner["name"]
            if name in self.runner_states:
                # Merge telemetry into state
                prev_telemetry = self.runner_states[name].get("telemetry") or {}
                incoming_ver = str(data.get("code_version") or "").strip()
                prev_ver = str(prev_telemetry.get("code_version") or "").strip()
                if incoming_ver in ("", "unknown", "pi-agent/unknown") and prev_ver and prev_ver not in ("unknown", "pi-agent/unknown"):
                    data = dict(data)
                    data["code_version"] = prev_ver
                elif incoming_ver.startswith("pi-agent/") and prev_ver.startswith("pi-agent/"):
                    pass
                self.runner_states[name]["telemetry"] = data
                self.runner_states[name]["last_contact"] = time.time()
                # Update status if provided
                if "status" in data:
                    reported_agent = str(data.get("status") or "").strip()
                    current_status = str(self.runner_states[name].get("status") or "").strip().lower()
                    prev_agent = str(self.runner_states[name].get("agent_status") or "").strip()
                    session_markers = (
                        "generating_traffic", "bouncing", "waiting_dhcp", "connected",
                        "active", "discovery", "restarting", "cooldown", "switching",
                    )
                    prev_lower = prev_agent.lower()
                    new_lower = reported_agent.lower()
                    prev_is_session = any(m in prev_lower for m in session_markers)
                    new_is_idle = new_lower in ("idle", "")
                    new_is_session = any(m in new_lower for m in session_markers)
                    if current_status == "stopped" and new_is_session:
                        # Orchestrator stopped this runner; ignore stale session subprocess telemetry.
                        pass
                    elif prev_is_session and new_is_idle:
                        # Stale idle heartbeat while session subprocess still owns the runner row.
                        pass
                    else:
                        self.runner_states[name]["agent_status"] = reported_agent
                    # Windows runners don't use pending assignments, so their live state
                    # should come from agent telemetry status to avoid stale "stopped/idle"
                    # after orchestration start/stop toggles.
                    runner_type = str(target_runner.get("runner_type") or "pi").strip().lower()
                    if runner_type in ("windows", "pi"):
                        reported = str(data.get("status") or "").strip()
                        current_status = str(self.runner_states[name].get("status") or "").strip().lower()
                        # Respect explicit stop commands from the dashboard: keep the row
                        # in "stopped" while still accepting telemetry heartbeats.
                        if current_status == "stopped":
                            pass
                        elif (
                            runner_type == "windows"
                            and self.running
                            and current_status == "windows-pending-traffic"
                            and reported.lower().startswith("discovery")
                        ):
                            # Agent may still hold a stale discovery plan for up to one refresh
                            # cycle after orchestration start; keep pending on the dashboard.
                            pass
                        elif reported:
                            self.runner_states[name]["status"] = reported
                # Append streamed log lines for troubleshooting (keep last 150)
                log_lines = data.get("log_lines")
                if isinstance(log_lines, list) and log_lines:
                    state = self.runner_states[name]
                    if "recent_logs" not in state:
                        state["recent_logs"] = []
                    state["recent_logs"].extend(log_lines)
                    state["recent_logs"] = state["recent_logs"][-150:]
                return True
        return False

    def update_windows_host_context(
        self,
        runner_id: str,
        remote_ip: str,
        hostname: str,
        username: str,
        interactive_username: str = "",
        principal_type: str = "",
        user_logged_in: bool = False,
        fqdn: str = "",
        domain_joined: bool = False,
        machine_auth_capable: bool = False,
    ) -> bool:
        """Persist current Windows host context in runner state for dashboard visibility."""
        if runner_id not in self.runner_states:
            return False
        state = self.runner_states[runner_id]
        state["last_contact"] = time.time()
        state["windows_context"] = {
            "hostname": hostname,
            "username": username,
            "interactive_username": interactive_username,
            "principal_type": principal_type,
            "user_logged_in": bool(user_logged_in),
            "fqdn": fqdn,
            "remote_ip": remote_ip,
            "domain_joined": bool(domain_joined),
            "machine_auth_capable": bool(machine_auth_capable),
        }
        return True

    def get_windows_host_plan(
        self,
        runner_id: str,
        hostname: str,
        username: str,
        fallback_persona: str = "Sales",
        interactive_username: str = "",
    ) -> Dict[str, Any]:
        """Build a stable traffic plan for a real Windows host.

        Unlike Pi runners, Windows hosts do not identity-switch. The plan is derived
        from the logged-in user and the runner's configured scope.
        """
        if runner_id not in self.runner_states:
            return {}

        runner = next((r for r in self.runners if r.get("name") == runner_id), None)
        if not runner:
            return {}

        state = self.runner_states[runner_id]
        is_runner_stopped = str(state.get("status") or "").strip().lower() == "stopped"
        is_orchestrator_stopped = not self.running
        control_reason = ""
        # Global stop marks every runner "stopped"; surface that first in the UI.
        if is_orchestrator_stopped:
            control_reason = "orchestrator_stopped"
        elif is_runner_stopped:
            control_reason = "runner_stopped"
        control_message = ""
        if control_reason == "orchestrator_stopped":
            control_message = (
                "Orchestration is STOPPED — use Start or Launch Session on the dashboard to allow traffic"
            )
        elif control_reason == "runner_stopped":
            control_message = (
                "This runner is stopped — click Start on this row, or Launch with this runner selected"
            )
        configured_fallback = str(runner.get("fallback_persona") or "").strip()
        # Windows runners: default to traffic (HTTP) when windows_mode is omitted so lab hosts generate
        # connections without extra dashboard edits. Use windows_mode "discovery" for telemetry-only.
        runner_type = str(runner.get("runner_type") or "pi").strip().lower()
        wm_raw = runner.get("windows_mode")
        if runner_type == "windows":
            if wm_raw is None or (isinstance(wm_raw, str) and not str(wm_raw).strip()):
                execution_mode = "traffic"
            else:
                execution_mode = str(wm_raw).strip().lower() or "traffic"
        else:
            execution_mode = str(wm_raw or "discovery").strip().lower() or "discovery"
        # Orchestrator-level stop or per-runner stop forces discovery mode on Windows
        # hosts so traffic generation can be started/stopped from the dashboard.
        if is_orchestrator_stopped or is_runner_stopped:
            execution_mode = "discovery"
        orchestrator_control = {
            "paused": bool(control_reason),
            "reason": control_reason,
            "message": control_message,
        }
        hc = state.get("health_control") or {}
        if hc.get("restart_requested"):
            orchestrator_control["restart_requested"] = True
            orchestrator_control["restart_reason"] = hc.get("restart_reason") or "health_controller"
        effective_fallback = str(fallback_persona or "").strip() or configured_fallback or "Sales"
        normalized_username = self._normalize_username(username)
        normalized_hostname = str(hostname or "").strip().lower()
        cached_plan = state.get("windows_plan")
        host_persona = self._intended_windows_persona(runner)
        if isinstance(cached_plan, dict):
            cached_user = self._normalize_username(cached_plan.get("username"))
            cached_host = str(cached_plan.get("hostname") or "").strip().lower()
            cached_mode = str(cached_plan.get("execution_mode") or "discovery").strip().lower() or "discovery"
            cached_host_persona = str(cached_plan.get("host_persona") or "").strip()
            if cached_mode == execution_mode and cached_user == normalized_username and (
                not normalized_hostname or not cached_host or cached_host == normalized_hostname
            ) and cached_host_persona == host_persona:
                cached_plan["orchestrator_control"] = orchestrator_control
                state["current_identity"] = cached_plan.get("identity") or state.get("current_identity")
                if not is_runner_stopped:
                    state["status"] = f"windows-{'discovery' if execution_mode == 'discovery' else 'active'} ({username or hostname or runner_id})"
                state["current_plan"] = {
                    "persona": host_persona or ((cached_plan.get("identity") or {}).get("persona")) or effective_fallback,
                    "host_persona": host_persona,
                    "logged_in_persona": cached_plan.get("logged_in_persona") or ((cached_plan.get("identity") or {}).get("logged_in_persona")),
                    "department": ((cached_plan.get("identity") or {}).get("department")) or "",
                    "groups": ((cached_plan.get("identity") or {}).get("groups")) or [],
                    "access_urls": cached_plan.get("access_urls") or [],
                    "traffic_method": cached_plan.get("traffic_method") or "GET",
                    "traffic_min_sleep": cached_plan.get("traffic_min_sleep"),
                    "traffic_max_sleep": cached_plan.get("traffic_max_sleep"),
                    "session_duration": cached_plan.get("session_duration"),
                    "execution_mode": cached_mode,
                    "orchestrator_control": orchestrator_control,
                }
                return cached_plan

        lookup_username = self._resolve_windows_lookup_username(username, interactive_username)
        identity = self._find_identity_for_windows_user(lookup_username)
        if not identity:
            identity = {
                "username": lookup_username or username,
                "device_name": hostname,
                "department": effective_fallback,
                "persona": effective_fallback,
                "auth": "dot1x",
                "os": "Windows",
            }
        # Windows host plans should always report the host OS as Windows in UI/telemetry,
        # even when the matched identity profile was originally imported with another OS.
        effective_identity = dict(identity)
        effective_identity["os"] = "Windows"
        logged_in_persona = self._persona_from_identity(effective_identity, effective_fallback)
        host_persona = self._intended_windows_persona(runner)
        # Traffic + connectivity policies follow the PC's assigned department, not whoever is logged in.
        policy_identity = dict(effective_identity)
        policy_identity["persona"] = host_persona
        policy_identity["department"] = host_persona
        persona = host_persona

        services_dict = self._build_services_dict()
        session_plan = self._build_session_plan(policy_identity, runner, services_dict)
        groups = self._identity_groups(policy_identity)
        user_agent = f"ClarionLab-Windows-{str(persona).replace(' ', '')}/2.0"

        state["current_identity"] = {
            **effective_identity,
            "persona": host_persona,
            "host_persona": host_persona,
            "logged_in_persona": logged_in_persona,
        }
        if not is_runner_stopped:
            state["status"] = f"windows-{'discovery' if execution_mode == 'discovery' else 'active'} ({username or hostname or runner_id})"
        state["current_plan"] = {
            "persona": persona,
            "host_persona": host_persona,
            "logged_in_persona": logged_in_persona,
            "department": effective_identity.get("department") or "",
            "groups": groups,
            "access_urls": session_plan["access_urls"],
            "traffic_method": session_plan["traffic_method"],
            "traffic_min_sleep": session_plan["traffic_min_sleep"],
            "traffic_max_sleep": session_plan["traffic_max_sleep"],
            "session_duration": session_plan["session_duration"],
            "execution_mode": execution_mode,
            "orchestrator_control": orchestrator_control,
        }
        plan = {
            "runner_id": runner_id,
            "runner_type": "windows",
            "execution_mode": execution_mode,
            "hostname": hostname,
            "username": username,
            "interactive_username": interactive_username or lookup_username,
            "identity": {
                "username": effective_identity.get("username") or username,
                "display_name": effective_identity.get("display_name") or username,
                "department": effective_identity.get("department") or logged_in_persona,
                "persona": persona,
                "host_persona": host_persona,
                "logged_in_persona": logged_in_persona,
                "groups": groups,
                "os": "Windows",
            },
            "host_persona": host_persona,
            "logged_in_persona": logged_in_persona,
            "persona": persona,
            "access_urls": session_plan["access_urls"],
            "policy_test_plan": session_plan.get("policy_test_plan") or {"enabled": False, "cases": []},
            "traffic_method": session_plan["traffic_method"],
            "traffic_min_sleep": session_plan["traffic_min_sleep"],
            "traffic_max_sleep": session_plan["traffic_max_sleep"],
            "session_duration": session_plan["session_duration"],
            "user_agent": user_agent,
            "orchestrator_control": orchestrator_control,
        }
        state["windows_plan"] = plan
        return plan

    def get_service_targets(self, persona: str, services_dict: Dict = None) -> List[str]:
        """Resolve service targets for a given persona based on connectivity policies.

        Pass a pre-built services_dict to avoid rebuilding it on every call when
        iterating over multiple groups for the same identity.
        """
        policies = self.config.get("connectivity_policies", {})
        if services_dict is None:
            services_dict = {s["id"]: s for s in self.config.get("services", [])}

        targets = []
        for srv_id in policies.get(persona, []):
            service = services_dict.get(srv_id)
            if service:
                url = f"{service['protocol']}://{service['target']}:{service['port']}"
                if service.get("path"):
                    url += service["path"]
                targets.append(url)
        return targets

    def log_ground_truth(
        self,
        identity: Dict,
        runner: Dict,
        status: str = "started",
        access_urls: List[str] = None,
        session_duration_seconds=None,
        launch_id: Optional[str] = None,
    ):
        """Log ground truth entry for validation.

        access_urls, when supplied, are the fully-merged URLs already resolved by the
        assignment loop (including multi-group targets). Falls back to identity["urls"]
        and then to a single-persona policy lookup if omitted.
        """
        start_dt = datetime.datetime.now()
        timestamp = start_dt.isoformat()
        scheduled_end = (
            (start_dt + datetime.timedelta(seconds=float(session_duration_seconds))).isoformat()
            if session_duration_seconds is not None
            else ""
        )

        # Extract identity info
        mac = identity.get("mac", "UNKNOWN")
        device_name = identity.get("device_name") or identity.get("username", "UNKNOWN")
        username = identity.get("username") or ""
        persona = identity.get("persona") or identity.get("department", "UNKNOWN")
        auth = (identity.get("auth") or "dot1x").strip()
        identity_kind = lp.identity_kind(identity)
        runner_type = str(runner.get("runner_type") or "pi").strip()
        os_type = identity.get("os") or ""
        launch_id = launch_id or self.launch_id or ""
        preset_id = ""
        campaign_label = ""
        if self.launch_profile:
            preset_id = self.launch_profile.get("preset_id") or ""
            campaign_label = self.launch_profile.get("campaign_label") or ""

        # Prefer caller-supplied URLs (multi-group merged) → identity-embedded → policy lookup
        urls = access_urls or identity.get("urls") or self.get_service_targets(persona)

        expected_destinations = ",".join(urls) if isinstance(urls, list) else urls
        expected_protocols = "http,https,dns"  # Default
        
        # Write to CSV
        file_exists = os.path.exists(self.ground_truth_log)
        try:
            fieldnames = [
                "timestamp",
                "status",
                "launch_id",
                "preset_id",
                "campaign_label",
                "runner",
                "runner_type",
                "identity_kind",
                "auth",
                "username",
                "device_mac",
                "device_name",
                "persona",
                "os",
                "expected_destinations",
                "expected_protocols",
                "session_duration_seconds",
                "scheduled_end_timestamp",
            ]

            header_needs_write = not file_exists
            # If the CSV already exists with a legacy header, rewrite it once so
            # DictReader-based validators can reliably access new columns.
            if not header_needs_write:
                try:
                    with open(self.ground_truth_log, "r", newline="") as f:
                        reader = csv.reader(f)
                        existing_header = next(reader, [])
                    if existing_header and (
                        "session_duration_seconds" not in existing_header
                        or "launch_id" not in existing_header
                        or "campaign_label" not in existing_header
                    ):
                        with open(self.ground_truth_log, "r", newline="") as f:
                            reader = csv.reader(f)
                            _old_header = next(reader, [])
                            rows = list(reader)
                        with open(self.ground_truth_log, "w", newline="") as f:
                            writer = csv.writer(f)
                            writer.writerow(fieldnames)
                            for row in rows:
                                if len(row) < len(fieldnames):
                                    row = row + [""] * (len(fieldnames) - len(row))
                                writer.writerow(row[: len(fieldnames)])
                        header_needs_write = False
                except Exception:
                    # If rewrite fails, we still append; validators will gracefully
                    # fall back to legacy behavior when new columns are missing.
                    pass

            with open(self.ground_truth_log, "a", newline="") as f:
                writer = csv.writer(f)
                if header_needs_write:
                    writer.writerow(fieldnames)
                writer.writerow([
                    timestamp,
                    status,
                    launch_id,
                    preset_id,
                    campaign_label,
                    runner["name"],
                    runner_type,
                    identity_kind,
                    auth,
                    username,
                    mac,
                    device_name,
                    persona,
                    os_type,
                    expected_destinations,
                    expected_protocols,
                    session_duration_seconds if session_duration_seconds is not None else "",
                    scheduled_end,
                ])
            if (status or "").strip().lower() == "started":
                self._record_launch_progress(identity, runner)
            logger.info(f"Logged ground truth: {device_name} ({persona}) - {status}")
        except Exception as e:
            logger.error(f"Failed to log ground truth: {e}")

    def run(
        self,
        duration_hours: int = 24,
        cycle_once: bool = False,
        launch_profile: Optional[Dict[str, Any]] = None,
    ):
        """Main orchestration loop.
        If cycle_once is True, each runner goes through every identity in its persona set
        once, then stops; orchestration ends when all runners have completed their cycle.
        Otherwise runs until duration_hours elapses or Stop is pressed."""
        logger.info("=" * 80)
        logger.info("Starting Lab Orchestration")
        logger.info("=" * 80)

        if launch_profile is not None:
            profile = self.set_launch_profile(launch_profile)
            # prepare_launch_runners is also called from POST /api/start before the thread starts.
            started = self.prepare_launch_runners(profile)
            logger.info(
                "Launch profile %s: kinds=%s personas=%s max_concurrent=%s started_runners=%s",
                self.launch_id,
                profile.get("identity_kinds"),
                profile.get("personas") or "(all)",
                profile.get("max_concurrent"),
                started,
            )
        else:
            self.clear_launch_profile()
        
        start_time = time.time()
        end_time = start_time + (duration_hours * 3600)
        
        self.running = True
        logger.info(f"Orchestration will run for {duration_hours} hours" + (" (cycle once per persona)" if cycle_once else ""))
        
        # Reset only "completed" runners so cycle-once can run again; do NOT reset "stopped"
        # (stopped runners stay stopped so only runners in "start" mode get assignments)
        for r in self.runners:
            name = r["name"]
            if name in self.runner_states:
                state = self.runner_states[name]
                if cycle_once:
                    state["used_identity_ids"] = set()
                if state.get("status") == "completed":
                    state["status"] = "idle"
                    state["next_switch"] = 0
                    logger.info(f"{name}: reset from completed to idle for new run")
        
        # Log runner statuses so we can verify only non-stopped runners get assignments
        statuses = {name: self.runner_states.get(name, {}).get("status", "?") for name in (r["name"] for r in self.runners)}
        logger.info("Runner statuses at start: %s", statuses)
        
        try:
            while self.running and time.time() < end_time:
                if self.paused:
                    time.sleep(1)
                    continue

                current_time = time.time()
                
                for runner in self.runners:
                    runner_name = runner["name"]
                    state = self.runner_states.get(runner_name)
                    if not state:
                        self.runner_states[runner_name] = {"current_identity": None, "next_switch": 0, "status": "stopped"}
                        state = self.runner_states[runner_name]

                    # Windows lab PCs: traffic via windows_runner_agent.ps1 + /api/windows-hosts/<id>/plan only.
                    # Do not push Pi-style pending_assignment (802.1x identity rotation) to them.
                    if str(runner.get("runner_type") or "pi").strip().lower() == "windows":
                        continue

                    if not self._runner_in_launch(runner):
                        continue

                    max_concurrent = 0
                    if self.launch_profile:
                        max_concurrent = int(self.launch_profile.get("max_concurrent") or 0)
                    if max_concurrent > 0 and len(self.active_identities) >= max_concurrent:
                        continue
                    
                    # Check if manually stopped or cycle_once completed (only non-stopped runners get assignments)
                    status = (state.get("status") or "").strip().lower()
                    if status == "stopped":
                        continue
                    if cycle_once and state.get("status") == "completed":
                        continue

                    # Check if it's time to switch identity
                    if current_time >= state["next_switch"]:
                        # Don't assign again until current assignment is acked (agents only).
                        # However, clear a stalled assignment after a lease timeout so the lab can continue.
                        pending = state.get("pending_assignment")
                        if pending:
                            assigned_at = pending.get("assigned_at")
                            session_duration = pending.get("session_duration", runner.get("session_duration", 300))
                            lease_seconds = float(session_duration) + float(self.ASSIGNMENT_LEASE_GRACE_SECONDS)
                            if isinstance(assigned_at, (int, float)) and (current_time - assigned_at) > lease_seconds:
                                logger.error(
                                    "%s: pending assignment lease expired (age=%.1fs > lease=%.1fs) — clearing pending assignment",
                                    runner_name,
                                    float(current_time - assigned_at),
                                    lease_seconds,
                                )
                                pending_ident = pending.get("identity") or {}
                                pending_id = pending_ident.get("username") or pending_ident.get("device_name")
                                if pending_id and pending_id in self.active_identities:
                                    self.active_identities.discard(pending_id)
                                state.pop("pending_assignment", None)
                                state["status"] = "assignment_timeout"
                            else:
                                continue
                        # 1. Release previous identity if any
                        prev_identity = state.get("current_identity")
                        prev_id = None
                        if prev_identity:
                            prev_id = prev_identity.get("username") or prev_identity.get("device_name")
                            if prev_id and prev_id in self.active_identities:
                                self.active_identities.remove(prev_id)
                                logger.info(f"Released identity '{prev_id}' from {runner_name}")
                            state["current_identity"] = None

                        # 2. Get available identities
                        available_identities = self.get_identities_for_persona_set(
                            runner["persona_set"]
                        )
                        # In cycle_once mode, exclude identities this runner has already used
                        if cycle_once:
                            used = state.get("used_identity_ids") or set()
                            available_identities = [
                                i for i in available_identities
                                if (i.get("username") or i.get("device_name")) not in used
                            ]
                            if not available_identities:
                                state["status"] = "completed"
                                logger.info(f"{runner_name}: cycled through all identities for persona {runner['persona_set']}; completed.")
                                continue
                        # Prefer a different identity than the one we just had (when not cycle_once)
                        if not cycle_once and prev_id is not None and len(available_identities) > 1:
                            others = [
                                i for i in available_identities
                                if (i.get("username") or i.get("device_name")) != prev_id
                            ]
                            if others:
                                available_identities = others

                        if not available_identities:
                            logger.warning(f"No available identities for {runner_name} (Persona: {runner['persona_set']}). Waiting...")
                            state["status"] = "waiting for identity"
                            # Retry sooner than session duration, but not immediately
                            state["next_switch"] = current_time + 60
                            continue
                        
                        # 3. Pick next identity with recency and time-of-day weighting
                        next_identity = self._choose_next_identity(
                            available_identities,
                            runner,
                            state,
                            current_time,
                            cycle_once=cycle_once,
                            previous_identity_id=prev_id,
                        )
                        ident_id = next_identity.get("username") or next_identity.get("device_name")
                        
                        # 4. Assign session to runner (agents poll for assignment; no SSH)
                        state["status"] = "switching"
                        services_dict = self._build_services_dict()
                        session_plan = self._build_session_plan(next_identity, runner, services_dict)
                        access_urls = session_plan["access_urls"]
                        if not access_urls:
                            # Avoid creating misleading assignments/ground-truth when connectivity config resolves to nothing.
                            logger.warning(
                                "%s: resolved access_urls is empty for identity=%s — waiting for configuration",
                                runner_name,
                                ident_id,
                            )
                            state["status"] = "waiting-for-targets"
                            state["next_switch"] = current_time + 60
                            continue

                        state["pending_assignment"] = {
                            "identity": next_identity,
                            "session_duration": session_plan["session_duration"],
                            "access_urls": access_urls,
                            "policy_test_plan": session_plan.get("policy_test_plan") or {"enabled": False, "cases": []},
                            "traffic_method": session_plan["traffic_method"],
                            "traffic_min_sleep": session_plan["traffic_min_sleep"],
                            "traffic_max_sleep": session_plan["traffic_max_sleep"],
                            "interface": runner["interface"],
                            "management_interface": runner.get("management_interface") or ("wlan0" if runner.get("interface") == "eth0" else "eth0"),
                            "assigned_at": current_time,
                            "lease_grace_seconds": self.ASSIGNMENT_LEASE_GRACE_SECONDS,
                        }
                        # Mark as active
                        if ident_id:
                            self.active_identities.add(ident_id)
                        if cycle_once and ident_id:
                            state.setdefault("used_identity_ids", set()).add(ident_id)
                        self._record_identity_use(state, next_identity, current_time)

                        # Log ground truth (pass merged multi-group URLs)
                        self.log_ground_truth(
                            next_identity,
                            runner,
                            status="started",
                            access_urls=access_urls,
                            session_duration_seconds=session_plan["session_duration"],
                            launch_id=self.launch_id,
                        )

                        # Update state
                        state["current_identity"] = next_identity
                        state["next_switch"] = current_time + session_plan["session_duration"]
                        state["status"] = f"active ({ident_id})"

                        logger.info(
                            f"{runner_name}: identity={ident_id} behavior={session_plan['behavior_key']} "
                            f"targets={len(access_urls)} cadence={session_plan['traffic_min_sleep']}-{session_plan['traffic_max_sleep']}s "
                            f"next switch in {session_plan['session_duration']}s "
                            f"({datetime.datetime.fromtimestamp(state['next_switch']).strftime('%H:%M:%S')})"
                        )
                
                # If cycle_once and all runners are done or stopped, end orchestration.
                # Require at least one runner to have reached "completed" before exiting —
                # otherwise all runners being in the default "stopped" state (before any
                # session runs) would trigger an immediate exit on the very first iteration.
                if cycle_once and self.runners:
                    statuses = [
                        self.runner_states.get(r["name"], {}).get("status")
                        for r in self.runners
                    ]
                    any_completed = any(s == "completed" for s in statuses)
                    all_done = all(s in ("completed", "stopped") for s in statuses)
                    if any_completed and all_done:
                        logger.info("All runners have cycled through their personas; stopping orchestration.")
                        self.running = False
                        break
                
                # Sleep for 10 seconds before checking again
                time.sleep(10)
        
        except KeyboardInterrupt:
            logger.info("Orchestration interrupted by user")
        except Exception as e:
            logger.error(f"Orchestration fatal error: {e}", exc_info=True)
        finally:
            self.running = False
            self.clear_launch_profile()
            logger.info("=" * 80)
            logger.info("Orchestration complete")
            logger.info(f"Ground truth log: {self.ground_truth_log}")
            logger.info("=" * 80)

    def stop(self):
        """Stop the orchestration loop. Mark all runners stopped so they don't run until per-runner Start."""
        self.running = False
        for name, state in self.runner_states.items():
            if state.get("pending_assignment"):
                del state["pending_assignment"]
            if state.get("current_identity"):
                ident_id = state["current_identity"].get("username") or state["current_identity"].get("device_name")
                if ident_id and ident_id in self.active_identities:
                    self.active_identities.discard(ident_id)
            state["current_identity"] = None
            state["next_switch"] = 0
            state["status"] = "stopped"

    def stop_runner(self, runner_name: str) -> bool:
        """Stop a specific runner (mark stopped, clear assignment; no SSH)."""
        if runner_name not in self.runner_states:
            return False
        state = self.runner_states[runner_name]
        if state.get("current_identity"):
            ident_id = state["current_identity"].get("username") or state["current_identity"].get("device_name")
            if ident_id and ident_id in self.active_identities:
                self.active_identities.remove(ident_id)
        state.pop("pending_assignment", None)
        state["current_identity"] = None
        state["next_switch"] = 0
        state["status"] = "stopped"
        logger.info("Runner %s marked stopped", runner_name)
        return True

    def start_runner(self, runner_name: str) -> bool:
        """Manually start/restart a runner (clear stopped status)."""
        if runner_name not in self.runner_states:
            return False
        state = self.runner_states[runner_name]
        if state.get("current_identity"):
            ident_id = state["current_identity"].get("username") or state["current_identity"].get("device_name")
            if ident_id and ident_id in self.active_identities:
                self.active_identities.discard(ident_id)
        state["current_identity"] = None
        state["status"] = "idle"
        state["next_switch"] = 0
        state["agent_status"] = "idle"
        # Force Windows agent to pick up traffic mode on next plan poll.
        state.pop("windows_plan", None)
        state.pop("pending_assignment", None)
        runner = next((r for r in self.runners if r.get("name") == runner_name), None)
        rtype = str((runner or {}).get("runner_type") or "pi").strip().lower()
        if rtype == "windows" and self.running:
            state["status"] = "windows-pending-traffic"
        logger.info("Manual start requested for %s", runner_name)
        return True

    def _json_safe(self, obj: Any) -> Any:
        """Return a JSON-serializable copy (sets -> lists, leave dicts/lists/str/numbers/bool/None)."""
        if obj is None or isinstance(obj, (bool, int, float, str)):
            return obj
        if isinstance(obj, set):
            return list(obj)
        if isinstance(obj, dict):
            return {k: self._json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._json_safe(x) for x in obj]
        return obj

    def get_pending_assignment(self, runner_id: str) -> Dict:
        """Get pending assignment for agent-based runner (JSON-serializable). Returns {} if none or not agent."""
        if runner_id not in self.runner_states:
            return {}
        state = self.runner_states[runner_id]
        pending = state.get("pending_assignment")
        if not pending or not isinstance(pending, dict):
            return {}
        identity = pending.get("identity") or {}
        return {
            "identity": identity,
            "session_duration": pending.get("session_duration", 300),
            "access_urls": pending.get("access_urls") or [],
            "policy_test_plan": pending.get("policy_test_plan") or {"enabled": False, "cases": []},
            "traffic_method": pending.get("traffic_method") or "GET",
            "traffic_min_sleep": pending.get("traffic_min_sleep"),
            "traffic_max_sleep": pending.get("traffic_max_sleep"),
            "interface": pending.get("interface", "eth0"),
            "management_interface": pending.get("management_interface", "wlan0"),
        }

    def record_policy_test_results(self, runner_id: str, payload: Dict[str, Any]) -> bool:
        """Persist runner policy-test results and keep a recent in-memory cache per runner."""
        if runner_id not in self.runner_states:
            return False
        state = self.runner_states[runner_id]
        state["last_contact"] = time.time()
        recent = state.setdefault("policy_test_results", [])
        result_bundle = {
            "runner_id": runner_id,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "identity": payload.get("identity") or {},
            "session_id": payload.get("session_id"),
            "results": payload.get("results") or [],
        }
        recent.append(result_bundle)
        state["policy_test_results"] = recent[-20:]
        try:
            os.makedirs(os.path.dirname(self.policy_test_log), exist_ok=True)
            with open(self.policy_test_log, "a") as f:
                f.write(json.dumps(result_bundle) + "\n")
        except Exception as exc:
            logger.warning("Failed writing policy_test_log: %s", exc)
        return True

    def get_policy_test_results(self, runner_id: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        if runner_id:
            state = self.runner_states.get(runner_id, {})
            rows = list(state.get("policy_test_results") or [])
            return rows[-limit:]
        rows: List[Dict[str, Any]] = []
        for name, state in self.runner_states.items():
            for bundle in state.get("policy_test_results") or []:
                row = dict(bundle)
                row["runner_id"] = name
                rows.append(row)
        rows.sort(key=lambda r: str(r.get("timestamp") or ""))
        return rows[-limit:]

    def clear_policy_test_results(self) -> int:
        """Clear in-memory policy test bundles and truncate the JSONL log."""
        cleared = 0
        for state in self.runner_states.values():
            bundles = state.pop("policy_test_results", None)
            if bundles:
                cleared += len(bundles)
        try:
            os.makedirs(os.path.dirname(self.policy_test_log), exist_ok=True)
            with open(self.policy_test_log, "w", encoding="utf-8") as f:
                pass
        except Exception as exc:
            logger.warning("Failed truncating policy_test_log: %s", exc)
        logger.info("Cleared policy test results (%s in-memory bundles)", cleared)
        return cleared

    def clear_pending_assignment(self, runner_id: str) -> bool:
        """Clear pending assignment after agent completed session. Allows next assignment."""
        if runner_id not in self.runner_states:
            return False
        state = self.runner_states[runner_id]
        if "pending_assignment" in state:
            del state["pending_assignment"]
        # Release identity immediately so other runners can use it while we pick the next one.
        current = state.get("current_identity")
        if isinstance(current, dict):
            ident_id = current.get("username") or current.get("device_name")
            if ident_id and ident_id in self.active_identities:
                self.active_identities.discard(ident_id)
        state["current_identity"] = None
        state["next_switch"] = 0  # Eligible for next identity on the next orchestrator tick.
        if str(state.get("status") or "").strip().lower() not in ("stopped", "completed"):
            state["status"] = "idle"
        return True

    def get_runner_control_hints(self, runner_id: str) -> Dict[str, Any]:
        """Hints returned to agents in telemetry responses (restart requests, etc.)."""
        state = self.runner_states.get(runner_id) or {}
        hc = state.get("health_control") or {}
        return {
            "restart_requested": bool(hc.get("restart_requested")),
            "restart_reason": str(hc.get("restart_reason") or ""),
        }

    def acknowledge_runner_control(self, runner_id: str, control: Dict[str, Any]) -> None:
        """Clear one-shot control flags after an agent acts on them."""
        if runner_id not in self.runner_states:
            return
        state = self.runner_states[runner_id]
        hc = dict(state.get("health_control") or default_health_control())
        if control.get("restart_ack") and hc.get("restart_requested"):
            hc["restart_requested"] = False
            hc["restart_ack_at"] = time.time()
        state["health_control"] = hc

    def get_runner_config_by_name(self) -> Dict[str, Dict]:
        """Runner name -> config row (host, interfaces, etc.) from orchestrator DB config."""
        out: Dict[str, Dict] = {}
        for runner in self.runners or []:
            name = runner.get("name")
            if name:
                out[str(name)] = dict(runner)
        return out

    def get_status(self) -> Dict:
        """Return current status of runners (JSON-serializable). Excludes pending_assignment from response."""
        runners_out = {}
        config_by_name = self.get_runner_config_by_name()
        for name, state in self.runner_states.items():
            s = self._json_safe(dict(state))
            s.pop("pending_assignment", None)  # Don't expose credentials to UI
            cfg = config_by_name.get(name) or {}
            # Host IP from DB config: Pi = dedicated mgmt network; Windows = 192.168.12.x (single NIC).
            # Never infer from session telemetry (lab VLAN IPs on Pi eth0/wlan0 lab).
            mgmt_host = str(cfg.get("host") or "").strip()
            if mgmt_host:
                s["management_host"] = mgmt_host
            mgmt_iface = str(cfg.get("management_interface") or "").strip()
            if mgmt_iface:
                s["management_interface"] = mgmt_iface
            lab_iface = str(cfg.get("interface") or "").strip()
            if lab_iface:
                s["lab_interface"] = lab_iface
            runners_out[name] = s
        out = {
            "running": self.running,
            "runners": runners_out,
        }
        if self.launch_id:
            out["launch_id"] = self.launch_id
            out["launch_progress"] = self.get_launch_progress()
        if self.launch_profile:
            out["launch_profile"] = self._json_safe(dict(self.launch_profile))
        return out

def load_config(config_path: str = None) -> Dict:
    """Load orchestrator configuration."""
    if config_path and os.path.exists(config_path):
        logger.info(f"Loading config from {config_path}")
        with open(config_path, 'r') as f:
            return json.load(f)
    else:
        logger.info("Using default configuration")
        return DEFAULT_CONFIG

def main():
    parser = argparse.ArgumentParser(description="Lab Orchestration Controller (client/server; use web UI or DB)")
    parser.add_argument("--config", help="Path to orchestrator config JSON (optional; if omitted and DB exists, use DB)")
    parser.add_argument("--schedule", choices=["daily", "weekly", "continuous"], default="daily", help="Run schedule")
    parser.add_argument("--duration", type=int, default=8, help="Duration in hours (default: 8)")
    args = parser.parse_args()

    if args.schedule == "weekly":
        duration = 168
    elif args.schedule == "continuous":
        duration = 24 * 365
    else:
        duration = args.duration

    # Prefer DB when no config file given (same as web server)
    if not args.config:
        try:
            import db as _db
            if os.path.exists(_db.DEFAULT_DB_PATH):
                _db.migrate_from_json_if_present()
                config = _db.get_full_config()
                config["identities"] = _db.get_identities()
                orchestrator = LabOrchestrator(config)
                orchestrator.run(duration_hours=duration)
                return
        except Exception:
            pass
    config = load_config(args.config)
    if "identities" not in config:
        config["identities"] = []
    orchestrator = LabOrchestrator(config)
    orchestrator.run(duration_hours=duration)

if __name__ == "__main__":
    main()
