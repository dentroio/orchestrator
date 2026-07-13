# Clarion Lab Master Plan

**Last Updated:** February 12, 2026  
**Status:** Design & Implementation Phase  
**Goal:** Prove Clarion endpoint grouping and policy generation with controlled lab testbed

---

## Lab Objective

### Current Phase: Data Validation & Enrichment

**Immediate Goal (Phase 0):**

Validate that Clarion can reliably **collect and correlate** data from multiple sources:

- **Who**: AD user, MAB endpoint identity, ISE group/profile (via pxGrid)
- **Where**: wired/wireless path, SSID/VLAN, switch/AP location (via ISE)
- **What**: destination behavior (apps, backends, protocols, cadence) (via NetFlow/IPFIX)
- **Enrichment**: DNS hostnames, DHCP assignments, proper correlation across sources

**Why This Matters:**

Clarion's analytics engine (grouping, policy generation) is still under development. Before we can test those capabilities, we must ensure the foundation is solid: clean, correlated, enriched data flowing into Clarion.

**Current Success Criteria:**

- Data completeness: All identity switches visible in Clarion with complete metadata
- Correlation accuracy: MAC ↔ Username ↔ IP ↔ Hostname correctly linked
- Enrichment quality: Hostnames follow convention (first initial + lastname + "-ws")
- Flow association: NetFlow correctly attributed to identities
- Source coverage: pxGrid, NetFlow, DNS, DHCP all feeding Clarion

---

### Future Phase: Full Analytics Validation

**Future Goal (When Analytics Are Ready):**

Once Clarion's grouping and policy generation features are complete, validate:

- Identity correlation coverage (`who+where+what` complete): **>= 90%**
- Correct persona grouping purity: **>= 85%**
- IoT backend exclusivity (correct destinations): **>= 90%**
- False merges (wrong personas grouped): **<= 10%**
- Policy recommendation usefulness: **>= 80%**
- Anomaly detection (catches violations): **>= 75%**

## Lab Equipment Inventory

### Client Endpoints (13 total)

| Device Type | Count | Connectivity | Current Status | Primary Role |
|-------------|-------|--------------|----------------|--------------|
| Windows PC  | 4     | Wireless     | Production-ready | Stable user personas (control group) |
| Raspberry Pi | 4    | Wired (eth0) | Production-ready | User/IoT identity rotation (high churn) |
| Raspberry Pi | 2    | Wireless     | Production-ready | Wireless user/IoT variation |
| ESP32       | 2     | Wireless     | Needs firmware | Constrained IoT telemetry |

### Lab Automation Runners (6 Pis - READY)

| Pi ID | Role | IP Address | Assigned Persona(s) | Notes |
|-------|------|------------|---------------------|-------|
| Pi-Runner-1 | Sales baseline | `192.168.1.187` | Sales users (alice.johnson, etc.) | Stable, predictable traffic |
| Pi-Runner-2 | Finance baseline | `192.168.1.193` | Finance users (jane.robinson, etc.) | Stable, business app focus |
| Pi-Runner-3 | Engineering baseline | `192.168.1.188` | Engineering users | Dev/code API access |
| Pi-Runner-4 | IT baseline | `192.168.1.189` | IT users | Admin/service patterns |
| Pi-Runner-5 | IoT persona rotation | `192.168.20.91` | Badge Reader, Camera, Printer, Sensor, HVAC | Wireless MAB, 30-60 min each |
| Pi-Runner-6 | IoT persona rotation | `192.168.20.90` | Door Lock, Display, VoIP, Robot, Medical | Wireless MAB, 30-60 min each |

### Rebuild Pis (5 - TO BUILD)

| Pi ID | New Role | Recommended IP | Purpose |
|-------|----------|----------------|---------|
| Pi-Rebuild-1 | IoT backend services | `192.168.31.3` | Hosts persona-specific endpoints (ports 9001-9010) |
| Pi-Rebuild-2 | Additional endpoint generator | `192.168.1.190` | Increases population, adds lateral traffic test cases |
| Pi-Rebuild-3 | Orchestration controller | `192.168.20.95` | **ASSIGNED** (User: admin). Runs schedules, captures ground truth |
| Pi-Rebuild-4 | Validation & scoring node | `192.168.1.11` | Queries Clarion API, compares to expected, scores |
| Pi-Rebuild-5 | Fault injection / anomaly generator | `192.168.1.12` | Intentional violations for testing detection |

### Existing Backend Servers (3 - ALREADY RUNNING)

| Server IP | Hostnames | Services | Used By |
|-----------|-----------|----------|---------|
| `192.168.30.2` | `thehub.netlab.net`, `finance.netlab.net`, `engineering.netlab.net`, `code.netlab.net`, `mab.netlab.net` | Business apps, internal portals | Sales, Finance, Engineering, IT users |
| `192.168.31.2` | `iotdev.netlab.net` | IoT dev APIs (ports 8080-8082) + **NEW: IoT persona endpoints (9001-9010)** | Engineering, IT, all IoT personas |
| `192.168.40.2` | `www.netlab.net` | Public DMZ-style site | All users (limited), no IoT |

## Final Lab Architecture

### Endpoint pool

- **Windows wireless (4):** stable user baselines (AD group driven behavior)
- **Pi wired (4):** high-confidence wired Dot1x and MAB behavior generation
- **Pi wireless (2):** wireless variation for same personas
- **ESP32 wireless (2):** constrained IoT telemetry clients

### Script runners

Use the 6 ready Pis as dedicated runners/controllers:

- 4 Pis mapped 1:1 to major user personas (Sales, Finance, Engineering, IT)
- 2 Pis mapped to IoT persona rotation schedules

### Rebuilt 5 Pis (recommended use)

- 2 Pis as extra endpoint generators (increase endpoint count/churn)
- 1 Pi as orchestration node (scheduler + runbook automation)
- 1 Pi as ground-truth logger (syslog, test timestamps, expected flow ledger)
- 1 Pi as spare/fault injection node (simulate outages, DNS shifts, route changes)

---

## Device-to-Role Assignments (Execution Map)

### Windows Machines (4) - Stable Control Group

| Device | Hostname | Primary User | AD Groups | Connection | Traffic Profile |
|--------|----------|--------------|-----------|------------|-----------------|
| Win-1 | `WIN-SALES-01` | alice.johnson (Sales) | `Sales-Employees`, `Business-Users` | Wireless (netlab_employee) | thehub, finance → 192.168.30.2 |
| Win-2 | `WIN-FINANCE-01` | jane.robinson (Finance) | `Finance-Dept`, `Business-Users` | Wireless (netlab_employee) | finance, thehub → 192.168.30.2 |
| Win-3 | `WIN-ENG-01` | henry.brown (Engineering) | `Engineering`, `Dev-Team` | Wireless (netlab_employee) | code, engineering, iotdev → 192.168.30.2, 192.168.31.2 |
| Win-4 | `WIN-IT-01` | victor.wilson (IT) | `IT-Admins`, `Infrastructure` | Wireless (netlab_employee) | All apps, admin/troubleshooting access |

**Setup:** Configure each Windows machine with stable AD-backed Dot1x profile (WPA2-Enterprise). Do NOT rotate identity on these—they are baselines for user behavior.

### Raspberry Pi - Wired (4) - High-Churn Identity Rotation

| Device | Pi Runner | Rotation Set | Connection | Session Duration | Notes |
|--------|-----------|--------------|------------|------------------|-------|
| Pi-Wired-1 | Pi-Runner-1 | Sales users (alice, frank, tina) | Wired eth0 (netplan-eth0) | 5-10 min per user | Tests user correlation under churn |
| Pi-Wired-2 | Pi-Runner-2 | Finance users (jane, + others) | Wired eth0 | 5-10 min per user | Stable access pattern, different identity |
| Pi-Wired-3 | Pi-Runner-3 | Engineering users (henry, + others) | Wired eth0 | 5-10 min per user | Dev API heavy |
| Pi-Wired-4 | Pi-Runner-4 | IT users (victor, charlie, + others) | Wired eth0 | 5-10 min per user | Admin patterns |

**Setup:** Run `auto_lab_runner.py` with `--interface eth0`, rotating through assigned user set.

