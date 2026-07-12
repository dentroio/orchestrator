#!/usr/bin/env python3
"""
Clarion Lab Auto-Runner

Fully automates the lifecycle of a Lab Client (Raspberry Pi):
1. Selects a User Identity (from orchestrator DB when using runner_agent; from local identities file when standalone)
2. Configures 802.1x and bounces network (via identity_switcher.py)
3. Verifies Connectivity
4. Generates Traffic for X minutes (via traffic_gen.py)
5. Sleeps/Idles
6. Repeats with a new identity
"""

import argparse
import time
import sys
import logging
import random
import os
import subprocess
import socket
import json
import uuid

import urllib.request
import urllib.error
import threading
from collections import deque

# Ensure we can import sibling scripts and traffic-simulation dependencies.
_app_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(_app_dir)
_traffic_app = os.path.abspath(os.path.join(_app_dir, "..", "..", "traffic-simulation", "app"))
if os.path.isdir(_traffic_app) and _traffic_app not in sys.path:
    sys.path.insert(0, _traffic_app)

# IoT personas use POST for their backend endpoints (badge/events, camera/stream, etc.)
IOT_PERSONAS_NEED_POST = {
    "badge reader", "camera", "printer", "environmental sensor", "hvac controller",
    "door lock", "display", "voip phone", "robot", "medical device",
}

# Apple OUIs so Mac/Apple identities never use a Dell/HP MAC (ISE would show "Dell-Device")
def _apple_ouis():
    try:
        import mac_oui_database
        return set(oui.replace(":", "").upper() for oui in mac_oui_database.USER_DEVICE_MANUFACTURERS.get("Apple", []))
    except Exception:
        return {"001B63", "A45E60", "F01898", "BCD074", "AC87A3", "001CB3"}

def _mac_has_apple_oui(mac):
    """True if the MAC address uses an Apple OUI (first 3 octets)."""
    if not mac or not isinstance(mac, str):
        return False
    normalized = mac.strip().replace(":", "").replace("-", "").upper()
    if len(normalized) < 6:
        return False
    return normalized[:6] in _apple_ouis()


PI_AGENT_VERSION = "2026.05.19.1"


def get_code_version():
    """Report the same pi-agent version string as runner_agent.py for consistent dashboard display."""
    return f"pi-agent/{PI_AGENT_VERSION}"


# Ring buffer of recent log lines for streaming to orchestrator (troubleshooting)
RECENT_LOG_LINES = deque(maxlen=80)


class LogStreamHandler(logging.Handler):
    """Appends formatted log records to RECENT_LOG_LINES for telemetry."""
    def emit(self, record):
        try:
            msg = self.format(record)
            RECENT_LOG_LINES.append(msg)
        except Exception:
            pass


