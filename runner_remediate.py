#!/usr/bin/env python3
"""
Pi runner safe remediation (run on the Pi via SSH from the orchestrator).

Only touches the LAB interface for teardown and the MANAGEMENT interface for
reachability. Never brings down the management interface.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
from typing import Any, Dict, List

# Reuse read-only helpers from preflight (same directory on the Pi).
NETPLAN_ETH0_YAML = """# Clarion Lab: bring up eth0 with DHCP (lab interface for dot1x).
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      optional: true
"""

from runner_preflight import (
    _iface_ipv4_addrs,
    _iface_operstate,
    _nmcli_exists,
    _route_to_ip,
    _run,
    run_preflight,
    tear_down_lab_interface,
)


def _log(actions: List[Dict[str, Any]], step: str, ok: bool, detail: str) -> None:
    actions.append({"step": step, "ok": ok, "detail": detail})


def _sudo(args: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return _run(["sudo", *args], timeout=timeout)


def _ensure_orchestrator_route(orchestrator_url: str, mgmt: str, actions: List[Dict[str, Any]]) -> None:
    orch_ip = urllib.parse.urlparse(orchestrator_url).hostname or ""
    if not orch_ip or not mgmt:
        _log(actions, "orchestrator_route", False, "missing orchestrator host or mgmt interface")
        return

    current = _route_to_ip(orch_ip)
    if current.get("dev") == mgmt:
        _log(actions, "orchestrator_route", True, f"already via {mgmt}")
        return

    result = _run(["ip", "route", "show", "default", "dev", mgmt])
    gateway = ""
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "via" in parts:
            gateway = parts[parts.index("via") + 1]
            break

    if gateway:
        cmd = [
            "ip", "route", "replace", orch_ip,
            "via", gateway, "dev", mgmt, "proto", "static", "metric", "50",
        ]
    else:
        cmd = ["ip", "route", "replace", orch_ip, "dev", mgmt, "proto", "static", "metric", "50"]

    proc = _sudo(cmd)
    detail = (proc.stderr or proc.stdout or "").strip() or "route installed"
    _log(actions, "orchestrator_route", proc.returncode == 0, detail)


def _ensure_eth0_netplan(lab: str, actions: List[Dict[str, Any]]) -> None:
    """One-time lab wiring for wired runners; safe to create if missing."""
    if lab != "eth0":
        _log(actions, "netplan_eth0", True, f"skipped (lab is {lab})")
        return
    path = "/etc/netplan/99-clarion-lab-eth0.yaml"
    if os.path.isfile(path):
        _log(actions, "netplan_eth0", True, "already present")
        return

    try:
        write_proc = subprocess.run(
            ["sudo", "tee", path],
            input=NETPLAN_ETH0_YAML,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        chmod_proc = _sudo(["chmod", "600", path], timeout=10)
        present = os.path.isfile(path)
        ok = present and write_proc.returncode == 0 and chmod_proc.returncode == 0
        # Do not run netplan apply here: it can disrupt wlan* management paths on dual-interface Pis.
        detail = "created (netplan apply skipped to protect wlan management)" if ok else (
            (write_proc.stderr or write_proc.stdout or "")[:200]
        )
        _log(actions, "netplan_eth0", ok, detail)
    except Exception as exc:
        _log(actions, "netplan_eth0", False, str(exc)[:200])


def _mgmt_is_wireless(mgmt: str) -> bool:
    return mgmt.startswith("wlan") or mgmt.startswith("wl")


def _mgmt_dhcp(mgmt: str, actions: List[Dict[str, Any]]) -> None:
    if not mgmt:
        return
    addrs_before = _iface_ipv4_addrs(mgmt)
    if addrs_before:
        _log(actions, "mgmt_dhcp", True, f"{mgmt} already has {', '.join(addrs_before)}")
        return

    if not _nmcli_exists():
        _log(actions, "mgmt_dhcp", False, "NetworkManager required for management DHCP")
        return

    _run(["nmcli", "radio", "wifi", "on"], timeout=10)
    proc = _run(["nmcli", "device", "connect", mgmt], timeout=45)
    addrs = _iface_ipv4_addrs(mgmt)
    if addrs:
        _log(actions, "mgmt_dhcp", True, f"nmcli connect {mgmt}: {', '.join(addrs)}")
        return

    # Bring up any connection profile already bound to this WiFi/ethernet device.
    result = _run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show"], timeout=15)
    for line in (result.stdout or "").splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == mgmt and parts[0] not in (
            "clarion-lab-auth",
            "clarion-lab-wifi",
        ):
            up = _run(["nmcli", "connection", "up", parts[0]], timeout=45)
            if up.returncode == 0:
                addrs = _iface_ipv4_addrs(mgmt)
                if addrs:
                    _log(actions, "mgmt_dhcp", True, f"nmcli up {parts[0]}: {', '.join(addrs)}")
                    return

    # Wired management only: dhclient is appropriate. Never on wlan* (breaks NM).
    if not _mgmt_is_wireless(mgmt):
        proc = _sudo(["dhclient", "-1", mgmt], timeout=45)
        addrs = _iface_ipv4_addrs(mgmt)
        _log(
            actions,
            "mgmt_dhcp",
            bool(addrs),
            f"dhclient {mgmt}: {', '.join(addrs) if addrs else (proc.stderr or proc.stdout or '')[:200]}",
        )
        return

    _log(
        actions,
        "mgmt_dhcp",
        False,
        f"{mgmt} has no IP; connect WiFi management network manually (remediate will not run dhclient on wlan)",
    )


def run_remediate(
    runner_id: str,
    lab_interface: str,
    management_interface: str,
    orchestrator_url: str,
    *,
    restart_service: bool = True,
    reconfigure_service: bool = True,
) -> Dict[str, Any]:
    actions: List[Dict[str, Any]] = []
    lab = (lab_interface or "").strip()
    mgmt = (management_interface or "").strip()
    base_report = {
        "runner_id": runner_id,
        "lab_interface": lab,
        "management_interface": mgmt,
        "ok": False,
        "ready": False,
        "actions": actions,
        "preflight": None,
    }

    try:
        return _run_remediate_body(
            runner_id,
            lab,
            mgmt,
            orchestrator_url,
            actions,
            base_report,
            restart_service=restart_service,
            reconfigure_service=reconfigure_service,
        )
    except Exception as exc:
        import traceback

        base_report["error"] = str(exc)
        base_report["traceback"] = traceback.format_exc()[-800:]
        base_report["actions"] = actions
        return base_report


def _run_remediate_body(
    runner_id: str,
    lab: str,
    mgmt: str,
    orchestrator_url: str,
    actions: List[Dict[str, Any]],
    base_report: Dict[str, Any],
    *,
    restart_service: bool,
    reconfigure_service: bool,
) -> Dict[str, Any]:
    if not lab or not mgmt or lab == mgmt:
        _log(actions, "validate_interfaces", False, f"invalid lab={lab} mgmt={mgmt}")
        return {
            "runner_id": runner_id,
            "ok": False,
            "actions": actions,
            "preflight": None,
        }

    # 1. Stop agent so it does not fight interface changes.
    proc = _sudo(["systemctl", "stop", "clarion-runner"], timeout=30)
    _log(actions, "stop_clarion_runner", proc.returncode == 0, (proc.stderr or proc.stdout or "stopped").strip()[:200])

    # 2. Tear down LAB only (never touch mgmt). sudo nmcli required on most Pis.
    for step in tear_down_lab_interface(lab, mgmt):
        _log(actions, step["step"], step["ok"], step["detail"])

    # 3. Ensure MANAGEMENT is up (never down).
    if _iface_operstate(mgmt) == "down":
        proc = _sudo(["ip", "link", "set", mgmt, "up"], timeout=10)
        _log(actions, "mgmt_link_up", proc.returncode == 0, f"{mgmt} -> up")
    else:
        _log(actions, "mgmt_link_up", True, f"{mgmt} operstate={_iface_operstate(mgmt)}")

    _mgmt_dhcp(mgmt, actions)

    # 3b. Ensure eth0 netplan exists for wired-lab runners (setup-time artifact).
    _ensure_eth0_netplan(lab, actions)

    # 4. Static route to orchestrator via management.
    _ensure_orchestrator_route(orchestrator_url, mgmt, actions)

    # 5. Fix systemd runner id if override is wrong (optional).
    if reconfigure_service:
        script_dir = "/home/admin/clarion/lab/orchestrator/app"
        cfg = f"{script_dir}/configure_clarion_runner.sh"

        if os.path.isfile(cfg):
            proc = _run(
                [
                    "sudo",
                    "env",
                    f"RUNNER_ID={runner_id}",
                    f"ORCHESTRATOR_URL={orchestrator_url}",
                    "bash",
                    cfg,
                ],
                timeout=60,
            )
            _log(
                actions,
                "configure_clarion_runner",
                proc.returncode == 0,
                (proc.stderr or proc.stdout or "configured")[:200],
            )
            _sudo(["systemctl", "daemon-reload"], timeout=15)

    # 6. Restart agent.
    if restart_service:
        proc = _sudo(["systemctl", "start", "clarion-runner"], timeout=20)
        _log(actions, "start_clarion_runner", proc.returncode == 0, (proc.stderr or proc.stdout or "started")[:200])
        # clarion-lab-auth may autoconnect on wlan0 when the service starts.
        for step in tear_down_lab_interface(lab, mgmt):
            _log(actions, f"post_start_{step['step']}", step["ok"], step["detail"])

    preflight = run_preflight(runner_id, lab, mgmt, orchestrator_url)
    base_report["ok"] = preflight.get("ready", False)
    base_report["ready"] = preflight.get("ready", False)
    base_report["actions"] = actions
    base_report["preflight"] = preflight
    base_report["checks"] = preflight.get("checks", [])
    return base_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Pi runner safe remediation")
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--lab-interface", required=True)
    parser.add_argument("--management-interface", required=True)
    parser.add_argument("--orchestrator-url", required=True)
    parser.add_argument("--no-restart", action="store_true")
    parser.add_argument("--no-reconfigure", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        report = run_remediate(
            args.runner_id,
            args.lab_interface,
            args.management_interface,
            args.orchestrator_url,
            restart_service=not args.no_restart,
            reconfigure_service=not args.no_reconfigure,
        )
    except Exception as exc:
        import traceback

        report = {
            "runner_id": args.runner_id,
            "ok": False,
            "ready": False,
            "error": str(exc),
            "traceback": traceback.format_exc()[-800:],
            "actions": [],
            "preflight": None,
            "checks": [],
        }
    print(json.dumps(report, indent=2))
    return 0 if report.get("ready") else 1


if __name__ == "__main__":
    sys.exit(main())