### Raspberry Pi - Wireless (2) - Wireless Variation

| Device | Pi Runner | Rotation Set | Connection | Notes |
|--------|-----------|--------------|------------|-------|
| Pi-Wireless-1 | Pi-Runner-5 | IoT personas (Badge, Camera, Printer, Sensor, HVAC) | Wireless wlan0 | MAB mode, 30-60 min per persona |
| Pi-Wireless-2 | Pi-Runner-6 | IoT personas (Door Lock, Display, VoIP, Robot, Medical) | Wireless wlan0 | MAB mode, 30-60 min per persona |

**Setup:** Run `auto_lab_runner.py` with `--interface wlan0`, MAB identities only.

### ESP32 (2) - Constrained IoT

| Device | Persona | Connection | Behavior | Firmware Needed |
|--------|---------|------------|----------|-----------------|
| ESP32-1 | Environmental Sensor | Wireless (MAB) | MQTT publish to `192.168.31.2:1883` topic `/sensors/env` every 60s | Arduino/ESP-IDF with MQTT |
| ESP32-2 | Door Lock | Wireless (MAB) | HTTP POST to `192.168.31.2:9006/lock/events` every 120s | Arduino with HTTP client |

**Setup:** Flash ESP32 with firmware that does MAB (no 802.1x), uses assigned MAC from identities, sends device name in DHCP if possible.

### Rebuild Pis (5) - Support Infrastructure

#### Pi-Rebuild-1: IoT Backend Services Node

**Role:** Host persona-specific HTTP endpoints (ports 9001-9010) so each IoT persona has a distinct destination.

**Setup:**
1. Deploy simple Flask app or nginx with 10 endpoints (see `iot_backend_mock.py` below)
2. Optional: install Mosquitto for MQTT (port 1883)
3. Configure firewall: allow 9001-9010, 1883 from lab network

#### Pi-Rebuild-2: Additional Endpoint Generator

**Role:** Increase endpoint population; can rotate through user or IoT personas to add volume.

**Setup:** Standard lab runner setup with `auto_lab_runner.py`.

#### Pi-Rebuild-3: Orchestration Controller

**Role:** Central scheduler that starts/stops persona runs on other Pis and logs ground truth.

**Setup:**
1. Install: `pip install fabric paramiko`  # SSH orchestration
2. Deploy `lab_orchestrator.py` (to be created) that:
   - SSH to each Pi runner
   - Starts identity_switcher or auto_lab_runner with specific persona/schedule
   - Logs "at time T, device X became persona Y"
   - Stops sessions after duration
   
#### Pi-Rebuild-4: Validation & Scoring Node

**Role:** Query Clarion API, compare to expected ground truth, output scores.

**Setup:**
1. Deploy `validate_clarion_grouping.py` (to be created)
2. Reads ground truth from orchestrator
3. Queries Clarion `/api/devices`, `/api/groups` (if exists)
4. Compares observed vs expected, calculates scores
5. Outputs pass/fail report

#### Pi-Rebuild-5: Fault Injection / Anomaly Node

**Role:** Generate intentional violations to test Clarion anomaly detection.

**Setup:**
1. Run sessions with intentional policy violations:
   - Sales user accessing `iotdev` APIs (should flag)
   - Camera MAC accessing `finance.netlab.net` (wrong persona behavior)
   - High-volume port scanning
2. Log violations as "expected anomalies" for Clarion to detect

## Detailed Traffic Matrix by Persona

This matrix defines **expected behavior** for Clarion to validate against.

### User Personas (Dot1x + AD)

| Persona | AD Groups | Primary Destinations | Protocols/Ports | Cadence | Notes |
|---------|-----------|---------------------|-----------------|---------|-------|
| **Sales** | `Sales-Employees`, `Business-Users` | `finance.netlab.net` (80/443), `thehub.netlab.net` (80/443), `www.netlab.net` (80) | HTTP/HTTPS, DNS (53), NTP (123) | Frequent (2-15s) | No dev API access, no IoT backend |
| **Finance** | `Finance-Dept`, `Business-Users` | `finance.netlab.net` (80/443), `thehub.netlab.net` (80/443) | HTTP/HTTPS, DNS, NTP | Moderate (5-20s) | Limited DMZ, no dev access |
| **Engineering** | `Engineering`, `Dev-Team` | `code.netlab.net` (80/443), `engineering.netlab.net` (80/443), `iotdev.netlab.net:8080-8082` (TCP), `192.168.31.2:9001-9010` (test IoT endpoints) | HTTP/HTTPS, DNS, NTP, SSH (22 optional) | Varied (5-60s) | Broad access for testing |
| **IT** | `IT-Admins`, `Infrastructure` | All business apps, `192.168.31.2` (all ports), SMB (445 optional), LDAP (389/636 optional) | HTTP/HTTPS, DNS, NTP, SMB, LDAP | Low frequency (10-120s) | Admin access, can reach IoT for troubleshooting |

### IoT Personas (MAB)

| Persona | Vendor OUI | Device Name Pattern | Primary Destinations | Protocol/Port | Cadence | Payload |
|---------|-----------|---------------------|----------------------|---------------|---------|---------|
| **Badge Reader** | `00:04:5A` (Zebra) | `badge-reader-##` | `192.168.31.2:9001/badge/events` | HTTP POST | 30-300s | JSON: badge swipe events |
| **Camera** | `54:C4:15` (Hikvision) | `camera-##` | `192.168.31.2:9002/camera/stream`, RTSP optional | HTTP POST, RTSP | 10-60s | Video metadata/frames |
| **Printer** | `00:30:6E` (HP) | `printer-##` | `192.168.30.2:9003/print/jobs` | HTTP GET/POST | 60-600s | Print job status |
| **Environmental Sensor** | `00:1E:58` (Honeywell) | `env-sensor-##` | `192.168.31.2:9004/telemetry` | HTTP POST, MQTT (1883) | 30-180s | Temp/humidity JSON |
| **HVAC Controller** | `00:26:55` (Honeywell) | `hvac-zone-##` | `192.168.31.2:9005/hvac/status` | HTTP POST, SNMP (161) | 60-300s | Zone status |
| **Door Lock** | `00:0D:67` (HID) | `door-lock-##` | `192.168.31.2:9006/lock/events` | HTTP POST | 30-180s | Lock/unlock events |
| **Display** | `00:12:47` (Samsung) | `display-##` | `192.168.31.2:9007/display/feed` | HTTP GET | 30-120s | Content retrieval |
| **VoIP Phone** | `00:1E:7A` (Cisco) | `voip-phone-##` | `192.168.31.2:9008/voip/register`, SIP optional | HTTP GET, SIP (5060) | 300-900s | Registration keepalive |
| **Robot** | `00:1E:67` (Fanuc) | `robot-agv-##` | `192.168.31.2:9009/robot/telemetry` | HTTP POST | 10-60s | Position/status |
| **Medical Device** | `00:50:7F` (Philips) | `medical-##` | `192.168.31.2:9010/medical/vitals` | HTTP POST | 30-120s | Patient data (simulated) |

### Universal Infrastructure Access (All Personas)

| Service | Destination | Protocol/Port | Purpose |
|---------|-------------|---------------|---------|
| DNS | `192.168.10.1` or network DNS | UDP/53 | Name resolution |
| NTP | `192.168.10.1` or `pool.ntp.org` | UDP/123 | Time sync |

---

## Backend Deployment Plan

### Current State

- `192.168.30.2`: Business apps (thehub, finance, engineering, code, mab) - **COMPLETE**
- `192.168.31.2`: iotdev stack (ports 8080-8082) - **COMPLETE**
- `192.168.40.2`: www.netlab.net - **COMPLETE**

### Required Additions

#### On 192.168.31.2 (or Pi-Rebuild-1)

Add simple HTTP endpoints for each IoT persona (ports 9001-9010). Use Flask/nginx or simple Python HTTP servers:

