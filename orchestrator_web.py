#!/usr/bin/env python3
"""
Clarion Lab Orchestrator UI

Web dashboard to control and monitor the lab orchestrator.
"""

from flask import Flask, render_template, jsonify, request
import threading
import os
import json
import logging
from typing import Optional, Tuple, Dict, Any
import time
from collections import deque
import lab_orchestrator
from lab_orchestrator import LabOrchestrator
import ad_connector
import mac_oui_database
import db
import validator_engine
import runner_audit
import runner_health_controller
import launch_presets
import launch_verify

# Ring buffer of recent orchestrator log lines for UI streaming (last 300 lines)
ORCHESTRATOR_LOG_LINES = deque(maxlen=300)


class OrchestratorLogHandler(logging.Handler):
    """Appends formatted log records to ORCHESTRATOR_LOG_LINES for the UI."""
    def emit(self, record):
        try:
            msg = self.format(record)
            ORCHESTRATOR_LOG_LINES.append(msg)
        except Exception:
            pass


# Attach to lab_orchestrator logger so we capture orchestration logs (runner statuses, assignments, etc.)
_orch_log_handler = OrchestratorLogHandler()
_orch_log_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logging.getLogger("lab_orchestrator").addHandler(_orch_log_handler)

# Import templates directory if it exists, otherwise use in-line templates (for simplicity in single file, but standard is templates/)
# We'll expect a templates/dashboard.html

app = Flask(__name__)
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)


@app.after_request
def _set_csp(response):
    """Allow dashboard scripts to run; avoid CSP blocking Start/Stop and Bootstrap."""
    # script-src: 'unsafe-eval' so Bootstrap (or strict env CSP) does not block our handlers
    val = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'self';"
    )
    response.headers["Content-Security-Policy"] = val
    return response


# Global Orchestrator Instance (config from DB)
ORCHESTRATOR = None
ORCHESTRATOR_THREAD = None
HEALTH_CONTROLLER = None


def _health_settings() -> dict:
    return runner_health_controller.merge_health_settings(db.get_config("runner_health_settings"))


def get_health_controller():
    global HEALTH_CONTROLLER
    if HEALTH_CONTROLLER is None:
        HEALTH_CONTROLLER = runner_health_controller.get_health_controller(
            get_orchestrator,
            _canonical_orchestrator_url,
            _health_settings,
        )
    return HEALTH_CONTROLLER


def _ensure_health_controller_started():
    controller = get_health_controller()
    controller.start()

def _ensure_migrated():
    """One-time migration from JSON files into DB if present."""
    db.migrate_from_json_if_present()

def _build_config_from_db():
    """Build config dict from DB for LabOrchestrator (runners, identities, services, etc.)."""
    _ensure_migrated()
    full = db.get_full_config()
    full["identities"] = db.get_identities()
    return full

def get_orchestrator():
    global ORCHESTRATOR
    if ORCHESTRATOR is None:
        config = _build_config_from_db()
        ORCHESTRATOR = LabOrchestrator(config)
        _ensure_health_controller_started()
    return ORCHESTRATOR


def _clarion_api_url() -> str:
    env = os.environ.get("CLARION_API_URL", "").strip()
    if env:
        return env.rstrip("/")
    return (db.get_config("clarion_api_url") or "http://192.168.30.2:5000/api").strip().rstrip("/")


def _canonical_orchestrator_url() -> str:
    """
    URL Pi runners should use for API/telemetry (configure_clarion_runner.sh).

    Do not use request.url_root alone — browsers or proxies may use :8080 while the
    orchestrator listens on :5000, which breaks agent heartbeats after remediate.
    """
    env = os.environ.get("ORCHESTRATOR_URL", "").strip()
    if env:
        return env.rstrip("/")
    db_url = (db.get_config("orchestrator_url") or "").strip()
    if db_url:
        return db_url.rstrip("/")
    return db.DEFAULT_ORCHESTRATOR_URL.rstrip("/")

def _get_expected_code_version():
    """Read expected runner code version from lab/VERSION (orchestrator's deploy = source of truth)."""
    try:
        lab_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(lab_dir, "VERSION")
        if os.path.isfile(path):
            with open(path, "r") as f:
                return f.read().strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def _get_persona_options():
    """Load persona options from DB for template embedding."""
    try:
        identities = db.get_identities()
        personas = set()
        for ident in identities:
            if ident.get("persona"):
                personas.add(ident["persona"])
            if ident.get("department"):
                personas.add(ident["department"])
            for g in ident.get("groups", []):
                personas.add(g)
        for p in (db.get_config("custom_personas") or []):
            personas.add(p)
        return sorted(list(personas))
    except Exception:
        return []


def _is_ip_address(value: str) -> bool:
    """Return True when value is an IPv4 or IPv6 address."""
    import ipaddress
    try:
        ipaddress.ip_address((value or "").strip())
        return True
    except Exception:
        return False


def _is_private_ip(value: str) -> bool:
    import ipaddress
    try:
        return ipaddress.ip_address((value or "").strip()).is_private
    except Exception:
        return False


# When orchestrator DNS does not match runner DNS, map known lab vhosts to the
# hosting IPs documented in lab/BACKEND_SERVERS.md.
_KNOWN_NETLAB_HOST_TO_IP = {
    "www.netlab.net": "192.168.40.2",
    "finance.netlab.net": "192.168.30.2",
    "code.netlab.net": "192.168.30.2",
    "thehub.netlab.net": "192.168.30.2",
    "engineering.netlab.net": "192.168.30.2",
    "mab.netlab.net": "192.168.30.2",
    "cmdb.netlab.net": "192.168.30.2",
    "iotdev.netlab.net": "192.168.31.2",
}


