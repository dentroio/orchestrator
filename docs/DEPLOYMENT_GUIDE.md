# Clarion Lab Deployment Guide

**Quick Reference:** Building the complete lab environment from scratch

---

## 📋 Overview

This guide provides step-by-step instructions for deploying the complete Clarion Lab testbed across all devices.

**Total Devices:** 15 (6 Pi Runners + 5 Pi Rebuilds + 4 Windows + 2 ESP32) + 3 Existing Servers

**Estimated Setup Time:** 2-3 days for complete deployment

---

## 🗂️ Lab Documentation Structure

| Document | Purpose |
|----------|---------|
| `LAB_MASTER_PLAN.md` | Complete execution roadmap with phased checklist |
| `LAB_FILE_STRUCTURE.md` | Detailed file/directory structure for each device type |
| `DEPLOYMENT_GUIDE.md` | This document - deployment procedures |
| `BACKEND_SERVERS.md` | Backend servers setup (IoT mock, business hosts, DNS) |
| `RASPBERRY_PI_SETUP.md` | Original Pi setup documentation |

---

## 🚀 Quick Deployment Checklist

### Prerequisites

- [ ] All Raspberry Pis have fresh Raspbian/Ubuntu installed
- [ ] All devices have network connectivity
- [ ] You have admin access to ISE, Clarion, and backend servers
- [ ] Git repository is accessible: `https://github.com/dentroio/clarion.git`

### Phase 1: Core Infrastructure (Day 1)

**1.1 Deploy Pi Runners (6 devices: ~2 hours)**

```bash
# On each Pi Runner (Pi-Runner-1 through Pi-Runner-6)
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_pi_runner.sh | bash
```

After running setup script on each runner:
- [ ] Note hostname and IP address
- [ ] Copy SSH public key (displayed at end of setup)
- [ ] Edit `/home/pi/clarion_lab/runner_config.json`:
  - Pi-Runner-1: `eth0`, persona_set: `["Sales"]`
  - Pi-Runner-2: `eth0`, persona_set: `["Finance"]`
  - Pi-Runner-3: `eth0`, persona_set: `["Engineering"]`
  - Pi-Runner-4: `eth0`, persona_set: `["IT"]`
  - Pi-Runner-5: `wlan0`, persona_set: `["Badge Reader", "Camera", "Printer", "Environmental Sensor", "HVAC Controller"]`
  - Pi-Runner-6: `wlan0`, persona_set: `["Door Lock", "Display", "VoIP Phone", "Robot", "Medical Device"]`

**1.2 Deploy Orchestrator (Pi-Rebuild-3: ~30 min)**

```bash
# On Pi-Rebuild-3
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_orchestrator.sh | bash
```

After setup:
- [ ] Determine management interface IPs for all 6 runners:
  ```bash
  # Pi-Runner-1-4: Management on wlan0
  # Pi-Runner-5-6: Management on eth0
  # Get IPs: ssh to each runner and run: ip addr show wlan0 (or eth0)
  ```
- [ ] Edit `/home/pi/clarion/lab/orchestrator_config.json`:
  - Update all `"host"` fields with actual management interface IPs
  - Verify `"interface"` field matches the lab interface (eth0 for runners 1-4, wlan0 for runners 5-6)
- [ ] Edit `/home/pi/.ssh/config` with actual management IPs
- [ ] Distribute SSH key to all runners (using management IPs):
  ```bash
  cd /home/pi/clarion_lab
  # Edit distribute_ssh_keys.sh with correct management IPs first
  ./distribute_ssh_keys.sh
  ```
- [ ] Test orchestrator:
  ```bash
  python3 /home/pi/clarion/lab/lab_orchestrator.py --schedule daily --duration 1
  ```

**1.3 Deploy IoT Backend (Pi-Rebuild-1 or 192.168.31.2: ~20 min)**

For full backend setup (business hosts, DNS, all targets), see **`BACKEND_SERVERS.md`**.

```bash
# On Pi-Rebuild-1 (or on 192.168.31.2 if using existing server)
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_iot_backend.sh | bash
```

