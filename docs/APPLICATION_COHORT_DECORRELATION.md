# Application Cohort Decorrelation

**Status:** Specification — not yet implemented
**Audience:** Implementing agent or engineer
**Repo:** `dentroio/orchestrator`
**Related:** [LAB_MASTER_PLAN.md](LAB_MASTER_PLAN.md), [PEER_CONNECTIVITY_PLAN.md](PEER_CONNECTIVITY_PLAN.md), [LAUNCH_SESSION.md](LAUNCH_SESSION.md)

---

## Why

Clarion groups endpoints two different ways, and the lab currently cannot tell those ways apart.

- **Behavioral clustering** (UMAP → HDBSCAN → GMM on a 37-feature vector) answers *what is this device*.
- **Graph community detection** on the flow graph answers *what application does this device participate in*.

In the lab today those two questions have the same answer, because `connectivity_policies` is keyed by persona and each persona maps to exactly one destination port. Any algorithm that recovers persona scores perfectly, and any algorithm that recovers application structure recovers persona again. An evaluation run against this data is circular and cannot justify or rule out adding community detection to the product.

This spec makes device type and application membership **independent variables** so the two approaches produce measurably different partitions. It also makes the traffic substantially more realistic as a side effect, which is worth doing on its own merits.

---

## Part 0 — Read this first

### `orchestrator_config.json` is dead config on a running lab

`db.migrate_from_json_if_present()` (`db.py` lines 336–344) imports the JSON into SQLite **only when the `runners` config key is absent from the database**. After the first run it is never read again. `DEPLOY_THIS.md` line 28 states this, and both `deploy_orchestrator.sh` and `deploy_runner.sh` pass `--exclude 'orchestrator_config.json'` to rsync.

**Editing the JSON file alone changes nothing on the live lab.**

All changes go through the REST API on the orchestrator host (default `http://192.168.20.95:5000`), which writes to SQLite and hot-reloads the running engine:

| Endpoint | Method | Payload | Semantics |
|---|---|---|---|
| `/api/services` | GET / POST | list of service objects | **Full replace** |
| `/api/connectivity` | GET / POST | dict of `policy_name -> [service_id]` | **Full replace** |
| `/api/identities` | GET / POST | list of identity objects | **Full replace** |
| `/api/config` | GET / POST | full config dict | Full replace |

Every POST is read-modify-write: `GET` the current value, mutate in memory, `POST` the whole thing back. `tools/add_250_devices.py` is the working reference for this pattern.

Update `orchestrator_config.json` in git as well so a **fresh** deployment reproduces the same lab — but understand it does not affect the running system.

### Identities are schema-free

`db.set_identities()` stores each identity as an opaque JSON blob (`db.py` line 291) and `db.get_identities()` decodes it. New keys such as `groups`, `application_cohort` and `traffic_min_sleep` persist with no migration.

### Back up before starting

```bash
ssh admin@192.168.20.95 'cp ~/clarion/lab/clarion_lab.db ~/clarion/lab/clarion_lab.db.bak-$(date +%F)'
curl -s http://192.168.20.95:5000/api/identities   > backup-identities.json
curl -s http://192.168.20.95:5000/api/services     > backup-services.json
curl -s http://192.168.20.95:5000/api/connectivity > backup-connectivity.json
```

---

## Part 1 — What is wrong today

### Persona and destination port are a bijection

`connectivity_policies` is keyed by persona, and each persona resolves to a single port replicated across three hosts:

```
Badge Reader → srv_badge, srv_badge_20_3, srv_badge_20_4 → :9001 on 192.168.31.2, .20.3, .20.4
Camera       → srv_camera, srv_camera_20_3, ...          → :9002 on the same three hosts
Printer      → srv_printer, ...                          → :9003 on the same three hosts
```

`_select_session_urls()` returns the list unmodified for MAB identities (`lab_orchestrator.py` lines 590–591), so every device of a persona hits all three hosts. There is no destination variation *within* a persona and no destination overlap *between* personas.

### Sales and Finance are identical

Both resolve to `['srv_web_corp', 'srv_finance']`. Two departments that should be separable produce indistinguishable traffic — the inverse failure mode.