class TelemetryReporter(threading.Thread):
    def __init__(self, orchestrator_url, runner_id, interface="wlan0", history_queue=None, interval=5):
        super().__init__()
        self.orchestrator_url = orchestrator_url
        self.runner_id = runner_id
        self.interface = interface
        self.history_queue = history_queue
        self.interval = interval
        self.running = False
        self.daemon = True # Stop thread when main thread exits
        self.current_status = "idle"
        self.current_persona = "none"
        self.current_target = "none"
        self.start_time = time.time()
        self._last_telemetry_error_ts = 0.0

    def run(self):
        self.running = True
        logger.info(f"Telemetry started. Reporting to {self.orchestrator_url}")
        while self.running:
            try:
                self.report()
            except Exception as e:
                # Don't spam logs with connection errors
                pass
            time.sleep(self.interval)

    def get_system_stats(self):
        # CPU Load (1 min avg)
        try:
            load1, load5, load15 = os.getloadavg()
        except:
            load1 = 0
        
        # Memory (approximate from /proc/meminfo)
        mem_percent = 0
        try:
            mem_total = 0
            mem_avail = 0
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemTotal' in line:
                        mem_total = int(line.split()[1])
                    elif 'MemAvailable' in line:
                        mem_avail = int(line.split()[1])
            if mem_total > 0:
                mem_percent = ((mem_total - mem_avail) / mem_total) * 100
        except:
             pass

        # RSSI (from /proc/net/wireless) example: "wlan0: 0000 50. -60."
        rssi = 0
        try:
             with open('/proc/net/wireless', 'r') as f:
                lines = f.readlines()
                for line in lines:
                    if ':' in line: # Interface line
                        parts = line.split()
                        # Level is typically index 3, usually negative dBm (e.g. -60)
                        # Depending on driver, but commonly reported there.
                        # Sometimes field is link quality.
                        # Let's try to parse the dBm value.
                        if len(parts) > 3:
                            val = parts[3].replace('.', '')
                            rssi = float(val)
        except:
            pass

        return {
            "cpu_load": load1,
            "memory_percent": round(mem_percent, 1),
            "rssi": rssi,
            "uptime": int(time.time() - self.start_time)
        }

    def report(self):
        stats = self.get_system_stats()
        net_details = get_network_details(self.interface)
        payload = {
            "runner_id": self.runner_id,
            "status": self.current_status,
            "persona": self.current_persona,
            "target": self.current_target,
            "stats": stats,
            "network": net_details,
            "traffic_history": list(self.history_queue) if self.history_queue else [],
            "timestamp": time.time(),
            "code_version": get_code_version(),
            "log_lines": list(RECENT_LOG_LINES),
        }
        
        try:
            req = urllib.request.Request(
                f"{self.orchestrator_url}/api/runner/telemetry",
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=2) as response:
                pass
        except urllib.error.URLError:
            now = time.time()
            if now - self._last_telemetry_error_ts > 60:
                # Rate-limit to keep logs readable during interface bounces.
                self._last_telemetry_error_ts = now
                logger.warning("Telemetry POST failed (rate-limited): orchestrator=%s runner=%s", self.orchestrator_url, self.runner_id)


def post_policy_test_results(orchestrator_url, runner_id, session_id, identity, results):
    """Send structured allow/deny policy outcomes to orchestrator."""
    if not orchestrator_url or not runner_id:
        return
    payload = {
        "runner_id": runner_id,
        "session_id": session_id,
        "identity": {
            "username": identity.get("username"),
            "device_name": identity.get("device_name"),
            "persona": identity.get("persona"),
            "department": identity.get("department"),
        },
        "results": results or [],
        "timestamp": time.time(),
    }
    try:
        req = urllib.request.Request(
            f"{orchestrator_url}/api/runner/policy-test-results",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as exc:
        logger.warning("Failed posting policy test results: %s", exc)


# Setup logging FIRST to ensure we capture everything and override imported modules
# Use force=True to reconfigure root logger even if imports managed to configure it
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [AutoRunner] - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("lab_runner.log")
    ],
    force=True
)
logger = logging.getLogger("AutoRunner")
# Stream recent logs to orchestrator for troubleshooting
_stream_handler = LogStreamHandler()
_stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_stream_handler)

try:
    import identity_switcher
    import traffic_gen
except ImportError as e:
    logger.error(f"Error: Could not import dependency: {e}")
    sys.exit(1)
logger = logging.getLogger("AutoRunner")

def get_wlan_gateway(interface="wlan0"):
    """Get the default gateway for the management interface."""
    try:
        # Link-local or default route
        result = subprocess.check_output(f"ip route show dev {interface}", shell=True).decode()
        for line in result.split('\n'):
            if "default via" in line:
                return line.split()[2]
    except:
        pass
    return None

def get_network_details(interface="wlan0"):
    """Get current SSID, IP, and MAC address."""
    details = {
        "ssid": "unknown",
        "ip": "unknown",
        "mac": "unknown"
    }
    try:
        # SSID
        try:
            # Try iwgetid first
            ssid = subprocess.check_output(["iwgetid", "-r"], stderr=subprocess.DEVNULL).decode().strip()
            if ssid: details["ssid"] = ssid
        except:
            # Fallback to iwconfig
            out = subprocess.check_output(f"iwconfig {interface}", shell=True, stderr=subprocess.DEVNULL).decode()
            if 'ESSID:"' in out:
                details["ssid"] = out.split('ESSID:"')[1].split('"')[0]
    except:
        pass

    try:
        # IP — exclude 169.254.x.x APIPA/link-local addresses (no real DHCP lease)
        out = subprocess.check_output(f"ip -o -4 addr list {interface} | awk '{{print $4}}' | cut -d/ -f1", shell=True).decode().strip()
        for candidate in out.splitlines():
            candidate = candidate.strip()
            if candidate and not candidate.startswith("169.254."):
                details["ip"] = candidate
                break
    except:
        pass

    try:
        # MAC
        with open(f"/sys/class/net/{interface}/address", 'r') as f:
            details["mac"] = f.read().strip()
    except:
        pass

    return details