After setup:
- [ ] Verify all 10 endpoints are responding (automated test runs at end of setup)
- [ ] Test from another machine:
  ```bash
  curl http://192.168.31.3:9001/badge/events -X POST -H "Content-Type: application/json" -d '{"test": true}'
  ```
- [ ] Check service status:
  ```bash
  sudo systemctl status iot_backend_mock
  ```

**1.4 Deploy Validator (Pi-Rebuild-4: ~20 min)**

```bash
# On Pi-Rebuild-4
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_validator.sh | bash
```

After setup:
- [ ] Edit `/home/pi/clarion/lab/validation_config.json`:
  - Update `clarion_api` URL to match your Clarion server
- [ ] Setup SSH access to orchestrator:
  ```bash
  ssh-copy-id pi@192.168.10.10
  ```
- [ ] Test ground truth sync:
  ```bash
  /home/pi/clarion_lab/sync_ground_truth.sh
  ```

**1.5 Deploy Additional Endpoint & Fault Injection (Pi-Rebuild-2 & Pi-Rebuild-5: ~30 min)**

```bash
# On both Pi-Rebuild-2 and Pi-Rebuild-5
# Use same setup as Pi Runners
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_pi_runner.sh | bash
```

### Phase 2: Configuration (Day 1-2)

**2.1 Update Identities Configuration**

On your development machine or orchestrator:

```bash
cd /home/pi/clarion/lab
nano identities1.json
```

Update required fields (see `LAB_MASTER_PLAN.md` Phase 1.5):
- [ ] Add `ad_groups` to all user identities
- [ ] Add `traffic_method`, `traffic_min_sleep`, `traffic_max_sleep` to IoT identities
- [ ] Add 5-10 violation identities
- [ ] Validate JSON syntax:
  ```bash
  python3 -m json.tool identities1.json > /dev/null && echo "✓ Valid JSON"
  ```

**2.2 Sync Identities to ISE**

```bash
# From any Pi with the repo cloned
cd /home/pi/clarion/lab

# Configure ISE credentials
nano ise_sync_config.json
# Update: host, username, password

# Run sync
python3 sync_ise_groups_and_endpoints.py --identities identities1.json
```

Verify in ISE:
- [ ] All identity groups created (Lab-BadgeReader, Lab-Camera, etc.)
- [ ] All MAC addresses registered

**2.3 Configure Windows Machines (4 devices: ~1 hour)**

On each Windows machine:

1. Create directory structure:
   ```powershell
   New-Item -Path "C:\ClarionLab\config" -ItemType Directory -Force
   New-Item -Path "C:\ClarionLab\scripts" -ItemType Directory -Force
   New-Item -Path "C:\ClarionLab\logs" -ItemType Directory -Force
   ```

2. Download traffic generator:
   ```powershell
   # Download from repository or create manually
   # See LAB_FILE_STRUCTURE.md for PowerShell script
   ```

3. Configure 802.1X profile:
   - Open Network Settings → Wi-Fi → Manage Known Networks
   - Select `netlab_employee` → Properties
   - Security: WPA2-Enterprise
   - Network authentication method: Microsoft: Protected EAP (PEAP)
   - Authentication method: Secured password (EAP-MSCHAP v2)
   - Enter credentials for assigned user (alice.johnson, jane.robinson, etc.)

4. Create scheduled task to run traffic generator on login

5. Test authentication and traffic generation

**2.4 Flash ESP32 Firmware (2 devices: ~1 hour)**

See `LAB_FILE_STRUCTURE.md` Section 8 for complete firmware code.

For each ESP32:
1. Install PlatformIO or Arduino IDE
2. Create project with firmware code
3. Update `config.h` with device-specific settings:
   - ESP32-1: Environmental Sensor, MAC `00:1E:58:aa:11:01`, backend `192.168.31.2:9004`
   - ESP32-2: Door Lock, MAC `00:0D:67:aa:12:01`, backend `192.168.31.2:9006`
4. Flash firmware
5. Test connectivity and traffic

### Phase 3: Validation Testing (Day 2)

**3.1 Test Individual Pi Runners**

For each runner:

```bash
# SSH to runner
ssh pi@192.168.10.10X

# Test identity switch
sudo python3 /home/pi/clarion/lab/identity_switcher.py \
  --user alice.johnson \
  --interface eth0

# Check authentication succeeded
# Check traffic is being generated
```

