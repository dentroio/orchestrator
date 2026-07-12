# What You Need to Deploy This

**Client/server architecture:** No SSH from orchestrator to runners. Config and identities live in the server’s SQLite DB. Runners are agents that poll the orchestrator for assignments. Run from **repo root** or **lab/**.

---

## Prerequisites

- **Orchestrator host:** Python 3, network reachable by runners (agents poll the API).
- **Runner hosts (Pis):** One-time **setup** per box: `setup_pi_runner.sh` (packages, netplan/NM). Then install and run the **runner agent** (systemd service). No SSH from orchestrator to runners.

---

## 1. Deploy Orchestrator (server)

**Target:** One host (e.g. Pi-Rebuild-3).

```bash
# From repo root:
./lab/deploy_orchestrator.sh user@ORCHESTRATOR_IP

# Or from lab/:
./deploy_orchestrator.sh user@ORCHESTRATOR_IP
```

**What it does:** Rsyncs `lab/` to `~/clarion/lab/` (code only; no config files copied), then runs `setup_orchestrator.sh` on the remote.

**Config:** On first run, the server creates **`clarion_lab.db`** (SQLite) in `lab/`. All configuration (runners, identities, services, connectivity) is stored there and managed via the dashboard API. If `orchestrator_config.json` or `identities1.json` exist in `lab/`, they are **imported once** into the DB and are **not used afterward**. The orchestrator always reads from the database, not from JSON files.

**After deploy:** Open the dashboard at `http://ORCHESTRATOR_IP:5000`. Add runners and identities in **Configuration** and **User Identities / IoT Devices**. Start/stop orchestration from the dashboard. To change a runner’s lab/management interface (e.g. pi-runner-6), edit that runner in **Configuration** in the dashboard, or run `python3 lab/set_runner_interface.py --runner pi-runner-6 --interface eth0 --management-interface wlan0` on the orchestrator host (see script help).

---

## 2. Deploy Runners (agents)

Runners are **always** agents: they poll the orchestrator for assignments. No script mode, no SSH from orchestrator.

1. **One-time per Pi:** Run `setup_pi_runner.sh` on the Pi (packages, netplan/NM, WiFi profile if needed). For Pis that use **WiFi as the lab interface**, run `./setup_pi_runner.sh --lab-interface=wlan0` (default is `eth0`). You can combine options, e.g. `./setup_pi_runner.sh --skip-git --lab-interface=wlan0`.
2. **Push code** (from your machine or CI):
   ```bash
   ./lab/deploy_runner.sh user@RUNNER_IP
   ```
   Pushes `lab/` to `~/clarion/lab/` on the Pi (e.g. `runner_agent.py`, `auto_lab_runner.py`, `identity_switcher.py`, `traffic_gen.py`). No config files are pushed; the agent gets identity and session from the API.
3. **On each Pi:** Install and start the agent service (SSH to the Pi once, or use your automation):
   ```bash
   sudo cp ~/clarion/lab/clarion-runner.service /etc/systemd/system/
   # Set RUNNER_ID to match this Pi (must match the runner name in the dashboard)
   sudo RUNNER_ID=pi-runner-4 ORCHESTRATOR_URL=http://192.168.20.95:5000 ./configure_clarion_runner.sh
   sudo systemctl daemon-reload
   sudo systemctl enable clarion-runner
   sudo systemctl start clarion-runner
   ```
   **Important:** `RUNNER_ID` is stored in `/etc/systemd/system/clarion-runner.service.d/override.conf` on that Pi only. If you see the wrong runner ID in `systemctl status clarion-runner` (e.g. pi-runner-2 on the box that should be runner-4), fix the override on that Pi (re-run `configure_clarion_runner.sh` with the correct `RUNNER_ID` or edit the override file), then `sudo systemctl daemon-reload && sudo systemctl restart clarion-runner`.
4. **Dashboard:** In **Configuration**, add a runner with **name** = `RUNNER_ID` (e.g. `pi-runner-1`), interface, persona set, session duration. No SSH fields required; runners are identified by name.

---

## 3. Quick Reference

| What | Command / action |
|------|------------------|
| Deploy orchestrator | `./lab/deploy_orchestrator.sh user@ORCHESTRATOR_IP` |
| Deploy runner (code) | `./lab/deploy_runner.sh user@RUNNER_IP` |
| First-time runner setup | Run `setup_pi_runner.sh` on the Pi (see DEPLOYMENT_GUIDE) |
| Agent on runner | Install `clarion-runner.service`; set `ORCHESTRATOR_URL` and `RUNNER_ID` |
| Config & identities | Stored in `lab/clarion_lab.db`; managed via dashboard (no JSON files) |
| Preflight audit / fix | Dashboard **Audit** / **Fix Issues**; per-runner **Fix** in audit modal. See `docs/RUNNER_PREFLIGHT_AUDIT.md` |

**No SSH from orchestrator for sessions.** Runners poll `GET /api/runner/assignment/<runner_id>`, run the session, then `POST .../ack`. The server is the single source of truth for identities and runner list. **Audit/remediate** uses SSH to each Pi’s management `host` only for preflight checks and safe lab teardown.

---

## 4. Troubleshooting: Runner not switching users or generating traffic

If a runner (e.g. runner-1 at 192.168.1.187) stops switching identities or generating traffic after a code deploy:

**On the Pi (e.g. SSH to 192.168.1.187):**

1. **Restart the service** so it loads new code and clears any bad state:
   ```bash
   sudo systemctl restart clarion-runner
   ```

2. **Confirm service and override:**
   ```bash
   sudo systemctl status clarion-runner
   cat /etc/systemd/system/clarion-runner.service.d/override.conf
   ```
   Check: `RUNNER_ID=pi-runner-1`, `ORCHESTRATOR_URL` correct, `WorkingDirectory` points to where you deployed (e.g. `/home/admin/clarion/lab`).

3. **Watch live logs** (agent + one-shot sessions go to journal):
   ```bash
   sudo journalctl -u clarion-runner -f
   ```
   Look for: `Runner agent started`, `Running one-shot session for <user>`, `Session failed`, `Session timed out`, or Python tracebacks. If you never see "Running one-shot session", the runner is not getting assignments.

4. **Confirm code is where the service runs:**
   ```bash
   ls -la $(cat /etc/systemd/system/clarion-runner.service.d/override.conf | grep WorkingDirectory | cut -d= -f2)/runner_agent.py
   ls -la $(cat /etc/systemd/system/clarion-runner.service.d/override.conf | grep WorkingDirectory | cut -d= -f2)/auto_lab_runner.py
   ```

**On the orchestrator / dashboard:**

5. **Orchestration must be running:** Dashboard shows "Status: RUNNING" and **Start** is disabled.

6. **Runner must be started:** In Runner Status, pi-runner-1 should not show "stopped". If it does, click **Start** for that runner.

7. **Runner must be in Configuration** with the same name as `RUNNER_ID` (e.g. `pi-runner-1`), with interface, persona set, and session duration set.

8. **Identities:** User Identities (or IoT Devices) must have identities with credentials; the runner’s persona set must match at least one identity.

**Connectivity from Pi to orchestrator:**

9. From the Pi: `curl -s -o /dev/null -w "%{http_code}" http://ORCHESTRATOR_IP:5000/api/status` should return `200`. If the Pi cannot reach the orchestrator URL, it will never get assignments.

For full procedures and troubleshooting, see **DEPLOYMENT_GUIDE.md** and **RUNNER_ARCHITECTURE.md**.