| Port | Path | Persona | Method | Response |
|------|------|---------|--------|----------|
| 9001 | `/badge/events` | Badge Reader | POST | `{"status": "ok", "timestamp": ...}` |
| 9002 | `/camera/stream` | Camera | POST | `{"status": "ok", "frame_id": ...}` |
| 9003 | `/print/jobs` | Printer | GET/POST | `{"queue_length": 0}` |
| 9004 | `/telemetry` | Environmental Sensor | POST | `{"status": "received"}` |
| 9005 | `/hvac/status` | HVAC | POST | `{"zone": "ok"}` |
| 9006 | `/lock/events` | Door Lock | POST | `{"status": "ok"}` |
| 9007 | `/display/feed` | Display | GET | `{"content": "..."}` |
| 9008 | `/voip/register` | VoIP Phone | GET | `{"registered": true}` |
| 9009 | `/robot/telemetry` | Robot | POST | `{"status": "ok"}` |
| 9010 | `/medical/vitals` | Medical Device | POST | `{"status": "received"}` |

**Implementation:** Simple Flask app or nginx static/echo endpoints. See `deploy_iot_backends.py` (to be created).

#### On Pi-Rebuild-1 (IoT Backend Node)

If `192.168.31.2` can't host ports 9001-9010, deploy them on Pi-Rebuild-1:
- Install Flask: `pip install flask`
- Run `iot_backend_mock.py` (to be created) that listens on all 10 ports
- Configure firewall to allow inbound from lab network

#### Optional: MQTT Broker

For Environmental Sensor and HVAC personas, add MQTT:
- Install Mosquitto on `192.168.31.2` or Pi-Rebuild-1: `sudo apt install mosquitto`
- Configure port 1883 (plain) or 8883 (TLS)
- Update identities for sensor/HVAC to include MQTT in addition to HTTP

---

## Identity Configuration Updates Required

The following changes must be made to `identities1.json` to support the full lab plan:

### User Identities - Hostname Convention & AD Groups

**Hostname Convention:** User device hostnames follow the pattern: **first initial + lastname + "-ws"**

Examples:
- alice.johnson → **ajohnson-ws**
- henry.brown → **hbrown-ws**
- jane.robinson → **jrobinson-ws**

Update all user personas with correct `device_name` and `ad_groups`:

```json
{
  "username": "alice.johnson",
  "display_name": "Alice Johnson",
  "device_name": "ajohnson-ws",
  "department": "Sales",
  "password": "C!sco#123",
  "mac": "dc:a6:32:4f:0b:7c",
  "ssid": "netlab_employee",
  "ad_groups": ["Sales-Employees", "Business-Users"]
}
```

**Apply to all user identities:**
- Sales users → device_name: `<firstInitial><lastname>-ws`, ad_groups: `["Sales-Employees", "Business-Users"]`
- Finance users → device_name: `<firstInitial><lastname>-ws`, ad_groups: `["Finance-Dept", "Business-Users"]`
- Engineering users → device_name: `<firstInitial><lastname>-ws`, ad_groups: `["Engineering", "Dev-Team"]`
- IT users → device_name: `<firstInitial><lastname>-ws`, ad_groups: `["IT-Admins", "Infrastructure"]`

### IoT Identities - Add Traffic Config

Update IoT MAB identities to include specific traffic parameters:

```json
{
  "auth": "mab",
  "persona": "Badge Reader",
  "device_name": "badge-reader-01",
  "department": "IoT",
  "description": "IoT Badge Reader MAB",
  "mac": "00:04:5A:aa:11:01",
  "urls": ["http://192.168.31.2:9001/badge/events"],
  "traffic_method": "POST",
  "traffic_min_sleep": 30,
  "traffic_max_sleep": 300,
  "payload_template": "{\"event\": \"badge_scan\", \"timestamp\": \"{{NOW}}\", \"badge_id\": \"{{DEVICE_NAME}}\"}"
}
```

**Apply persona-specific configs:**
- Badge Reader → Port 9001, POST, 30-300s
- Camera → Port 9002, POST, 10-60s  
- Printer → Port 9003, GET/POST, 60-600s
- Environmental Sensor → Port 9004, POST, 30-180s (+ MQTT optional)
- HVAC Controller → Port 9005, POST, 60-300s
- Door Lock → Port 9006, POST, 30-180s
- Display → Port 9007, GET, 30-120s
- VoIP Phone → Port 9008, GET, 300-900s
- Robot → Port 9009, POST, 10-60s
- Medical Device → Port 9010, POST, 30-120s

### Validation Identities - Add Negative Test Cases

Add 5-10 "violation" identities for fault injection:

```json
{
  "username": "alice.johnson.violation",
  "display_name": "Alice (Violation Mode)",
  "device_name": "alice-violation",
  "department": "Sales",
  "password": "C!sco#123",
  "mac": "dc:a6:32:4f:0b:7d",
  "ssid": "netlab_employee",
  "ad_groups": ["Sales-Employees", "Business-Users"],
  "urls": ["http://192.168.31.2:8080/api", "http://192.168.31.2:9002/camera/stream"],
  "description": "VIOLATION: Sales user accessing dev/IoT backends"
}
```

Examples:
- `camera-01-violation`: Camera MAC accessing finance.netlab.net
- `badge-reader-portscan`: Badge reader doing port scan (nmap)
- `finance-user-ssh-attempt`: Finance user trying SSH to servers

---

## Validation Automation Plan

### Overview

Automated validation scores Clarion's grouping accuracy by comparing observed groups/policies to expected ground truth.

### Ground Truth Generation

**Source:** Orchestration controller (Pi-Rebuild-3) logs all persona assignments:

```csv
timestamp,device_mac,device_name,persona,expected_destinations,expected_protocols
2026-02-13T10:00:00Z,dc:a6:32:4f:0b:7c,ajohnson-ws,Sales,"finance.netlab.net,thehub.netlab.net","http,https,dns"
2026-02-13T10:05:30Z,00:04:5A:aa:11:01,badge-reader-01,Badge Reader,"192.168.31.2:9001","http,dns"
```

**Output:** `ground_truth_log.csv`

### Clarion API Queries

Validation node (Pi-Rebuild-4) runs `validate_clarion_grouping.py` which:

1. **Fetch Clarion device inventory:**  
   `GET /api/devices` → All devices Clarion has seen with metadata (MAC, name, last_seen, assigned_group, etc.)

2. **Fetch Clarion groups:**  
   `GET /api/groups` → All auto-generated groups with members and rules

3. **Fetch Clarion policy suggestions:**  
   `GET /api/policy/suggestions` → Drafted policies based on grouping

### Scoring Metrics

| Metric | Calculation | Target |
|--------|-------------|--------|
| **Correlation Coverage** | `(Devices with who+where+what) / (Total devices)` | >= 90% |
| **Grouping Purity** | For each expected persona, `(Correct members) / (Total in that group)` | >= 85% |
| **Backend Exclusivity** | For IoT personas, `(Correct destination hits) / (Total hits)` | >= 90% |
| **False Merges** | `(Personas incorrectly merged) / (Total groups)` | <= 10% |
| **Policy Usefulness** | Manual review: do suggested policies match expected? | >= 80% subjective |
| **Anomaly Detection** | `(Violations caught) / (Total violations injected)` | >= 75% |

### Validation Script: `validate_clarion_grouping.py`

**Inputs:**
- `ground_truth_log.csv` from orchestrator
- Clarion API endpoint (e.g., `http://192.168.30.2:5000/api`)

**Logic:**
1. Load ground truth
2. Query Clarion API for devices and groups
3. For each device in ground truth:
   - Check if Clarion has `who` (identity), `where` (network path), `what` (destinations)
   - Verify assigned group matches expected persona
   - Check destination list matches expected backend
4. Calculate metrics
5. Output JSON report + human-readable summary

**Output:**
```json
{
  "timestamp": "2026-02-13T12:00:00Z",
  "correlation_coverage": 0.92,
  "grouping_purity": {
    "Sales": 0.88,
    "Finance": 0.91,
    "Badge Reader": 0.95,
    "Camera": 0.87
  },
  "backend_exclusivity": {
    "Badge Reader": 0.93,
    "Camera": 0.89
  },
  "false_merges": 0.08,
  "anomaly_detection": 0.76,
  "pass": true
}
```

---

## Negative Test Cases (Fault Injection)

Purpose: Ensure Clarion detects anomalies and policy violations.

### Test Cases