**3.2 Test Orchestrated Run**

```bash
# On orchestrator (Pi-Rebuild-3)
cd /home/pi/clarion/lab

# Start a short test run (1 hour)
python3 lab_orchestrator.py --schedule daily --duration 1

# Monitor ground truth log
tail -f /home/pi/clarion_lab/ground_truth/ground_truth_log.csv
```

**3.3 Test Validation**

```bash
# On validator (Pi-Rebuild-4)
cd /home/pi/clarion_lab

# Run validation
./run_validation.sh

# Review report
cat validation/validation_report.json | python3 -m json.tool
```

---

## 📊 IP Address Reference

### Pi Runners (6 devices) - Dual Interface Configuration

**Network Design:**
- **Lab Interface:** Used for identity rotation, 802.1x/MAB authentication, traffic generation (DHCP)
- **Management Interface:** Used for SSH access from orchestrator, already configured (DHCP or Static)

| Device | Hostname | Lab Interface | Mgmt Interface | Lab Role |
|--------|----------|---------------|----------------|----------|
| Pi-Runner-1 | pi-runner-1 | eth0 (DHCP) | wlan0 (configured) | Wired, Sales |
| Pi-Runner-2 | pi-runner-2 | eth0 (DHCP) | wlan0 (configured) | Wired, Finance |
| Pi-Runner-3 | pi-runner-3 | eth0 (DHCP) | wlan0 (configured) | Wired, Engineering |
| Pi-Runner-4 | pi-runner-4 | eth0 (DHCP) | wlan0 (configured) | Wired, IT |
| Pi-Runner-5 | pi-runner-5 | wlan0 (DHCP) | eth0 (configured) | Wireless, IoT Set A |
| Pi-Runner-6 | pi-runner-6 | wlan0 (DHCP) | eth0 (configured) | Wireless, IoT Set B |

**Note:** Management interface IPs are already configured. Use these IPs in `orchestrator_config.json`.

### Infrastructure Nodes (5 Pis)

| Device | Hostname | Recommended IP | Interface | Notes |
|--------|----------|----------------|-----------|-------|
| Pi-Rebuild-1 | pi-iot-backend | 192.168.31.3 | eth0 | IoT backends, can be static or DHCP reservation |
| Pi-Rebuild-2 | pi-endpoint-extra | DHCP | eth0 (lab), wlan0 (mgmt) | Same dual-interface as runners |
| Pi-Rebuild-3 | pi-orchestrator | Static or DHCP reservation | eth0 | Orchestration controller |
| Pi-Rebuild-4 | pi-validator | Static or DHCP reservation | eth0 | Validation node |
| Pi-Rebuild-5 | pi-fault-injection | DHCP | eth0 (lab), wlan0 (mgmt) | Same dual-interface as runners |

### Client Endpoints

| Device | Hostname | IP Assignment | Interface | Notes |
|--------|----------|---------------|-----------|-------|
| Win-1 | WIN-SALES-01 | DHCP | wlan0 | alice.johnson |
| Win-2 | WIN-FINANCE-01 | DHCP | wlan0 | jane.robinson |
| Win-3 | WIN-ENG-01 | DHCP | wlan0 | henry.brown |
| Win-4 | WIN-IT-01 | DHCP | wlan0 | victor.wilson |
| ESP32-1 | env-sensor-esp32-01 | DHCP | wlan0 | Env Sensor |
| ESP32-2 | door-lock-esp32-01 | DHCP | wlan0 | Door Lock |

---

## 🔑 SSH Key Distribution Matrix

The orchestrator (Pi-Rebuild-3) needs SSH access to all runners:

```
Orchestrator (Pi-Rebuild-3)
    ├─> Pi-Runner-1
    ├─> Pi-Runner-2
    ├─> Pi-Runner-3
    ├─> Pi-Runner-4
    ├─> Pi-Runner-5
    └─> Pi-Runner-6

Validator (Pi-Rebuild-4)
    └─> Orchestrator (Pi-Rebuild-3) [for ground truth sync]
```

**Distribution Command:**