def _resolve_lab_server_ip(target: str) -> Tuple[Optional[str], str]:
    """
    Map a service ``target`` (IP or hostname) to an internal lab server IP when possible.

    Returns:
        (ip_or_none, reason) where reason is one of:
        ``private_ip``, ``netlab_static``, ``netlab_dns``, ``external``, ``unresolved``.
    """
    import ipaddress
    import socket

    raw = (target or "").strip()
    if not raw:
        return None, "unresolved"

    if _is_ip_address(raw):
        try:
            ip = str(ipaddress.ip_address(raw))
        except Exception:
            return None, "unresolved"
        if _is_private_ip(ip):
            return ip, "private_ip"
        return ip, "external"

    host_key = raw.lower().split("/")[0].split(":")[0]

    if host_key in _KNOWN_NETLAB_HOST_TO_IP:
        return _KNOWN_NETLAB_HOST_TO_IP[host_key], "netlab_static"

    if host_key.endswith(".netlab.net"):
        try:
            resolved = socket.gethostbyname(host_key)
            if _is_private_ip(resolved):
                return resolved, "netlab_dns"
        except Exception:
            pass
        return None, "unresolved"

    try:
        resolved = socket.gethostbyname(host_key)
        if _is_private_ip(resolved):
            return resolved, "netlab_dns"
    except Exception:
        pass

    return None, "external"


def _build_service_url(service: dict) -> str:
    """Build a URL-like string from a service definition."""
    protocol = str(service.get("protocol") or "http").strip().lower()
    target = str(service.get("target") or "").strip()
    if not target:
        return ""
    port = service.get("port")
    path = str(service.get("path") or "").strip()
    if path and not path.startswith("/"):
        path = f"/{path}"
    if not path:
        path = "/"
    if port in (None, ""):
        return f"{protocol}://{target}{path}"
    return f"{protocol}://{target}:{port}{path}"


def _build_service_inventory() -> dict:
    """
    Group services by **internal lab server IP** (private addresses and resolvable
    ``*.netlab.net`` vhosts). Internet / unresolvable targets are listed separately
    so the UI can show internal **hosts** on the left, not raw URLs/FQDNs.
    """
    import ipaddress

    services = db.get_config("services") or []
    connectivity = db.get_config("connectivity_policies") or {}
    runners = db.get_config("runners") or []

    service_to_personas = {}
    for persona, svc_ids in (connectivity or {}).items():
        for svc_id in (svc_ids or []):
            service_to_personas.setdefault(svc_id, set()).add(persona)

    persona_to_runners = {}
    for runner in runners:
        runner_name = runner.get("name")
        rtype = str(runner.get("runner_type") or "pi").strip().lower()
        if rtype == "windows":
            personas = [
                str(
                    runner.get("primary_persona")
                    or runner.get("windows_persona")
                    or (runner.get("persona_set") or [None])[0]
                    or ""
                ).strip()
            ]
        else:
            personas = [str(p).strip() for p in (runner.get("persona_set") or []) if str(p).strip()]
        for persona in personas:
            if persona:
                persona_to_runners.setdefault(persona, set()).add(runner_name)

    internal: dict[str, dict] = {}
    external: dict[str, dict] = {}

    def _append(bucket_map: dict, key: str, factory):
        if key not in bucket_map:
            bucket_map[key] = factory()
        return bucket_map[key]

    for svc in services:
        target = str(svc.get("target") or "").strip() or "(no target)"
        resolved_ip, reason = _resolve_lab_server_ip(target)

        if resolved_ip and reason in ("private_ip", "netlab_static", "netlab_dns"):
            bucket = _append(
                internal,
                resolved_ip,
                lambda: {
                    "server_key": resolved_ip,
                    "host_type": "ip",
                    "resolution": reason,
                    "vhosts": set(),
                    "service_count": 0,
                    "protocols": set(),
                    "ports": set(),
                    "personas": set(),
                    "runners": set(),
                    "services": [],
                },
            )
            if not _is_ip_address(target) and target != resolved_ip:
                bucket["vhosts"].add(target)
        else:
            ext_key = target if target != "(no target)" else "(no target)"
            bucket = _append(
                external,
                ext_key,
                lambda k=ext_key: {
                    "target": k,
                    "host_type": "external" if reason == "external" else "unresolved",
                    "resolution": reason,
                    "service_count": 0,
                    "protocols": set(),
                    "ports": set(),
                    "personas": set(),
                    "runners": set(),
                    "services": [],
                },
            )

        svc_id = svc.get("id")
        personas = sorted(list(service_to_personas.get(svc_id, set())))
        runner_names = set()
        for persona in personas:
            runner_names.update(persona_to_runners.get(persona, set()))

        bucket["service_count"] += 1
        bucket["protocols"].add(str(svc.get("protocol") or "").lower())
        if svc.get("port") not in (None, ""):
            bucket["ports"].add(svc.get("port"))
        bucket["personas"].update(personas)
        bucket["runners"].update(runner_names)
        bucket["services"].append(
            {
                "id": svc.get("id"),
                "name": svc.get("name"),
                "protocol": svc.get("protocol"),
                "port": svc.get("port"),
                "path": svc.get("path"),
                "url": _build_service_url(svc),
                "original_target": target,
                "resolved_server_ip": resolved_ip,
                "resolution": reason,
                "allowed_personas": personas,
                "allowed_runners": sorted(list(runner_names)),
                "config": svc,
            }
        )

    def _finalize(rows: dict[str, dict]) -> list:
        out = []
        for _, row in rows.items():
            row["protocols"] = sorted([p for p in row["protocols"] if p])
            row["ports"] = sorted(list(row["ports"]))
            row["personas"] = sorted(list(row["personas"]))
            row["runners"] = sorted(list(row["runners"]))
            row["services"] = sorted(
                row["services"], key=lambda s: ((s.get("name") or "").lower(), str(s.get("id") or "")))
            if "vhosts" in row:
                row["vhosts"] = sorted(list(row["vhosts"]))
            out.append(row)
        return out

    internal_rows = _finalize(internal)
    external_rows = _finalize(external)

    def _ip_sort_key(ip: str):
        try:
            return (0, ipaddress.ip_address(ip).packed)
        except Exception:
            return (1, ip.lower())

    internal_rows.sort(key=lambda r: _ip_sort_key(r["server_key"]))
    external_rows.sort(key=lambda r: str(r.get("target") or "").lower())

    return {"internal_servers": internal_rows, "external_targets": external_rows}