| Test Case ID | Description | Expected Clarion Behavior |
|--------------|-------------|---------------------------|
| **VIOLATION-01** | Sales user (alice) accesses `iotdev.netlab.net:8080` | Flag as anomaly: "Sales-Employees accessing Dev APIs" |
| **VIOLATION-02** | Camera MAC (54:C4:15:xx) accesses `finance.netlab.net` | Flag: "IoT Camera accessing business app" |
| **VIOLATION-03** | Badge Reader does port scan (nmap) on 192.168.30.0/24 | Flag: "IoT device unusual port behavior" |
| **VIOLATION-04** | Finance user (jane) attempts SSH to `192.168.31.2:22` | Flag: "Business user SSH to backend" (if not allowed) |
| **VIOLATION-05** | VoIP Phone sends HTTP POST to `thehub.netlab.net` | Flag: "VoIP persona accessing business portal" |
| **VIOLATION-06** | ESP32 device changes MAC to Sales user's MAC | Flag: "MAC spoofing or device type mismatch" |
| **VIOLATION-07** | Printer sends high-volume traffic (1000 reqs/min) | Flag: "Printer persona bandwidth anomaly" |
| **VIOLATION-08** | HVAC Controller connects after hours (2 AM) | Flag: "Unusual time for HVAC activity" (if baseline is daytime) |

### Execution

- Run via Pi-Rebuild-5 (fault injection node)
- Log each violation with expected detection
- Validation script checks if Clarion flagged it

---

## Execution Phases & Task Roadmap

**Important:** Phases are designed to build incrementally. Start with Phase 0 (data validation) before attempting full analytics validation.

### Phase 0: Data Validation & Initial Testing (First Priority)

**Goal:** Verify Clarion data collection, correlation, and enrichment BEFORE building analytics.

**Why Start Here:** Clarion's grouping/policy analytics are in development. First, we must prove the data pipeline works correctly.

#### What to Test

- [ ] **Data Collection:**
  - [ ] ISE pxGrid data flowing into Clarion
  - [ ] NetFlow/IPFIX flows being captured
  - [ ] DNS queries logged
  - [ ] DHCP assignments recorded

- [ ] **Identity Correlation:**
  - [ ] Each identity switch creates entry in Clarion
  - [ ] MAC address changes tracked
  - [ ] Username ↔ MAC ↔ IP correctly linked
  - [ ] Hostnames properly set (format: first initial + lastname + "-ws", e.g., "ajohnson-ws")

- [ ] **Behavioral Data:**
  - [ ] NetFlow destinations associated with correct identity
  - [ ] Traffic patterns visible per identity
  - [ ] Protocol/port information captured
  - [ ] Timing/frequency data available

- [ ] **Data Quality:**
  - [ ] No missing correlations (orphaned MACs, IPs without identity)
  - [ ] Timestamps synchronized across sources
  - [ ] AD group membership visible
  - [ ] ISE endpoint groups assigned correctly

#### How to Validate (Manual Inspection)

```bash
# Example queries to check data in Clarion:

1. Check if Alice's identity switch was recorded:
   - Look for MAC dc:a6:32:4f:0b:7c
   - Username: alice.johnson
   - Hostname: ajohnson-ws
   - AD Groups: Sales-Employees, Business-Users

2. Check if traffic is associated:
   - Source: alice.johnson (or MAC)
   - Destinations: finance.netlab.net, thehub.netlab.net
   - Protocol: HTTP/HTTPS
   - Timing: Every 30-60 seconds

3. Check IoT device correlation:
   - Camera MAC: 54:C4:15:aa:11:01
   - Device name: camera-01
   - ISE Group: Lab-Camera
   - Destination: 192.168.31.2:9002 exclusively
```

#### Phase 0 Exit Criteria

- [ ] 100% of identity switches visible in Clarion with complete metadata
- [ ] No correlation gaps (all MACs have username, all IPs have identity)
- [ ] Hostnames correctly formatted and recorded
- [ ] Traffic flows correctly attributed to identities
- [ ] Manual spot-checks confirm data accuracy

**Once Phase 0 passes, Clarion's data foundation is solid. Analytics can be built on top with confidence.**

---

### Phase 1: Foundation Setup (Days 1-3)

**Goal:** All infrastructure and identities ready for traffic generation.

#### 1.1 ISE Configuration

- [ ] Deploy/verify ISE server is accessible
- [ ] Configure Dot1x policy for `netlab_employee` SSID (wired + wireless)
- [ ] Configure MAB policy for IoT devices
- [ ] Enable ISE ERS API (for sync script)
- [ ] Test: Authenticate one Dot1x user successfully
- [ ] Test: Authenticate one MAB device successfully
  
#### 1.2 Clarion Deployment

- [ ] Deploy Clarion collectors (NetFlow/IPFIX, pxGrid)
- [ ] Configure pxGrid integration with ISE
- [ ] Verify Clarion backend API accessible (e.g., `http://192.168.30.2:5000/api`)
- [ ] Test: Query `/api/devices` endpoint returns data

#### 1.3 Network Infrastructure

- [ ] Configure wired VLAN for `netplan-eth0` with DHCP
- [ ] Configure wireless SSID `netlab_employee` with Dot1x
- [ ] Verify DNS servers configured (192.168.10.1 or network DNS)
- [ ] Verify NTP server accessible
- [ ] Document network topology (VLAN IDs, SSID names, switch ports)

#### 1.4 Backend Services

**Existing servers (verify):**
- [ ] `192.168.30.2`: Test thehub.netlab.net (HTTP 80/443)
- [ ] `192.168.30.2`: Test finance.netlab.net (HTTP 80/443)
- [ ] `192.168.30.2`: Test engineering.netlab.net (HTTP 80/443)
- [ ] `192.168.30.2`: Test code.netlab.net (HTTP 80/443)
- [ ] `192.168.30.2`: Test mab.netlab.net (HTTP 80/443)
- [ ] `192.168.31.2`: Test iotdev.netlab.net:8080-8082 (TCP)
- [ ] `192.168.40.2`: Test www.netlab.net (HTTP 80)

**New IoT backend endpoints (deploy):**
- [ ] Create `iot_backend_mock.py` Flask app (ports 9001-9010)
- [ ] Deploy to `192.168.31.2` (preferred) or Pi-Rebuild-1
- [ ] Start service: `python iot_backend_mock.py &`
- [ ] Test each endpoint:
  - [ ] Port 9001: `curl -X POST http://192.168.31.2:9001/badge/events`
  - [ ] Port 9002: `curl -X POST http://192.168.31.2:9002/camera/stream`
  - [ ] Port 9003: `curl http://192.168.31.2:9003/print/jobs`
  - [ ] Port 9004: `curl -X POST http://192.168.31.2:9004/telemetry`
  - [ ] Port 9005: `curl -X POST http://192.168.31.2:9005/hvac/status`
  - [ ] Port 9006: `curl -X POST http://192.168.31.2:9006/lock/events`
  - [ ] Port 9007: `curl http://192.168.31.2:9007/display/feed`
  - [ ] Port 9008: `curl http://192.168.31.2:9008/voip/register`
  - [ ] Port 9009: `curl -X POST http://192.168.31.2:9009/robot/telemetry`
  - [ ] Port 9010: `curl -X POST http://192.168.31.2:9010/medical/vitals`
- [ ] (Optional) Deploy MQTT broker (Mosquitto) on 192.168.31.2 or Pi-Rebuild-1
  - [ ] Install: `sudo apt install mosquitto`
  - [ ] Test: `mosquitto_pub -h 192.168.31.2 -t test -m "hello"`

#### 1.5 Identity Configuration

- [ ] Backup current `identities1.json`: `cp identities1.json identities1.json.backup`
- [ ] Update all user identities with `ad_groups` field:
  - [ ] Sales users: add `["Sales-Employees", "Business-Users"]`
  - [ ] Finance users: add `["Finance-Dept", "Business-Users"]`
  - [ ] Engineering users: add `["Engineering", "Dev-Team"]`
  - [ ] IT users: add `["IT-Admins", "Infrastructure"]`