```bash
# On orchestrator
cd /home/pi/clarion_lab
./distribute_ssh_keys.sh

# On validator
ssh-copy-id pi@192.168.10.10
```

---

## 📦 Required Packages Summary

### All Raspberry Pis (Runners + Infrastructure)

```bash
# System packages
sudo apt install python3-pip wpasupplicant network-manager git curl

# Python packages (runners + orchestrator + validator)
pip3 install requests flask psutil fabric paramiko pandas matplotlib
```

### IoT Backend Node Only

```bash
# Additional system packages
sudo apt install ufw

# Optional: MQTT broker
sudo apt install mosquitto mosquitto-clients
```

---

## 🔧 Troubleshooting Common Issues

### Issue: SSH key distribution fails

**Solution:**
```bash
# Manually copy key to each runner
for ip in 192.168.10.{101..106}; do
    echo "Copying to $ip..."
    ssh-copy-id pi@$ip
done
```

### Issue: Identity switch fails with "authentication failed"

**Checklist:**
- [ ] ISE is configured for Dot1x (PEAP/MSCHAPV2)
- [ ] Credentials in `identities1.json` are correct
- [ ] MAC address is synced to ISE (run `sync_ise_groups_and_endpoints.py`)
- [ ] Check ISE RADIUS logs for failure reason

### Issue: An identity never appears in ISE Live Log (e.g. frank.moore)

If the orchestrator shows a user (e.g. frank.moore on pi-runner-4) but that identity **never** shows a successful auth in ISE Live Log:

1. **Identity must exist in ISE’s identity store**  
   The username (e.g. `frank.moore`) must exist in the same store ISE uses for 802.1X (AD or local). If it’s missing, ISE will reject or drop the request and you won’t see a successful entry for that user.

2. **Password must match**  
   The password in `identities1.json` must match the one in AD (or local). Wrong password → auth failure; check ISE Live Log for failed attempts for that identity.

3. **Add the user to the repo and to ISE**  
   The repo’s `identities1.json` now includes `frank.moore`, `steve.jackson`, `bob.williams`, `diana.jackson`, `larry.lewis`. Ensure each of these users exists in your AD (or ISE local store) with the same password you use in the lab (e.g. `C!sco#123`). Sync or add them so ISE can authenticate them.

### Issue: Auth works for a few switches, then starts failing (Misconfigured Supplicants / Client Stopped Responding)

ISE “Misconfigured Supplicants” or “Client Stopped Responding” after several identity switches often means:

1. **Supplicant/NetworkManager state**  
   After many NM connection down/up cycles, the supplicant or NM can get into a bad state. On the Pi runner:
   - Check `journalctl -u NetworkManager` and `~/lab_runner.log` for EAP or NM errors.
   - Try restarting NM: `sudo systemctl restart NetworkManager`, then let the orchestrator switch again.

2. **Switch port err-disable or rate limiting**  
   If the switch port goes err-disable from link flapping, the Pi will stop getting link. Fix the port (or wait for recovery) and/or avoid frequent link down/up; the lab uses “NM re-auth without link down” on wired to reduce flapping.

3. **Stale credentials in NM**  
   The runner updates the `clarion-lab-auth` profile with the new username/password before each bounce. If an old profile or UUID is still in use, the wrong credentials can be sent. Ensure `identity_switcher` is updating and activating the correct profile (check runner log for “Updating wired 802.1X config for user: …”).

4. **ISE session or failure limits**  
   Check ISE for policies that limit re-auths or fail after N attempts; relax or adjust for lab use if needed.

### Issue: IoT backend endpoints not accessible

**Solution:**
```bash
# Check firewall
sudo ufw status

# Check service status
sudo systemctl status iot_backend_mock

# Check logs
sudo journalctl -u iot_backend_mock -f

# Test locally first
curl http://localhost:9001/badge/events -X POST
```

### Issue: Ground truth log is empty

**Checklist:**
- [ ] Orchestrator has SSH access to all runners (test with `ssh pi@192.168.10.101`)
- [ ] Runner IPs in `orchestrator_config.json` are correct
- [ ] Orchestrator is running (check logs: `tail -f /var/log/clarion_lab/orchestrator.log`)