### All MAB devices share one cadence

`_behavior_key()` (lines 531–539) returns `"iot"` for any MAB identity, so a camera and a badge reader both get `traffic_min_sleep=30, traffic_max_sleep=300`. Real cameras stream continuously; real badge readers emit a handful of events per hour.

### Protocol realism is out of band

`tools/real_port_iot_backend.py` serves genuinely protocol-aware listeners on 80, 104, 443, 502, 554, 1883, 5060, 8883, 9100, 44818 and 47808 — but it is **not referenced by the service catalog**, so the assignment engine never drives traffic to it. All orchestrated traffic is HTTP on 9001–9010.

---

## Part 2 — The mechanism already exists

**Do not modify the URL resolution path.** It already resolves policies against every group an identity belongs to:

```python
# lab_orchestrator.py lines 523-529
def _identity_groups(self, identity: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    for value in [identity.get("persona"), identity.get("department"), *(identity.get("groups") or [])]:
        text = str(value or "").strip()
        if text and text not in values:
            values.append(text)
    return values
```

```python
# lab_orchestrator.py lines 565-573
for group in self._identity_groups(identity):
    for srv_id in self.config.get("connectivity_policies", {}).get(group, []):
        service = services_dict.get(srv_id)
        if not service:
            continue
        url = self._service_to_url(service)
```

Any string in an identity's `groups[]` becomes a `connectivity_policies` lookup key.

Likewise `_build_session_plan()` already honours per-identity cadence before falling back to the role profile:

```python
"traffic_min_sleep": int(identity.get("traffic_min_sleep") or profile["traffic_min_sleep"]),
"traffic_max_sleep": int(identity.get("traffic_max_sleep") or profile["traffic_max_sleep"]),
```

So per-cohort destinations and per-persona timing are both achievable as pure data.

### The one rule that must not be broken

**Remove every persona key from `connectivity_policies`.** If `Camera → [srv_camera*]` survives, every camera still hits :9002 and the correlation remains. Persona must contribute no application-specific destination.

After the change the two dimensions carry strictly separate signal:

| Dimension | Drives | Mechanism | Should be recovered by |
|---|---|---|---|
| **persona** | OUI, DHCP vendor class, user-agent, traffic cadence | `tools/identity_switcher.py` `PERSONA_PROFILES` + per-identity sleep | behavioral clustering (HDBSCAN) |
| **cohort** | destination service set | `groups[]` → `connectivity_policies` | graph community detection (Leiden) |

---

## Part 3 — Target topology

### Available backends

| Host | Serves | Provisioned by |
|---|---|---|
| 192.168.31.2 | HTTP mock, ports 9001–9010 | `tools/setup_iot_backend.sh` |
| 192.168.20.3 | Same mock, ports 9001–9010 | same |
| 192.168.20.4 | Same mock, ports 9001–9010 | same |
| 192.168.30.4 | Protocol-aware: 80, 104, 443, 502, 554, 1883, 5060, 8883, 9100, 44818, 47808 | `tools/real_port_iot_backend.py` |
| 192.168.30.2 | Business apps by hostname: thehub, finance, code, engineering, mab, cmdb | lab DNS |
| 192.168.40.2 | `www.netlab.net` (DMZ) | lab DNS |

**Stop treating 31.2 / 20.3 / 20.4 as three replicas of one service.** Repurpose them as three distinct application backends. They already each run all ten ports, so no new infrastructure is required.

### Cohorts

