#!/usr/bin/env python3
"""
Pi runner preflight checks (run on the Pi via SSH or locally).

Validates dual-interface safety: management path to orchestrator must stay intact;
lab interface should be down with no active user session when idle.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


def _run(args: List[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)


def _check(
    checks: List[Dict[str, Any]],
    check_id: str,
    title: str,
    ok: bool,
    detail: str,
    *,
    critical: bool = False,
    warning: bool = False,
) -> None:
    if ok:
        status = "pass"
    elif critical:
        status = "fail"
    elif warning:
        status = "warn"
    else:
        status = "fail"
    checks.append(
        {
            "id": check_id,
            "title": title,
            "status": status,
            "critical": critical,
            "detail": detail,
        }
    )


def _parse_route_get(stdout: str) -> Dict[str, str]:
    route: Dict[str, str] = {}
    for line in stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        if "dev" in parts:
            idx = parts.index("dev")
            if idx + 1 < len(parts):
                route["dev"] = parts[idx + 1]
        if "via" in parts:
            idx = parts.index("via")
            if idx + 1 < len(parts):
                route["via"] = parts[idx + 1]
        break
    return route


def _route_to_ip(target_ip: str) -> Dict[str, str]:
    result = _run(["ip", "route", "get", target_ip])
    if result.returncode != 0:
        return {}
    return _parse_route_get(result.stdout)


def _iface_operstate(iface: str) -> str:
    result = _run(["cat", f"/sys/class/net/{iface}/operstate"])
    if result.returncode != 0:
        return "missing"
    return (result.stdout or "").strip().lower()


def _iface_ipv4_addrs(iface: str) -> List[str]:
    result = _run(["ip", "-o", "-4", "addr", "show", "dev", iface])
    addrs: List[str] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "inet" in parts:
            idx = parts.index("inet")
            if idx + 1 < len(parts):
                addrs.append(parts[idx + 1])
    return addrs


def _default_routes() -> List[Dict[str, str]]:
    result = _run(["ip", "-4", "route", "show", "default"])
    routes: List[Dict[str, str]] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if not parts:
            continue
        entry: Dict[str, str] = {"raw": line.strip()}
        if "dev" in parts:
            entry["dev"] = parts[parts.index("dev") + 1]
        if "via" in parts:
            entry["via"] = parts[parts.index("via") + 1]
        routes.append(entry)
    return routes


def _nmcli_exists() -> bool:
    return _run(["which", "nmcli"]).returncode == 0


def _nm_connection_exists(name: str) -> bool:
    if not _nmcli_exists():
        return False
    result = _run(["nmcli", "-t", "-f", "NAME", "connection", "show"])
    names = {(line or "").strip() for line in (result.stdout or "").splitlines()}
    return name in names


def _nm_device_active_connection(device: str) -> str:
    if not _nmcli_exists() or not device:
        return ""
    result = _run(["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "dev", "status"])
    for line in (result.stdout or "").splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == device:
            state = parts[1]
            conn = parts[2]
            if state in ("connected", "connecting") and conn and conn != "--":
                return conn
    return ""


def _sudo_run(args: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return _run(["sudo", *args], timeout=timeout)


def tear_down_lab_interface(lab: str, mgmt: str = "") -> List[Dict[str, Any]]:
    """
    Deactivate NetworkManager lab sessions and bring the lab link down.

    Uses sudo for nmcli — non-root ``nmcli connection down`` often fails with
    "Not authorized to deactivate connections" on Pi runners.
    NEVER touches the management interface (mgmt). Every destructive command
    is guarded by an explicit ``iface != mgmt`` check.
    """
    steps: List[Dict[str, Any]] = []
    lab = (lab or "").strip()
    mgmt = (mgmt or "").strip()
    if not lab or (mgmt and lab == mgmt):
        return steps

    def _is_mgmt(iface: str) -> bool:
        """Return True if iface is (or looks like) the management interface."""
        return bool(mgmt) and iface.strip() == mgmt

    def _record(step: str, ok: bool, detail: str) -> None:
        steps.append({"step": step, "ok": ok, "detail": detail[:200]})

    if _nmcli_exists():
        for conn_name in ("clarion-lab-auth", "clarion-lab-wifi"):
            # Safety: confirm this named connection is NOT on the management interface
            # before bringing it down.
            bound_dev = ""
            try:
                chk = _run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"], timeout=10)
                for ln in (chk.stdout or "").splitlines():
                    p = ln.split(":")
                    if len(p) >= 2 and p[0].strip() == conn_name:
                        bound_dev = p[1].strip()
                        break
            except Exception:
                pass
            if _is_mgmt(bound_dev):
                _record(f"nm_down_{conn_name}", True, f"skipped — {conn_name} is on management interface {mgmt}")
                continue
            proc = _sudo_run(["nmcli", "connection", "down", conn_name], timeout=20)
            detail = (proc.stderr or proc.stdout or "").strip() or conn_name
            lowered = detail.lower()
            ok = proc.returncode == 0 or "not active" in lowered or "not an active connection" in lowered
            _record(f"nm_down_{conn_name}", ok, detail)

        active = _nm_device_active_connection(lab)
        if active and not _is_mgmt(lab):
            proc = _sudo_run(["nmcli", "device", "disconnect", lab], timeout=20)
            detail = (proc.stderr or proc.stdout or "").strip() or f"disconnected {lab}"
            _record("nm_disconnect_lab", proc.returncode == 0, detail)

        result = _run(
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            timeout=15,
        )
        for line in (result.stdout or "").splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == lab and not _is_mgmt(parts[1]):
                conn = parts[0]
                proc = _sudo_run(["nmcli", "connection", "down", conn], timeout=15)
                detail = (proc.stderr or proc.stdout or "").strip() or f"down {conn}"
                _record("nm_down_active_on_lab", proc.returncode == 0, detail)

    if not _is_mgmt(lab) and _iface_operstate(lab) != "down":
        proc = _sudo_run(["ip", "link", "set", lab, "down"], timeout=10)
        detail = (proc.stderr or proc.stdout or "").strip() or f"{lab} -> down"
        _record("lab_link_down", proc.returncode == 0, detail)
    elif _is_mgmt(lab):
        _record("lab_link_down", False, f"REFUSED — {lab} is the management interface")
    else:
        _record("lab_link_down", True, f"{lab} operstate=down")

    lab_addrs = _iface_ipv4_addrs(lab)
    if lab_addrs and not _is_mgmt(lab):
        proc = _sudo_run(["ip", "addr", "flush", "dev", lab], timeout=10)
        detail = (proc.stderr or proc.stdout or "").strip() or f"flushed {lab_addrs}"
        _record("lab_addr_flush", proc.returncode == 0, detail)

    remaining = _nm_device_active_connection(lab)
    _record(
        "lab_nm_idle",
        not remaining,
        remaining or "no active NM connection on lab",
    )
    return steps


def _systemd_runner_id() -> str:
    override = "/etc/systemd/system/clarion-runner.service.d/override.conf"
    if os.path.isfile(override):
        with open(override, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                match = re.search(r'^Environment="RUNNER_ID=([^"]+)"', line.strip())
                if match:
                    return match.group(1).strip()
    result = _run(
        ["systemctl", "show", "clarion-runner", "--property=Environment", "--no-pager"],
        timeout=10,
    )
    if result.returncode == 0:
        for part in (result.stdout or "").replace("Environment=", "").split():
            if part.startswith("RUNNER_ID="):
                return part.split("=", 1)[1].strip()
    return ""


def run_preflight(
    runner_id: str,
    lab_interface: str,
    management_interface: str,
    orchestrator_url: str,
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    lab = (lab_interface or "").strip()
    mgmt = (management_interface or "").strip()
    orch_ip = urllib.parse.urlparse(orchestrator_url).hostname or ""

    # --- Critical: distinct interfaces ---
    distinct = bool(lab and mgmt and lab != mgmt)
    _check(
        checks,
        "interfaces_distinct",
        "Lab and management interfaces differ",
        distinct,
        f"lab={lab or '?'} mgmt={mgmt or '?'}",
        critical=True,
    )

    for iface, label, critical in (
        (mgmt, "Management", True),
        (lab, "Lab", False),
    ):
        if not iface:
            continue
        state = _iface_operstate(iface)
        exists = state != "missing"
        _check(
            checks,
            f"{iface}_exists",
            f"{label} interface {iface} present",
            exists,
            f"operstate={state}",
            critical=critical and label == "Management",
        )

    mgmt_state = _iface_operstate(mgmt) if mgmt else "missing"
    mgmt_up = mgmt_state in ("up", "unknown")
    _check(
        checks,
        "management_interface_up",
        "Management interface is up",
        mgmt_up,
        f"{mgmt} operstate={mgmt_state}",
        critical=True,
    )

    mgmt_addrs = _iface_ipv4_addrs(mgmt) if mgmt else []
    _check(
        checks,
        "management_has_ipv4",
        "Management interface has IPv4",
        bool(mgmt_addrs),
        ", ".join(mgmt_addrs) if mgmt_addrs else "no address",
        critical=True,
    )

    _check(
        checks,
        "management_not_down",
        "Management interface is not administratively down",
        mgmt_state != "down",
        f"{mgmt} operstate={mgmt_state}",
        critical=True,
    )

    lab_state = _iface_operstate(lab) if lab else "missing"
    lab_addrs = _iface_ipv4_addrs(lab) if lab else []
    lab_active_conn = _nm_device_active_connection(lab) if lab else ""

    lab_down = lab_state == "down"
    _check(
        checks,
        "lab_interface_down",
        "Lab interface is down while idle",
        lab_down,
        f"{lab} operstate={lab_state}",
        warning=not lab_down and not lab_addrs,
        critical=bool(lab_addrs) and lab != mgmt,
    )

    _check(
        checks,
        "lab_no_ipv4",
        "No IPv4 address on lab interface (no active user session)",
        not lab_addrs,
        ", ".join(lab_addrs) if lab_addrs else "none",
        critical=bool(lab_addrs),
    )

    _check(
        checks,
        "lab_no_active_nm_user",
        "No active NetworkManager user connection on lab interface",
        not lab_active_conn,
        lab_active_conn or "none",
        critical=bool(lab_active_conn),
    )

    # --- Routing (critical) ---
    if orch_ip:
        orch_route = _route_to_ip(orch_ip)
        via_mgmt = orch_route.get("dev") == mgmt
        _check(
            checks,
            "orchestrator_route_via_mgmt",
            "Route to orchestrator uses management interface",
            via_mgmt,
            f"ip route get {orch_ip} -> dev {orch_route.get('dev', '?')} via {orch_route.get('via', '(on-link)')}",
            critical=True,
        )

        try:
            req = urllib.request.Request(
                orchestrator_url.rstrip("/") + "/api/status",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                reachable = resp.status == 200
        except Exception as exc:
            reachable = False
            reach_detail = str(exc)
        else:
            reach_detail = "HTTP 200 from /api/status"
        _check(
            checks,
            "orchestrator_http_reachable",
            "Orchestrator HTTP reachable from this host",
            reachable,
            reach_detail,
            critical=True,
        )

    defaults = _default_routes()
    default_devs = [r.get("dev") for r in defaults if r.get("dev")]
    lab_is_only_default = len(default_devs) == 1 and default_devs[0] == lab
    lab_in_default = lab in default_devs
    mgmt_in_default = mgmt in default_devs

    _check(
        checks,
        "default_route_not_lab_only",
        "Default route is not pinned only to lab interface",
        not lab_is_only_default,
        "; ".join(r.get("raw", "") for r in defaults) or "no default route",
        critical=True,
    )

    if defaults and orch_ip:
        safe_default = mgmt_in_default or not lab_in_default
        _check(
            checks,
            "default_route_safe_for_mgmt",
            "Default route does not bypass management path",
            safe_default,
            "; ".join(r.get("raw", "") for r in defaults),
            critical=lab_in_default and not mgmt_in_default,
        )

    # --- Lab interface setup (warnings only — not required for idle/routing safety) ---
    if lab == "eth0":
        netplan_path = "/etc/netplan/99-clarion-lab-eth0.yaml"
        present = os.path.isfile(netplan_path)
        _check(
            checks,
            "netplan_lab_eth0",
            "Netplan eth0 lab file present",
            present,
            "present" if present else f"missing (expected {netplan_path})",
            warning=True,
        )
    else:
        _check(
            checks,
            "netplan_lab_eth0",
            "Netplan eth0 lab file (not used)",
            True,
            f"lab interface is {lab}; eth0 netplan not required",
            warning=False,
        )

    if lab == "wlan0":
        wifi_prof = _nm_connection_exists("clarion-lab-wifi")
        _check(
            checks,
            "nm_clarion_lab_wifi",
            "NetworkManager clarion-lab-wifi profile present",
            wifi_prof,
            "present" if wifi_prof else "missing (run setup_pi_runner.sh --lab-interface=wlan0)",
            warning=True,
        )
    else:
        _check(
            checks,
            "nm_clarion_lab_wifi",
            "clarion-lab-wifi profile (not used)",
            True,
            f"lab interface is {lab}; WiFi lab profile not required",
            warning=False,
        )

    _check(
        checks,
        "nm_clarion_lab_auth",
        "NetworkManager clarion-lab-auth profile exists",
        _nm_connection_exists("clarion-lab-auth"),
        "802.1x profile for lab sessions",
        warning=True,
    )

    if lab and _nmcli_exists():
        result = _run(["nmcli", "-t", "-f", "DEVICE,STATE", "dev", "status"])
        unmanaged_lab = False
        for line in (result.stdout or "").splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[0] == lab and parts[1] == "unmanaged":
                unmanaged_lab = True
        _check(
            checks,
            "lab_interface_nm_managed",
            "Lab interface is NetworkManager-managed",
            not unmanaged_lab,
            f"{lab} must not be unmanaged (see NetworkManager.conf)",
            critical=unmanaged_lab,
        )

    # --- Service / agent ---
    svc = _run(["systemctl", "is-active", "clarion-runner"], timeout=10)
    _check(
        checks,
        "clarion_runner_active",
        "clarion-runner service is active",
        (svc.stdout or "").strip() == "active",
        (svc.stdout or svc.stderr or "").strip(),
        critical=True,
    )

    configured_id = _systemd_runner_id()
    id_match = configured_id == runner_id
    _check(
        checks,
        "systemd_runner_id",
        "systemd RUNNER_ID matches orchestrator config",
        id_match,
        f"systemd={configured_id or '?'} expected={runner_id}",
        critical=not id_match,
    )

    critical_failures = [
        c for c in checks if c["status"] == "fail" and c.get("critical")
    ]
    any_fail = any(c["status"] == "fail" for c in checks)
    ok = not critical_failures

    return {
        "runner_id": runner_id,
        "lab_interface": lab,
        "management_interface": mgmt,
        "orchestrator_url": orchestrator_url,
        "ok": ok,
        "ready": ok,
        "checks": checks,
        "summary": {
            "pass": sum(1 for c in checks if c["status"] == "pass"),
            "warn": sum(1 for c in checks if c["status"] == "warn"),
            "fail": sum(1 for c in checks if c["status"] == "fail"),
            "critical_failures": len(critical_failures),
        },
        "any_fail": any_fail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pi runner preflight audit")
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--lab-interface", required=True)
    parser.add_argument("--management-interface", required=True)
    parser.add_argument("--orchestrator-url", required=True)
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()

    report = run_preflight(
        args.runner_id,
        args.lab_interface,
        args.management_interface,
        args.orchestrator_url,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
