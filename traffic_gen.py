#!/usr/bin/env python3
"""
Clarion Lab Traffic Generator

Simulates realistic network traffic from Clients (Pis/Windows) to Servers.
Supports different "Personas" to generate varied traffic patterns.
"""

import argparse
import time
import random
import logging
import socket
from urllib.parse import urlparse
import requests
import json
import sys
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("traffic_gen.log")
    ]
)
logger = logging.getLogger("TrafficGen")

# Load Configuration
def load_traffic_config(path: str) -> Dict:
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Config file not found: {path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file: {e}")
        return {}

def resolve_targets(persona_config: Dict, all_targets: Dict) -> List[str]:
    """Resolve target groups and specific targets into a single list."""
    resolved = []
    
    # Add groups
    for group in persona_config.get("target_groups", []):
        if group in all_targets:
            resolved.extend(all_targets[group])
        else:
            logger.warning(f"Unknown target group: {group}")
            
    # Add specific targets
    resolved.extend(persona_config.get("specific_targets", []))
    
    return list(set(resolved)) # Dedupe

def persona_to_user_agent(persona: str) -> str:
    """Build a User-Agent string for ISE profiling from an IoT persona name (e.g. Badge Reader -> ClarionLab-BadgeReader/1.0)."""
    if not persona or not str(persona).strip():
        return "ClarionLab/1.0"
    # Remove spaces so ISE can match: "Badge Reader" -> "BadgeReader"
    clean = "".join(str(persona).strip().split())
    return f"ClarionLab-{clean}/1.0"


ALL_POTENTIAL_TARGETS = [
    "http://192.168.31.2:9001/badge/events",
    "http://192.168.31.2:9002/camera/stream",
    "http://192.168.31.2:9006/lock/events",
    "http://192.168.20.3:9004/telemetry",
    "http://192.168.20.3:9005/hvac/status",
    "http://192.168.20.3:9007/display/feed",
    "http://192.168.20.4:9002/camera/stream",
    "http://192.168.20.4:9003/printer/jobs",
    "http://192.168.20.4:9010/medical/telemetry",
    "http://thehub.netlab.net/index.html",
    "http://finance.netlab.net/index.html",
    "http://code.netlab.net/index.html",
    "http://engineering.netlab.net/index.html",
    "http://cmdb.netlab.net/index.html",
    "http://mab.netlab.net/index.html",
    "http://www.netlab.net/index.html"
]


class TrafficPersona:
    """A traffic persona that generates HTTP requests to targets."""
    
    def __init__(self, name: str, config: Dict, history_queue=None):
        self.name = name
        raw = config.get("targets", [])
        self.targets = [t.strip() for t in raw if t and str(t).strip()]
        self.policy_test_cases = [c for c in (config.get("policy_test_cases") or []) if isinstance(c, dict)]
        self.method = config.get("method", "GET")
        self.min_sleep = config.get("min_sleep", 5)
        self.max_sleep = config.get("max_sleep", 30)
        self.user_agent = config.get("user_agent")
        self.drift_behavioral = bool(config.get("drift_behavioral", False))
        self.drift_cohort = bool(config.get("drift_cohort", False))
        self.history_queue = history_queue  # Optional deque/list to store request history
        self.running = True
        self._target_index = 0  # Round-robin across all targets
        
    def run(self):
        """Main loop for generating traffic."""
        target_count = len(self.policy_test_cases) if self.policy_test_cases else len(self.targets)
        logger.info(f"[{self.name}] Starting traffic generation to {target_count} targets")
        headers = {"User-Agent": self.user_agent} if self.user_agent else {}
        start_session_time = time.time()
        
        while self.running:
            if not self.targets and not self.policy_test_cases:
                logger.warning(f"[{self.name}] No targets configured!")
                break
            case = None
            if self.policy_test_cases:
                case = self.policy_test_cases[self._target_index % len(self.policy_test_cases)]
                target = str(case.get("target_url") or "").strip()
                method = str(case.get("method") or self.method or "GET").upper()
                expected_action = str(case.get("expected_action") or "").strip().lower()
                case_id = str(case.get("case_id") or "")
            else:
                is_drift_target = False
                if self.drift_cohort and random.random() < 0.20:
                    potential_drift_targets = [t for t in ALL_POTENTIAL_TARGETS if t not in self.targets]
                    if potential_drift_targets:
                        target = random.choice(potential_drift_targets)
                        is_drift_target = True
                
                if not is_drift_target:
                    target = self.targets[self._target_index % len(self.targets)]
                
                method = str(self.method or "GET").upper()
                expected_action = ""
                case_id = ""
                if is_drift_target:
                    logger.info(f"[{self.name}] COHORT DRIFT: Selected non-cohort target: {target}")
            
            self._target_index += 1
            start_time = time.time()
            status_code = 0
            observed_action = "unknown"
            test_result = "inconclusive"
            
            try:
                parsed = urlparse(target)
                scheme = (parsed.scheme or "").lower()

                if scheme == "tcp":
                    host = parsed.hostname
                    port = parsed.port
                    if not host or not port:
                        raise ValueError(f"Invalid TCP target: {target}")
                    with socket.create_connection((host, port), timeout=5):
                        pass
                    status_code = 200
                    observed_action = "allow"
                    logger.info(f"[{self.name}] TCP {host}:{port} -> connected")
                # Make request based on method (User-Agent helps ISE profile device type)
                elif method == "GET":
                    response = requests.get(target, timeout=10, headers=headers)
                    status_code = response.status_code
                    observed_action = "allow"
                    logger.info(f"[{self.name}] GET {target} -> {status_code}")
                elif method == "POST":
                    # Simple POST with minimal data
                    response = requests.post(target, json={"timestamp": time.time()}, timeout=10, headers=headers)
                    status_code = response.status_code
                    observed_action = "allow"
                    logger.info(f"[{self.name}] POST {target} -> {status_code}")
                else:
                    logger.warning(f"[{self.name}] Unknown method: {method}")
            
            except (requests.exceptions.RequestException, OSError, ValueError) as e:
                logger.warning(f"[{self.name}] Request failed to {target}: {e}")
                status_code = -1 # Error
                observed_action = "deny"

            if expected_action in ("allow", "deny"):
                test_result = "pass" if observed_action == expected_action else "fail"
                
            # Log to history if available
            if self.history_queue is not None:
                duration = time.time() - start_time
                self.history_queue.append({
                    "timestamp": time.time(),
                    "target": target,
                    "method": method,
                    "status": status_code,
                    "latency": round(duration, 3),
                    "case_id": case_id,
                    "expected_action": expected_action,
                    "observed_action": observed_action,
                    "test_result": test_result,
                })
            
            # Sleep for random duration
            current_min = self.min_sleep
            current_max = self.max_sleep
            if self.drift_behavioral:
                elapsed = time.time() - start_session_time
                decay = max(0.1, 1.0 - (elapsed / 600.0))
                current_min = max(1.0, self.min_sleep * decay)
                current_max = max(2.0, self.max_sleep * decay)
                
            sleep_time = random.uniform(current_min, current_max)
            if self.drift_behavioral:
                logger.info(f"[{self.name}] TIMING DRIFT: sleep range {current_min:.1f}s-{current_max:.1f}s, sleeping {sleep_time:.1f}s")
            
            # Check running flag periodically during sleep
            remaining = sleep_time
            while remaining > 0 and self.running:
                chunk = min(1.0, remaining)
                time.sleep(chunk)
                remaining -= chunk
            
        logger.info(f"[{self.name}] Stopped traffic generation")

def main():
    parser = argparse.ArgumentParser(description="Clarion Lab Traffic Generator")
    parser.add_argument("--mode", default="random", help="Simulation mode (office, dev, iot)")
    parser.add_argument("--threads", type=int, default=1, help="Number of concurrent threads")
    parser.add_argument("--config", default="traffic_config.json", help="Path to traffic config file")
    args = parser.parse_args()

    # Load Config
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(os.path.dirname(__file__), config_path)
        
    full_config = load_traffic_config(config_path)
    if not full_config:
        sys.exit(1)
        
    personas_def = full_config.get("personas", {})
    all_targets = full_config.get("targets", {})

    # Select Mode
    mode = args.mode
    if mode == "random" or mode not in personas_def:
        if mode != "random":
             logger.warning(f"Mode '{mode}' not found in config. Picking random.")
        mode = random.choice(list(personas_def.keys()))

    logger.info(f"Starting Traffic Generator Mode: {mode}")
    
    # Prepare Persona Config object
    p_def = personas_def[mode]
    
    # Hydrate targets list for the persona object
    final_targets = resolve_targets(p_def, all_targets)
    
    persona_config = {
        "targets": final_targets,
        "method": p_def.get("method", "GET"),
        "min_sleep": p_def.get("min_sleep", 5),
        "max_sleep": p_def.get("max_sleep", 30)
    }
    
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        for i in range(args.threads):
            persona = TrafficPersona(f"{mode.capitalize()}-{i+1}", persona_config)
            executor.submit(persona.run)

if __name__ == "__main__":
    main()