| Cohort | App tier | Protocol tier (192.168.30.4) |
|---|---|---|
| `infra-shared` | `www.netlab.net:80` | — |
| `app-physec` | 31.2:9001, 31.2:9002, 31.2:9006 | 554 RTSP, 443 TLS, 8883 MQTT-TLS |
| `app-bms` | 20.3:9005, 20.3:9004, 20.3:9007 | 47808 BACnet, 1883 MQTT |
| `app-clinical` | 20.4:9010, 20.4:9002, 20.4:9003, 20.4:9007 | 104 DICOM, 9100 JetDirect |
| `app-ot-line1` | 20.3:9009 | 502 Modbus, 44818 EtherNet/IP |
| `app-ot-line2` | 20.4:9009 | 502 Modbus, 44818 EtherNet/IP |
| `app-voice` | 31.2:9008 | 5060 SIP |
| `app-crm` | `thehub.netlab.net:80` | — |
| `app-erp` | `finance.netlab.net:80` | — |
| `app-devtools` | `code.netlab.net:80`, `engineering.netlab.net:80`, `iotdev.netlab.net:8080` | — |
| `app-itops` | `cmdb.netlab.net:80`, `mab.netlab.net:80` | 9100 JetDirect |

There is deliberately no standalone print cohort. A cohort whose only persona is Printer would violate invariant 1 below, and folding JetDirect into IT ops and clinical is closer to reality anyway — print servers belong to IT, and clinical runs its own label and wristband printing.

`app-ot-line1` and `app-ot-line2` deliberately share the Modbus and EtherNet/IP listeners and differ only in app host. Real plants share protocol infrastructure, and the partial overlap keeps the clustering problem non-trivial.

`infra-shared` goes in **every** identity's `groups[]`. This matters: real networks have hub services that every device touches, and a graph without hubs is unrealistically clean. Community detection has to cope with hubs, so the lab must generate them.

### Persona → cohort distribution

Assignment must be **deterministic** — derived from a stable hash of the identity key — so re-runs reproduce the same labels and ground truth stays valid across campaigns.

| Persona | Cohort split |
|---|---|
| Camera | 60% `app-physec`, 25% `app-clinical`, 15% `app-ot-line1` |
| Badge Reader | 80% `app-physec`, 20% `app-clinical` |
| Door Lock | 70% `app-physec`, 30% `app-bms` |
| Display | 40% `app-bms`, 30% `app-voice`, 30% `app-clinical` |
| Environmental Sensor | 60% `app-bms`, 40% `app-ot-line2` |
| HVAC Controller | 85% `app-bms`, 15% `app-clinical` |
| Printer | 55% `app-itops`, 45% `app-clinical` |
| VoIP Phone | 90% `app-voice`, 10% `app-clinical` |
| Medical Device | 100% `app-clinical` |
| Robot | 50% `app-ot-line1`, 50% `app-ot-line2` |
| PLC / RTU / HMI / Historian / SCADA Server / DCS Controller / Safety Controller / Field Device / Industrial Gateway | 50% `app-ot-line1`, 50% `app-ot-line2` (each) |
| Engineering Workstation | 40% `app-devtools`, 60% `app-ot-line1` |
| Sales (dot1x) | 70% `app-crm`, 30% `app-erp` |
| Finance (dot1x) | 65% `app-erp`, 35% `app-crm` |
| Engineering (dot1x) | 70% `app-devtools`, 30% `app-ot-line1` |
| IT (dot1x) | 50% `app-itops`, 25% `app-devtools`, 25% `app-erp` |

**Three invariants must hold.** Assert them programmatically after assignment; if any fails, the experiment cannot distinguish the algorithms.

1. Every cohort contains at least two distinct personas.
2. Every persona except Medical Device spans at least two cohorts.
3. Neither partition is a refinement of the other — they genuinely cross-cut.

The invariant check in `tools/apply_cohorts.py` runs over the **whole identity pool**. Campaign coverage is a separate concern: a launch preset that only selects `population-iot` will exercise a subset of cohorts. For the decorrelation run, use a preset that spans user, IoT and OT runners so every cohort appears in the captured traffic.

### Per-persona cadence

Set `traffic_min_sleep` / `traffic_max_sleep` on each identity. These override the role profile with no code change, and give behavioral clustering real signal to separate personas that now share destinations.

