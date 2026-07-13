# Quick Start - You're Almost Ready!

**Current Status:** 🎉 You already have the hard parts done!

✅ **ISE** - Ready  
✅ **Clarion** - Ready  
✅ **6 Pi Runners** - Ready  
⚠️ **5 Infrastructure Pis** - Need to build  
⚠️ **IoT Backend** - Need to deploy

---

## What You Need to Build (Priority Order)

### 🚀 TODAY: Get Testing Started (2-3 hours)

#### 1. IoT Backend (30 minutes) - HIGHEST PRIORITY

**Server:** `192.168.31.2` (iotdev.netlab.net) – already has iotdev site; add IoT backend here.

**SSH:** `steve@192.168.31.2`  
(See [SERVER_ACCESS.md](SERVER_ACCESS.md) for access details; keep password out of repo.)

**Deploy now (from your machine, local files only – no git pull):**
```bash
# From your machine (you'll be prompted for steve's password)
cd /path/to/clarion/lab
./deploy_iot_backend.sh
```
The script copies your local `iot_backend_mock.py` to the server and installs/runs it. No GitHub access on the server.

**Or deploy manually on the server (copy file first):**
```bash
# From your machine: copy file to server
scp lab/iot_backend_mock.py steve@192.168.31.2:/tmp/

# SSH to iotdev server
ssh steve@192.168.31.2
mkdir -p ~/clarion/lab
cp /tmp/iot_backend_mock.py ~/clarion/lab/
# Then run setup_iot_backend.sh if you have it there, or install deps and systemd manually
```

**Why First:** Your 6 Pi Runners need these endpoints to generate IoT traffic. Existing iotdev.netlab.net stays as-is; IoT backend uses ports 9001–9010.

---

#### 2. Test One Pi Runner (30 minutes) - VERIFY IT WORKS

**Pick one Pi (e.g., Pi-Runner-1) and test:**

```bash
# SSH to Pi-Runner-1
ssh pi@<pi-runner-1-management-ip>

# Test identity switch
sudo python3 /home/pi/clarion/lab/identity_switcher.py \
  --user alice.johnson \
  --interface eth0

# Wait 2 minutes, then check:
# - Did ISE authenticate? (check ISE logs)
# - Did interface get new IP? (ip addr show eth0)
# - Is hostname ajohnson-ws? (hostname)

# Generate traffic for 10 minutes
sudo python3 /home/pi/clarion/lab/auto_lab_runner.py \
  --interface eth0 \
  --session-duration 600
```

**Check in Clarion (manual):**
- Open Clarion UI/API
- Look for: `ajohnson-ws` or `alice.johnson` or MAC `dc:a6:32:4f:0b:7c`
- Verify: Identity visible, traffic flows present, correlation complete

**If this works:** ✅ You're ready to test Phase 0!  
**If this fails:** 🔧 Debug before building more infrastructure

---

#### 3. Test All 6 Pi Runners (1-2 hours) - PARALLEL TEST

**Once one Pi works, test all 6 in parallel:**

```bash
# From your admin machine, test each Pi with different identity
ssh pi@<pi-runner-1-mgmt-ip> "sudo python3 /home/pi/clarion/lab/identity_switcher.py --user alice.johnson --interface eth0" &
ssh pi@<pi-runner-2-mgmt-ip> "sudo python3 /home/pi/clarion/lab/identity_switcher.py --user jane.robinson --interface eth0" &
ssh pi@<pi-runner-3-mgmt-ip> "sudo python3 /home/pi/clarion/lab/identity_switcher.py --user henry.brown --interface eth0" &
ssh pi@<pi-runner-4-mgmt-ip> "sudo python3 /home/pi/clarion/lab/identity_switcher.py --user victor.wilson --interface eth0" &
ssh pi@<pi-runner-5-mgmt-ip> "sudo python3 /home/pi/clarion/lab/identity_switcher.py --user badge-reader-01 --interface wlan0" &
ssh pi@<pi-runner-6-mgmt-ip> "sudo python3 /home/pi/clarion/lab/identity_switcher.py --user camera-01 --interface wlan0" &
wait

echo "All identity switches complete - check ISE and Clarion for 6 new identities"
```

**Verify in Clarion:**
- Should see 6 identities: alice, jane, henry, victor, badge-reader, camera
- Each should have complete correlation (MAC, IP, hostname, traffic)

**If all 6 work:** 🎉 You can run Phase 0 validation NOW (even without orchestrator)!

---

### 📅 THIS WEEK: Add Automation (Build 5 Infrastructure Pis)

Now that you know the core lab works, add the infrastructure:

#### 4. Pi-Rebuild-3: Orchestrator (1 hour) - CONVENIENCE

**Why:** Automates identity switching across all 6 runners