### Issue: Validation fails with "cannot connect to Clarion API"

**Solution:**
```bash
# Test Clarion API manually
curl http://192.168.30.2:5000/api/devices

# Update validation_config.json with correct URL
nano /home/pi/clarion/lab/validation_config.json
```

---

## 📝 Daily Operations Checklist

Once deployed, use this checklist for daily lab operation:

**Morning Startup (10 min)**
- [ ] Verify all services running:
  ```bash
  # Check IoT backend
  ssh pi-iot-backend "sudo systemctl status iot_backend_mock"
  
  # Check orchestrator
  ssh pi-orchestrator "ls -lh /home/pi/clarion_lab/ground_truth/"
  ```

**Start Lab Run (5 min)**
- [ ] SSH to orchestrator
- [ ] Start orchestrated run:
  ```bash
  python3 /home/pi/clarion/lab/lab_orchestrator.py --schedule daily --duration 8
  ```

**End of Day Validation (15 min)**
- [ ] SSH to validator
- [ ] Run validation:
  ```bash
  /home/pi/clarion_lab/run_validation.sh
  ```
- [ ] Review report
- [ ] Archive results

---

## 🔄 Runner maintenance: setup vs deploy

**Do I have to run setup on these boxes as well?**

- **Run setup once per box** (or after a reimage): use `setup_pi_runner.sh` on each Pi runner. That installs packages (apt, pip), configures the lab interface (netplan/NM), creates dirs, SSH keys, and the `clarion-lab-wifi` profile for WiFi-lab runners.
- **For code-only updates**, you do **not** need to run setup again. Use **deploy** instead:
  - From your dev machine (or orchestrator): `./deploy_runner.sh user@runner-host` to push the latest `lab/` code (e.g. `auto_lab_runner.py`, `identity_switcher.py`, `traffic_gen.py`) to `~/clarion/lab/` on that runner.
  - Or use the dashboard **Check runner files** to see which runners are out of date, then run `deploy_runner.sh` for each.

So: **setup = once per box (or after reimage); deploy = whenever you want to refresh code.**

### Runner agent (client/server; no SSH from orchestrator)

Runners are **always** agents: they poll the orchestrator for assignments. Config and identities are in the server’s SQLite DB; no JSON file copy.

1. **Orchestrator:** Add the runner in the dashboard **Configuration** (name = `RUNNER_ID`, interface, persona set, session duration). No SSH fields required.
2. **Deploy agent code:** Run `./deploy_runner.sh user@runner-host` so the runner has `runner_agent.py` and `clarion-runner.service` in `~/clarion/lab/`.
3. **On each runner (one-time):**
   - `sudo cp ~/clarion/lab/clarion-runner.service /etc/systemd/system/`
   - `sudo systemctl edit clarion-runner` — set `ORCHESTRATOR_URL` and `RUNNER_ID` (e.g. `pi-runner-1`, must match runner name in dashboard).
   - If lab dir is not `/home/pi/clarion/lab`, set `WorkingDirectory` and `ExecStart` path.
   - `sudo systemctl daemon-reload && sudo systemctl enable clarion-runner && sudo systemctl start clarion-runner`
4. **Check:** `sudo systemctl status clarion-runner` and dashboard; start orchestration and confirm the runner gets assignments and runs sessions.

See **RUNNER_ARCHITECTURE.md** and **DEPLOY_THIS.md**.

### Preflight audit before Start

Use the dashboard **Audit** button before starting orchestration. It verifies dual-interface safety (management path to orchestrator, lab idle with no active NM session). **Fix Issues** remediates all Pi runners; each runner card also has a **Fix** button for that runner only. See **[RUNNER_PREFLIGHT_AUDIT.md](RUNNER_PREFLIGHT_AUDIT.md)** for API endpoints, check list, and wlan/eth0 notes.

Use **Launch…** (not a blind Start) to choose users vs IoT, personas, max concurrent sessions, and which runners to enable. Ground truth rows include a `launch_id` for Clarion correlation. See **[LAUNCH_SESSION.md](LAUNCH_SESSION.md)**.

---

## ⚠️ When you see “system is being managed” / NetworkManager messages