| Persona | min / max sleep (s) | Rationale |
|---|---|---|
| Camera | 1 / 5 | continuous stream |
| PLC, RTU, Safety Controller | 1 / 3 | Modbus poll cycle |
| Historian, SCADA Server, DCS Controller | 5 / 15 | scheduled collection |
| Medical Device | 10 / 30 | vitals push |
| Robot, Industrial Gateway, Field Device, HMI | 5 / 20 | control loop |
| Environmental Sensor | 30 / 60 | periodic telemetry |
| Display | 30 / 120 | content refresh |
| HVAC Controller | 60 / 300 | setpoint poll |
| VoIP Phone | 60 / 120 | SIP registration |
| Badge Reader | 60 / 600 | sparse events |
| Door Lock | 120 / 900 | sparse events |
| Printer | 300 / 1800 | bursty jobs |

---

## Part 4 — Service catalog

Replace the entire services list via `POST /api/services`.

```json
[
  {"id": "srv_web_corp",    "name": "Corporate Web (shared)", "protocol": "http", "target": "www.netlab.net",         "port": 80},
  {"id": "srv_intranet",    "name": "Intranet Hub / CRM",     "protocol": "http", "target": "thehub.netlab.net",      "port": 80},
  {"id": "srv_finance",     "name": "Finance ERP",            "protocol": "http", "target": "finance.netlab.net",     "port": 80},
  {"id": "srv_git",         "name": "Source Control",         "protocol": "http", "target": "code.netlab.net",        "port": 80},
  {"id": "srv_eng_portal",  "name": "Engineering Portal",     "protocol": "http", "target": "engineering.netlab.net", "port": 80},
  {"id": "srv_cmdb",        "name": "CMDB",                   "protocol": "http", "target": "cmdb.netlab.net",        "port": 80},
  {"id": "srv_mab_portal",  "name": "MAB Portal",             "protocol": "http", "target": "mab.netlab.net",         "port": 80},
  {"id": "srv_iot_legacy",  "name": "Legacy IoT Dev",         "protocol": "http", "target": "iotdev.netlab.net",      "port": 8080},

  {"id": "svc_physec_badge",  "name": "PhysSec Badge Events",   "protocol": "http",  "target": "192.168.31.2", "port": 9001, "path": "/badge/events"},
  {"id": "svc_physec_video",  "name": "PhysSec Video Mgmt",     "protocol": "http",  "target": "192.168.31.2", "port": 9002, "path": "/camera/stream"},
  {"id": "svc_physec_door",   "name": "PhysSec Door Control",   "protocol": "http",  "target": "192.168.31.2", "port": 9006, "path": "/lock/events"},
  {"id": "svc_physec_rtsp",   "name": "PhysSec RTSP Stream",    "protocol": "http",  "target": "192.168.30.4", "port": 554,  "path": "/stream"},
  {"id": "svc_physec_tls",    "name": "PhysSec TLS API",        "protocol": "https", "target": "192.168.30.4", "port": 443,  "path": "/badge"},
  {"id": "svc_physec_mqtts",  "name": "PhysSec Door MQTT-TLS",  "protocol": "https", "target": "192.168.30.4", "port": 8883, "path": "/lock"},

  {"id": "svc_bms_hvac",      "name": "BMS HVAC Controller",    "protocol": "http",  "target": "192.168.20.3", "port": 9005, "path": "/hvac/status"},
  {"id": "svc_bms_sensor",    "name": "BMS Sensor Telemetry",   "protocol": "http",  "target": "192.168.20.3", "port": 9004, "path": "/telemetry"},
  {"id": "svc_bms_signage",   "name": "BMS Signage Feed",       "protocol": "http",  "target": "192.168.20.3", "port": 9007, "path": "/display/feed"},
  {"id": "svc_bms_bacnet",    "name": "BMS BACnet",             "protocol": "http",  "target": "192.168.30.4", "port": 47808, "path": "/bacnet"},
  {"id": "svc_bms_mqtt",      "name": "BMS MQTT Broker",        "protocol": "http",  "target": "192.168.30.4", "port": 1883, "path": "/telemetry"},

  {"id": "svc_clin_vitals",   "name": "Clinical Vitals",        "protocol": "http",  "target": "192.168.20.4", "port": 9010, "path": "/medical/vitals"},
  {"id": "svc_clin_imaging",  "name": "Clinical Imaging Feed",  "protocol": "http",  "target": "192.168.20.4", "port": 9002, "path": "/camera/stream"},
  {"id": "svc_clin_print",    "name": "Clinical Print",         "protocol": "http",  "target": "192.168.20.4", "port": 9003, "path": "/print/jobs"},
  {"id": "svc_clin_signage",  "name": "Clinical Signage",       "protocol": "http",  "target": "192.168.20.4", "port": 9007, "path": "/display/feed"},
  {"id": "svc_clin_dicom",    "name": "Clinical DICOM (PACS)",  "protocol": "http",  "target": "192.168.30.4", "port": 104,  "path": "/dicom"},

  {"id": "svc_ot1_robot",     "name": "OT Line 1 Robot Telemetry", "protocol": "http", "target": "192.168.20.3", "port": 9009, "path": "/robot/telemetry"},
  {"id": "svc_ot2_robot",     "name": "OT Line 2 Robot Telemetry", "protocol": "http", "target": "192.168.20.4", "port": 9009, "path": "/robot/telemetry"},
  {"id": "svc_ot_modbus",     "name": "OT Modbus/TCP",             "protocol": "http", "target": "192.168.30.4", "port": 502,   "path": "/"},
  {"id": "svc_ot_enip",       "name": "OT EtherNet/IP",            "protocol": "http", "target": "192.168.30.4", "port": 44818, "path": "/"},

  {"id": "svc_voice_reg",     "name": "Voice Registration",     "protocol": "http",  "target": "192.168.31.2", "port": 9008, "path": "/voip/register"},
  {"id": "svc_voice_sip",     "name": "Voice SIP Proxy",        "protocol": "http",  "target": "192.168.30.4", "port": 5060, "path": "/sip"},

  {"id": "svc_print_jetdirect", "name": "Print JetDirect",      "protocol": "http",  "target": "192.168.30.4", "port": 9100, "path": "/"}
]
```

