#!/usr/bin/env python3
"""
Clarion Lab Orchestrator - Validator Engine

Parses ground truth logs from the orchestrator and queries the Clarion 
PostgreSQL database to validate that expected traffic was seen and no 
unexpected traffic occurred.
"""

import csv
import logging
import os
import urllib.parse
from datetime import datetime
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

try:
    import psycopg2
    from psycopg2.extras import DictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    logger.warning("psycopg2 is not installed! Validator Engine will not be able to connect to PostgreSQL.")

def get_db_connection():
    """Connects to the Clarion PostgreSQL database."""
    try:
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", 5432),
            dbname=os.getenv("POSTGRES_DB", "clarion"),
            user=os.getenv("POSTGRES_USER", "clarion"),
            password=os.getenv("POSTGRES_PASSWORD", "clarion")
        )
        return conn
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        return None

logger = logging.getLogger(__name__)

DEFAULT_CSV_PATH = os.path.expanduser("~/clarion/lab/ground_truth/ground_truth_log.csv")


def parse_csv_history(csv_path=DEFAULT_CSV_PATH, limit=50):
    """
    Parses the ground truth CSV log from the lab orchestrator.
    Returns a list of run dictionaries in descending chronological order.
    """
    if not os.path.exists(csv_path):
        return []
    
    runs = []
    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Basic validation
                if not row.get('timestamp') or not row.get('device_mac'):
                    continue
                runs.append(row)
    except Exception as e:
        logger.error(f"Failed to read CSV runs from {csv_path}: {e}")
        return []
    
    # Sort descending by timestamp
    runs.sort(key=lambda x: x['timestamp'], reverse=True)
    return runs[:limit]


def clear_history(csv_path=DEFAULT_CSV_PATH):
    """
    Clears the ground truth CSV log by overwriting it with just the headers.
    Returns True if successful, False otherwise.
    """
    if not os.path.exists(csv_path):
        return True
    
    try:
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            # Keep header aligned with LabOrchestrator.log_ground_truth() field order.
            writer.writerow([
                'timestamp',
                'status',
                'runner',
                'device_mac',
                'device_name',
                'persona',
                'expected_destinations',
                'expected_protocols',
                'session_duration_seconds',
                'scheduled_end_timestamp',
            ])
        return True
    except Exception as e:
        logger.error(f"Failed to clear CSV history at {csv_path}: {e}")
        return False