If you see a message that the **system** or **network/connection is “managed”** (e.g. by NetworkManager or systemd), that usually means:

- The lab interface (eth0 or wlan0) is **managed by NetworkManager**. Our lab scripts are built for that: they use `nmcli` and expect connections like `clarion-lab-auth` (802.1X) and `clarion-lab-wifi` (WiFi lab).
- **You should not** manually edit `/etc/network/interfaces` for that interface or run conflicting tools (e.g. old `ifup`/`ifdown`) for the same device. Use our scripts or `nmcli` only.

**If something fails when you see “managed”:**

1. Confirm NetworkManager is running:  
   `systemctl is-active NetworkManager` → should be `active`.
2. Confirm the expected connection exists:  
   `nmcli connection show` — you should see `clarion-lab-auth` (and on WiFi-lab runners, `clarion-lab-wifi`). If missing, re-run `setup_pi_runner.sh` on that runner (steps 7–8 create netplan and the WiFi profile).
3. If the interface is listed as **unmanaged** and you need it managed:  
   Check `/etc/NetworkManager/NetworkManager.conf` — under `[keyfile]`, `unmanaged-devices` should not list your lab interface (e.g. `eth0` or `wlan0`). If it does, remove it and restart NetworkManager.

---

## 🎯 Next Steps After Deployment

Once all devices are deployed and tested:

1. **Review `LAB_MASTER_PLAN.md`** - Follow the phased execution roadmap
2. **Phase 1: Foundation** - Complete all setup checklists
3. **Phase 2: Baseline** - Run isolated persona sessions (30-60 min each)
4. **Phase 3: Mixed Load** - Run all endpoints simultaneously for 4-8 hours
5. **Phase 4: Validation** - Score Clarion grouping against ground truth
6. **Phase 5: Continuous** - Setup daily/weekly runs for ongoing testing

---

## 📞 Quick Reference Commands

### Check Status of All Services

```bash
# Create a status check script
cat > /home/pi/check_lab_status.sh << 'EOF'
#!/bin/bash
echo "=== Lab Status Check ==="
echo ""
echo "Pi Runners:"
for ip in 192.168.10.{101..106}; do
    echo -n "  $ip: "
    ssh -o ConnectTimeout=2 pi@$ip "echo OK" 2>/dev/null || echo "OFFLINE"
done

echo ""
echo "Infrastructure:"
echo -n "  Orchestrator (192.168.10.10): "
ssh -o ConnectTimeout=2 pi@192.168.10.10 "echo OK" 2>/dev/null || echo "OFFLINE"

echo -n "  Validator (192.168.10.11): "
ssh -o ConnectTimeout=2 pi@192.168.10.11 "echo OK" 2>/dev/null || echo "OFFLINE"

echo -n "  IoT Backend (192.168.31.3): "
ssh -o ConnectTimeout=2 pi@192.168.31.3 "echo OK" 2>/dev/null || echo "OFFLINE"

echo ""
echo "IoT Backend Endpoints:"
for port in {9001..9010}; do
    echo -n "  Port $port: "
    curl -s -o /dev/null -w "%{http_code}" http://192.168.31.3:$port/ --connect-timeout 2 || echo "FAILED"
done
EOF

chmod +x /home/pi/check_lab_status.sh
./check_lab_status.sh
```

### Emergency Stop All Runners

```bash
# SSH to orchestrator, then:
for ip in 192.168.10.{101..106}; do
    ssh pi@$ip "sudo pkill -f auto_lab_runner.py" &
done
wait
echo "All runners stopped"
```

---

## 📚 Document Cross-Reference

- **LAB_MASTER_PLAN.md** - Phased execution roadmap with detailed checklists
- **LAB_FILE_STRUCTURE.md** - Complete file/directory structure for each device
- **RASPBERRY_PI_SETUP.md** - Original setup documentation with technical details
- **DEPLOYMENT_GUIDE.md** - This document (quick deployment procedures)
- **identities1.json** - Master identity database
- **orchestrator_config.json** - Orchestration configuration
- **validation_config.json** - Validation thresholds and API config

---

**Last Updated:** February 12, 2026  
**Lab Version:** 1.0  
**Status:** Ready for Deployment