### Connectivity policies

Replace the entire dict via `POST /api/connectivity`. **Note there are no persona keys and no department keys.**

```json
{
  "infra-shared":  ["srv_web_corp"],

  "app-physec":    ["svc_physec_badge", "svc_physec_video", "svc_physec_door",
                    "svc_physec_rtsp", "svc_physec_tls", "svc_physec_mqtts"],
  "app-bms":       ["svc_bms_hvac", "svc_bms_sensor", "svc_bms_signage",
                    "svc_bms_bacnet", "svc_bms_mqtt"],
  "app-clinical":  ["svc_clin_vitals", "svc_clin_imaging", "svc_clin_print",
                    "svc_clin_signage", "svc_clin_dicom", "svc_print_jetdirect"],
  "app-ot-line1":  ["svc_ot1_robot", "svc_ot_modbus", "svc_ot_enip"],
  "app-ot-line2":  ["svc_ot2_robot", "svc_ot_modbus", "svc_ot_enip"],
  "app-voice":     ["svc_voice_reg", "svc_voice_sip"],

  "app-crm":       ["srv_intranet"],
  "app-erp":       ["srv_finance"],
  "app-devtools":  ["srv_git", "srv_eng_portal", "srv_iot_legacy"],
  "app-itops":     ["srv_cmdb", "srv_mab_portal", "svc_print_jetdirect"]
}
```

### Protocol-port caveat — validate before rolling out

The runner emits HTTP(S) via `traffic_gen.TrafficPersona`. Sending an HTTP GET to port 502, 554, 1883, 5060, 104, 44818 or 47808 reaches a protocol-aware listener that replies with protocol-appropriate bytes, not an HTTP response. The **TCP flow is recorded correctly**, which is all Clarion's NetFlow pipeline consumes — but the HTTP client will fail to parse the reply.

**Before rolling this out to the fleet, verify on a single runner that a failed parse does not abort the session and skip the remaining URLs.** If it does, either wrap the request in a try/except in `auto_lab_runner.run_traffic_session()`, or drop the protocol-tier services from `connectivity_policies` and keep them as a separate low-rate traffic source. Do not discover this after a full campaign.

Ports 443 and 8883 are declared `https` so the client performs a TLS handshake. `real_port_iot_backend.py` uses a self-signed certificate (CN `192.168.30.4`), so verification will fail unless `traffic_gen` disables it. The TCP flow still records.

