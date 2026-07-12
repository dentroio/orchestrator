#!/usr/bin/env python3
"""Runner health evaluation and automated recovery for the lab orchestrator."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import runner_audit

logger = logging.getLogger(__name__)

DEFAULT_HEALTH_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "auto_recovery_enabled": True,
    "evaluate_interval_seconds": 30,
    "stale_threshold_seconds": 15,
    "offline_threshold_seconds": 90,
    "windows_offline_threshold_seconds": 120,
    "remediation_cooldown_seconds": 300,
    "max_remediations_per_hour": 3,
    "circuit_breaker_failures": 5,
    "pi_restart_before_full_remediate": True,
    "assignment_timeout_triggers_recovery": True,
    "per_runner_auto_recovery": {},
}

DEFAULT_HEALTH_EVENT_LOG = os.path.expanduser(
    "~/clarion/lab/ground_truth/runner_health_events.jsonl"
)

HEALTH_STATES = ("healthy", "degraded", "recovering", "failed")


def default_runner_health_state() -> Dict[str, Any]:
    return {
        "state": "healthy",
        "failure_class": None,
        "detail": "",
        "last_evaluated_at": None,
        "last_remediation_at": None,
        "remediation_timestamps": [],
        "consecutive_failures": 0,
        "last_action": None,
        "last_action_at": None,
        "circuit_breaker_open": False,
    }


def default_health_control() -> Dict[str, Any]:
    return {
        "restart_requested": False,
        "restart_requested_at": None,
        "restart_ack_at": None,
        "restart_reason": "",
    }


def _plan_persona(plan: Any) -> str:
    if not isinstance(plan, dict):
        return ""
    return (
        str(plan.get("persona") or plan.get("host_persona") or "").strip()
        or str((plan.get("identity") or {}).get("persona") or "").strip()
    )


def _windows_in_restart_grace(state: Dict[str, Any], now: float, seconds: float = 180) -> bool:
    hc = state.get("health_control") or {}
    if hc.get("restart_requested"):
        return True
    for key in ("restart_ack_at", "restart_requested_at"):
        ts = float(hc.get(key) or 0)
        if ts and (now - ts) < seconds:
            return True
    agent_status = str(state.get("agent_status") or state.get("status") or "").lower()
    return "restarting" in agent_status


def merge_health_settings(raw: Any) -> Dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_HEALTH_SETTINGS))
    if isinstance(raw, dict):
        merged.update({k: v for k, v in raw.items() if k in merged or k == "per_runner_auto_recovery"})
        if not isinstance(merged.get("per_runner_auto_recovery"), dict):
            merged["per_runner_auto_recovery"] = {}
    return merged


class RunnerHealthController:
    """Background watchdog: detect runner failures and trigger recovery actions."""

    def __init__(
        self,
        orchestrator_getter: Callable[[], Any],
        orchestrator_url_getter: Callable[[], str],
        settings_getter: Callable[[], Dict[str, Any]],
        event_log_path: Optional[str] = None,
    ) -> None:
        self._get_orchestrator = orchestrator_getter
        self._get_orchestrator_url = orchestrator_url_getter
        self._get_settings = settings_getter
        self._event_log_path = event_log_path or DEFAULT_HEALTH_EVENT_LOG
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_summary: Dict[str, Any] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="runner-health", daemon=True)
        self._thread.start()
        logger.info("Runner health controller started")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            settings = merge_health_settings(self._get_settings())
            interval = max(10, int(settings.get("evaluate_interval_seconds") or 30))
            try:
                if settings.get("enabled", True):
                    self.evaluate_all()
            except Exception as exc:
                logger.exception("Runner health evaluation failed: %s", exc)
            self._stop.wait(interval)

    def evaluate_all(self) -> Dict[str, Any]:
        with self._lock:
            orc = self._get_orchestrator()
            settings = merge_health_settings(self._get_settings())
            orchestrator_url = self._get_orchestrator_url()
            now = time.time()
            runners_out: Dict[str, Any] = {}

            for runner in orc.runners or []:
                name = runner.get("name")
                if not name:
                    continue
                state = orc.runner_states.setdefault(name, {})
                health = state.setdefault("health", default_runner_health_state())
                state.setdefault("health_control", default_health_control())
                evaluation = self._evaluate_runner(
                    runner, state, orc, settings, now
                )
                runners_out[name] = evaluation
                previous_state = health.get("state")
                health["state"] = evaluation["state"]
                health["failure_class"] = evaluation.get("failure_class")
                health["detail"] = evaluation.get("detail") or ""
                health["last_evaluated_at"] = now

                if evaluation["state"] == "healthy":
                    health["consecutive_failures"] = 0
                    if health.get("circuit_breaker_open"):
                        health["circuit_breaker_open"] = False
                        self._log_event(name, "circuit_breaker_closed", "Runner recovered")
                elif evaluation["state"] in ("degraded", "failed"):
                    if health.get("circuit_breaker_open"):
                        pass  # Already tripped; do not inflate failure counts every eval cycle.
                    elif previous_state == "healthy":
                        health["consecutive_failures"] = int(health.get("consecutive_failures") or 0) + 1
                    elif evaluation["state"] == "failed":
                        health["consecutive_failures"] = int(health.get("consecutive_failures") or 0) + 1

                if (
                    settings.get("auto_recovery_enabled", True)
                    and self._auto_recovery_enabled_for(name, settings)
                    and evaluation.get("should_remediate")
                    and not health.get("circuit_breaker_open")
                ):
                    action_result = self._attempt_remediation(
                        runner, state, orc, settings, orchestrator_url, evaluation, now
                    )
                    runners_out[name]["remediation"] = action_result

            summary = {
                "timestamp": now,
                "orchestration_running": bool(getattr(orc, "running", False)),
                "runners": runners_out,
            }
            self._last_summary = summary
            return summary

    def get_summary(self) -> Dict[str, Any]:
        return dict(self._last_summary or {})

    def get_runner_health(self, runner_name: Optional[str] = None) -> Dict[str, Any]:
        orc = self._get_orchestrator()
        settings = merge_health_settings(self._get_settings())
        out: Dict[str, Any] = {}
        for runner in orc.runners or []:
            name = runner.get("name")
            if not name:
                continue
            if runner_name and name != runner_name:
                continue
            state = orc.runner_states.get(name) or {}
            health = dict(state.get("health") or default_runner_health_state())
            health["runner_type"] = str(runner.get("runner_type") or "pi").lower()
            health["auto_recovery_enabled"] = self._auto_recovery_enabled_for(name, settings)
            out[name] = health
        if runner_name:
            return out.get(runner_name) or {"error": f"Unknown runner: {runner_name}"}
        return {
            "timestamp": time.time(),
            "settings": settings,
            "orchestration_running": bool(getattr(orc, "running", False)),
            "runners": out,
        }

    def reset_runner_health(self, runner_names: Optional[List[str]] = None) -> Dict[str, Any]:
        """Clear circuit breaker and failure counters so auto-recovery can resume."""
        orc = self._get_orchestrator()
        names_filter = {n.strip() for n in (runner_names or []) if n and str(n).strip()}
        reset: List[str] = []
        for runner in orc.runners or []:
            name = runner.get("name")
            if not name:
                continue
            if names_filter and name not in names_filter:
                continue
            state = orc.runner_states.get(name)
            if not isinstance(state, dict):
                continue
            health = state.setdefault("health", default_runner_health_state())
            if health.get("circuit_breaker_open") or int(health.get("consecutive_failures") or 0) > 0:
                health["circuit_breaker_open"] = False
                health["consecutive_failures"] = 0
                health["state"] = "healthy"
                health["failure_class"] = None
                health["detail"] = ""
                self._log_event(name, "circuit_breaker_reset", "Manual health reset")
                reset.append(name)
        return {"reset": reset, "count": len(reset)}

    def _auto_recovery_enabled_for(self, runner_name: str, settings: Dict[str, Any]) -> bool:
        per = settings.get("per_runner_auto_recovery") or {}
        if runner_name in per:
            return bool(per[runner_name])
        return bool(settings.get("auto_recovery_enabled", True))

    def _evaluate_runner(
        self,
        runner: Dict[str, Any],
        state: Dict[str, Any],
        orc: Any,
        settings: Dict[str, Any],
        now: float,
    ) -> Dict[str, Any]:
        name = runner.get("name")
        rtype = str(runner.get("runner_type") or "pi").strip().lower()
        status = str(state.get("status") or "").strip().lower()
        orchestration_running = bool(getattr(orc, "running", False))
        last_contact = float(state.get("last_contact") or 0)
        age = (now - last_contact) if last_contact else None

        if status == "stopped" or not orchestration_running:
            return {
                "state": "healthy",
                "failure_class": None,
                "detail": "Stopped or orchestration not running",
                "should_remediate": False,
            }

        offline_threshold = float(
            settings.get("windows_offline_threshold_seconds" if rtype == "windows" else "offline_threshold_seconds")
            or (120 if rtype == "windows" else 90)
        )
        stale_threshold = float(settings.get("stale_threshold_seconds") or 15)
        if rtype == "windows":
            # Windows agents can legitimately spend close to a minute in traffic
            # probes before the next telemetry post. Do not show noisy degraded
            # states unless contact is approaching the offline threshold.
            stale_threshold = max(stale_threshold, 90)

        in_restart_grace = rtype == "windows" and _windows_in_restart_grace(state, now)
        agent_status = str(state.get("agent_status") or state.get("status") or "").lower()
        active_session_statuses = (
            "generating_traffic", "bouncing", "waiting_dhcp",
            "connected", "active", "dhcp_failed", "switching",
            "windows-active",
        )
        active_session = any(s in status for s in active_session_statuses) or any(
            s in agent_status for s in active_session_statuses
        )
        fresh_contact = age is not None and age < offline_threshold

        health = state.setdefault("health", default_runner_health_state())
        if health.get("circuit_breaker_open"):
            if fresh_contact and active_session:
                # A runner that is actively reporting session telemetry has recovered.
                # Do not leave it permanently failed because of an older recovery loop.
                health["circuit_breaker_open"] = False
                health["consecutive_failures"] = 0
                self._log_event(name, "circuit_breaker_closed", "Fresh active telemetry")
            else:
                return {
                    "state": "failed",
                    "failure_class": "circuit_breaker",
                    "detail": "Auto-recovery disabled after repeated failures",
                    "should_remediate": False,
                }

        failures: List[Tuple[str, str, bool]] = []

        if age is None:
            if not in_restart_grace:
                if rtype == "windows":
                    failures.append(("awaiting_telemetry", "Awaiting Windows agent telemetry", False))
                else:
                    failures.append(("unreachable", "No telemetry received", True))
        elif age >= offline_threshold:
            if not in_restart_grace:
                failures.append(("unreachable", f"Offline ({int(age)}s since last contact)", True))
        elif age >= stale_threshold:
            if not in_restart_grace:
                failures.append(("stale", f"Stale telemetry ({int(age)}s)", False))

        if settings.get("assignment_timeout_triggers_recovery") and status == "assignment_timeout":
            failures.append(("stuck_session", "Assignment lease expired without ack", True))

        pending = state.get("pending_assignment")
        if pending and isinstance(pending, dict):
            assigned_at = pending.get("assigned_at")
            session_duration = float(pending.get("session_duration") or runner.get("session_duration") or 300)
            lease = session_duration + float(getattr(orc, "ASSIGNMENT_LEASE_GRACE_SECONDS", 600))
            if isinstance(assigned_at, (int, float)) and (now - assigned_at) > lease:
                failures.append(("stuck_session", "Pending assignment past lease", True))

        agent_health = (state.get("telemetry") or {}).get("health") or {}
        if isinstance(agent_health, dict):
            last_error = str(agent_health.get("last_error") or "").strip()
            if last_error and rtype != "windows":
                # Treat runner-agent errors as critical only while idle. During an active
                # session the one-shot runner owns behavior and can report transient traffic
                # or DHCP errors that should not trip recovery.
                failures.append(("agent_error", last_error, not active_session))
            last_session = str(agent_health.get("last_session_result") or "").strip().lower()
            if last_session == "failed" and not active_session:
                failures.append(("session_failed", "Last session reported failure", False))

        if "restarting" not in agent_status and "error" in agent_status:
            failures.append(("agent_error", agent_status, True))

        expected_version = self._expected_code_version()
        reported = str((state.get("telemetry") or {}).get("code_version") or "").strip()
        if expected_version and reported and expected_version not in reported and reported not in expected_version:
            failures.append(("version_drift", f"Agent {reported} != expected {expected_version}", False))

        if rtype == "windows":
            telemetry = state.get("telemetry") or {}
            plan = state.get("current_plan") or telemetry.get("current_plan") or {}
            persona = _plan_persona(plan)
            if (
                orchestration_running
                and status not in ("stopped",)
                and "restarting" not in agent_status
                and "discovery" in agent_status
            ):
                plan_control = (plan.get("orchestrator_control") or {}) if isinstance(plan, dict) else {}
                plan_mode = str(plan.get("execution_mode") or "").lower()
                stale_plan = bool(plan_control.get("paused")) or plan_control.get("reason") in (
                    "orchestrator_stopped",
                    "runner_stopped",
                )
                if (
                    str(runner.get("windows_mode") or "traffic").strip().lower() != "discovery"
                    and not stale_plan
                    and plan_mode == "traffic"
                ):
                    failures.append(("orchestrator_desync", "Runner active but agent in discovery mode", False))

        if rtype == "pi" and age is not None and age < offline_threshold and not active_session:
            last_preflight = float(health.get("last_preflight_at") or 0)
            if now - last_preflight >= 300:
                audit = runner_audit.audit_pi_runner(runner, self._get_orchestrator_url())
                health["last_preflight_at"] = now
                critical = [
                    c for c in (audit.get("checks") or []) if c.get("critical") and c.get("status") != "pass"
                ]
                if critical:
                    title = critical[0].get("title") or critical[0].get("id")
                    failures.append(("preflight_fail", str(title), True))

        remediate_classes = {
            "unreachable",
            "stuck_session",
            "agent_error",
            "preflight_fail",
            "orchestrator_desync",
            "session_failed",
        }
        should_remediate = any(critical for cls, _, critical in failures if cls in remediate_classes)
        if rtype == "windows" and in_restart_grace:
            should_remediate = False
        if rtype == "windows":
            # Only restart Windows agents for hard connectivity/session failures.
            # Do not auto-remediate ``awaiting_telemetry``: if the agent has not
            # checked in, it cannot receive a restart request and recovery will just
            # trip the circuit breaker.
            windows_remediate = {"stuck_session"}
            should_remediate = any(critical for cls, _, critical in failures if cls in windows_remediate)

        if not failures:
            return {
                "state": "healthy",
                "failure_class": None,
                "detail": "",
                "should_remediate": False,
            }

        primary_class, primary_detail, _ = failures[0]
        has_critical = any(item[2] for item in failures)
        state_label = "failed" if has_critical and primary_class in remediate_classes else "degraded"
        if int((state.get("health") or {}).get("consecutive_failures") or 0) >= int(
            settings.get("circuit_breaker_failures") or 5
        ):
            state_label = "failed"
            health = state.setdefault("health", default_runner_health_state())
            health["circuit_breaker_open"] = True
            self._log_event(name, "circuit_breaker_open", primary_detail)

        return {
            "state": state_label,
            "failure_class": primary_class,
            "detail": primary_detail,
            "failures": [{"class": c, "detail": d, "critical": crit} for c, d, crit in failures],
            "should_remediate": should_remediate and state_label != "failed",
        }

    def _attempt_remediation(
        self,
        runner: Dict[str, Any],
        state: Dict[str, Any],
        orc: Any,
        settings: Dict[str, Any],
        orchestrator_url: str,
        evaluation: Dict[str, Any],
        now: float,
    ) -> Dict[str, Any]:
        name = runner.get("name")
        health = state.setdefault("health", default_runner_health_state())
        rtype = str(runner.get("runner_type") or "pi").strip().lower()
        failure_class = evaluation.get("failure_class") or "unknown"

        if not self._cooldown_elapsed(health, settings, now):
            return {"skipped": True, "reason": "cooldown", "failure_class": failure_class}

        if not self._remediation_budget_available(health, settings, now):
            health["circuit_breaker_open"] = True
            self._log_event(name, "circuit_breaker_open", "Remediation budget exhausted")
            return {"skipped": True, "reason": "budget_exhausted", "failure_class": failure_class}

        health["state"] = "recovering"
        action = "none"
        result: Dict[str, Any] = {"ok": False}

        if rtype == "windows":
            action = "windows_restart_requested"
            hc = state.setdefault("health_control", default_health_control())
            hc["restart_requested"] = True
            hc["restart_requested_at"] = now
            hc["restart_reason"] = evaluation.get("detail") or failure_class
            result = {"ok": True, "action": action, "detail": hc["restart_reason"]}
        else:
            if settings.get("pi_restart_before_full_remediate", True):
                action = "pi_service_restart"
                result = runner_audit.restart_pi_runner_service(runner)
                if not result.get("ok"):
                    action = "pi_full_remediate"
                    result = runner_audit.remediate_pi_runner(runner, orchestrator_url)
            else:
                action = "pi_full_remediate"
                result = runner_audit.remediate_pi_runner(runner, orchestrator_url)

            if failure_class in ("stuck_session", "orchestrator_desync"):
                orc.start_runner(name)

            if result.get("ok"):
                audit = runner_audit.audit_pi_runner(runner, orchestrator_url)
                result["post_audit_ok"] = bool(audit.get("ok"))

        health["last_remediation_at"] = now
        health["last_action"] = action
        health["last_action_at"] = now
        timestamps = list(health.get("remediation_timestamps") or [])
        timestamps.append(now)
        health["remediation_timestamps"] = [t for t in timestamps if now - t <= 3600]

        self._log_event(
            name,
            action,
            evaluation.get("detail") or failure_class,
            ok=bool(result.get("ok")),
            extra=result,
        )
        return {"action": action, **result}

    def _cooldown_elapsed(self, health: Dict[str, Any], settings: Dict[str, Any], now: float) -> bool:
        last = float(health.get("last_remediation_at") or 0)
        cooldown = float(settings.get("remediation_cooldown_seconds") or 300)
        return (now - last) >= cooldown if last else True

    def _remediation_budget_available(self, health: Dict[str, Any], settings: Dict[str, Any], now: float) -> bool:
        timestamps = [float(t) for t in (health.get("remediation_timestamps") or []) if now - float(t) <= 3600]
        health["remediation_timestamps"] = timestamps
        max_per_hour = int(settings.get("max_remediations_per_hour") or 3)
        return len(timestamps) < max_per_hour

    def _log_event(
        self,
        runner_name: str,
        action: str,
        detail: str,
        *,
        ok: Optional[bool] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = {
            "timestamp": time.time(),
            "runner": runner_name,
            "action": action,
            "detail": detail,
            "ok": ok,
        }
        if extra:
            event["extra"] = {
                k: extra[k]
                for k in ("ok", "action", "detail", "error", "post_audit_ok", "skipped", "reason")
                if k in extra
            }
        try:
            os.makedirs(os.path.dirname(self._event_log_path), exist_ok=True)
            with open(self._event_log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\n")
        except Exception as exc:
            logger.warning("Failed writing health event log: %s", exc)
        logger.info("Runner health [%s] %s: %s", runner_name, action, detail)

    def read_recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not os.path.isfile(self._event_log_path):
            return []
        try:
            with open(self._event_log_path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return []
        events = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def _expected_code_version(self) -> str:
        try:
            app_dir = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(app_dir, "VERSION")
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as handle:
                    return handle.read().strip()
        except OSError:
            pass
        return ""

    def acknowledge_restart(self, runner_name: str) -> None:
        orc = self._get_orchestrator()
        state = orc.runner_states.get(runner_name) or {}
        hc = state.get("health_control") or {}
        if hc.get("restart_requested"):
            hc = dict(hc)
            hc["restart_requested"] = False
            hc["restart_ack_at"] = time.time()
            state["health_control"] = hc
            self._log_event(runner_name, "restart_acknowledged", "Agent received restart request")


_HEALTH_CONTROLLER: Optional[RunnerHealthController] = None


def get_health_controller(
    orchestrator_getter: Callable[[], Any],
    orchestrator_url_getter: Callable[[], str],
    settings_getter: Callable[[], Dict[str, Any]],
) -> RunnerHealthController:
    global _HEALTH_CONTROLLER
    if _HEALTH_CONTROLLER is None:
        event_log = os.path.expanduser(
            os.environ.get("RUNNER_HEALTH_EVENT_LOG", DEFAULT_HEALTH_EVENT_LOG)
        )
        _HEALTH_CONTROLLER = RunnerHealthController(
            orchestrator_getter, orchestrator_url_getter, settings_getter, event_log
        )
    return _HEALTH_CONTROLLER