@app.route("/")
def dashboard():
    """
    Render the main dashboard template.
    
    Returns:
        Rendered HTML template for the dashboard.
    """
    persona_options = _get_persona_options()
    return render_template("dashboard.html", persona_options=persona_options)

@app.route("/api/status")
def get_status():
    """
    Get the current status of the orchestrator.
    
    Returns:
        JSON response containing orchestration status and expected code version.
    """
    orc = get_orchestrator()
    status = orc.get_status()
    status["expected_code_version"] = _get_expected_code_version()
    try:
        status["runner_health"] = get_health_controller().get_runner_health()
    except Exception:
        status["runner_health"] = {}
    return jsonify(status)


@app.route("/api/runners/health", methods=["GET"])
def get_runners_health():
    """Aggregated runner health/recovery state from the health controller."""
    runner_name = (request.args.get("runner") or "").strip() or None
    controller = get_health_controller()
    payload = controller.get_runner_health(runner_name)
    if runner_name and payload.get("error"):
        return jsonify(payload), 404
    return jsonify(payload)


@app.route("/api/runners/health/settings", methods=["GET", "PUT"])
def runners_health_settings():
    """Read or update automated recovery settings."""
    if request.method == "GET":
        return jsonify(runner_health_controller.merge_health_settings(db.get_config("runner_health_settings")))
    data = request.get_json(force=True, silent=True) or {}
    merged = runner_health_controller.merge_health_settings(data)
    db.set_config("runner_health_settings", merged)
    return jsonify(merged)


@app.route("/api/runners/health/events", methods=["GET"])
def runners_health_events():
    """Recent health controller actions (remediation, circuit breaker, etc.)."""
    limit = request.args.get("limit", 100, type=int)
    controller = get_health_controller()
    return jsonify({"events": controller.read_recent_events(limit=limit)})


@app.route("/api/runners/health/evaluate", methods=["POST"])
def runners_health_evaluate_now():
    """Trigger an immediate health evaluation cycle."""
    controller = get_health_controller()
    summary = controller.evaluate_all()
    return jsonify(summary)


@app.route("/api/runners/health/reset", methods=["POST"])
def runners_health_reset():
    """Clear circuit breakers and failure counters (all runners or runner_names list)."""
    data = request.get_json(force=True, silent=True) or {}
    names = data.get("runner_names")
    if names is not None and not isinstance(names, list):
        return jsonify({"error": "runner_names must be a list"}), 400
    controller = get_health_controller()
    result = controller.reset_runner_health(names)
    controller.evaluate_all()
    return jsonify(result)


@app.route("/api/runners/management-hosts", methods=["GET"])
def get_runner_management_hosts():
    """
    Pi runner management SSH/orchestrator-reachability addresses from DB config.

    Pi runners: configured ``host`` on management network (192.168.1.x wireless or
    192.168.20.x wired). Windows runners: single interface on employee VLAN (192.168.12.x).
    Do not infer Pi management IPs from lab-session telemetry.
    """
    runners = db.get_config("runners") or []
    out: Dict[str, Any] = {}
    for runner in runners:
        name = str(runner.get("name") or "").strip()
        if not name:
            continue
        rtype = str(runner.get("runner_type") or "pi").strip().lower()
        host = str(runner.get("host") or "").strip()
        entry = {
            "runner_type": rtype,
            "host": host or None,
            "management_interface": runner.get("management_interface"),
            "lab_interface": runner.get("interface"),
            "user": runner.get("user") or "admin",
        }
        out[name] = entry
    return jsonify({"runners": out, "timestamp": time.time()})


@app.route("/api/logs")
def get_logs():
    """Return recent orchestrator log lines for UI streaming (last 300)."""
    return jsonify({"log_lines": list(ORCHESTRATOR_LOG_LINES)})


@app.route("/api/launch/presets", methods=["GET"])
def launch_presets_list():
    """Named campaigns: per-group runs and population-build presets."""
    orc = get_orchestrator()
    presets = launch_presets.list_presets()
    for preset in presets:
        try:
            resolved = launch_presets.resolve_preset(
                preset["id"], orc.runners or [], user_personas=orc.get_user_personas()
            )
            preset["resolved_runner_names"] = resolved.get("runner_names") or []
        except ValueError:
            preset["resolved_runner_names"] = []
    return jsonify({"presets": presets})


