#!/usr/bin/env python3
"""
Clarion Lab Validation & Analytics Engine

Compares 'Ground Truth' (what we commanded the lab to do)
vs. 'Clarion Perception' (what Clarion grouped/policy).

Usage:
    python3 validate_clarion_grouping.py --ground-truth ground_truth.csv --mock
"""

import argparse
import csv
import json
import logging
import sys
import datetime
import requests
import statistics
from typing import Dict, List, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("validator")

class ClarionValidator:
    def __init__(self, ground_truth_path: str, api_url: str, mock: bool = False):
        self.ground_truth_path = ground_truth_path
        self.api_url = api_url.rstrip('/')
        self.mock = mock
        self.ground_truth_data = []
        self.clarion_data = {"devices": [], "groups": []}
        self.report = {
            "timestamp": datetime.datetime.now().isoformat(),
            "metrics": {},
            "details": []
        }

    def load_ground_truth(self):
        """Load ground truth CSV."""
        logger.info(f"Loading ground truth from {self.ground_truth_path}")
        try:
            with open(self.ground_truth_path, 'r') as f:
                reader = csv.DictReader(f)
                self.ground_truth_data = list(reader)
            logger.info(f"Loaded {len(self.ground_truth_data)} ground truth entries")
        except FileNotFoundError:
            logger.error("Ground truth file not found!")
            sys.exit(1)

    def _extract_list_payload(self, payload: Any, key: str) -> List[Dict[str, Any]]:
        """Support both legacy list responses and current paginated dict responses."""
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            value = payload.get(key, [])
            if isinstance(value, list):
                return value
        return []

    def fetch_clarion_data(self):
        """Fetch device and group data from Clarion API (or mock)."""
        if self.mock:
            logger.info("Using MOCK Clarion data")
            self._generate_mock_data()
        else:
            logger.info(f"Querying Clarion API at {self.api_url}")
            try:
                # Fetch Devices
                dev_resp = requests.get(f"{self.api_url}/devices", timeout=5)
                if dev_resp.status_code == 200:
                    self.clarion_data["devices"] = self._extract_list_payload(dev_resp.json(), "devices")
                else:
                    logger.warning(f"Failed to fetch devices: {dev_resp.status_code}")

                # Fetch Groups
                grp_resp = requests.get(f"{self.api_url}/groups", timeout=5)
                if grp_resp.status_code == 200:
                    self.clarion_data["groups"] = self._extract_list_payload(grp_resp.json(), "groups")
                else:
                    logger.warning(f"Failed to fetch groups: {grp_resp.status_code}")
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"API Connection failed: {e}")
                if not self.clarion_data["devices"]:
                    logger.warning("Falling back to empty data structure")

    def _generate_mock_data(self):
        """Generate mock data that roughly matches ground truth but with some 'errors' to test scoring."""
        # Simple mock: assume Clarion saw 90% of devices
        mock_devices = []
        for entry in self.ground_truth_data:
            # Simulate 10% missed devices
            if hash(entry['device_mac']) % 10 == 0:
                continue
                
            mock_dev = {
                "mac": entry['device_mac'],
                "ip": "192.168.1.x",
                "hostname": entry['device_name'],
                "assigned_group": entry['persona'], # mostly correct
                "last_seen": entry['timestamp']
            }
            
            # Simulate some grouping errors (Identity Churn confusion)
            if "Sales" in entry['persona'] and hash(entry['timestamp']) % 20 == 0:
                mock_dev["assigned_group"] = "Finance" # Wrong group
                
            mock_devices.append(mock_dev)
            
        self.clarion_data["devices"] = mock_devices
        logger.info(f"Generated {len(mock_devices)} mock devices")

    def analyze(self):
        """Compare Ground Truth vs Clarion Perception."""
        logger.info("Analyzing data...")
        
        # 1. Unique Devices in Ground Truth
        gt_devices = {d['device_mac']: d for d in self.ground_truth_data} # Last entry wins
        clarion_devices = {}
        for d in self.clarion_data["devices"]:
            if not isinstance(d, dict):
                continue
            key = (d.get("mac") or d.get("endpoint_id") or "").strip().lower()
            if key:
                clarion_devices[key] = d
        
        total_gt = len(gt_devices)
        if total_gt == 0:
            logger.warning("No ground truth data to analyze")
            return

        # Metrics
        found_count = 0
        correct_group_count = 0
        
        for mac, gt_entry in gt_devices.items():
            device_result = {
                "mac": mac,
                "name": gt_entry['device_name'],
                "expected_persona": gt_entry['persona'],
                "status": "missing",
                "observed_group": None
            }
            
            normalized_mac = (mac or "").strip().lower()
            if normalized_mac in clarion_devices:
                found_count += 1
                c_dev = clarion_devices[normalized_mac]
                device_result["status"] = "found"
                # Current APIs expose cluster_label/sgt_name; older mock payload used assigned_group.
                observed_group = (
                    c_dev.get("assigned_group")
                    or c_dev.get("cluster_label")
                    or c_dev.get("sgt_name")
                    or "Unassigned"
                )
                device_result["observed_group"] = observed_group
                
                # Check Grouping
                # Relaxed matching: string containment (e.g. "Sales" in "Sales-Dept")
                if gt_entry['persona'].lower() in str(device_result["observed_group"]).lower():
                     device_result["grouping_verdict"] = "correct"
                     correct_group_count += 1
                else:
                     device_result["grouping_verdict"] = "incorrect"
            
            self.report["details"].append(device_result)

        # Calculate Scores
        coverage = found_count / total_gt if total_gt > 0 else 0
        purity = correct_group_count / found_count if found_count > 0 else 0
        
        self.report["metrics"] = {
            "total_devices_expected": total_gt,
            "devices_seen_by_clarion": found_count,
            "correlation_coverage": round(coverage, 2),
            "grouping_purity": round(purity, 2),
            "pass": coverage >= 0.9 and purity >= 0.85
        }
        
    def save_report(self, output_path: str):
        """Save analysis report to JSON."""
        try:
            with open(output_path, 'w') as f:
                json.dump(self.report, f, indent=2)
            logger.info(f"Report saved to {output_path}")
            
            # Print Summary
            print("\n=== Validation Report ===")
            print(f"Coverage: {self.report['metrics']['correlation_coverage']*100}%")
            print(f"Purity:   {self.report['metrics']['grouping_purity']*100}%")
            print(f"Result:   {'PASS' if self.report['metrics']['pass'] else 'FAIL'}")
            print("=========================\n")
            
        except Exception as e:
            logger.error(f"Failed to save report: {e}")

def main():
    parser = argparse.ArgumentParser(description="Clarion Lab Validator")
    parser.add_argument("--ground-truth", required=True, help="Path to ground_truth_log.csv")
    parser.add_argument("--clarion-api", default="http://localhost:8000/api", help="Clarion API URL")
    parser.add_argument("--output", default="validation_report.json", help="Output JSON path")
    parser.add_argument("--mock", action="store_true", help="Use mock data (no API call)")
    
    args = parser.parse_args()
    
    validator = ClarionValidator(args.ground_truth, args.clarion_api, args.mock)
    validator.load_ground_truth()
    validator.fetch_clarion_data()
    validator.analyze()
    validator.save_report(args.output)

if __name__ == "__main__":
    main()