def ensure_orchestrator_route(orchestrator_url, mgmt_iface="wlan0"):
    """Ensure a static route exists to the Orchestrator AND DNS servers via the management interface."""
    if not orchestrator_url:
        return

    try:
        # 1. Parse Orchestrator IP
        from urllib.parse import urlparse
        parsed = urlparse(orchestrator_url)
        hostname = parsed.hostname
        
        # Resolve to IP if needed (though usually it's an IP in config)
        target_ip = socket.gethostbyname(hostname)
        
        # 2. Get WLAN Gateway
        gateway = get_wlan_gateway(mgmt_iface)
        if not gateway:
            logger.warning(f"Could not find gateway for {mgmt_iface}. Skipping static route.")
            return

        # 3. Add Route for Orchestrator
        check = subprocess.run(f"ip route show {target_ip}", shell=True, stdout=subprocess.PIPE).stdout.decode()
        if mgmt_iface not in check:
            logger.info(f"Adding static route to Orchestrator {target_ip} via {gateway} ({mgmt_iface})")
            subprocess.run(f"sudo ip route replace {target_ip} via {gateway} dev {mgmt_iface}", shell=True)
        else:
            logger.info(f"Route to Orchestrator {target_ip} already exists via {mgmt_iface}")

        # 4. Pin routes for DNS servers via the management interface so DNS works even when
        #    the lab interface is the default route.
        try:
            dns_out = subprocess.run(
                ["nmcli", "dev", "show", mgmt_iface],
                capture_output=True, text=True
            ).stdout
            dns_servers = []
            for line in dns_out.splitlines():
                if "IP4.DNS" in line:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        dns_ip = parts[1].strip()
                        if dns_ip:
                            dns_servers.append(dns_ip)
            for dns_ip in dns_servers:
                dns_check = subprocess.run(f"ip route show {dns_ip}", shell=True, stdout=subprocess.PIPE).stdout.decode()
                if mgmt_iface not in dns_check:
                    subprocess.run(f"sudo ip route replace {dns_ip} via {gateway} dev {mgmt_iface}", shell=True)
                    logger.info(f"Pinned DNS server {dns_ip} via {gateway} ({mgmt_iface})")
        except Exception as dns_ex:
            logger.debug("Could not pin DNS server routes: %s", dns_ex)
            
    except Exception as e:
        logger.error(f"Failed to manage routes: {e}")


def check_connectivity(target="8.8.8.8", port=53, timeout=3):
    """Check if we have network access."""
    # If target includes a URL scheme, strip it
    if "://" in target:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(target)
            target = parsed.hostname
            if parsed.port:
                port = parsed.port
            elif parsed.scheme == "https":
                port = 443
            elif parsed.scheme == "http":
                port = 80
        except:
            pass
            
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((target, port))
        return True
    except socket.error:
        return False

def wait_for_network(max_attempts=30, target_url=None):
    logger.info("Waiting for network connectivity...")
    
    # Prefer checking Orchestrator if known, else default gateway 
    check_target = "192.168.1.1" 
    check_port = 53 # DNS port on gateway
    
    if target_url:
        # Use Orchestrator URL for connectivity check
        # This confirms our static route is working and we can reach the controller
        try:
            from urllib.parse import urlparse
            parsed = urlparse(target_url)
            check_target = parsed.hostname
            if parsed.port:
                check_port = parsed.port
            elif parsed.scheme == "https":
                check_port = 443
            elif parsed.scheme == "http":
                check_port = 80
        except:
            pass
        
    for i in range(max_attempts):
        if check_connectivity(check_target, check_port):
            logger.info(f"Network is UP! (Reached {check_target}:{check_port})")
            return True
        time.sleep(3)
    logger.error(f"Network failed to come up. Could not reach {check_target}")
    return False

