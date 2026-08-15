#!/usr/bin/env python3
import socket
import urllib.request
import json
import sys

_KNOWN_NETLAB_HOST_TO_IP = {
    "www.netlab.net": "192.168.40.2",
    "finance.netlab.net": "192.168.30.2",
    "code.netlab.net": "192.168.30.2",
    "thehub.netlab.net": "192.168.30.2",
    "engineering.netlab.net": "192.168.30.2",
    "mab.netlab.net": "192.168.30.2",
    "cmdb.netlab.net": "192.168.30.2",
    "iotdev.netlab.net": "192.168.31.2",
    "iotsrv1.netlab.net": "192.168.20.3",
}

def resolve_target(target):
    raw = (target or "").strip().lower()
    if not raw:
        return None
    
    # Check if target is already an IP address
    parts = raw.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return raw

    host_key = raw.split("/")[0].split(":")[0]
    if host_key in _KNOWN_NETLAB_HOST_TO_IP:
        return _KNOWN_NETLAB_HOST_TO_IP[host_key]
    
    try:
        return socket.gethostbyname(host_key)
    except Exception:
        return None

def test_tcp_port(ip, port, timeout=2):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True, "Port Open"
    except Exception as e:
        return False, str(e)

def main():
    orchestrator_url = "http://192.168.20.95:5000/api/services"
    print(f"Fetching services from {orchestrator_url}...")
    try:
        with urllib.request.urlopen(orchestrator_url, timeout=5) as resp:
            services = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Failed to fetch services config: {e}")
        sys.exit(1)

    print(f"Found {len(services)} services. Filtering for internal lab servers...")
    
    internal_services = []
    for svc in services:
        target = svc.get("target")
        resolved = resolve_target(target)
        # We consider it internal if it resolves to a private IP (starts with 192.168 or 10. or 172.)
        if resolved and (resolved.startswith("192.168.") or resolved.startswith("10.") or resolved.startswith("172.")):
            internal_services.append((svc, resolved))

    print(f"Testing {len(internal_services)} internal services:\n")
    print(f"{'Service ID':<25} | {'Service Name':<32} | {'Target':<22} | {'Port':<5} | {'Resolved IP':<15} | {'TCP Connect':<12} | {'HTTP/Probe Status'}")
    print("-" * 135)
    
    results = {}
    for svc, resolved_ip in internal_services:
        svc_id = svc.get("id")
        name = svc.get("name")
        target = svc.get("target")
        port = svc.get("port")
        protocol = svc.get("protocol", "http")
        path = svc.get("path") or ""
        
        # Test TCP
        tcp_ok, tcp_msg = test_tcp_port(resolved_ip, port)
        
        # Test HTTP if protocol is http/https and TCP port is open
        http_status = "N/A"
        if tcp_ok and protocol in ("http", "https"):
            sep = "" if path.startswith("/") or not path else "/"
            url = f"{protocol}://{resolved_ip}:{port}{sep}{path}"
            try:
                req = urllib.request.Request(url, method="GET")
                req.add_header("Host", target)
                req.add_header("User-Agent", "ClarionLab-Validator/2.0")
                with urllib.request.urlopen(req, timeout=2) as r:
                    http_status = f"HTTP {r.status} (OK)"
            except urllib.error.HTTPError as e:
                http_status = f"HTTP {e.code}"
            except Exception as e:
                http_status = f"Err: {type(e).__name__}"
        else:
            http_status = tcp_msg
            
        status_str = "PASS" if tcp_ok else "FAIL"
        print(f"{svc_id:<25} | {name[:32]:<32} | {target:<22} | {port:<5} | {resolved_ip:<15} | {status_str:<12} | {http_status}")
        
        results.setdefault(resolved_ip, []).append({
            "id": svc_id,
            "name": name,
            "port": port,
            "ok": tcp_ok,
            "detail": http_status
        })

    print("\nSummary by Server IP:")
    for ip, svcs in sorted(results.items()):
        total = len(svcs)
        up = sum(1 for s in svcs if s["ok"])
        print(f"  - {ip}: {up}/{total} services serving successfully")

if __name__ == "__main__":
    main()
