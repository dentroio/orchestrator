#!/usr/bin/env python3
"""
Clarion Lab Runner Agent (service-based)

Runs as a systemd service on each Pi. Polls the orchestrator for assignments;
orchestrator User Identities are the master list and are sent per assignment.
Executes one session per assignment (identity + traffic) then acks so the
orchestrator can assign the next identity. No SSH needed for session start.

Usage:
  python3 runner_agent.py --orchestrator-url http://192.168.20.95:5000 --runner-id pi-runner-1

Install as service: see lab/clarion-runner.service (systemd unit).
"""

import argparse
import ipaddress
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [RunnerAgent] - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

try:
    import urllib.error
    import urllib.parse
    import urllib.request
except ImportError:
    urllib = None

# ---------------------------------------------------------------------------
# Module-level state shared between the main loop and the signal handler.
# ---------------------------------------------------------------------------
_current_session_proc = None   # subprocess.Popen tracking the running session
_cached_lab_iface = None       # lab interface from the last assignment
_cached_mgmt_iface = None      # management interface from the last assignment
PI_AGENT_VERSION = "2026.05.19.1"
RECENT_LOG_LINES = deque(maxlen=80)
_heartbeat_status = "idle"
_heartbeat_lock = threading.Lock()
_AGENT_STARTED_AT = time.time()
_AGENT_HEALTH = {
    "phase": "idle",
    "last_error": "",
    "last_session_result": "",
    "uptime_s": 0,
}
_AGENT_HEALTH_LOCK = threading.Lock()
_RESTART_REQUESTED = threading.Event()


class LogStreamHandler(logging.Handler):
    """Append formatted log records for orchestrator log streaming."""

    def emit(self, record):
        try:
            RECENT_LOG_LINES.append(self.format(record))
        except Exception:
            pass


_log_stream_handler = LogStreamHandler()
_log_stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_log_stream_handler)