@app.route("/api/launch/scale-status", methods=["GET"])
def launch_scale_status():
    """Clarion endpoint inventory vs optional target (for grouping scale)."""
    target = request.args.get("target", type=int) or 0
    api_url = _clarion_api_url()
    stats = launch_verify.clarion_endpoint_stats(api_url)
    count = int(stats.get("endpoint_count") or 0)
    stats["target_endpoints"] = target
    stats["at_target"] = bool(target and count >= target)
    stats["remaining"] = max(0, target - count) if target else 0
    stats["optional"] = True
    stats["configured_url"] = api_url
    if not stats.get("ok"):
        stats["hint"] = (
            "Lab orchestration and traffic do not need the Clarion product API. "
            "Set clarion_api_url in Configuration only if you want endpoint counts "
            "for clustering scale or Verify last launch."
        )
    return jsonify(stats)


@app.route("/api/launch/verify", methods=["POST"])
def launch_verify_endpoint():
    """Compare ground-truth sessions for a launch_id to Clarion inventory."""
    orc = get_orchestrator()
    data = request.get_json(force=True, silent=True) or {}
    launch_id = (data.get("launch_id") or "").strip()
    if not launch_id:
        return jsonify({"error": "launch_id required"}), 400
    expected_persona = data.get("expected_persona") or data.get("verify_persona")
    if not expected_persona and orc.launch_profile:
        expected_persona = orc.launch_profile.get("verify_persona")
    report = launch_verify.verify_launch_against_clarion(
        orc.ground_truth_log,
        _clarion_api_url(),
        launch_id,
        expected_persona=expected_persona,
    )
    return jsonify(report)


@app.route("/api/launch/preview", methods=["POST"])
def launch_preview():
    """Preview identity/runner counts for a launch profile (no orchestration start)."""
    orc = get_orchestrator()
    data = request.get_json(force=True, silent=True) or {}
    profile = _resolve_launch_body(data, orc)
    try:
        preview = orc.preview_launch(profile)
        preview["clarion"] = launch_verify.clarion_endpoint_stats(_clarion_api_url())
        target = int(profile.get("target_endpoints") or 0)
        if target:
            preview["scale"] = {
                "target_endpoints": target,
                "clarion_endpoints": preview["clarion"].get("endpoint_count", 0),
                "at_target": preview["clarion"].get("endpoint_count", 0) >= target,
            }
        return jsonify(preview)
    except Exception as e:
        log.exception("Launch preview failed")
        return jsonify({"error": str(e)}), 500


def _resolve_launch_body(data: dict, orc: LabOrchestrator) -> dict:
    """Merge preset_id into launch_profile when provided."""
    data = data or {}
    preset_id = (data.get("preset_id") or "").strip()
    manual = dict(data.get("launch_profile") or {})
    profile: dict = {}
    if preset_id:
        profile = launch_presets.resolve_preset(
            preset_id, orc.runners or [], user_personas=orc.get_user_personas()
        )
    if manual:
        # Empty lists from the UI mean "use preset default", not "select nothing".
        manual = dict(manual)
        if not (manual.get("runner_names") or []):
            manual.pop("runner_names", None)
        if manual.get("personas") == []:
            manual.pop("personas", None)
        profile.update(manual)
    if not profile and any(
        k in data
        for k in ("identity_kinds", "personas", "runner_names", "max_concurrent")
    ):
        profile = {
            k: v
            for k, v in data.items()
            if k not in ("cycle_once", "preset_id", "launch_profile")
        }
    if "cycle_once" in data:
        profile["cycle_once"] = bool(data["cycle_once"])
    return profile


@app.route("/api/start", methods=["POST"])
def start_orchestration():
    global ORCHESTRATOR_THREAD
    orc = get_orchestrator()
    if orc.running:
        return jsonify({"status": "already_running"}), 400
    if not (orc.runners or []):
        return jsonify({"error": "no_runners", "message": "Add at least one runner in the Configuration tab."}), 400
    
    duration = 24 * 365  # Default to continuous
    cycle_once = False
    launch_profile = None
    try:
        data = request.get_json(force=True, silent=True) or {}
        launch_profile = _resolve_launch_body(data, orc)
        cycle_once = bool(
            data.get("cycle_once", launch_profile.get("cycle_once", False))
        )
        launch_profile["cycle_once"] = cycle_once
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        pass

    preview = orc.preview_launch(launch_profile) if launch_profile else None
    if preview and launch_profile:
        preview["clarion"] = launch_verify.clarion_endpoint_stats(_clarion_api_url())

    # RUNNING before runner start so Windows plan API returns traffic mode immediately.
    orc.running = True
    orc.invalidate_windows_plan_cache()

    started_runners: list = []
    windows_started: list = []
    if launch_profile:
        profile = orc.set_launch_profile(launch_profile)
        started_runners = orc.prepare_launch_runners(profile)
        for name in started_runners:
            runner = next((r for r in (orc.runners or []) if r.get("name") == name), None)
            if runner and str(runner.get("runner_type") or "").strip().lower() == "windows":
                windows_started.append(name)

    def run_wrapper():
        orc.run(
            duration_hours=duration,
            cycle_once=cycle_once,
            launch_profile=launch_profile,
        )
        
    ORCHESTRATOR_THREAD = threading.Thread(target=run_wrapper, daemon=True)
    ORCHESTRATOR_THREAD.start()
    
    resp = {
        "status": "started",
        "cycle_once": cycle_once,
        "started_runners": started_runners,
        "windows_started": windows_started,
    }
    if orc.launch_id:
        resp["launch_id"] = orc.launch_id
    if preview:
        resp["preview"] = preview
    if windows_started:
        resp["windows_note"] = (
            "Windows runners do not use Pi assignments. With Status RUNNING, each PC's "
            "windows_runner_agent polls /api/windows-hosts/<id>/plan for execution_mode=traffic. "
            "Ensure a user is logged in on each PC (not HOSTNAME$ machine account only)."
        )
    return jsonify(resp)