---

## Part 5 — Identity tagging script

Create `tools/apply_cohorts.py`. Run it on the orchestrator host after posting services and connectivity policies.

```python
#!/usr/bin/env python3
"""
Assign application cohorts orthogonal to persona, and per-persona traffic cadence.

Cohort assignment is deterministic (SHA-256 of MAC or username), so repeated
runs produce identical labels and previously logged ground truth stays valid.

Usage:
    python3 tools/apply_cohorts.py --dry-run
    python3 tools/apply_cohorts.py --apply
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from collections import defaultdict

API = "http://localhost:5000/api"

# persona -> [(cohort, cumulative_percent), ...]; last entry must be 100
COHORT_SPLITS = {
    "Camera":                   [("app-physec", 60), ("app-clinical", 85), ("app-ot-line1", 100)],
    "Badge Reader":             [("app-physec", 80), ("app-clinical", 100)],
    "Door Lock":                [("app-physec", 70), ("app-bms", 100)],
    "Display":                  [("app-bms", 40), ("app-voice", 70), ("app-clinical", 100)],
    "Environmental Sensor":     [("app-bms", 60), ("app-ot-line2", 100)],
    "HVAC Controller":          [("app-bms", 85), ("app-clinical", 100)],
    "Printer":                  [("app-itops", 55), ("app-clinical", 100)],
    "VoIP Phone":               [("app-voice", 90), ("app-clinical", 100)],
    "Medical Device":           [("app-clinical", 100)],
    "Robot":                    [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "PLC":                      [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "RTU":                      [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "HMI":                      [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "Historian":                [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "SCADA Server":             [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "DCS Controller":           [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "Safety Controller":        [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "Field Device":             [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "Industrial Gateway":       [("app-ot-line1", 50), ("app-ot-line2", 100)],
    "Engineering Workstation":  [("app-devtools", 40), ("app-ot-line1", 100)],
    "Sales":                    [("app-crm", 70), ("app-erp", 100)],
    "Finance":                  [("app-erp", 65), ("app-crm", 100)],
    "Engineering":              [("app-devtools", 70), ("app-ot-line1", 100)],
    "IT":                       [("app-itops", 50), ("app-devtools", 75), ("app-erp", 100)],
}

# persona -> (traffic_min_sleep, traffic_max_sleep)
CADENCE = {
    "Camera": (1, 5),
    "PLC": (1, 3), "RTU": (1, 3), "Safety Controller": (1, 3),
    "Historian": (5, 15), "SCADA Server": (5, 15), "DCS Controller": (5, 15),
    "Medical Device": (10, 30),
    "Robot": (5, 20), "Industrial Gateway": (5, 20),
    "Field Device": (5, 20), "HMI": (5, 20),
    "Environmental Sensor": (30, 60),
    "Display": (30, 120),
    "HVAC Controller": (60, 300),
    "VoIP Phone": (60, 120),
    "Badge Reader": (60, 600),
    "Door Lock": (120, 900),
    "Printer": (300, 1800),
    "Engineering Workstation": (3, 20),
}

SHARED = "infra-shared"
ALL_COHORTS = {c for splits in COHORT_SPLITS.values() for c, _ in splits}


def get(path):
    with urllib.request.urlopen(f"{API}/{path}", timeout=15) as r:
        return json.loads(r.read().decode())


def post(path, payload):
    req = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def bucket(identity):
    """Stable 0-99 bucket from MAC, falling back to username or device_name."""
    key = (identity.get("mac") or identity.get("username")
           or identity.get("device_name") or "").strip().lower()
    if not key:
        return None
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 100


def cohort_for(identity):
    persona = (identity.get("persona") or identity.get("department") or "").strip()
    splits = COHORT_SPLITS.get(persona)
    if not splits:
        return None, persona
    b = bucket(identity)
    if b is None:
        return None, persona
    for cohort, ceiling in splits:
        if b < ceiling:
            return cohort, persona
    return splits[-1][0], persona


def check_invariants(assignments):
    """assignments: list of (persona, cohort). Returns list of failure strings."""
    by_cohort, by_persona = defaultdict(set), defaultdict(set)
    for persona, cohort in assignments:
        by_cohort[cohort].add(persona)
        by_persona[persona].add(cohort)

    failures = []
    for cohort, personas in sorted(by_cohort.items()):
        if len(personas) < 2:
            failures.append(f"cohort {cohort!r} has only persona(s) {sorted(personas)}")
    for persona, cohorts in sorted(by_persona.items()):
        if persona == "Medical Device":
            continue
        if len(cohorts) < 2:
            failures.append(f"persona {persona!r} sits in only cohort(s) {sorted(cohorts)}")
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="POST changes (default is dry run)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    policies = get("connectivity")
    leaked = [k for k in policies if k not in ALL_COHORTS and k != SHARED]
    if leaked:
        print(f"ERROR: connectivity_policies still contains non-cohort keys: {leaked}")
        print("Persona keys must be removed first or device type stays correlated.")
        return 1

    identities = get("identities")
    print(f"Loaded {len(identities)} identities")

    assignments, skipped = [], []
    for ident in identities:
        cohort, persona = cohort_for(ident)
        if not cohort:
            skipped.append(ident.get("device_name") or ident.get("username") or "?")
            continue
        ident["application_cohort"] = cohort
        groups = [g for g in (ident.get("groups") or []) if g not in ALL_COHORTS and g != SHARED]
        groups.extend([cohort, SHARED])
        ident["groups"] = groups
        if persona in CADENCE:
            ident["traffic_min_sleep"], ident["traffic_max_sleep"] = CADENCE[persona]
        assignments.append((persona, cohort))

    counts = defaultdict(int)
    for persona, cohort in assignments:
        counts[(persona, cohort)] += 1
    print("\npersona -> cohort distribution:")
    for (persona, cohort), n in sorted(counts.items()):
        print(f"  {persona:26} {cohort:16} {n:4}")

    if skipped:
        print(f"\nWARNING: {len(skipped)} identities had no persona mapping: {skipped[:10]}")

    failures = check_invariants(assignments)
    if failures:
        print("\nINVARIANT FAILURES — the experiment will not be able to separate the two dimensions:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nInvariants OK: every cohort spans >=2 personas, every persona spans >=2 cohorts.")

    if not args.apply:
        print("\nDry run. Re-run with --apply to POST.")
        return 0

    print(post("identities", identities))
    print("Applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## Part 6 — Code changes

### 6.1 Ground-truth cohort column (required)

Three edits in `log_ground_truth()` in `lab_orchestrator.py`.

**Edit 1** — add the column to `fieldnames` (list begins line 1258). Insert immediately after `"persona"` at line 1271:

```python
                "persona",
                "application_cohort",
                "os",