- [ ] Update all IoT MAB identities with traffic configs:
  - [ ] Badge Reader: `urls`, `traffic_method: POST`, `traffic_min_sleep: 30`, `traffic_max_sleep: 300`
  - [ ] Camera: Port 9002, POST, 10-60s
  - [ ] Printer: Port 9003, GET/POST, 60-600s
  - [ ] Environmental Sensor: Port 9004, POST, 30-180s
  - [ ] HVAC Controller: Port 9005, POST, 60-300s
  - [ ] Door Lock: Port 9006, POST, 30-180s
  - [ ] Display: Port 9007, GET, 30-120s
  - [ ] VoIP Phone: Port 9008, GET, 300-900s
  - [ ] Robot: Port 9009, POST, 10-60s
  - [ ] Medical Device: Port 9010, POST, 30-120s
- [ ] Add 5-10 violation identities for negative testing
- [ ] Validate JSON syntax: `python -m json.tool identities1.json`
- [ ] Sync identities to ISE:
  - [ ] Configure `ise_sync_config.json` with ISE credentials
  - [ ] Run: `python sync_ise_groups_and_endpoints.py --identities identities1.json`
  - [ ] Verify all identity groups created in ISE
  - [ ] Verify all MACs registered in ISE

#### 1.6 Lab Runner Pis (6 Ready)

**Pi-Runner-1 (Sales, Wired):**
- [ ] Set hostname: `sudo hostnamectl set-hostname pi-runner-1`
- [ ] Clone lab scripts: `git pull` in `/home/pi/clarion`
- [ ] Test single identity switch: `sudo python identity_switcher.py --user alice.johnson --interface eth0`
- [ ] Verify Dot1x auth succeeds
- [ ] Verify traffic to finance.netlab.net
- [ ] Configure auto-start: `auto_lab_runner.py --interface eth0 --session-duration 300` (5 min sessions)

**Pi-Runner-2 (Finance, Wired):**
- [ ] Set hostname: `sudo hostnamectl set-hostname pi-runner-2`
- [ ] Test: `sudo python identity_switcher.py --user jane.robinson --interface eth0`
- [ ] Verify traffic to finance.netlab.net, thehub.netlab.net
- [ ] Configure auto-start

**Pi-Runner-3 (Engineering, Wired):**
- [ ] Set hostname: `sudo hostnamectl set-hostname pi-runner-3`
- [ ] Test: `sudo python identity_switcher.py --user henry.brown --interface eth0`
- [ ] Verify traffic to code.netlab.net, engineering.netlab.net, iotdev ports 8080-8082
- [ ] Configure auto-start

**Pi-Runner-4 (IT, Wired):**
- [ ] Set hostname: `sudo hostnamectl set-hostname pi-runner-4`
- [ ] Test: `sudo python identity_switcher.py --user victor.wilson --interface eth0`
- [ ] Verify traffic to all backends (IT has broad access)
- [ ] Configure auto-start

**Pi-Runner-5 (IoT Set A, Wireless):**
- [ ] Set hostname: `sudo hostnamectl set-hostname pi-runner-5`
- [ ] Test MAB identities:
  - [ ] Badge Reader: `sudo python identity_switcher.py --user badge-reader-01 --interface wlan0`
  - [ ] Camera: `sudo python identity_switcher.py --user camera-01 --interface wlan0`
  - [ ] Printer: `sudo python identity_switcher.py --user printer-01 --interface wlan0`
  - [ ] Environmental Sensor: `sudo python identity_switcher.py --user env-sensor-01 --interface wlan0`
  - [ ] HVAC Controller: `sudo python identity_switcher.py --user hvac-zone-01 --interface wlan0`
- [ ] Verify MAB auth succeeds for each
- [ ] Verify traffic to correct port (9001-9005)
- [ ] Configure auto-start with rotation: 30-60 min per persona

**Pi-Runner-6 (IoT Set B, Wireless):**
- [ ] Set hostname: `sudo hostnamectl set-hostname pi-runner-6`
- [ ] Test MAB identities:
  - [ ] Door Lock: `sudo python identity_switcher.py --user door-lock-01 --interface wlan0`
  - [ ] Display: `sudo python identity_switcher.py --user display-01 --interface wlan0`
  - [ ] VoIP Phone: `sudo python identity_switcher.py --user voip-phone-01 --interface wlan0`
  - [ ] Robot: `sudo python identity_switcher.py --user robot-agv-01 --interface wlan0`
  - [ ] Medical Device: `sudo python identity_switcher.py --user medical-01 --interface wlan0`
- [ ] Verify MAB auth and traffic to ports 9006-9010
- [ ] Configure auto-start with rotation

#### 1.7 Windows Machines (4 - Stable Control Group)

**Win-1 (Sales):**
- [ ] Set hostname: `WIN-SALES-01`
- [ ] Configure stable Dot1x profile:
  - Username: `alice.johnson`
  - Password: `C!sco#123`
  - Network: `netlab_employee` (wireless)
- [ ] Test authentication
- [ ] Install traffic generator or browser automation (optional)
- [ ] Configure startup script to hit thehub.netlab.net, finance.netlab.net every 30-60s

**Win-2 (Finance):**
- [ ] Set hostname: `WIN-FINANCE-01`
- [ ] Configure Dot1x: `jane.robinson`
- [ ] Test authentication
- [ ] Configure traffic to finance.netlab.net, thehub.netlab.net

**Win-3 (Engineering):**
- [ ] Set hostname: `WIN-ENG-01`
- [ ] Configure Dot1x: `henry.brown`
- [ ] Test authentication
- [ ] Configure traffic to code.netlab.net, engineering.netlab.net, iotdev.netlab.net:8080-8082

**Win-4 (IT):**
- [ ] Set hostname: `WIN-IT-01`
- [ ] Configure Dot1x: `victor.wilson`
- [ ] Test authentication
- [ ] Configure traffic to all backends (admin/troubleshooting patterns)

#### 1.8 ESP32 Devices (2)

**ESP32-1 (Environmental Sensor):**
- [ ] Flash firmware: ESP32 Environmental Sensor (MQTT or HTTP POST)
- [ ] Configure MAC from identities: `00:1E:58:aa:11:01` (from `identities1.json`)
- [ ] Configure device_name: `env-sensor-esp32-01`
- [ ] Configure target: `192.168.31.2:9004/telemetry` or MQTT `192.168.31.2:1883`
- [ ] Set cadence: POST every 60-120s
- [ ] Test connectivity and traffic

**ESP32-2 (Door Lock):**
- [ ] Flash firmware: ESP32 Door Lock (HTTP POST)
- [ ] Configure MAC: `00:0D:67:aa:12:01`
- [ ] Configure device_name: `door-lock-esp32-01`
- [ ] Configure target: `192.168.31.2:9006/lock/events`
- [ ] Set cadence: POST every 120-180s
- [ ] Test connectivity and traffic

#### 1.9 Rebuild Pis (5 - Support Infrastructure)

**Pi-Rebuild-1 (IoT Backend Node):**
- [ ] Rebuild with fresh Raspbian/Ubuntu
- [ ] Set hostname: `pi-iot-backend`
- [ ] Set static IP (if not done): `192.168.31.3` (or similar)
- [ ] Install Python/Flask: `sudo apt install python3-pip && pip3 install flask`
- [ ] Deploy `iot_backend_mock.py`
- [ ] Start service (systemd or screen)
- [ ] Configure firewall: `sudo ufw allow 9001:9010/tcp`
- [ ] (Optional) Install Mosquitto for MQTT
- [ ] Test all 10 endpoints respond

**Pi-Rebuild-2 (Additional Endpoint Generator):**
- [ ] Rebuild OS
- [ ] Set hostname: `pi-endpoint-extra`
- [ ] Clone lab scripts
- [ ] Configure as standard lab runner (can rotate user or IoT personas)
- [ ] Test identity switch and traffic

**Pi-Rebuild-3 (Orchestration Controller):**
- [ ] Rebuild OS
- [ ] Set hostname: `pi-orchestrator`
- [ ] Install: `pip3 install fabric paramiko`
- [ ] Create `lab_orchestrator.py` script:
  - SSH to each Pi runner
  - Start/stop identity sessions on schedule
  - Log ground truth: timestamp, MAC, persona, expected destinations
- [ ] Configure SSH keys for passwordless access to all runners
- [ ] Test: Run orchestrator to start one session on Pi-Runner-1
- [ ] Verify ground truth log created: `ground_truth_log.csv`