@app.route("/api/stop", methods=["POST"])
def stop_orchestration():
    orc = get_orchestrator()
    if not orc.running:
        return jsonify({"status": "not_running"}), 400
    
    orc.stop()
    return jsonify({"status": "stopped"})


@app.route("/api/audit/runners", methods=["POST"])
def audit_runners_endpoint():
    """
    Pre-run audit: SSH to Pi management hosts and run runner_preflight.py.
    Windows runners are checked via recent telemetry and plan state.
    """
    orc = get_orchestrator()
    orchestrator_url = _canonical_orchestrator_url()
    try:
        report = runner_audit.audit_all_runners(
            orc.runners or [],
            orc.runner_states or {},
            orchestrator_url,
        )
        return jsonify(report)
    except Exception as e:
        log.exception("Runner audit failed")
        return jsonify({"error": str(e), "ok": False, "ready": False}), 500


@app.route("/api/audit/runners/remediate", methods=["POST"])
def remediate_runners_endpoint():
    """
    Safe auto-fix for Pi runners: stop agent, tear down lab iface only, restore mgmt
    routing, re-run preflight. Windows runners are skipped (telemetry-only).
    """
    orc = get_orchestrator()
    orchestrator_url = _canonical_orchestrator_url()
    try:
        report = runner_audit.remediate_all_runners(
            orc.runners or [],
            orc.runner_states or {},
            orchestrator_url,
        )
        return jsonify(report)
    except Exception as e:
        log.exception("Runner remediate failed")
        return jsonify({"error": str(e), "ok": False, "ready": False}), 500


@app.route("/api/audit/runners/<runner_name>", methods=["POST"])
def audit_one_runner_endpoint(runner_name: str):
    """Preflight audit for a single runner (Pi SSH or Windows telemetry)."""
    orc = get_orchestrator()
    orchestrator_url = _canonical_orchestrator_url()
    try:
        report = runner_audit.audit_runner_by_name(
            orc.runners or [],
            orc.runner_states or {},
            orchestrator_url,
            runner_name,
        )
        status = 404 if report.get("error") and not report.get("runners") else 200
        return jsonify(report), status
    except Exception as e:
        log.exception("Runner audit failed for %s", runner_name)
        return jsonify({"error": str(e), "ok": False, "ready": False, "runner": runner_name}), 500


@app.route("/api/audit/runners/<runner_name>/remediate", methods=["POST"])
def remediate_one_runner_endpoint(runner_name: str):
    """Safe auto-fix for one Pi runner; Windows runners are skipped."""
    orc = get_orchestrator()
    orchestrator_url = _canonical_orchestrator_url()
    try:
        report = runner_audit.remediate_runner_by_name(
            orc.runners or [],
            orc.runner_states or {},
            orchestrator_url,
            runner_name,
        )
        status = 404 if report.get("error") and not report.get("runners") else 200
        return jsonify(report), status
    except Exception as e:
        log.exception("Runner remediate failed for %s", runner_name)
        return jsonify({"error": str(e), "ok": False, "ready": False, "runner": runner_name}), 500


@app.route("/api/runners/<runner_name>/start", methods=["POST"])
def start_runner_endpoint(runner_name):
    """
    Start a single runner (clear stopped status so the orchestration loop will schedule it).
    
    Args:
        runner_name: The name of the runner to start.
        
    Returns:
        JSON response indicating success or failure.
    """
    orc = get_orchestrator()
    if runner_name not in orc.runner_states:
        return jsonify({"error": "runner_not_found"}), 404
    orc.start_runner(runner_name)
    return jsonify({"status": "started", "runner": runner_name})

@app.route("/api/runners/<runner_name>/switch", methods=["POST"])
def force_switch(runner_name):
    """
    Triggers immediate switch for a runner, also clears "stopped" status.
    
    Args:
        runner_name: The name of the runner to switch.
        
    Returns:
        JSON response indicating success or failure.
    """
    # Triggers immediate switch, also clears "stopped" status
    orc = get_orchestrator()
    if runner_name in orc.runner_states:
        if hasattr(orc, 'start_runner'):
            orc.start_runner(runner_name)
        else:
             orc.runner_states[runner_name]["next_switch"] = 0
        return jsonify({"status": "scheduled_immediate_switch"})
    return jsonify({"error": "runner_not_found"}), 404

@app.route("/api/runners/<runner_name>/stop", methods=["POST"])
def stop_runner_endpoint(runner_name):
    """
    Stop a specific runner (mark stopped, clear assignment; no SSH).
    
    Args:
        runner_name: The name of the runner to stop.
        
    Returns:
        JSON response indicating success or failure.
    """
    orc = get_orchestrator()
    if orc.stop_runner(runner_name):
        return jsonify({"status": "stopped", "runner": runner_name})
    return jsonify({"error": "runner_not_found", "runner": runner_name}), 404