```

**Edit 2** — supply the value in the `writer.writerow([...])` call at line 1313, at the **same ordinal position**. Source it from the identity:

```python
cohort = identity.get("application_cohort") or ""
```

Store the cohort as an explicit `application_cohort` key on the identity in addition to putting it in `groups[]`. The `groups[]` entry drives traffic resolution; the explicit key is what ground truth reads. Do not make the logger parse `groups[]`.

**Edit 3** — extend the legacy-header migration guard at lines 1287–1291. It currently rewrites the header only when `session_duration_seconds`, `launch_id` or `campaign_label` are missing. Add the new column so existing CSVs are migrated instead of silently misaligning:

```python
                    if existing_header and (
                        "session_duration_seconds" not in existing_header
                        or "launch_id" not in existing_header
                        or "campaign_label" not in existing_header
                        or "application_cohort" not in existing_header
                    ):
```

### 6.2 Role behavior patterns for users (required)

`_select_session_urls()` gives dot1x identities a random 2–5 URL subset chosen by substring matching against `primary_patterns` / `secondary_patterns` in `ROLE_BEHAVIORS`. New cohort hostnames will not match the existing patterns and will be deprioritised or dropped.

Add `thehub`, `cmdb`, `engineering` and `mab` to the relevant pattern tuples. These live in the database under the `orchestration_settings` config key — change them with `POST /api/config`, **not** by editing the `ROLE_BEHAVIORS` class attribute, which is only a default used when the key is absent. MAB identities bypass this path entirely.

### 6.3 Second-dimension scoring (recommended)

`validate_clarion_grouping.py` currently computes purity of `persona` against Clarion's `cluster_label` / `sgt_name` / `assigned_group` and passes at ≥ 0.85. Extend it to report the same metric for `application_cohort`, so a run yields two numbers rather than one. The interpretation:

| Behavioral grouping | Application grouping | Conclusion |
|---|---|---|
| high on persona, low on cohort | high on cohort | complementary — both are needed |
| high on both | — | community detection adds nothing |
| — | high on both | reconsider the grouping pipeline |

### 6.4 Byte-volume realism (optional, phase 2)

Flow *count* now varies correctly via cadence, but bytes-per-flow does not. `auto_lab_runner.py` builds `persona_config` with only `{targets, method, min_sleep, max_sleep, user_agent}`, and `traffic_gen.TrafficPersona` lives in the separate `traffic-simulation` repo. Adding a payload-size parameter requires changing both. A camera and a badge reader should differ by orders of magnitude in bytes, but this is not required for the decorrelation experiment.

### 6.5 Multi-tier applications (optional, phase 2)

`iot_backend_mock.py` (deployed by `tools/setup_iot_backend.sh`) never originates outbound traffic, so there are no server-to-server flows and no application tiers to discover. Having the app tier call a database tier on each request would make dependency mapping genuinely interesting, and is the difference between "which clients share a server" and "what does this application depend on".

---

## Part 7 — Verification

### Before launching a campaign

1. `GET /api/connectivity` — no persona names remain as keys; only `app-*` and `infra-shared`.
2. `GET /api/identities` — confirm `groups`, `application_cohort`, `traffic_min_sleep` and `traffic_max_sleep` survived the round-trip through SQLite.
3. `python3 tools/apply_cohorts.py --dry-run` exits 0 with all three invariants satisfied.
4. `./check_iot_backends.sh 192.168.31.2 192.168.20.3 192.168.20.4` — all thirty host/port combinations respond.
5. `tools/real_port_iot_backend.py` is running on 192.168.30.4 and its ports are reachable. `tools/verify_internal_services.py` probes the catalog against `/api/services`.
6. **Single-runner smoke test** with one protocol-tier URL in the assignment, confirming the session completes and the remaining URLs are still visited (see the caveat in Part 4).

### After a campaign

7. The ground-truth CSV has a populated `application_cohort` column on every row.
8. In Clarion, two endpoints with the **same persona and different cohorts** show different `endpoint_behavior.top_dst_ips`, and two endpoints with **different personas and the same cohort** show overlapping ones. If this is not true, a persona key survived in `connectivity_policies`.
9. Re-run `scripts/graph/leiden_partition_spike.py` in the `sgerhart/clarion` repo with the cohort labels joined in. It already reports modularity, rule compression and identity-explainability; with the second ground-truth dimension it will report ARI against both persona and cohort, which is the result that decides whether community detection earns a place alongside the existing clustering pipeline.

---

## Summary of changes

| # | Change | Type | File / endpoint |
|---|---|---|---|
| 1 | Replace service catalog | config | `POST /api/services` |
| 2 | Replace connectivity policies, removing all persona keys | config | `POST /api/connectivity` |
| 3 | Tag identities with cohort, groups, cadence | config | `tools/apply_cohorts.py` → `POST /api/identities` |
| 4 | Add cohort hostnames to role behavior patterns | config | `POST /api/config` (`orchestration_settings`) |
| 5 | Add `application_cohort` ground-truth column | **code** | `lab_orchestrator.py` `log_ground_truth()` |
| 6 | Score the second dimension | **code** | `validate_clarion_grouping.py` |
| 7 | Mirror 1–2 into the seed file for fresh deploys | config | `orchestrator_config.json` |
| 8 | Byte-volume and multi-tier realism | **code**, phase 2 | `auto_lab_runner.py`, `traffic_gen`, `iot_backend_mock.py` |