def validate_run(run_data):
    """
    Executes validation for a single orchestrated run.
    Expects run_data dictionary to have:
      - timestamp (ISO 8601 start time)
      - device_mac
      - expected_destinations (comma separated URLs/IPs)
      - expected_protocols
    """
    report = {
        "status": "pending",
        "timestamp": run_data.get('timestamp'),
        "device_mac": run_data.get('device_mac'),
        "persona": run_data.get('persona', 'Unknown'),
        "expected_destinations": [],
        "matched": [],
        "missing": [],
        "unexpected": [],
        "associated_ips": []
    }

    mac_address = run_data.get('device_mac', '').strip().lower()
    if not mac_address or mac_address == "unknown":
        report["status"] = "error"
        report["error"] = "Invalid MAC address in ground truth log."
        return report

    raw_timestamp = run_data.get('timestamp')
    try:
        start_dt = date_parser.parse(raw_timestamp)
    except Exception:
        report["status"] = "error"
        report["error"] = f"Invalid timestamp format: {raw_timestamp}"
        return report
        
    start_ts_ms = int(start_dt.timestamp() * 1000)

    # Use the scheduled session duration when available.
    # Ground truth timestamp is recorded at assignment time, so we also use a small
    # pre-roll to avoid missing traffic that starts a bit later.
    try:
        session_duration_seconds = float(run_data.get("session_duration_seconds") or 0)
    except Exception:
        session_duration_seconds = 0
    if session_duration_seconds <= 0:
        session_duration_seconds = 15 * 60  # backward-compatible fallback

    pre_roll_seconds = 60
    post_roll_seconds = 300
    end_ts_ms = start_ts_ms + int(session_duration_seconds * 1000) + int(post_roll_seconds * 1000)
    start_ts_ms = max(0, start_ts_ms - int(pre_roll_seconds * 1000))

    raw_dests = run_data.get('expected_destinations', '')
    expected_dests = [d.strip() for d in raw_dests.split(',') if d.strip()]
    report["expected_destinations"] = expected_dests
    
    # Standardize expected targets (extract hostnames/IPs from URLs)
    expected_hosts = set()
    for dest in expected_dests:
        if "://" in dest:
            parsed = urllib.parse.urlparse(dest)
            expected_hosts.add(parsed.hostname.lower())
        else:
            # Handle IP:port or just FQDN
            host = dest.split(':')[0]
            expected_hosts.add(host.lower())

    conn = get_db_connection()
    if not conn:
        report["status"] = "error"
        report["error"] = "Failed to connect to Clarion Database."
        return report

    try:
        cursor = conn.cursor()

        # Step 1: Find all IPs associated with this MAC in the given time window
        # Querying identity_timeline for IP mappings
        cursor.execute("""
            SELECT DISTINCT ip_address::text
            FROM identity_timeline
            WHERE lower(mac_address::text) = %s 
              AND first_seen <= %s 
              AND last_seen >= %s
              AND ip_address IS NOT NULL
        """, (mac_address, end_ts_ms, start_ts_ms))
        
        ips = [row[0] for row in cursor.fetchall() if row[0]]
        report["associated_ips"] = ips

        if not ips:
            # Maybe the connection data is in L2 flows without L3 IP? 
            # Or identity correlates failed. We will fallback to MAC based queries where possible.
            report["status"] = "failed"
            report["error"] = "No IP associated with MAC in timeline during this window."
            return report

        # Step 2: Query observed traffic for these IPs 
        # Using l7_metadata to catch Application level visibility (Zeek L7)
        # We look at both src_ip and dst_ip just in case, but usually runner is src_ip
        placeholders = ', '.join(['%s'] * len(ips))
        query = f"""
            SELECT DISTINCT dst_ip
            FROM l7_metadata
            WHERE src_ip IN ({placeholders})
              AND timestamp >= to_timestamp(%s)
              AND timestamp <= to_timestamp(%s)
        """
        
        # Postgres timestamps for parameterized queries expect seconds
        q_args = ips + [start_ts_ms / 1000.0, end_ts_ms / 1000.0]
        cursor.execute(query, q_args)
        
        observed_dst_ips = set(row[0] for row in cursor.fetchall() if row[0])

        # Step 3: Reconcile observed vs expected
        # To do this robustly, we'd need to know what IPs the FQDNs resolved to (DNS logs).
        # We will query Zeek DNS logs in l7_metadata to map expected FQDN -> Observed IPs
        
        resolved_expected_ips = set()
        for fqdn in expected_hosts:
            # Check if it's already an IP
            import ipaddress
            try:
                ipaddress.ip_address(fqdn)
                resolved_expected_ips.add(fqdn)
                continue
            except ValueError:
                pass 
                
            # If it's a domain, see if Zeek saw this runner resolve it
            dns_query = f"""
                SELECT event_data->>'answers' 
                FROM l7_metadata 
                WHERE src_ip IN ({placeholders})
                  AND protocol = 'dns'
                  AND event_data->>'query' = %s
                  AND timestamp >= to_timestamp(%s)
                  AND timestamp <= to_timestamp(%s)
            """
            dns_args = ips + [fqdn, start_ts_ms / 1000.0, end_ts_ms / 1000.0]
            cursor.execute(dns_query, dns_args)
            
            # Simple parsing: answers is usually a JSON array or string
            for row in cursor.fetchall():
                ans = row[0]
                if ans:
                    import json
                    try:
                        # Depends on exact Zeek JSON schema in Clarion
                        if isinstance(ans, str):
                            ans_list = json.loads(ans)
                        else:
                            ans_list = ans
                        if isinstance(ans_list, list):
                            for a in ans_list:
                                resolved_expected_ips.add(str(a))
                    except json.JSONDecodeError:
                        # Fallback simple string match
                        resolved_expected_ips.add(str(ans))

        # Core Infrastructure IPs that are ALWAYS allowed and ignored as "Unexpected"
        ALLOWED_INFRA_IPS = {
            '255.255.255.255', '0.0.0.0', # Broadcasts
            # Commonly ignored local ranges could be added here
        }
        
        # Determine Matches and Missing
        matched_destinations = set()
        missing_destinations = set()

        # Did we see traffic to the expected destinations?
        for expected_ip in resolved_expected_ips:
            if expected_ip in observed_dst_ips:
                matched_destinations.add(expected_ip)
            else:
                missing_destinations.add(expected_ip)

        # Are there destinations we saw but didn't expect?
        unexpected_destinations = set()
        for obs_ip in observed_dst_ips:
            if obs_ip not in resolved_expected_ips and obs_ip not in ALLOWED_INFRA_IPS:
                # Basic check for multicast/broadcast
                if obs_ip.startswith('224.') or obs_ip.startswith('239.') or obs_ip.endswith('.255'):
                    continue
                unexpected_destinations.add(obs_ip)
        
        report["matched"] = list(matched_destinations)
        report["missing"] = list(missing_destinations)
        report["unexpected"] = list(unexpected_destinations)

        if missing_destinations or unexpected_destinations:
            report["status"] = "anomalous"
        else:
            report["status"] = "success"

        return report

    except Exception as e:
        logger.error(f"Validation failed: {e}", exc_info=True)
        report["status"] = "error"
        report["error"] = str(e)
        return report
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    # Quick standalone test format
    runs = parse_csv_history()
    print(f"Found {len(runs)} orchestrator runs in history.")
    if runs:
        print(validate_run(runs[0]))