@app.route("/api/config")
def get_config():
    """Get current orchestrator configuration from DB."""
    try:
        config = db.get_full_config()
        return jsonify(config)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/config", methods=["POST"])
def update_config():
    """Update orchestrator configuration in DB and hot-reload orchestrator."""
    try:
        new_config = request.json
        db.save_full_config(new_config)
        global ORCHESTRATOR
        if ORCHESTRATOR:
            ORCHESTRATOR.apply_config({**db.get_full_config(), "identities": ORCHESTRATOR.identities})
        return jsonify({"status": "saved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/services", methods=["GET"])
def get_services():
    """Get all defined services from DB."""
    try:
        services = db.get_config("services")
        return jsonify(services or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/service-inventory", methods=["GET"])
def get_service_inventory():
    """Return internal lab servers (by IP) plus external/unmapped service targets."""
    try:
        return jsonify(_build_service_inventory())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/services", methods=["POST"])
def update_services():
    """Update services list in DB."""
    try:
        services = request.json
        db.set_config("services", services)
        if ORCHESTRATOR:
            ORCHESTRATOR.config["services"] = services
        return jsonify({"status": "saved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/connectivity", methods=["GET"])
def get_connectivity():
    """Get connectivity policies from DB."""
    try:
        policies = db.get_config("connectivity_policies")
        return jsonify(policies or {})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/connectivity", methods=["POST"])
def update_connectivity():
    """Update connectivity policies in DB."""
    try:
        policies = request.json
        db.set_config("connectivity_policies", policies)
        if ORCHESTRATOR:
            ORCHESTRATOR.config["connectivity_policies"] = policies
        return jsonify({"status": "saved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/runner/assignment/<runner_id>", methods=["GET"])
def get_runner_assignment(runner_id):
    """
    Agent-based runners poll this for the next identity/session (orchestrator = master list).
    
    Args:
        runner_id: The ID of the polling runner.
        
    Returns:
        JSON response containing the assignment or None.
    """
    orc = get_orchestrator()
    assignment = orc.get_pending_assignment(runner_id)
    if not assignment:
        return jsonify({"assignment": None})
    return jsonify({"assignment": assignment})

@app.route("/api/runner/assignment/<runner_id>/ack", methods=["POST"])
def ack_runner_assignment(runner_id):
    """
    Agent calls this after completing a session so orchestrator can assign next.
    
    Args:
        runner_id: The ID of the acknowledging runner.
        
    Returns:
        JSON response indicating success or failure.
    """
    orc = get_orchestrator()
    if orc.clear_pending_assignment(runner_id):
        return jsonify({"status": "ok"})
    return jsonify({"error": "runner not found"}), 404

@app.route("/api/runner/telemetry", methods=["POST"])
def receive_telemetry():
    """Receive telemetry data from runners."""
    try:
        data = request.json
        remote_ip = request.remote_addr
        runner_id = data.get("runner_id")
        
        orc = get_orchestrator()
        success = orc.update_runner_telemetry(runner_id, remote_ip, data)
        
        if success:
            control = orc.get_runner_control_hints(runner_id)
            ack = (data.get("control_ack") or {}) if isinstance(data.get("control_ack"), dict) else {}
            if ack.get("restart"):
                orc.acknowledge_runner_control(runner_id, {"restart_ack": True})
                get_health_controller().acknowledge_restart(runner_id)
            return jsonify({"status": "ok", "control": control})
        else:
            return jsonify({"status": "ignored", "reason": "runner not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/runner/policy-test-results", methods=["POST"])
def receive_policy_test_results():
    """Receive structured allow/deny policy test results from runners."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        runner_id = str(data.get("runner_id") or "").strip()
        if not runner_id:
            return jsonify({"error": "runner_id is required"}), 400
        orc = get_orchestrator()
        success = orc.record_policy_test_results(runner_id, data)
        if success:
            return jsonify({"status": "ok"})
        return jsonify({"status": "ignored", "reason": "runner not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/policy-test-results", methods=["GET"])
def get_policy_test_results():
    """List recent policy test result bundles."""
    try:
        runner_id = request.args.get("runner_id")
        limit = request.args.get("limit", 100, type=int)
        orc = get_orchestrator()
        rows = orc.get_policy_test_results(runner_id=runner_id, limit=max(1, min(limit or 100, 1000)))
        return jsonify({"results": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/policy-test-results", methods=["DELETE"])
def clear_policy_test_results():
    """Clear policy test outcome tables (in-memory cache + JSONL log)."""
    try:
        orc = get_orchestrator()
        cleared = orc.clear_policy_test_results()
        return jsonify({"status": "cleared", "bundles_cleared": cleared})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/windows-hosts/<runner_id>/plan", methods=["POST"])
def get_windows_host_plan(runner_id):
    """Return a stable orchestrator-driven plan for a Windows host."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        hostname = str(data.get("hostname") or "").strip()
        username = str(data.get("username") or "").strip()
        interactive_username = str(data.get("interactive_username") or "").strip()
        principal_type = str(data.get("principal_type") or "").strip()
        user_logged_in = bool(data.get("user_logged_in"))
        fallback_persona = str(data.get("fallback_persona") or "").strip()
        fqdn = str(data.get("fqdn") or "").strip()
        domain_joined = bool(data.get("domain_joined"))
        machine_auth_capable = bool(data.get("machine_auth_capable"))

        if not username:
            return jsonify({"error": "username is required"}), 400

        orc = get_orchestrator()
        remote_ip = request.remote_addr or ""
        runner = orc.resolve_runner(runner_id=runner_id, remote_ip=remote_ip, hostname=hostname)
        if not runner:
            return jsonify({"error": "runner not found"}), 404
        resolved_runner_id = runner["name"]
        orc.update_windows_host_context(
            runner_id=resolved_runner_id,
            remote_ip=remote_ip,
            hostname=hostname,
            username=username,
            interactive_username=interactive_username,
            principal_type=principal_type,
            user_logged_in=user_logged_in,
            fqdn=fqdn,
            domain_joined=domain_joined,
            machine_auth_capable=machine_auth_capable,
        )
        plan = orc.get_windows_host_plan(
            runner_id=resolved_runner_id,
            hostname=hostname,
            username=username,
            fallback_persona=fallback_persona,
            interactive_username=interactive_username,
        )
        if not plan:
            return jsonify({"error": "runner not found"}), 404
        return jsonify(plan)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/config/runners", methods=["POST"])
def update_runners_config():
    """Update just the runners configuration in DB."""
    try:
        runners = request.json
        orc = get_orchestrator()
        if orc:
            runners = [orc._normalize_windows_runner(dict(r)) for r in (runners or [])]
        db.set_config("runners", runners)
        global ORCHESTRATOR
        if ORCHESTRATOR:
            ORCHESTRATOR.apply_config({**db.get_full_config(), "identities": ORCHESTRATOR.identities})
        return jsonify({"status": "saved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/identities")
def get_identities():
    """Get all identities from database (source of truth)."""
    try:
        identities = db.get_identities()
        return jsonify(identities)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/identities", methods=["POST"])
def update_identities():
    """Update identities: persist to database (source of truth) and hot-reload orchestrator."""
    try:
        new_identities = request.json
        if not isinstance(new_identities, list):
            return jsonify({"error": "Expected a list of identities"}), 400
        # Ensure Mac/Apple identities have an Apple OUI MAC in the DB so runners get correct MAC from orchestrator
        existing_macs = [i.get("mac", "") for i in new_identities if i.get("mac")]
        for ident in new_identities:
            is_mac_apple = (
                (str(ident.get("os") or "").strip().lower() == "mac") or
                (str(ident.get("manufacturer") or "").strip().lower() == "apple")
            )
            if is_mac_apple:
                current_mac = ident.get("mac", "")
                if not current_mac or not mac_oui_database.mac_has_manufacturer_oui(current_mac, "Apple"):
                    ident["mac"] = mac_oui_database.generate_mac_for_manufacturer("Apple", existing_macs, None)
                    existing_macs.append(ident["mac"])
        db.set_identities(new_identities)
        global ORCHESTRATOR
        if ORCHESTRATOR is not None:
            ORCHESTRATOR.identities = new_identities
        return jsonify({"status": "saved", "message": "Identities saved to database."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/persona-options")
def get_persona_options():
    """Get list of available personas from identities and custom list in DB."""
    try:
        identities = db.get_identities()
        personas = set()
        for ident in identities:
            if ident.get("persona"):
                personas.add(ident["persona"])
            if ident.get("department"):
                personas.add(ident["department"])
            for g in ident.get("groups", []):
                personas.add(g)
        for p in (db.get_config("custom_personas") or []):
            personas.add(p)
        return jsonify(sorted(list(personas)))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/custom-personas", methods=["GET"])
def get_custom_personas():
    """Get the list of custom-defined personas."""
    try:
        return jsonify(db.get_config("custom_personas") or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/custom-personas", methods=["POST"])
def update_custom_personas():
    """Replace the list of custom-defined personas."""
    try:
        personas = request.json
        if not isinstance(personas, list):
            return jsonify({"error": "Expected a list"}), 400
        db.set_config("custom_personas", personas)
        return jsonify({"status": "saved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Active Directory Integration Endpoints
AD_CONNECTOR = None

@app.route("/api/ad/test-connection", methods=["POST"])
def test_ad_connection():
    """Test connection to Active Directory."""
    global AD_CONNECTOR
    try:
        data = request.json
        server = data.get("server")
        port = int(data.get("port", 389))
        use_ssl = data.get("use_ssl", False)
        bind_dn = data.get("bind_dn")
        password = data.get("password")
        
        if not all([server, bind_dn, password]):
            return jsonify({"error": "Missing required fields"}), 400
        
        # Create new connector
        AD_CONNECTOR = ad_connector.ADConnector()
        success, message = AD_CONNECTOR.test_connection(server, port, use_ssl, bind_dn, password)
        
        if success:
            return jsonify({"status": "success", "message": message})
        else:
            AD_CONNECTOR = None
            return jsonify({"status": "failed", "message": message}), 400
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ad/ous", methods=["POST"])
def get_ad_ous():
    """Get organizational units from AD."""
    global AD_CONNECTOR
    try:
        if not AD_CONNECTOR or not AD_CONNECTOR.connection:
            return jsonify({"error": "Not connected to AD. Test connection first."}), 400
        
        data = request.json
        base_dn = data.get("base_dn")
        
        if not base_dn:
            return jsonify({"error": "base_dn required"}), 400
        
        ous = AD_CONNECTOR.get_organizational_units(base_dn)
        return jsonify(ous)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ad/query-users", methods=["POST"])
def query_ad_users():
    """Query users from a specific OU."""
    global AD_CONNECTOR
    try:
        if not AD_CONNECTOR or not AD_CONNECTOR.connection:
            return jsonify({"error": "Not connected to AD. Test connection first."}), 400
        
        data = request.json
        ou_dn = data.get("ou_dn")
        active_only = data.get("active_only", True)
        
        if not ou_dn:
            return jsonify({"error": "ou_dn required"}), 400
        
        users = AD_CONNECTOR.get_users_from_ou(ou_dn, active_only)
        return jsonify(users)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ad/import-users", methods=["POST"])
def import_ad_users():
    """Import selected AD users as identities into DB."""
    try:
        data = request.json
        ad_users = data.get("users", [])
        default_password = data.get("default_password", "C!sco#123")
        if not ad_users:
            return jsonify({"error": "No users to import"}), 400
        existing_identities = db.get_identities()
        new_identities = ad_connector.convert_ad_users_to_identities(
            ad_users, existing_identities, default_password=default_password
        )
        existing_identities.extend(new_identities)
        db.set_identities(existing_identities)
        global ORCHESTRATOR
        if ORCHESTRATOR is not None:
            ORCHESTRATOR.identities = existing_identities
        return jsonify({"status": "success", "imported_count": len(new_identities), "identities": new_identities})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ad/disconnect", methods=["POST"])
def disconnect_ad():
    """Disconnect from Active Directory."""
    global AD_CONNECTOR
    try:
        if AD_CONNECTOR:
            AD_CONNECTOR.disconnect()
            AD_CONNECTOR = None
        return jsonify({"status": "disconnected"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Manufacturer and MAC Address Management Endpoints

@app.route("/api/manufacturers/users", methods=["GET"])
def get_user_manufacturers():
    """Get list of available user device manufacturers."""
    try:
        manufacturers = mac_oui_database.get_user_manufacturers()
        return jsonify(manufacturers)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/manufacturers/iot", methods=["GET"])
def get_iot_manufacturers():
    """Get IoT device personas and their manufacturers."""
    try:
        personas = mac_oui_database.get_iot_personas()
        result = {}
        for persona in personas:
            result[persona] = mac_oui_database.get_manufacturers_for_persona(persona)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/manufacturers/generate-mac", methods=["POST"])
def generate_manufacturer_mac():
    """Generate MAC address for specific manufacturer."""
    try:
        data = request.json
        manufacturer = data.get("manufacturer")
        persona = data.get("persona")  # Optional, for IoT devices
        
        if not manufacturer:
            return jsonify({"error": "manufacturer required"}), 400
        
        existing_identities = db.get_identities()
        existing_macs = [i.get("mac", "") for i in existing_identities]
        
        # Generate MAC
        mac = mac_oui_database.generate_mac_for_manufacturer(manufacturer, existing_macs, persona)
        
        return jsonify({"mac": mac})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Data Validation Endpoints

@app.route("/api/validation/runs", methods=["GET"])
def get_validation_runs():
    """Get history of orchestrator runs from ground truth CSV."""
    try:
        limit = request.args.get('limit', 50, type=int)
        runs = validator_engine.parse_csv_history(limit=limit)
        return jsonify({"runs": runs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/validation/runs", methods=["DELETE"])
def delete_validation_runs():
    """Clear all orchestrator runs from the ground truth log."""
    try:
        success = validator_engine.clear_history()
        if success:
            return jsonify({"message": "Validation history cleared successfully."})
        else:
            return jsonify({"error": "Failed to clear validation history."}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/validation/run", methods=["POST"])
def validate_run():
    """Trigger validation for a specific orchestration run."""
    try:
        data = request.json
        if not data or 'device_mac' not in data or 'timestamp' not in data:
            return jsonify({"error": "Missing required run data (device_mac, timestamp)"}), 400
        
        report = validator_engine.validate_run(data)
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def main():
    """
    Main entry point for the Clarion Lab Orchestrator UI.
    Parses arguments and starts the Flask application.
    """
    import argparse
    import sys
    import traceback
    parser = argparse.ArgumentParser(description="Clarion Lab Orchestrator UI")
    parser.add_argument("--host", default="0.0.0.0", help="Host IP to bind to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind to")
    parser.add_argument("--autostart", action="store_true", help="Automatically start orchestration loop")
    args = parser.parse_args()

    try:
        # Ensure templates dir exists
        templates_dir = os.path.join(os.path.dirname(__file__), "templates")
        os.makedirs(templates_dir, exist_ok=True)
        
        if args.autostart:
            print("Autostarting orchestration loop...")
            # Start thread directly to avoid Flask context issues
            duration = 24 * 365
            orc = get_orchestrator()
            def run_wrapper():
                orc.run(duration_hours=duration)
            
            t = threading.Thread(target=run_wrapper, daemon=True)
            t.start()
            # Update global thread reference just in case
            global ORCHESTRATOR_THREAD
            ORCHESTRATOR_THREAD = t
        
        print(f"Starting Orchestrator UI on {args.host}:{args.port}...")
        app.run(host=args.host, port=args.port)
    except Exception as e:
        traceback.print_exc()
        sys.stderr.write(f"Clarion orchestrator failed: {e}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