**Pi-Rebuild-4 (Validation & Scoring Node):**
- [ ] Rebuild OS
- [ ] Set hostname: `pi-validator`
- [ ] Install: `pip3 install requests`
- [ ] Create `validate_clarion_grouping.py` script:
  - Load `ground_truth_log.csv`
  - Query Clarion API `/api/devices`, `/api/groups`
  - Compare observed vs expected
  - Calculate scores (correlation coverage, grouping purity, etc.)
  - Output JSON report
- [ ] Test: Run validation against sample data
- [ ] Verify report generated with scores

**Pi-Rebuild-5 (Fault Injection Node):**
- [ ] Rebuild OS
- [ ] Set hostname: `pi-fault-injection`
- [ ] Clone lab scripts
- [ ] Load violation identities (alice.violation, camera-01-violation, etc.)
- [ ] Test identity switch to violation mode
- [ ] Verify intentional violation traffic generated (e.g., Sales user hitting iotdev APIs)
- [ ] Log violations for comparison with Clarion anomaly detection

#### Phase 1 Exit Criteria

- [ ] All endpoints (Windows, Pi, ESP32) can authenticate (Dot1x or MAB)
- [ ] >= 95% authentication success rate across all devices
- [ ] All personas visible in ISE with correct groups
- [ ] All personas visible in Clarion with identity data
- [ ] Single identity on each device can produce traffic to assigned backend
- [ ] All backend endpoints (ports 9001-9010) respond to test requests
- [ ] Ground truth logging operational on orchestrator
- [ ] Validation script can query Clarion API and generate report

---

### Phase 2: Controlled Traffic Baseline (Days 4-5)

**Goal:** Establish per-persona behavior signatures in isolation before mixing.

#### 2.1 User Persona Isolation Runs (30-60 min each)

- [ ] Run Sales persona only (Pi-Runner-1 + Win-1):
  - [ ] Start session on Pi-Runner-1 (alice.johnson)
  - [ ] Verify traffic to thehub, finance on 192.168.30.2
  - [ ] Log 30+ min of traffic
  - [ ] Capture flow signatures in Clarion
- [ ] Run Finance persona only:
  - [ ] Start session on Pi-Runner-2 (jane.robinson)
  - [ ] Verify traffic to finance, thehub
  - [ ] Log 30+ min
- [ ] Run Engineering persona only:
  - [ ] Start session on Pi-Runner-3 (henry.brown)
  - [ ] Verify traffic to code, engineering, iotdev:8080-8082
  - [ ] Log 30+ min
- [ ] Run IT persona only:
  - [ ] Start session on Pi-Runner-4 (victor.wilson)
  - [ ] Verify broad backend access
  - [ ] Log 30+ min

#### 2.2 IoT Persona Isolation Runs (30-60 min each)

- [ ] Badge Reader:
  - [ ] Start on Pi-Runner-5
  - [ ] Verify POST to 192.168.31.2:9001 every 30-300s
  - [ ] Capture 30+ min baseline
- [ ] Camera:
  - [ ] Start on Pi-Runner-5
  - [ ] Verify POST to 192.168.31.2:9002 every 10-60s
  - [ ] Capture 30+ min
- [ ] Printer:
  - [ ] Start on Pi-Runner-5
  - [ ] Verify GET/POST to 192.168.31.2:9003 every 60-600s
  - [ ] Capture 30+ min
- [ ] Environmental Sensor:
  - [ ] Start on Pi-Runner-5 or ESP32-1
  - [ ] Verify POST/MQTT to 192.168.31.2:9004 every 30-180s
  - [ ] Capture 30+ min
- [ ] HVAC Controller:
  - [ ] Start on Pi-Runner-5
  - [ ] Verify POST to 192.168.31.2:9005 every 60-300s
  - [ ] Capture 30+ min
- [ ] Door Lock:
  - [ ] Start on Pi-Runner-6 or ESP32-2
  - [ ] Verify POST to 192.168.31.2:9006 every 30-180s
  - [ ] Capture 30+ min
- [ ] Display:
  - [ ] Start on Pi-Runner-6
  - [ ] Verify GET to 192.168.31.2:9007 every 30-120s
  - [ ] Capture 30+ min
- [ ] VoIP Phone:
  - [ ] Start on Pi-Runner-6
  - [ ] Verify GET to 192.168.31.2:9008 every 300-900s
  - [ ] Capture 30+ min
- [ ] Robot:
  - [ ] Start on Pi-Runner-6
  - [ ] Verify POST to 192.168.31.2:9009 every 10-60s
  - [ ] Capture 30+ min
- [ ] Medical Device:
  - [ ] Start on Pi-Runner-6
  - [ ] Verify POST to 192.168.31.2:9010 every 30-120s
  - [ ] Capture 30+ min

#### 2.3 Baseline Validation

- [ ] For each persona:
  - [ ] Verify Clarion captured identity (who)
  - [ ] Verify Clarion captured network path (where)
  - [ ] Verify Clarion captured destination list (what)
  - [ ] Confirm destination exclusivity >= 90%
  - [ ] Confirm protocol/port match expected
- [ ] Document baseline signature for each persona in `baseline_signatures.csv`

#### Phase 2 Exit Criteria

- [ ] All 14 personas (4 user + 10 IoT) have clean baseline runs
- [ ] Per-persona behavior is separable and repeatable
- [ ] Clarion captures `who+where+what` for >= 90% of baseline sessions
- [ ] No unexpected cross-persona traffic observed

---

### Phase 3: Mixed Population & Identity Churn (Days 6-7)

**Goal:** Run all endpoints simultaneously with identity rotation to test Clarion's correlation under churn.

#### 3.1 Start All Endpoints

- [ ] Windows machines (4):
  - [ ] Win-1 (Sales): Running stable
  - [ ] Win-2 (Finance): Running stable
  - [ ] Win-3 (Engineering): Running stable
  - [ ] Win-4 (IT): Running stable
- [ ] Pi runners (6):
  - [ ] Pi-Runner-1: Rotating Sales users every 5-10 min
  - [ ] Pi-Runner-2: Rotating Finance users every 5-10 min
  - [ ] Pi-Runner-3: Rotating Engineering users every 5-10 min
  - [ ] Pi-Runner-4: Rotating IT users every 5-10 min
  - [ ] Pi-Runner-5: Rotating IoT Set A (5 personas) every 30-60 min
  - [ ] Pi-Runner-6: Rotating IoT Set B (5 personas) every 30-60 min
- [ ] ESP32 devices (2):
  - [ ] ESP32-1 (Env Sensor): Running continuously
  - [ ] ESP32-2 (Door Lock): Running continuously
- [ ] Additional endpoint (Pi-Rebuild-2): Optional, can add more rotation

#### 3.2 Orchestration & Ground Truth

- [ ] Start orchestrator (Pi-Rebuild-3):
  - [ ] Orchestrator schedules all rotations
  - [ ] Logs all identity changes to `ground_truth_log.csv`
- [ ] Run for 4-8 hours (or 24 hours for extended test)

#### 3.3 Monitoring During Run

- [ ] Check authentication success rate every hour:
  - [ ] Target: >= 95% auth success
- [ ] Check Clarion API for device count:
  - [ ] Target: All expected devices visible
- [ ] Monitor for ISE auth failures or network issues
- [ ] Capture any anomalies or errors in `run_log.txt`

#### Phase 3 Exit Criteria

- [ ] >= 95% authentication success across all identity changes
- [ ] Clarion maintains >= 90% correlation coverage under churn
- [ ] Groups remain stable (no excessive merging/splitting)
- [ ] Ground truth log complete with all transitions recorded

---

### Phase 4: Grouping & Policy Validation (Future - When Analytics Are Ready)

**Goal:** Validate Clarion's grouping accuracy and policy recommendations against ground truth.

**Status:** ⚠️ This phase is for future use once Clarion's analytics engine is complete.

**Current Alternative:** Until analytics are ready, use Phase 0 validation (data quality checks) instead of full grouping validation.

#### 4.1 Run Validation

