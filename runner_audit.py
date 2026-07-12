#!/usr/bin/env python3
"""Orchestrator-side runner audit and remediation (SSH to Pi management hosts)."""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SSH_OPTS = [
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=8",
    "-o",
    "BatchMode=yes",
]

REMOTE_DIR = "~/clarion/lab/orchestrator/app"
REMOTE_PREFLIGHT = f"{REMOTE_DIR}/runner_preflight.py"
REMOTE_REMEDIATE = f"{REMOTE_DIR}/runner_remediate.py"
LOCAL_APP_DIR = Path(__file__).resolve().parent
PI_SCRIPTS = ("runner_preflight.py", "runner_remediate.py")


def _ssh_target(runner: Dict[str, Any]) -> Optional[str]:
    host = str(runner.get("host") or "").strip()
    if not host:
        return None
    user = str(runner.get("user") or "admin").strip() or "admin"
    return f"{user}@{host}"


def _runner_interfaces(runner: Dict[str, Any]) -> Tuple[str, str]:
    lab = str(runner.get("interface") or "eth0").strip()
    mgmt = str(runner.get("management_interface") or "").strip()
    if not mgmt:
        mgmt = "wlan0" if lab == "eth0" else "eth0"
    return lab, mgmt


def _ensure_remote_scripts(target: str) -> Dict[str, Any]:
    """Copy audit/remediate scripts to the Pi if missing or outdated."""
    actions = []
    ok = True
    for name in PI_SCRIPTS:
        local = LOCAL_APP_DIR / name
        if not local.is_file():
            if name == "runner_network.py":
                continue
            continue
        remote = f"{REMOTE_DIR}/{name}"
        proc = subprocess.run(
            ["scp", *SSH_OPTS, str(local), f"{target}:{remote}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        step_ok = proc.returncode == 0
        ok = ok and step_ok
        actions.append(
            {
                "step": f"deploy_{name}",
                "ok": step_ok,
                "detail": (proc.stderr or proc.stdout or "ok").strip()[:200],
            }
        )
    return {"ok": ok, "actions": actions}


def _ssh_pi_json_script(
    target: str,
    script_path: str,
    args: List[str],
    *,
    ssh_timeout: int = 90,
) -> Tuple[Optional[Dict[str, Any]], str]:
    remote_cmd = " ".join(["python3", script_path, *args, "--json"])
    cmd = ["ssh", *SSH_OPTS, target, remote_cmd]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=ssh_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"SSH timed out after {ssh_timeout}s"
    except Exception as exc:
        return None, str(exc)

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if not stdout:
        combined = stderr or "empty response"
        if "Traceback" in combined:
            return None, combined[:2000]
        return None, combined[:500]

    try:
        return json.loads(stdout), ""
    except json.JSONDecodeError:
        combined = stdout
        if stderr:
            combined = stdout + "\n" + stderr
        return None, combined[:2000]


def _pi_runner_base(runner: Dict[str, Any]) -> Dict[str, Any]:
    name = runner.get("name") or "unknown"
    lab, mgmt = _runner_interfaces(runner)
    return {
        "runner": name,
        "runner_type": "pi",
        "host": runner.get("host"),
        "lab_interface": lab,
        "management_interface": mgmt,
        "ok": False,
        "ready": False,
        "checks": [],
    }


def _pi_script_args(name: str, lab: str, mgmt: str, orchestrator_url: str) -> List[str]:
    return [
        "--runner-id",
        shlex.quote(name),
        "--lab-interface",
        shlex.quote(lab),
        "--management-interface",
        shlex.quote(mgmt),
        "--orchestrator-url",
        shlex.quote(orchestrator_url),
    ]


def audit_pi_runner(
    runner: Dict[str, Any],
    orchestrator_url: str,
    *,
    ssh_timeout: int = 45,
) -> Dict[str, Any]:
    base = _pi_runner_base(runner)
    name = base["runner"]
    lab = base["lab_interface"]
    mgmt = base["management_interface"]

    target = _ssh_target(runner)
    if not target:
        base["error"] = "No management host (host) configured for SSH audit"
        base["checks"] = [
            {
                "id": "ssh_host",
                "title": "Management SSH host configured",
                "status": "fail",
                "critical": True,
                "detail": "Set runner host to management IP in Configuration",
            }
        ]
        return base

    deploy = _ensure_remote_scripts(target)
    report, err = _ssh_pi_json_script(
        target,
        REMOTE_PREFLIGHT,
        _pi_script_args(name, lab, mgmt, orchestrator_url),
        ssh_timeout=ssh_timeout,
    )
    if report is None:
        detail = err
        if "runner_preflight.py" in err and "No such file" in err:
            detail = (
                "Missing runner_preflight.py on Pi. Click Fix Issues or run deploy_runner.sh "
                f"{target}"
            )
        base["error"] = err
        base["checks"] = [
            {
                "id": "ssh_reachable",
                "title": "SSH to management host",
                "status": "fail",
                "critical": True,
                "detail": detail,
            }
        ]
        if not deploy.get("ok"):
            base["deploy_actions"] = deploy.get("actions")
        return base

    report["runner"] = name
    report["runner_type"] = "pi"
    report["host"] = runner.get("host")
    report["ssh_target"] = target
    report["ready"] = report.get("ok", False)
    return report


def restart_pi_runner_service(runner: Dict[str, Any], *, ssh_timeout: int = 45) -> Dict[str, Any]:
    """Restart clarion-runner systemd unit on a Pi via SSH (lighter than full remediate)."""
    base = _pi_runner_base(runner)
    name = base["runner"]
    target = _ssh_target(runner)
    if not target:
        base["error"] = "No management host (host) configured for SSH restart"
        base["ok"] = False
        return base

    proc = subprocess.run(
        ["ssh", *SSH_OPTS, target, "sudo systemctl restart clarion-runner"],
        capture_output=True,
        text=True,
        timeout=ssh_timeout,
        check=False,
    )
    ok = proc.returncode == 0
    detail = (proc.stderr or proc.stdout or ("restarted" if ok else "restart failed")).strip()[:300]
    return {
        **base,
        "ok": ok,
        "action": "pi_service_restart",
        "detail": detail,
        "runner": name,
        "runner_type": "pi",
        "host": runner.get("host"),
        "ssh_target": target,
    }


def remediate_pi_runner(
    runner: Dict[str, Any],
    orchestrator_url: str,
    *,
    ssh_timeout: int = 180,
) -> Dict[str, Any]:
    base = _pi_runner_base(runner)
    name = base["runner"]
    lab = base["lab_interface"]
    mgmt = base["management_interface"]

    target = _ssh_target(runner)
    if not target:
        base["error"] = "No management host (host) configured for SSH remediation"
        return base

    deploy = _ensure_remote_scripts(target)
    base["deploy_actions"] = deploy.get("actions", [])

    report, err = _ssh_pi_json_script(
        target,
        REMOTE_REMEDIATE,
        _pi_script_args(name, lab, mgmt, orchestrator_url),
        ssh_timeout=ssh_timeout,
    )
    if report is None:
        base["error"] = err
        if err and "Traceback" in err:
            base["traceback"] = err
        return base

    report["runner"] = name
    report["runner_type"] = "pi"
    report["host"] = runner.get("host")
    report["ssh_target"] = target
    preflight = report.get("preflight") or {}
    report["checks"] = report.get("checks") or preflight.get("checks", [])
    report["ready"] = report.get("ready", preflight.get("ready", False))
    report["ok"] = report.get("ok", report.get("ready", False))
    return report


def audit_windows_runner(
    runner: Dict[str, Any],
    runner_state: Optional[Dict[str, Any]],
    orchestrator_url: str,
) -> Dict[str, Any]:
    name = runner.get("name") or "unknown"
    state = runner_state or {}
    checks: List[Dict[str, Any]] = []
    now = time.time()
    last = float(state.get("last_contact") or 0)
    age = now - last if last else None

    def add(cid, title, ok, detail, critical=False):
        checks.append(
            {
                "id": cid,
                "title": title,
                "status": "pass" if ok else "fail",
                "critical": critical,
                "detail": detail,
            }
        )

    add(
        "telemetry_recent",
        "Recent agent telemetry",
        age is not None and age < 120,
        f"last contact {int(age)}s ago" if age is not None else "never",
        critical=False,
    )
    telemetry = state.get("telemetry") or {}
    add(
        "user_logged_in",
        "Interactive user logged in",
        bool(telemetry.get("user_logged_in") or telemetry.get("interactive_username")),
        str(telemetry.get("interactive_username") or telemetry.get("username") or "none"),
        critical=False,
    )
    plan = state.get("current_plan") or telemetry.get("current_plan") or {}
    persona = plan.get("persona") or (state.get("current_identity") or {}).get("persona")
    add(
        "persona_resolved",
        "Persona resolved from logged-in user",
        bool(persona),
        str(persona or "unknown"),
        critical=False,
    )
    add(
        "runner_type",
        "Registered as Windows runner",
        str(runner.get("runner_type") or "").lower() == "windows",
        str(runner.get("runner_type")),
        critical=True,
    )

    critical_fail = [c for c in checks if c["status"] == "fail" and c.get("critical")]
    ok = not critical_fail
    return {
        "runner": name,
        "runner_type": "windows",
        "ok": ok,
        "ready": ok and (age is not None and age < 120),
        "checks": checks,
        "summary": {
            "pass": sum(1 for c in checks if c["status"] == "pass"),
            "warn": 0,
            "fail": sum(1 for c in checks if c["status"] == "fail"),
            "critical_failures": len(critical_fail),
        },
    }


def _summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    ready = all(r.get("ready") for r in results)
    ok = all(r.get("ok") for r in results)
    return {
        "ok": ok,
        "ready": ready,
        "runners": results,
        "summary": {
            "total": len(results),
            "ready": sum(1 for r in results if r.get("ready")),
            "failed": sum(1 for r in results if not r.get("ok")),
        },
    }


def _find_runner(runners: List[Dict[str, Any]], runner_name: str) -> Optional[Dict[str, Any]]:
    name = (runner_name or "").strip()
    if not name:
        return None
    for runner in runners:
        if (runner.get("name") or "").strip() == name:
            return runner
    return None


def _audit_one_result(
    runner: Dict[str, Any],
    runner_states: Dict[str, Any],
    orchestrator_url: str,
) -> Dict[str, Any]:
    name = runner.get("name")
    rtype = str(runner.get("runner_type") or "pi").strip().lower()
    if rtype == "windows":
        return audit_windows_runner(runner, runner_states.get(name), orchestrator_url)
    return audit_pi_runner(runner, orchestrator_url)


def _remediate_one_result(
    runner: Dict[str, Any],
    runner_states: Dict[str, Any],
    orchestrator_url: str,
) -> Dict[str, Any]:
    name = runner.get("name")
    rtype = str(runner.get("runner_type") or "pi").strip().lower()
    if rtype == "windows":
        return {
            "runner": name,
            "runner_type": "windows",
            "ok": True,
            "ready": True,
            "skipped": True,
            "actions": [{"step": "skip", "ok": True, "detail": "Windows: no SSH remediate"}],
            "checks": [],
        }
    return remediate_pi_runner(runner, orchestrator_url)


def audit_runner_by_name(
    runners: List[Dict[str, Any]],
    runner_states: Dict[str, Any],
    orchestrator_url: str,
    runner_name: str,
) -> Dict[str, Any]:
    runner = _find_runner(runners, runner_name)
    if not runner:
        return {
            "error": f"Unknown runner: {runner_name}",
            "ok": False,
            "ready": False,
            "runners": [],
            "runner": runner_name,
        }
    out = _summarize([_audit_one_result(runner, runner_states, orchestrator_url)])
    out["timestamp"] = time.time()
    out["orchestrator_url"] = orchestrator_url
    out["runner"] = runner_name
    return out


def remediate_runner_by_name(
    runners: List[Dict[str, Any]],
    runner_states: Dict[str, Any],
    orchestrator_url: str,
    runner_name: str,
) -> Dict[str, Any]:
    runner = _find_runner(runners, runner_name)
    if not runner:
        return {
            "error": f"Unknown runner: {runner_name}",
            "ok": False,
            "ready": False,
            "runners": [],
            "runner": runner_name,
        }
    out = _summarize([_remediate_one_result(runner, runner_states, orchestrator_url)])
    out["timestamp"] = time.time()
    out["orchestrator_url"] = orchestrator_url
    out["remediated"] = True
    out["runner"] = runner_name
    return out


def audit_all_runners(
    runners: List[Dict[str, Any]],
    runner_states: Dict[str, Any],
    orchestrator_url: str,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for runner in runners:
        name = runner.get("name")
        rtype = str(runner.get("runner_type") or "pi").strip().lower()
        if rtype == "windows":
            results.append(
                audit_windows_runner(runner, runner_states.get(name), orchestrator_url)
            )
        else:
            results.append(audit_pi_runner(runner, orchestrator_url))

    out = _summarize(results)
    out["timestamp"] = time.time()
    out["orchestrator_url"] = orchestrator_url
    return out


def remediate_all_runners(
    runners: List[Dict[str, Any]],
    runner_states: Dict[str, Any],
    orchestrator_url: str,
    *,
    pi_only: bool = True,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for runner in runners:
        name = runner.get("name")
        rtype = str(runner.get("runner_type") or "pi").strip().lower()
        if rtype == "windows":
            if pi_only:
                results.append(
                    {
                        "runner": name,
                        "runner_type": "windows",
                        "ok": True,
                        "ready": True,
                        "skipped": True,
                        "actions": [{"step": "skip", "ok": True, "detail": "Windows: no SSH remediate"}],
                        "checks": [],
                    }
                )
            else:
                results.append(
                    audit_windows_runner(runner, runner_states.get(name), orchestrator_url)
                )
        else:
            results.append(remediate_pi_runner(runner, orchestrator_url))

    out = _summarize(results)
    out["timestamp"] = time.time()
    out["orchestrator_url"] = orchestrator_url
    out["remediated"] = True
    return out