**Deploy:**
```bash
ssh pi@<pi-rebuild-3-ip>
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_orchestrator.sh | bash

# Get management IPs from all 6 runners
# Runners 1-4: ssh pi@runner "ip addr show wlan0 | grep inet"
# Runners 5-6: ssh pi@runner "ip addr show eth0 | grep inet"

# Edit orchestrator_config.json with actual management IPs
nano /home/pi/clarion/lab/orchestrator_config.json

# Edit SSH config
nano ~/.ssh/config

# Distribute SSH keys
cd /home/pi/clarion_lab
nano distribute_ssh_keys.sh  # Update IPs
./distribute_ssh_keys.sh

# Test orchestrator
python3 /home/pi/clarion/lab/lab_orchestrator.py --schedule daily --duration 1

# Check ground truth log
cat /home/pi/clarion_lab/ground_truth/ground_truth_log.csv
```

**Priority:** Medium - Nice to have, but you can manually run identity switches without it

---

#### 5. Pi-Rebuild-1: IoT Backend (Skip – use 192.168.31.2)

**Recommended:** Deploy IoT backend on **192.168.31.2** (iotdev.netlab.net) as user **steve** – see Step 1 above.

**Only use Pi-Rebuild-1** if you cannot use 192.168.31.2:

```bash
ssh pi@<pi-rebuild-1-ip>
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_iot_backend.sh | bash
```

**Priority:** Low – use iotdev server first

---

#### 6. Pi-Rebuild-2: Extra Endpoint (Optional)

**Why:** Adds more traffic volume, simulates additional devices

**Deploy:**
```bash
ssh pi@<pi-rebuild-2-ip>
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_pi_runner.sh | bash

# Configure like a regular runner
nano /home/pi/clarion_lab/runner_config.json
```

**Priority:** Low - Only if you want more traffic volume for testing

---

#### 7. Pi-Rebuild-4: Validator (Future)

**Why:** Automated scoring of Clarion analytics (grouping, policy generation)

**Status:** ⚠️ Not needed until Clarion analytics are ready

**Deploy when ready:**
```bash
ssh pi@<pi-rebuild-4-ip>
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_validator.sh | bash
```

**Priority:** Very Low - Skip for Phase 0, build when analytics are complete

---

#### 8. Pi-Rebuild-5: Fault Injection (Future)

**Why:** Tests Clarion's anomaly detection

**Status:** ⚠️ Not needed until Clarion analytics are ready

**Deploy:**
```bash
ssh pi@<pi-rebuild-5-ip>
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_pi_runner.sh | bash

# Load violation identities for testing
```

**Priority:** Very Low - Skip for Phase 0, build when anomaly detection is ready

---

## 🎯 Recommended Build Order for You

### TODAY (2-3 hours):
1. ✅ Deploy IoT backend (30 min)
2. ✅ Test one Pi Runner (30 min)
3. ✅ Test all 6 Pi Runners (1-2 hours)
4. 🎉 **Start Phase 0 validation** - You can test now!

### THIS WEEK (2-4 hours):
5. ⚪ Build Orchestrator (1 hour) - Makes life easier
6. ⚪ Build Extra Endpoint if needed (1 hour)

### LATER (When analytics ready):
7. ⚪ Build Validator
8. ⚪ Build Fault Injection

---

## 📊 What You Can Test NOW

**Even without the 5 infrastructure Pis, you can:**

### ✅ Run Manual Identity Rotation
```bash
# On each Pi, manually switch identities every 10 minutes
ssh pi@runner-1 "sudo python3 identity_switcher.py --user alice.johnson --interface eth0"
# Wait 10 minutes
ssh pi@runner-1 "sudo python3 identity_switcher.py --user frank.thompson --interface eth0"
# etc.
```

### ✅ Generate Traffic
```bash
# On each Pi, run traffic generation
ssh pi@runner-1 "sudo python3 auto_lab_runner.py --interface eth0 --session-duration 600"
```

### ✅ Validate Clarion Data (Manual)
- Check Clarion UI/API for each identity
- Verify correlation: MAC ↔ Username ↔ IP ↔ Hostname
- Check traffic flows: Are they attributed correctly?
- Look for gaps: Any orphaned MACs? Missing flows?

### ✅ Log Ground Truth (Manual)
- Keep a spreadsheet:
  ```
  Time       | Pi      | Identity      | MAC           | Expected Destination
  10:00 AM   | Runner-1| alice.johnson | dc:a6:32:... | finance.netlab.net
  10:10 AM   | Runner-1| frank.thompson| e4:5f:01:... | finance.netlab.net
  ```

**Orchestrator automates all of the above, but you can test Phase 0 without it!**

---

## 🚀 Quick Start Commands

### Deploy IoT Backend NOW:
```bash
# On 192.168.31.2 or Pi-Rebuild-1
curl -sSL https://raw.githubusercontent.com/dentroio/clarion/main/lab/setup_iot_backend.sh | bash
```