def run_traffic_session(duration_seconds, mode="random", identity=None, history_queue=None):
    """Run traffic generator for a specific duration. If identity has urls or customer_url, use those as targets."""
    logger.info(f"Starting Traffic Session (Mode: {mode}) for {duration_seconds}s")
    
    # Per-identity URLs (IoT customer URL per device)?
    final_targets = None
    policy_cases = []
    method = "GET"
    min_sleep = 5
    max_sleep = 60
    if identity:
        policy_test_plan = identity.get("policy_test_plan") or {}
        if isinstance(policy_test_plan, dict):
            policy_cases = [c for c in (policy_test_plan.get("cases") or []) if isinstance(c, dict)]
        urls = identity.get("urls") or identity.get("customer_url")
        if urls is not None and not policy_cases:
            final_targets = urls if isinstance(urls, list) else [urls]
            method = identity.get("traffic_method")
            if method is None:
                persona = (identity.get("persona") or "").strip().lower()
                method = "POST" if (persona in IOT_PERSONAS_NEED_POST or identity.get("auth") == "mab") else "GET"
            # Use 5–30s between requests (same as traffic_gen) so user hits all sites in the list regularly
            min_sleep = identity.get("traffic_min_sleep", 5)
            max_sleep = identity.get("traffic_max_sleep", 30)
        elif policy_cases:
            method = identity.get("traffic_method") or "GET"
            min_sleep = identity.get("traffic_min_sleep", 5)
            max_sleep = identity.get("traffic_max_sleep", 30)
    
    if final_targets is None and not policy_cases:
        # Load Config using traffic_gen's helper
        config_path = os.path.join(os.path.dirname(__file__), "traffic_config.json")
        full_config = traffic_gen.load_traffic_config(config_path)
        
        if not full_config:
            logger.error("Failed to load traffic config")
            return

        personas_def = full_config.get("personas", {})
        all_targets = full_config.get("targets", {})

        if mode == "random" or mode not in personas_def:
            mode = random.choice(list(personas_def.keys()))
        
        p_def = personas_def[mode]
        final_targets = traffic_gen.resolve_targets(p_def, all_targets)
        method = p_def.get("method", "GET")
        min_sleep = p_def.get("min_sleep", 5)
        max_sleep = p_def.get("max_sleep", 60)
    
    if not final_targets and not policy_cases:
        logger.warning("No traffic targets; skipping traffic session.")
        return
    
    # User-Agent from identity persona so ISE can profile device type (e.g. ClarionLab-BadgeReader/1.0)
    user_agent = None
    if identity and identity.get("persona"):
        user_agent = traffic_gen.persona_to_user_agent(identity["persona"])
        logger.info(f"Using User-Agent for ISE profiling: {user_agent}")
    
    if policy_cases:
        logger.info("Using policy test plan with %d allow/deny cases", len(policy_cases))
        persona_config = {
            "policy_test_cases": policy_cases,
            "method": method,
            "min_sleep": min_sleep,
            "max_sleep": max_sleep,
            "user_agent": user_agent,
        }
    else:
        persona_config = {
            "targets": final_targets,
            "method": method,
            "min_sleep": min_sleep,
            "max_sleep": max_sleep,
            "user_agent": user_agent
        }

    # Start Persona in a separate thread/object
    from concurrent.futures import ThreadPoolExecutor
    
    persona = traffic_gen.TrafficPersona(f"Auto-{mode}", persona_config, history_queue)
    
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(persona.run)
        
        # Let it run for duration
        time.sleep(duration_seconds)
        
        # Stop it
        logger.info("Stopping Traffic Session...")
        persona.running = False
        # wait for thread to finish (max_sleep is max wait)
        try:
            future.result(timeout=5)
        except:
            pass # Timeout waiting for cleanup is fine