- [ ] Stop all traffic generation
- [ ] Run validation script on Pi-Rebuild-4:
  ```bash
  python validate_clarion_grouping.py \
    --ground-truth ground_truth_log.csv \
    --clarion-api http://192.168.30.2:5000/api \
    --output validation_report.json
  ```
- [ ] Review `validation_report.json` for scores

#### 4.2 Score Review

- [ ] **Correlation Coverage:** >= 90%?
  - [ ] If < 90%: Identify which devices/personas are missing `who`/`where`/`what`
  - [ ] Fix: Check ISE integration, NetFlow collection, identity sync
- [ ] **Grouping Purity:** >= 85% for each persona?
  - [ ] If < 85%: Identify which personas are mixed incorrectly
  - [ ] Fix: Adjust Clarion grouping algorithm, add more distinct behavior
- [ ] **Backend Exclusivity:** >= 90% for IoT personas?
  - [ ] If < 90%: Identify which IoT devices are hitting wrong backends
  - [ ] Fix: Tighten traffic generation, check identity configs
- [ ] **False Merges:** <= 10%?
  - [ ] If > 10%: Identify which personas are incorrectly merged
  - [ ] Fix: Increase persona behavior differentiation
- [ ] **Anomaly Detection:** >= 75%?
  - [ ] If < 75%: Check if violations were logged but not detected
  - [ ] Fix: Adjust Clarion anomaly thresholds

#### 4.3 Policy Draft Review

- [ ] Extract policy suggestions from Clarion:
  - [ ] Query `/api/policy/suggestions` or export via UI
- [ ] Compare to expected policies:
  - [ ] Sales: Allow finance.netlab.net, thehub.netlab.net → Deny iotdev, code
  - [ ] Finance: Allow finance, thehub → Deny iotdev, code, engineering
  - [ ] Engineering: Allow all backends → Limited deny
  - [ ] IT: Allow all → Minimal deny (admin role)
  - [ ] Badge Reader: Allow 192.168.31.2:9001 → Deny all else
  - [ ] Camera: Allow 192.168.31.2:9002 → Deny all else
  - [ ] (etc. for all 10 IoT personas)
- [ ] Score policy usefulness: >= 80% subjective?
  - [ ] Count how many policies match expected
  - [ ] Calculate % match

#### 4.4 Negative Test Validation

- [ ] Run violation identities (Pi-Rebuild-5):
  - [ ] VIOLATION-01: Sales user (alice.violation) accesses iotdev:8080
  - [ ] VIOLATION-02: Camera MAC accesses finance.netlab.net
  - [ ] VIOLATION-03: Badge Reader port scan
  - [ ] VIOLATION-04: Finance user SSH attempt
  - [ ] VIOLATION-05: VoIP Phone accesses thehub
  - [ ] (etc. for all 8 test cases)
- [ ] Check Clarion anomaly detection:
  - [ ] Query `/api/anomalies` or check UI
  - [ ] Count how many violations were flagged
  - [ ] Calculate detection rate: (flagged / total) >= 75%?

#### Phase 4 Exit Criteria

- [ ] All scoring metrics meet or exceed targets
- [ ] Policy recommendations are >= 80% useful (minimal edits needed)
- [ ] Anomaly detection catches >= 75% of violations
- [ ] Lab is validated as **SUCCESS** and ready for demo/production testing

---

### Phase 5: Continuous Operation & Refinement (Ongoing)

**Goal:** Maintain lab for ongoing testing, demos, and Clarion development.

#### 5.1 Scheduled Runs

- [ ] Set up cron jobs or systemd timers on orchestrator to run daily:
  - [ ] Morning baseline (1 hour, all personas in isolation)
  - [ ] Afternoon mixed load (4 hours, all endpoints + churn)
  - [ ] Nightly validation (run scoring script)
- [ ] Archive ground truth and validation reports daily

#### 5.2 Demo Readiness

- [ ] Prepare demo script showing:
  - [ ] Identity rotation in real-time
  - [ ] Clarion UI showing device groups
  - [ ] Policy recommendations
  - [ ] Anomaly detection on violation
- [ ] Rehearse demo flow

#### 5.3 Iterative Improvements

- [ ] Add new personas as needed (e.g., Smart TV, Building Controller)
- [ ] Adjust traffic patterns based on real-world feedback
- [ ] Refine Clarion grouping/policy algorithms based on lab results
- [ ] Expand negative test cases

---

## Scripts & Tools to Create

The following scripts need to be developed to support the lab plan:

### 1. `iot_backend_mock.py` (Priority: HIGH)

**Purpose:** Simple Flask app hosting 10 IoT persona endpoints (ports 9001-9010).

**Features:**
- Listen on ports 9001-9010
- Accept GET/POST requests
- Log request timestamp, source IP, endpoint, method
- Return simple JSON response

**Deployment:** `192.168.31.2` or Pi-Rebuild-1

### 2. `lab_orchestrator.py` (Priority: HIGH)

**Purpose:** Central scheduler to coordinate identity rotations across all Pi runners.

**Features:**
- SSH to each Pi runner
- Start `identity_switcher.py` with specific persona/schedule
- Stop sessions after duration
- Log ground truth: `timestamp,device_mac,device_name,persona,expected_destinations`
- Output: `ground_truth_log.csv`

**Deployment:** Pi-Rebuild-3

### 3. `validate_clarion_grouping.py` (Priority: HIGH)

**Purpose:** Automated validation that scores Clarion's grouping accuracy.

**Features:**
- Load `ground_truth_log.csv`
- Query Clarion API (`/api/devices`, `/api/groups`, `/api/policy/suggestions`)
- Compare observed vs expected
- Calculate metrics (correlation coverage, grouping purity, backend exclusivity, false merges, anomaly detection)
- Output JSON report with scores and pass/fail

**Deployment:** Pi-Rebuild-4

### 4. ESP32 Firmware (Priority: MEDIUM)

**Purpose:** Constrained IoT device firmware for ESP32s.

**Features:**
- MAB authentication (no 802.1x)
- Set specific MAC from identities
- Send device_name in DHCP if possible
- HTTP POST or MQTT publish to assigned backend
- Environmental Sensor: POST to 192.168.31.2:9004 or MQTT every 60-120s
- Door Lock: POST to 192.168.31.2:9006 every 120-180s

**Deployment:** ESP32-1, ESP32-2

### 5. Windows Traffic Generator (Priority: LOW)

**Purpose:** Automated traffic generation for Windows control group.

**Options:**
- PowerShell script with `Invoke-WebRequest`
- Python script with `requests`
- Browser automation (Selenium)

**Features:**
- Loop GET requests to assigned backends every 30-60s
- Log timestamps and responses

**Deployment:** Win-1, Win-2, Win-3, Win-4

---

## Configuration Files Summary

| File | Purpose | Location | Status |
|------|---------|----------|--------|
| `identities1.json` | All user and IoT personas with MACs, credentials, traffic configs | `lab/` | **NEEDS UPDATE** (add ad_groups, traffic params, violations) |
| `lab_config.json` | Global lab settings (SSID, interface, dhcp_hostname) | `lab/` | **COMPLETE** |
| `ise_sync_config.json` | ISE ERS API credentials and group/endpoint mappings | `lab/` | **COMPLETE** |
| `traffic_config.json` | Traffic persona definitions (methods, sleep, targets) | `lab/` | **REVIEW** (ensure aligned with identities) |
| `ground_truth_log.csv` | Logged persona assignments for validation | Pi-Rebuild-3 | **TO BE CREATED** (by orchestrator) |
| `validation_report.json` | Scoring output from validation script | Pi-Rebuild-4 | **TO BE CREATED** (by validator) |
| `baseline_signatures.csv` | Per-persona baseline behavior | Pi-Rebuild-4 | **TO BE CREATED** (from Phase 2) |

---

## Operational Runbook (Daily/Weekly)

### Daily Operations

1. **Health Check (10 min):**
   - [ ] Verify ISE accessible and auth working
   - [ ] Verify AD reachable (if used)
   - [ ] Verify DNS/DHCP operational
   - [ ] Verify Clarion collectors running
   - [ ] Verify backend services responding (192.168.30.2, 192.168.31.2, 192.168.40.2)