def get_code_version():
    """Report pi-agent version; fall back to lab VERSION when present."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                lab_ver = f.read().strip()
                if lab_ver:
                    return f"pi-agent/{PI_AGENT_VERSION} (lab {lab_ver})"
    except Exception:
        pass
    return f"pi-agent/{PI_AGENT_VERSION}"


def _get_system_stats():
    try:
        load1, _, _ = os.getloadavg()
    except OSError:
        load1 = 0.0

    mem_percent = 0.0
    try:
        mem_total = 0
        mem_avail = 0
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1])
        if mem_total > 0:
            mem_percent = ((mem_total - mem_avail) / mem_total) * 100.0
    except OSError:
        pass

    rssi = 0
    try:
        with open("/proc/net/wireless", "r", encoding="utf-8") as f:
            for line in f.readlines():
                if ":" not in line:
                    continue
                parts = line.split()
                if len(parts) > 3:
                    rssi = float(parts[3].replace(".", ""))
    except (OSError, ValueError):
        pass

    return {
        "cpu_load": load1,
        "memory_percent": round(mem_percent, 1),
        "rssi": rssi,
    }


def _get_network_details(interface):
    details = {"ssid": "unknown", "ip": "unknown", "mac": "unknown"}
    if not interface:
        return details
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", interface],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if "inet" in parts:
                idx = parts.index("inet")
                if idx + 1 < len(parts):
                    details["ip"] = parts[idx + 1].split("/")[0]
                    break
    except Exception:
        pass
    try:
        mac_path = f"/sys/class/net/{interface}/address"
        if os.path.isfile(mac_path):
            with open(mac_path, "r", encoding="utf-8") as f:
                details["mac"] = f.read().strip() or details["mac"]
    except OSError:
        pass
    if interface.startswith("wlan"):
        try:
            result = subprocess.run(
                ["iwgetid", interface, "-r"],
                capture_output=True,
                text=True,
                check=False,
            )
            ssid = (result.stdout or "").strip()
            if ssid:
                details["ssid"] = ssid
        except Exception:
            pass
    return details


def _set_heartbeat_status(status):
    global _heartbeat_status
    with _heartbeat_lock:
        _heartbeat_status = status


def _get_heartbeat_status():
    with _heartbeat_lock:
        return _heartbeat_status


def _update_agent_health(**kwargs):
    with _AGENT_HEALTH_LOCK:
        _AGENT_HEALTH.update(kwargs)
        _AGENT_HEALTH["uptime_s"] = int(time.time() - _AGENT_STARTED_AT)


def _get_agent_health():
    with _AGENT_HEALTH_LOCK:
        out = dict(_AGENT_HEALTH)
        out["uptime_s"] = int(time.time() - _AGENT_STARTED_AT)
        return out


def _handle_control_response(body: str, base_url: str, runner_id: str) -> None:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return
    control = data.get("control") or {}
    if control.get("restart_requested"):
        reason = control.get("restart_reason") or "orchestrator"
        logger.warning("Orchestrator requested agent restart: %s", reason)
        _update_agent_health(phase="restarting", last_error=reason)
        _send_control_ack(runner_id, base_url)
        _RESTART_REQUESTED.set()


def _send_control_ack(runner_id: str, base_url: str) -> None:
    if not runner_id or not base_url:
        return
    url = f"{base_url.rstrip('/')}/api/runner/telemetry"
    payload = {
        "runner_id": runner_id,
        "status": _get_heartbeat_status(),
        "control_ack": {"restart": True},
        "health": _get_agent_health(),
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception as exc:
        logger.debug("Control ack failed: %s", exc)


def _session_subprocess_running() -> bool:
    proc = _current_session_proc
    return proc is not None and proc.poll() is None


def send_heartbeat(base_url, runner_id, management_interface=None):
    """POST lightweight telemetry so the orchestrator can show Online/alive for idle Pis."""
    if _session_subprocess_running():
        # auto_lab_runner owns telemetry during an active session; avoid alternating status/version.
        return
    if _RESTART_REQUESTED.is_set():
        logger.info("Exiting for orchestrator-requested restart")
        sys.exit(0)
    mgmt_iface = management_interface or _cached_mgmt_iface
    if not mgmt_iface:
        try:
            orch_ip = urllib.parse.urlparse(base_url).hostname
            if orch_ip:
                mgmt_iface = _resolve_management_interface(orch_ip)
        except Exception:
            mgmt_iface = None
    if not mgmt_iface:
        mgmt_iface = "wlan0"

    payload = {
        "runner_id": runner_id,
        "status": _get_heartbeat_status(),
        "platform": "raspberry-pi",
        "stats": _get_system_stats(),
        "network": _get_network_details(mgmt_iface),
        "traffic_history": [],
        "timestamp": time.time(),
        "code_version": get_code_version(),
        "log_lines": list(RECENT_LOG_LINES),
        "health": _get_agent_health(),
    }
    url = f"{base_url.rstrip('/')}/api/runner/telemetry"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            _handle_control_response(body, base_url, runner_id)
    except Exception as exc:
        logger.warning("Heartbeat telemetry failed: %s", exc)
        _update_agent_health(last_error=str(exc))


class HeartbeatReporter(threading.Thread):
    """Background heartbeat while runner_agent polls (Windows-style alive check)."""

    def __init__(self, base_url, runner_id, interval=10):
        super().__init__(daemon=True)
        self.base_url = base_url
        self.runner_id = runner_id
        self.interval = max(5, int(interval))
        self.running = False

    def run(self):
        self.running = True
        logger.info("Heartbeat telemetry started (every %ss)", self.interval)
        while self.running:
            try:
                send_heartbeat(self.base_url, self.runner_id)
            except Exception as exc:
                logger.warning("Heartbeat thread error: %s", exc)
            time.sleep(self.interval)

    def stop(self):
        self.running = False


def _handle_shutdown(signum, frame):
    """SIGTERM / SIGINT handler: stop the running session, clean up the lab
    interface, and exit cleanly.  The management interface is NEVER touched."""
    global _current_session_proc, _cached_lab_iface, _cached_mgmt_iface

    logger.info("Shutdown signal %d received — stopping agent", signum)

    # 1. Terminate the running session subprocess gracefully.
    proc = _current_session_proc
    if proc is not None and proc.poll() is None:
        logger.info("Terminating running session subprocess (pid=%d)...", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()

    # 2. Bring down the lab interface so it does not stay up with a stale
    #    identity. ONLY do this when the lab interface differs from the
    #    management interface — we must never take down the management link.
    lab = _cached_lab_iface
    mgmt = _cached_mgmt_iface
    if lab and mgmt and lab != mgmt:
        logger.info("Bringing down lab interface %s on shutdown", lab)
        _down_lab_interface(lab, mgmt)

    sys.exit(0)


def get_assignment(base_url: str, runner_id: str) -> dict:
    """GET pending assignment from orchestrator. Returns assignment dict or None."""
    url = f"{base_url.rstrip('/')}/api/runner/assignment/{runner_id}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("assignment")
    except Exception as e:
        logger.debug("get_assignment failed: %s", e)
        return None


def ack_assignment(base_url: str, runner_id: str, *, session_result: str = "ok") -> bool:
    """POST ack after completing session."""
    url = f"{base_url.rstrip('/')}/api/runner/assignment/{runner_id}/ack"
    try:
        payload = json.dumps({"session_result": session_result}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode() == 200
    except Exception as e:
        logger.warning("ack_assignment failed: %s", e)
        return False


def run_one_shot_from_assignment(assignment: dict, runner_id: str, base_url: str, script_path: str) -> bool:
    """Run auto_lab_runner.py one-shot with the same args the orchestrator would pass via SSH."""
    global _current_session_proc

    identity = assignment.get("identity") or {}
    session_duration = assignment.get("session_duration", 300)
    access_urls = assignment.get("access_urls") or []
    traffic_method = assignment.get("traffic_method")
    policy_test_plan = assignment.get("policy_test_plan") or {}
    traffic_min_sleep = assignment.get("traffic_min_sleep")
    traffic_max_sleep = assignment.get("traffic_max_sleep")
    interface = assignment.get("interface", "eth0")
    management_interface = assignment.get("management_interface", "wlan0")

    username = identity.get("username", "")
    password = identity.get("password", "")
    device_name = identity.get("device_name", "")
    mac = identity.get("mac", "")
    ssid = identity.get("ssid", "")
    persona = identity.get("persona") or identity.get("department", "")
    os_type = identity.get("os", "")

    cmd = [
        sys.executable,
        script_path,
        "--interface", interface,
        "--session-duration", str(session_duration),
        "--runner-id", runner_id,
        "--management-interface", management_interface,
        "--one-shot",
        "--no-cooldown",
        "--orchestrator-url", base_url,
    ]
    if username:
        cmd.extend(["--username", username])
    if password:
        cmd.extend(["--password", password])
    if device_name:
        cmd.extend(["--device-name", device_name])
    if mac:
        cmd.extend(["--mac", mac])
    if ssid:
        cmd.extend(["--ssid", ssid])
    if access_urls:
        cmd.extend(["--access-urls", ",".join(access_urls)])
    if traffic_method:
        cmd.extend(["--traffic-method", str(traffic_method)])
    if policy_test_plan:
        try:
            cmd.extend(["--policy-test-plan", json.dumps(policy_test_plan)])
        except Exception:
            logger.warning("Failed to serialize policy_test_plan for assignment")
    if traffic_min_sleep is not None:
        cmd.extend(["--traffic-min-sleep", str(traffic_min_sleep)])
    if traffic_max_sleep is not None:
        cmd.extend(["--traffic-max-sleep", str(traffic_max_sleep)])
    if persona:
        cmd.extend(["--persona", persona])
    if os_type:
        cmd.extend(["--os", os_type])

    logger.info("Running one-shot session for %s", username or device_name or "unknown")
    ident_label = username or device_name or "unknown"
    _set_heartbeat_status(f"active ({ident_label})")
    _update_agent_health(phase="active", last_error="")
    try:
        proc = subprocess.Popen(cmd)
        _current_session_proc = proc
        try:
            proc.wait(timeout=session_duration + 300)
        except subprocess.TimeoutExpired:
            logger.error("Session timed out — killing subprocess")
            proc.kill()
            proc.wait()
            _update_agent_health(last_session_result="failed", last_error="session timeout")
            return False
        ok = proc.returncode == 0
        _update_agent_health(last_session_result="ok" if ok else "failed")
        return ok
    except Exception as e:
        logger.error("Session failed: %s", e)
        _update_agent_health(last_session_result="failed", last_error=str(e))
        return False
    finally:
        _current_session_proc = None
        _set_heartbeat_status("idle")
        _update_agent_health(phase="idle")


def _down_lab_interface(lab: str, mgmt: str):
    """Deactivate lab NM profiles and bring the lab link down (never touch mgmt)."""
    if not lab or not mgmt or lab == mgmt:
        return
    try:
        from runner_preflight import tear_down_lab_interface

        steps = tear_down_lab_interface(lab, mgmt)
        failed = [s for s in steps if not s.get("ok")]
        if failed:
            logger.debug(
                "Lab teardown %s: %s",
                lab,
                "; ".join(f"{s['step']}={s['detail']}" for s in failed[:3]),
            )
        else:
            logger.debug("Lab interface %s idle (NM + link down)", lab)
    except Exception as e:
        logger.debug("Could not tear down lab interface %s: %s", lab, e)
        try:
            subprocess.run(
                ["sudo", "ip", "link", "set", lab, "down"],
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass


def _run_command(args):
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _parse_route_get(stdout: str):
    route = {}
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
        if parts[0] == "local":
            route["local"] = True
        break
    return route


def _get_route_to_ip(target_ip: str):
    result = _run_command(["ip", "route", "get", target_ip])
    if result.returncode != 0:
        return {}
    return _parse_route_get(result.stdout)


def _get_default_gateway_for_interface(interface: str) -> str:
    result = _run_command(["ip", "route", "show", "default", "dev", interface])
    for line in result.stdout.splitlines():
        parts = line.split()
        if "via" in parts:
            idx = parts.index("via")
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return ""


def _interface_has_onlink_route(interface: str, target_ip: str) -> bool:
    try:
        target = ipaddress.ip_address(target_ip)
    except ValueError:
        return False
    result = _run_command(["ip", "-o", "-4", "addr", "show", "dev", interface])
    for line in result.stdout.splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        idx = parts.index("inet")
        if idx + 1 >= len(parts):
            continue
        try:
            network = ipaddress.ip_interface(parts[idx + 1]).network
        except ValueError:
            continue
        if target in network:
            return True
    return False


def _resolve_management_interface(orchestrator_ip: str, preferred_interface: str = None) -> str:
    if preferred_interface:
        return preferred_interface
    current = _get_route_to_ip(orchestrator_ip)
    if current.get("dev"):
        return current["dev"]
    for candidate in ("wlan0", "eth0"):
        if _get_default_gateway_for_interface(candidate):
            return candidate
    return ""


def ensure_orchestrator_route(orchestrator_url: str, management_interface: str = None):
    """Ensure a static route to the Orchestrator via the management interface."""
    orch_ip = urllib.parse.urlparse(orchestrator_url).hostname
    if not orch_ip:
        return

    try:
        mgmt_iface = _resolve_management_interface(orch_ip, preferred_interface=management_interface)
        if not mgmt_iface:
            logger.debug("Could not determine management interface for orchestrator route")
            return

        current = _get_route_to_ip(orch_ip)
        current_dev = current.get("dev")
        current_via = current.get("via", "")
        desired_gateway = _get_default_gateway_for_interface(mgmt_iface)

        if desired_gateway:
            if current_dev == mgmt_iface and current_via == desired_gateway:
                return
            cmd = [
                "sudo", "ip", "route", "replace", orch_ip,
                "via", desired_gateway,
                "dev", mgmt_iface,
                "proto", "static",
                "metric", "50",
            ]
        elif _interface_has_onlink_route(mgmt_iface, orch_ip):
            if current_dev == mgmt_iface and not current_via:
                return
            cmd = [
                "sudo", "ip", "route", "replace", orch_ip,
                "dev", mgmt_iface,
                "proto", "static",
                "metric", "50",
            ]
        else:
            logger.debug(
                "No usable gateway found for management interface %s when routing to %s",
                mgmt_iface,
                orch_ip,
            )
            return

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.warning(
                "Failed to enforce static route to orchestrator via %s: %s",
                mgmt_iface,
                (result.stderr or result.stdout).strip(),
            )
    except Exception as e:
        logger.debug("Failed to enforce static route: %s", e)


def main():
    global _cached_lab_iface, _cached_mgmt_iface

    parser = argparse.ArgumentParser(description="Clarion Lab Runner Agent")
    parser.add_argument("--orchestrator-url", required=True, help="Orchestrator base URL (e.g. http://192.168.20.95:5000)")
    parser.add_argument("--runner-id", required=True, help="Runner name (e.g. pi-runner-1)")
    parser.add_argument("--poll-interval", type=int, default=10, help="Seconds between polls when no assignment (default 10)")
    parser.add_argument("--script", default=None, help="Path to auto_lab_runner.py (default: same dir as this script)")
    args = parser.parse_args()

    script_path = args.script
    if not script_path:
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_lab_runner.py")
    if not os.path.exists(script_path):
        logger.error("auto_lab_runner.py not found at %s", script_path)
        sys.exit(1)

    # Register shutdown handlers so systemd stop / Ctrl-C always cleans up.
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info("Runner agent started: runner_id=%s, orchestrator=%s", args.runner_id, args.orchestrator_url)
    logger.info("Agent version: %s", get_code_version())

    heartbeat = HeartbeatReporter(args.orchestrator_url, args.runner_id, interval=args.poll_interval)
    heartbeat.start()

    while True:
        try:
            ensure_orchestrator_route(args.orchestrator_url, management_interface=_cached_mgmt_iface)
            assignment = get_assignment(args.orchestrator_url, args.runner_id)

            if assignment and isinstance(assignment, dict) and assignment.get("identity"):
                # Persist interface names for signal handler and idle loop.
                _cached_lab_iface = assignment.get("interface", _cached_lab_iface)
                _cached_mgmt_iface = assignment.get("management_interface", _cached_mgmt_iface)

                success = run_one_shot_from_assignment(
                    assignment, args.runner_id, args.orchestrator_url, script_path
                )

                # Always bring the lab interface down between sessions.
                # auto_lab_runner does this in its own finally block; this is a
                # belt-and-suspenders guard that ensures the interface is clean
                # before the orchestrator assigns the next identity.
                # The management interface is NEVER touched here.
                _down_lab_interface(_cached_lab_iface, _cached_mgmt_iface)

                # Always ack a completed one-shot, even when the session reports
                # DHCP/auth failure. Otherwise one bad identity is retried forever
                # and the orchestrator eventually marks the runner stuck.
                result = "ok" if success else "failed"
                if ack_assignment(args.orchestrator_url, args.runner_id, session_result=result):
                    logger.info("Assignment acknowledged for %s (result=%s)", args.runner_id, result)
                else:
                    logger.warning("Session completed but ACK failed; will retry ACK without replaying session")
                    time.sleep(15)

            else:
                # Idle: keep the lab interface down while waiting for work.
                if _cached_lab_iface and _cached_mgmt_iface:
                    # We know both interfaces from a prior assignment — safe to use.
                    _down_lab_interface(_cached_lab_iface, _cached_mgmt_iface)
                else:
                    # Before the first assignment we do not yet have a reliable
                    # lab/mgmt interface mapping. Leave both interfaces alone so
                    # the static management path to the orchestrator stays intact.
                    logger.debug("No cached interface map yet; leaving interfaces unchanged while idle")

                time.sleep(args.poll_interval)

        except KeyboardInterrupt:
            logger.info("Stopped by user")
            break
        except Exception as e:
            logger.exception("Loop error: %s", e)
            time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