def main():
    parser = argparse.ArgumentParser(description="Clarion Lab Auto Runner")
    # Load Lab Config
    lab_config_path = os.path.join(os.path.dirname(__file__), "lab_config.json")
    lab_config = {}
    try:
        with open(lab_config_path, 'r') as f:
            lab_config = json.load(f)
    except:
        pass # Optional
        
    # specific defaults from config or hardcoded
    default_ssid = lab_config.get("network", {}).get("ssid", "ClarionLab")
    default_iface = lab_config.get("network", {}).get("interface", "wlan0")
    default_duration = lab_config.get("simulation", {}).get("session_duration_seconds", 300)
    default_cooldown = lab_config.get("simulation", {}).get("cooldown_seconds", 30)
    default_mac = lab_config.get("simulation", {}).get("random_mac_enabled", False)
    default_dhcp_hostname = lab_config.get("network", {}).get("dhcp_hostname")

    parser.add_argument("--interface", default=default_iface, help=f"Interface to manage (default: {default_iface})")
    parser.add_argument("--ssid", default=default_ssid, help=f"SSID for wireless (default: {default_ssid})")
    parser.add_argument("--session-duration", type=int, default=default_duration, help=f"Traffic generation duration (default: {default_duration}s)")
    parser.add_argument("--cooldown", type=int, default=default_cooldown, help=f"Sleep between sessions (default: {default_cooldown}s)")
    parser.add_argument("--random-mac", action="store_true", help="Randomize MAC address")
    parser.add_argument("--shutdown-duration", type=int, default=60, help="Seconds to keep interface down for cleanup (default: 60)")
    parser.add_argument("--dhcp-hostname", default=default_dhcp_hostname, help="Hostname to send in DHCP so Dot1x/DNS can see the device")
    
    # New arguments for Orchestrator control
    parser.add_argument("--identity", help="Specific identity (username or device_name) to use from local file. Bypasses random selection.")
    parser.add_argument("--username", help="Username for 802.1x authentication (passed from Orchestrator)")
    parser.add_argument("--password", help="Password for 802.1x authentication (passed from Orchestrator)")
    parser.add_argument("--device-name", help="Device name for MAB/IoT devices (passed from Orchestrator)")
    parser.add_argument("--mac", help="MAC address to use (passed from Orchestrator)")
    parser.add_argument("--one-shot", action="store_true", help="Run a single session and exit (do not loop).")
    parser.add_argument("--no-cooldown", action="store_true", help="Skip cooldown period at the end.")
    parser.add_argument("--skip-bounce", action="store_true", help="Skip interface bounce (useful for testing traffic gen only).")
    parser.add_argument("--access-urls", help="Comma-separated list of target URLs to access (passed from Orchestrator)")
    parser.add_argument("--orchestrator-url", help="Base URL of Orchestrator (e.g. http://192.168.20.95:5000)")
    parser.add_argument("--persona", help="Persona for IoT (Badge Reader, Camera, etc.) or department for users (passed from Orchestrator)")
    parser.add_argument("--os", dest="os_type", help="OS/device type for DHCP/HTTP fingerprinting: Windows, Mac, Linux, or IoT persona (passed from Orchestrator DB)")
    parser.add_argument("--traffic-method", help="Traffic method override from orchestrator session plan")
    parser.add_argument("--traffic-min-sleep", type=int, help="Minimum sleep between generated requests")
    parser.add_argument("--traffic-max-sleep", type=int, help="Maximum sleep between generated requests")
    parser.add_argument("--runner-id", help="Runner name for telemetry (passed from Orchestrator)")
    parser.add_argument("--management-interface", default=None, help="Interface matching host IP (pins orchestrator route via this interface)")
    parser.add_argument("--policy-test-plan", help="JSON policy test plan containing allow/deny cases")

    args = parser.parse_args()
    
    # Logic to handle bool flag from config if not set on CLI is tricky with argparse store_true
    # check if flag was passed? No, let's just use config if set and args.random_mac is false? 
    # Actually, simplistic approach: if args.random_mac is False, check config.
    use_random_mac = args.random_mac or default_mac

    # Check if credentials were passed via CLI (Orchestrator mode)
    target_identity = None
    identities = []
    
    if args.username or args.device_name:
        # Orchestrator passed credentials directly (identity from orchestrator DB)
        logger.info("Using credentials passed from Orchestrator (identity from DB)")
        urls = [u.strip() for u in args.access_urls.split(",") if u.strip()] if args.access_urls else []
        persona = (args.persona or "").strip()
        os_type = (getattr(args, "os_type", None) or "").strip()
        is_iot_persona = persona.lower() in [p.lower() for p in IOT_PERSONAS_NEED_POST]
        auth = "dot1x" if args.username else ("mab" if (args.device_name or is_iot_persona) else "dot1x")
        use_post = (persona.lower() in IOT_PERSONAS_NEED_POST) or (auth == "mab" and urls)
        parsed_policy_plan = {}
        if args.policy_test_plan:
            try:
                parsed_policy_plan = json.loads(args.policy_test_plan)
            except Exception as exc:
                logger.warning("Invalid --policy-test-plan JSON; ignoring policy test plan: %s", exc)
        target_identity = {
            "username": args.username or "",
            "password": args.password or "",
            "device_name": args.device_name or "",
            "mac": args.mac or "",
            "ssid": args.ssid if args.ssid != default_ssid else "",
            "auth": auth,
            "persona": persona,
            "department": args.persona or "",
            "os": os_type or None,
            "urls": urls,
            "traffic_method": args.traffic_method or ("POST" if use_post else "GET"),
            "traffic_min_sleep": args.traffic_min_sleep,
            "traffic_max_sleep": args.traffic_max_sleep,
            "policy_test_plan": parsed_policy_plan,
        }
        identities = [target_identity]  # Single identity mode
    else:
        # Standalone: load identities from local file (orchestrator mode uses DB and passes identity via CLI)
        identities_file = os.path.join(os.path.dirname(__file__), "identities1.json")
        identities = identity_switcher.load_identities(identities_file)
        
        if not identities:
            logger.error("No identities found!")
            sys.exit(1)

        # Filter/Select Identity if specified by name
        if args.identity:
            # Find identity by username OR device_name
            for ident in identities:
                if (ident.get("username") == args.identity or 
                    ident.get("device_name") == args.identity):
                    target_identity = ident
                    break
            
            if not target_identity:
                logger.error(f"Identity '{args.identity}' not found in local identities file")
                sys.exit(1)
                
            logger.info(f"Forced identity: {target_identity.get('username') or target_identity.get('device_name')}")

    logger.info(f"Starting Auto-Runner on {args.interface} for SSID '{args.ssid}'")

    # Save original hostname so we can restore it on exit
    try:
        original_hostname = subprocess.check_output(["hostname"], text=True).strip()
    except Exception:
        original_hostname = None

    # Pin orchestrator route via management interface (same as host IP's interface)
    if args.orchestrator_url and args.management_interface:
        ensure_orchestrator_route(args.orchestrator_url, mgmt_iface=args.management_interface)

    # Start Telemetry (will use mgmt interface via route)
    reporter = None
    history_queue = deque(maxlen=20)
    
    if args.orchestrator_url:
        try:
            runner_id = args.runner_id or socket.gethostname()
            reporter = TelemetryReporter(args.orchestrator_url, runner_id, interface=args.interface, history_queue=history_queue)
            reporter.start()
        except Exception as e:
            logger.error(f"Failed to start telemetry: {e}")

    if args.one_shot:
        logger.info("Mode: ONE-SHOT (Single session)")
    else:
        logger.info("Mode: CONTINUOUS LOOP")
        logger.info("Press Ctrl+C to stop.")
    
    session_ok = False  # set True when traffic runs; used for one-shot exit code
    try:
        while True:
            session_id = str(uuid.uuid4())
            # 1. Pick Identity
            if target_identity:
                user = target_identity
            else:
                user = random.choice(identities)
                
            username = user.get("username", "")
            password = user.get("password", "")
            
            # Resolve DHCP/device name: identity device_name > display_name > config/CLI
            dhcp_hostname = user.get("device_name") or user.get("display_name") or args.dhcp_hostname
            
            # Determine SSID: User-specific > Global Config > Default
            current_ssid = user.get("ssid", args.ssid)
            
            is_mab = (user.get("auth") or "dot1x").lower() == "mab"
            if is_mab:
                logger.info(f"=== Starting Session for MAB device: {user.get('device_name') or user.get('display_name') or 'unknown'} ===")
            else:
                logger.info(f"=== Starting Session for User: {username} on SSID: {current_ssid} ===")
            
            # 2. Switch Identity & Optionally MAC
            # Fingerprint key: use OS for DHCP Option 60 / HTTP so ISE doesn't profile as Dell for Mac users
            prof_key = user.get("os") or user.get("OS") or user.get("persona")
            # For user identities, prefer OS so we send Mac/Windows/Linux fingerprint, not department
            if user.get("username") and (user.get("os") or user.get("OS")):
                prof_key = user.get("os") or user.get("OS")
            # Field Device + SICK manufacturer → use SICK Scanner fingerprint so ISE doesn't show "unknown"
            if (prof_key or "").strip() == "Field Device" and (str(user.get("manufacturer") or "").strip().lower() == "sick"):
                prof_key = "SICK Scanner"
            prof = identity_switcher.get_persona_profile(prof_key)
            vendor_class = prof.get("vendor_class") if prof else None
            dhcp_req_list = prof.get("dhcp_req_list") if prof else None

            if not args.skip_bounce:
                # Ensure routing to Orchestrator is pinned via WLAN0 before we mess with ETH0
                if args.management_interface:
                    ensure_orchestrator_route(args.orchestrator_url, mgmt_iface=args.management_interface)
                
                if reporter:
                    reporter.current_status = "bouncing_interface"
                    reporter.current_persona = str(user.get("persona") or user.get("department") or user.get("device_name") or "unknown")
                    reporter.current_target = "network_config"

                if is_mab:
                    print(f"Switching to MAB device (MAC + device name)...")
                else:
                    print(f"Switching to user {username}...")
                
                # Determine MAC: User-specific > Random if enabled > Don't change
                target_mac = user.get("mac")
                if not target_mac and use_random_mac:
                    # Randomize MAC intelligently by matching the exact device OS or persona
                    target_mac = identity_switcher.generate_persona_mac(prof_key)
                    identity_switcher.change_mac_address(args.interface, target_mac)
                elif target_mac:
                    # User has a specific static MAC assigned (also set via NM profile so it sticks)
                    # If identity is Mac/Apple but stored MAC has non-Apple OUI (e.g. Dell), ISE will show "Dell-Device".
                    # Override so Mac users always use an Apple OUI.
                    is_mac_user = (str(user.get("os") or "").strip().lower() == "mac" or
                                   str(user.get("manufacturer") or "").strip().lower() == "apple")
                    if is_mac_user and not _mac_has_apple_oui(target_mac):
                        logger.info("Identity is Mac/Apple but stored MAC has non-Apple OUI — generating Apple MAC")
                        target_mac = identity_switcher.generate_persona_mac("Mac")
                        identity_switcher.change_mac_address(args.interface, target_mac)
                    else:
                        identity_switcher.change_mac_address(args.interface, target_mac)
                
                if is_mab:
                    identity_switcher.apply_mab_connection(args.interface, dhcp_hostname=dhcp_hostname, cloned_mac=target_mac, vendor_class=vendor_class, ssid=current_ssid)
                    identity_switcher.bounce_interface(args.interface, shutdown_duration=args.shutdown_duration,
                                                    dhcp_hostname=dhcp_hostname, mab=True, vendor_class=vendor_class, dhcp_req_list=dhcp_req_list)
                else:
                    identity_switcher.update_wpa_config(args.interface, current_ssid, username, password,
                                                        dhcp_hostname=dhcp_hostname, cloned_mac=target_mac, vendor_class=vendor_class)
                    identity_switcher.bounce_interface(args.interface, shutdown_duration=args.shutdown_duration,
                                                        dhcp_hostname=dhcp_hostname, mab=False, vendor_class=vendor_class, dhcp_req_list=dhcp_req_list)

                # Start DHCP fingerprint injector + HTTP persona spoofer so ISE profiles this device correctly
                identity_switcher.launch_fingerprint_tools(
                    interface=args.interface,
                    mac=target_mac or "",
                    persona=prof_key or "",
                    hostname=dhcp_hostname or "",
                )
            
            # 3. Wait for Network (longer after 2nd/3rd switch — NM/supplicant can be slow)
            # First verify the lab interface itself has an IP — wlan0 (mgmt) can reach
            # the orchestrator even when eth0 auth failed, so we must check eth0 directly.
            lab_if_ready = args.skip_bounce
            if not lab_if_ready:
                if reporter:
                    reporter.current_status = "waiting_dhcp"
                    reporter.current_target = args.interface
                for attempt in range(35):
                    result = subprocess.run(
                        ["ip", "-4", "addr", "show", args.interface],
                        capture_output=True, text=True
                    )
                    if "inet " in result.stdout:
                        lab_if_ready = True
                        logger.info(f"Lab interface {args.interface} has an IP — proceeding")
                        break
                    time.sleep(3)
                if not lab_if_ready:
                    logger.warning(f"Lab interface {args.interface} has no IP after ~105s — 802.1x auth may have failed. Skipping session.")
                    if reporter:
                        reporter.current_status = "dhcp_failed"
                        reporter.current_target = args.interface

            check_target = args.orchestrator_url
            if lab_if_ready and wait_for_network(target_url=check_target):
                if reporter:
                     reporter.current_status = "connected"
                # 4. Generate Traffic
                # Pick a traffic mode based on department/persona (or use identity's urls for IoT)
                department = user.get("department", "").lower()
                persona = user.get("persona", "")
                # IoT devices have persona (Badge Reader, Camera, etc.) but may lack department
                is_iot_persona = bool(persona) and not user.get("username")
                
                if is_iot_persona:
                    mode = "iot"
                elif department in ["engineering", "it", "devops", "development"]:
                    mode = "dev"
                elif department in ["iot", "operations", "manufacturing"]:
                    mode = "iot"
                else:
                    mode = "office"
                
                logger.info(f"Selected persona '{mode}' based on department '{user.get('department', 'Unknown')}'")
                if reporter:
                    reporter.current_status = "generating_traffic"
                    reporter.current_persona = mode
                    reporter.current_target = "traffic_targets"
                
                session_start_ts = time.time()
                run_traffic_session(args.session_duration, mode=mode, identity=user, history_queue=history_queue)
                session_ok = True
                if args.orchestrator_url and args.runner_id:
                    policy_rows = [
                        row for row in list(history_queue)
                        if isinstance(row, dict)
                        and row.get("expected_action")
                        and float(row.get("timestamp", 0) or 0) >= session_start_ts
                    ]
                    if policy_rows:
                        post_policy_test_results(
                            orchestrator_url=args.orchestrator_url,
                            runner_id=args.runner_id,
                            session_id=session_id,
                            identity=user,
                            results=policy_rows,
                        )
            
            else:
                logger.warning("Skipping traffic generation due to network failure.")
            
            # Check if one-shot
            if args.one_shot:
                logger.info("One-shot session complete. Exiting.")
                break

            # 5. Cooldown / Wait
            if not args.no_cooldown:
                if reporter:
                    reporter.current_status = "cooldown"
                    reporter.current_target = "none"
                logger.info(f"Session complete. Cooling down for {args.cooldown}s...")
                time.sleep(args.cooldown)
            
    except KeyboardInterrupt:
        logger.info("Auto-Runner stopped by user.")
    finally:
        # Stop telemetry reporter before any cleanup so it doesn't race.
        if reporter:
            reporter.running = False
            reporter.join(timeout=1)

        # One-shot cleanup: bring the lab interface down so runner_agent starts
        # the next session from a clean state.  ONLY touch the lab interface —
        # never the management interface (the one used to reach the orchestrator).
        if args.one_shot:
            try:
                mgmt_if = getattr(args, "management_interface", None)
                if args.interface and mgmt_if and args.interface != mgmt_if:
                    logger.info("One-shot cleanup: bringing down lab interface %s", args.interface)
                    subprocess.run(
                        ["sudo", "ip", "link", "set", args.interface, "down"],
                        stderr=subprocess.DEVNULL, check=False,
                    )
            except Exception as e:
                logger.debug("Could not bring down lab interface on exit: %s", e)

        # Restore hostname only if we captured the Pi's real hostname, not a previous
        # identity (e.g. tclark-ws, fthompson-ws). Identity hostnames typically end
        # with -ws; skip restore in that case so we don't set hostname to the wrong user.
        if original_hostname and not original_hostname.endswith("-ws"):
            try:
                subprocess.run(["hostnamectl", "set-hostname", original_hostname], check=False)
                with open("/etc/hosts", "r") as f:
                    lines = f.readlines()
                with open("/etc/hosts", "w") as f:
                    for line in lines:
                        if line.startswith("127.0.1.1"):
                            f.write(f"127.0.1.1\t{original_hostname}\n")
                        else:
                            f.write(line)
                logger.info(f"Restored hostname to {original_hostname}")
            except Exception as e:
                logger.warning(f"Could not restore hostname: {e}")
        elif original_hostname and original_hostname.endswith("-ws"):
            logger.info("Skipping hostname restore (identity hostname); next session will set new identity")

        if args.one_shot and not session_ok:
            sys.exit(1)

if __name__ == "__main__":
    print("DEBUG: Script entry point reached", flush=True)
    try:
        main()
    except Exception as e:
        print(f"DEBUG: Exception in main: {e}", flush=True)
        import traceback
        traceback.print_exc()