2. **Start Orchestrator (5 min):**
   - [ ] SSH to Pi-Rebuild-3 (orchestrator)
   - [ ] Run: `python lab_orchestrator.py --schedule daily` (starts all runners on schedule)
   - [ ] Verify ground truth log is being written

3. **Monitor Run (hourly during test):**
   - [ ] Check orchestrator log for errors
   - [ ] Check ISE for auth failures
   - [ ] Check Clarion UI for device count (should match expected)

4. **End-of-Day Validation (15 min):**
   - [ ] Stop orchestrator
   - [ ] SSH to Pi-Rebuild-4 (validator)
   - [ ] Run: `python validate_clarion_grouping.py --ground-truth /path/to/ground_truth_log.csv --clarion-api http://192.168.30.2:5000/api`
   - [ ] Review `validation_report.json`
   - [ ] Archive reports: `cp validation_report.json reports/validation_$(date +%Y%m%d).json`

### Weekly Operations

1. **Review Scores:**
   - [ ] Compare weekly validation reports
   - [ ] Identify trends (improving/degrading metrics)
   - [ ] Escalate if any metric fails threshold 2 weeks in a row

2. **Update Identities:**
   - [ ] Add new personas if needed
   - [ ] Adjust traffic patterns based on feedback
   - [ ] Sync to ISE: `python sync_ise_groups_and_endpoints.py --identities identities1.json`

3. **Backup:**
   - [ ] Backup `identities1.json`, `lab_config.json`, `ground_truth_log.csv`
   - [ ] Backup validation reports
   - [ ] Backup Clarion database (if applicable)

---

## Quick Reference: Key Commands

### Identity Switching (Manual)
```bash
# Switch to user identity (Dot1x)
sudo python /home/pi/clarion/lab/identity_switcher.py \
  --user alice.johnson \
  --interface eth0

# Switch to IoT MAB identity
sudo python /home/pi/clarion/lab/identity_switcher.py \
  --user badge-reader-01 \
  --interface wlan0
```

### Auto Lab Runner
```bash
# Run continuous rotation (5 min sessions)
sudo python /home/pi/clarion/lab/auto_lab_runner.py \
  --interface eth0 \
  --session-duration 300 \
  --mode random
```

### ISE Sync
```bash
# Sync identities to ISE (create groups + endpoints)
python /home/pi/clarion/lab/sync_ise_groups_and_endpoints.py \
  --identities identities1.json \
  --config ise_sync_config.json
```

### Validation
```bash
# Run validation and scoring
python /home/pi/clarion/lab/validate_clarion_grouping.py \
  --ground-truth ground_truth_log.csv \
  --clarion-api http://192.168.30.2:5000/api \
  --output validation_report.json
```

### Backend Test
```bash
# Test IoT backend endpoints
for port in {9001..9010}; do
  curl -X POST http://192.168.31.2:$port/test -d '{"test": true}' -H "Content-Type: application/json"
done
```

---

## Troubleshooting

### Issue: Authentication failures (> 5%)

**Symptoms:** Endpoints fail Dot1x or MAB auth repeatedly.

**Checks:**
- [ ] ISE policy configured for SSID/VLAN?
- [ ] Credentials correct in `identities1.json`?
- [ ] MAC addresses synced to ISE? Run `sync_ise_groups_and_endpoints.py`
- [ ] Check ISE RADIUS logs for failure reason

**Fix:**
- Verify ISE policy allows Dot1x (PEAP/MSCHAPV2) or MAB
- Re-sync identities to ISE
- Check `wpa_supplicant` logs on Pi: `journalctl -u wpa_supplicant`

### Issue: Clarion missing identity data (correlation coverage < 90%)

**Symptoms:** Clarion shows devices but missing `who`, `where`, or `what`.

**Checks:**
- [ ] pxGrid integration with ISE working?
- [ ] NetFlow/IPFIX collector receiving flows?
- [ ] DHCP hostname being sent correctly? Check `dhclient` logs

**Fix:**
- Verify pxGrid config in Clarion
- Restart NetFlow collector
- Check identity_switcher set DHCP hostname: `grep dhcp_hostname identities1.json`

### Issue: IoT devices hitting wrong backends (exclusivity < 90%)

**Symptoms:** Camera MAC accessing finance.netlab.net, etc.

**Checks:**
- [ ] Traffic config in identities correct? Check `urls` field
- [ ] `auto_lab_runner.py` using per-identity URLs?

**Fix:**
- Update `identities1.json` with correct `urls` for each IoT persona
- Restart runner: `sudo systemctl restart auto_lab_runner`

### Issue: Grouping purity low (< 85%)

**Symptoms:** Sales and Finance users grouped together, or Camera and Badge Reader in same group.

**Checks:**
- [ ] Are personas accessing distinct destinations?
- [ ] Is traffic cadence/method distinct enough?

**Fix:**
- Increase behavior differentiation (add unique protocol, port, or destination per persona)
- Adjust Clarion grouping algorithm (if accessible)

---

## Success Metrics Dashboard

Track these weekly:

| Metric | Week 1 | Week 2 | Week 3 | Week 4 | Target |
|--------|--------|--------|--------|--------|--------|
| **Correlation Coverage** | ___% | ___% | ___% | ___% | >= 90% |
| **Grouping Purity (Sales)** | ___% | ___% | ___% | ___% | >= 85% |
| **Grouping Purity (Finance)** | ___% | ___% | ___% | ___% | >= 85% |
| **Grouping Purity (Engineering)** | ___% | ___% | ___% | ___% | >= 85% |
| **Grouping Purity (IT)** | ___% | ___% | ___% | ___% | >= 85% |
| **Grouping Purity (Badge Reader)** | ___% | ___% | ___% | ___% | >= 85% |
| **Grouping Purity (Camera)** | ___% | ___% | ___% | ___% | >= 85% |
| **(other IoT personas...)** | ... | ... | ... | ... | >= 85% |
| **Backend Exclusivity (IoT avg)** | ___% | ___% | ___% | ___% | >= 90% |
| **False Merges** | ___% | ___% | ___% | ___% | <= 10% |
| **Anomaly Detection** | ___% | ___% | ___% | ___% | >= 75% |
| **Policy Usefulness** | ___% | ___% | ___% | ___% | >= 80% |

---

## Immediate Next Actions (Start Here)

### Week 1: Foundation
1. [ ] Complete Phase 1.1-1.4 (ISE, Clarion, Network, Backends)
2. [ ] Create `iot_backend_mock.py` and deploy to 192.168.31.2 or Pi-Rebuild-1
3. [ ] Update `identities1.json` with `ad_groups` and traffic configs (Phase 1.5)
4. [ ] Sync identities to ISE
5. [ ] Configure and test 6 ready Pi runners (Phase 1.6)

### Week 2: Baseline
1. [ ] Complete Phase 1.7-1.9 (Windows, ESP32, Rebuild Pis)
2. [ ] Run Phase 2: Controlled traffic baseline (all 14 personas in isolation)
3. [ ] Create `lab_orchestrator.py` on Pi-Rebuild-3
4. [ ] Create `validate_clarion_grouping.py` on Pi-Rebuild-4
5. [ ] Document baseline signatures

### Week 3: Mixed Load
1. [ ] Run Phase 3: Mixed population with identity churn (4-8 hour run)
2. [ ] Monitor and log ground truth
3. [ ] Verify Clarion maintains correlation under churn

### Week 4: Validation
1. [ ] Run Phase 4: Validation and scoring
2. [ ] Review all metrics against targets
3. [ ] Run negative test cases (fault injection)
4. [ ] Generate final validation report
5. [ ] If all metrics pass: **LAB SUCCESS** → Ready for demo/production

---

## Notes & Best Practices

- **Persona Consistency:** Keep endpoint hostnames and OUIs persona-consistent to improve ISE profiling confidence.
- **IoT Realism:** Avoid generic browser-like traffic for all personas; preserve persona-specific cadence and destinations.
- **Deterministic First:** Prefer deterministic schedules during baseline phases; introduce randomness only after baseline passes.
- **Ground Truth is Key:** Without accurate ground truth logging, validation is impossible. Prioritize orchestrator reliability.
- **Iterate:** Lab is a living testbed. Continuously refine personas, traffic patterns, and validation based on results.