### Test First Pi Runner NOW:
```bash
# On Pi-Runner-1
sudo python3 /home/pi/clarion/lab/identity_switcher.py --user alice.johnson --interface eth0
sudo python3 /home/pi/clarion/lab/auto_lab_runner.py --interface eth0 --session-duration 600
```

### Check Clarion Data NOW:
```
Open Clarion UI → Search for "ajohnson-ws" or "alice.johnson"
Verify: Identity visible, flows present, correlation complete
```

---

## 📋 Simplified Checklist

### Phase 0 Testing (Can Start Today):

**Prerequisites:**
- [x] ISE ready
- [x] Clarion ready
- [x] 6 Pi Runners ready
- [ ] IoT backend deployed (30 min - DO THIS FIRST)

**Testing:**
- [ ] Test 1 Pi Runner → identity switch + traffic (30 min)
- [ ] Test all 6 Pi Runners in parallel (1 hour)
- [ ] Check Clarion data for all 6 identities (30 min)
- [ ] Document any data quality issues
- [ ] Fix issues and re-test

**Success Criteria:**
- ✅ All 6 Pis can switch identities
- ✅ ISE authenticates successfully
- ✅ Clarion shows all 6 identities with complete data
- ✅ Traffic flows attributed correctly
- ✅ Hostnames formatted correctly (ajohnson-ws, etc.)

### Infrastructure (This Week):

**Nice to Have:**
- [ ] Deploy Orchestrator (1 hour)
- [ ] Test orchestrated run (30 min)
- [ ] Verify ground truth logging works

**Optional:**
- [ ] Deploy Extra Endpoint (if needed)
- [ ] Deploy Validator (when analytics ready)
- [ ] Deploy Fault Injection (when analytics ready)

---

## 💡 Pro Tips for Your Situation

### You're 80% Done!
- ISE + Clarion + 6 Pis = the hard parts
- Infrastructure Pis = convenience and automation
- You can test Phase 0 TODAY with what you have

### Start Testing, Add Automation Later
- Deploy IoT backend (30 min)
- Test with 1-2 Pis manually
- Prove Clarion data pipeline works
- THEN build orchestrator for automation

### Don't Build What You Don't Need Yet
- Skip Validator (analytics not ready)
- Skip Fault Injection (anomaly detection not ready)
- Focus on Phase 0 data validation

### Manual Testing is Fine for Phase 0
- Orchestrator is nice but not required
- You can manually switch identities
- You can manually check Clarion data
- You can manually log ground truth
- Automation comes later for longer tests

---

## 🎯 Your Action Plan

### Right Now (Next 30 Minutes):
```bash
# 1. Deploy IoT backend on iotdev server (user: steve)
ssh steve@192.168.31.2
cd ~ && git clone https://github.com/dentroio/clarion.git 2>/dev/null || (cd clarion && git pull)
cd clarion/lab && ./setup_iot_backend.sh

# 2. Test endpoints (from your machine or server)
for port in {9001..9010}; do curl http://192.168.31.2:$port/ && echo "✓ $port"; done
```

### Today (Next 2 Hours):
```bash
# 3. Test one Pi Runner
ssh pi@<pi-runner-1-mgmt-ip>
sudo python3 /home/pi/clarion/lab/identity_switcher.py --user alice.johnson --interface eth0
sudo python3 /home/pi/clarion/lab/auto_lab_runner.py --interface eth0 --session-duration 600

# 4. Start Windows Host Agent
# On Windows hosts, open PowerShell and run:
.\windows_runner_agent.ps1 -OrchestratorUrl "http://192.168.20.95:5000" -RunnerId "win-host-1"
# Default windows_mode is traffic (HTTP to connectivity URLs). Use dashboard
# "Discovery Only" or set windows_mode to discovery for telemetry without traffic.

# 5. Check Clarion data
# Open UI, search for ajohnson-ws, verify data complete
```

### This Week:
```bash
# 5. Test all 6 Pis (if first one worked)
# 6. Build orchestrator (for automation)
# 7. Run longer tests (4-8 hours)
# 8. Validate data quality in Clarion
```

---

## ✅ Bottom Line

**You're ready to start testing Phase 0 TODAY!**

**Must have (do now):**
- ✅ Deploy IoT backend (30 min)

**Nice to have (do this week):**
- ⚪ Build orchestrator (1 hour)

**Don't need yet:**
- ❌ Validator (wait for analytics)
- ❌ Fault Injection (wait for analytics)
- ❌ Extra Endpoint (optional volume)

**Start with IoT backend, test one Pi, then scale up testing. You'll have Phase 0 data validation running today!** 🚀

---

**Last Updated:** February 12, 2026  
**Version:** 1.0  
**Your Status:** Ready to test, just need IoT backend!
