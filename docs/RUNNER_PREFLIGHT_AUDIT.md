# Runner preflight audit and remediation

Pi lab runners use two network interfaces: a **lab** interface (802.1X / WiFi lab traffic) and a **management** interface (SSH and orchestrator API). Before starting orchestration, each Pi should be **idle**: lab link down, no active NetworkManager user session on the lab device, and management routing to the orchestrator intact.

Windows runners are checked via telemetry only (no SSH remediation).

## Dashboard workflow

1. Open the orchestrator dashboard.
2. Click **Audit** (toolbar) to run preflight on all configured runners.
3. Review the **Runner Preflight Audit** modal:
   - Summary banner: **Ready** / **Not ready** with pass/fail counts.
   - One card per runner with checks, actions, and errors.
4. Fix failures:
   - **Fix Issues** (footer): safe remediation on **all** Pi runners, then re-audit each.
   - **Fix** (per runner card): same remediation for **that runner only**; updates the card and summary without re-running other runners.
5. **Audit Only** re-runs checks without applying fixes.
6. **Start** warns if no audit was run or critical checks failed.

## What remediation does (Pi only)

Remediation runs on the Pi via SSH to the runner **management** `host` IP from Configuration. It never brings down the management interface.

| Step | Action |
|------|--------|
| 1 | Stop `clarion-runner` |
| 2 | Tear down **lab** only: `sudo nmcli connection down` for `clarion-lab-auth` / `clarion-lab-wifi`, disconnect lab device, `ip link set` down, flush lab addresses |
| 3 | Ensure management link is up; use NetworkManager (not `dhclient` on `wlan*`) for management DHCP |
| 4 | Create `/etc/netplan/99-clarion-lab-eth0.yaml` if lab is `eth0` and file is missing (**no** `netplan apply` — avoids disrupting wlan management) |
| 5 | Install static route to orchestrator via management interface |
| 6 | Re-run `configure_clarion_runner.sh` if present |
| 7 | Start `clarion-runner` |
| 8 | Tear down lab again (autoconnect may re-activate `clarion-lab-auth` on wlan lab) |
| 9 | Run `runner_preflight.py` and return JSON results |

The runner agent (`runner_agent.py`) uses the same `tear_down_lab_interface()` helper in its idle loop so lab sessions do not leave NM profiles active between polls.

### Why `sudo nmcli` is required

On many Pis, non-root `nmcli connection down` fails with *Not authorized to deactivate connections*. Remediation and the agent always use `sudo` for NM lab teardown. `ip link down` alone is not sufficient when `clarion-lab-auth` stays activated.

### wlan0 management runners

When management is `wlan0` (or any `wlan*`):

- Do not run `netplan apply` during remediate.
- Do not run `dhclient` on the wlan management device; use `nmcli device connect` / existing WiFi profiles.

## Scripts (on Pi after `deploy_runner.sh`)

| Script | Role |
|--------|------|
| `runner_preflight.py` | Read-only checks; `--json` for machine output |
| `runner_remediate.py` | Safe fix + post-fix preflight |
| `runner_audit.py` | Orchestrator-side SSH, scp scripts, aggregate results |

Deploy to Pis:

```bash
./lab/deploy_runner.sh admin@<management-ip>
```

Deploy orchestrator (dashboard + API):

```bash
./lab/orchestrator/app/deploy_orchestrator.sh --code-only admin@<orchestrator-ip>
ssh admin@<orchestrator-ip> 'sudo systemctl restart clarion-orchestrator'
```

## HTTP API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/audit/runners` | Audit all runners |
| `POST` | `/api/audit/runners/remediate` | Remediate all Pi runners |
| `POST` | `/api/audit/runners/<runner_name>` | Audit one runner |
| `POST` | `/api/audit/runners/<runner_name>/remediate` | Remediate one Pi runner |

Responses include `ready`, `ok`, `runners[]`, `summary`, and per-runner `checks[]`. Remediation responses set `remediated: true` and may include `actions[]`, `error`, `traceback`.

## Critical preflight checks (Pi)

| Check ID | Meaning |
|----------|---------|
| `interfaces_distinct` | Lab and management interfaces differ |
| `management_interface_up` | Management operstate up |
| `management_has_ipv4` | Management has an IPv4 address |
| `lab_interface_down` | Lab operstate down while idle |
| `lab_no_ipv4` | No address on lab interface |
| `lab_no_active_nm_user` | No active NM connection on lab (e.g. `clarion-lab-auth`) |
| `orchestrator_route_via_mgmt` | `ip route get <orchestrator>` uses management dev |
| `orchestrator_http_reachable` | HTTP GET `/api/status` succeeds |
| `default_route_not_lab_only` | Default route not pinned only to lab |
| `clarion_runner_active` | `clarion-runner` service active |
| `systemd_runner_id` | `RUNNER_ID` in systemd matches dashboard name |

Warnings (non-blocking): missing `clarion-lab-wifi` on wlan lab runners, missing netplan file when lab is not `eth0`.

## Configuration requirements

- **host** in Configuration must be the **management** IP (SSH target), not the lab subnet address.
- **interface** = lab device (`eth0` or `wlan0`).
- **management_interface** = management device (opposite of lab on dual-homed Pis).

Example: pi-runner-2 uses lab `wlan0`, management `eth0`; pi-runner-1 uses lab `eth0`, management `wlan0`.

For wlan lab runners missing `clarion-lab-wifi`:

```bash
sudo bash ~/clarion/lab/orchestrator/app/setup_pi_runner.sh --lab-interface=wlan0
```

## Troubleshooting: audit passes but no traffic

Preflight audit only confirms **idle** interface safety and management reachability. It does **not** start sessions by itself.

| Symptom | Likely cause | Fix |
|---------|----------------|-----|
| No data from one runner | Runner **stopped** in dashboard Runner Status | Click **Start** on that runner (or `POST /api/runners/<name>/start`) |
| No data from any runner | Orchestration not running | Click dashboard **Start** (global) |
| `last_contact` null, agent idle missing | Wrong `ORCHESTRATOR_URL` on Pi (e.g. `:8080` when service is on `:5000`) | Re-run remediate after orchestrator fix, or `sudo RUNNER_ID=... ORCHESTRATOR_URL=http://<host>:5000 configure_clarion_runner.sh` and restart `clarion-runner` |
| Sessions fail on wlan lab runner | Missing `clarion-lab-wifi` profile | `setup_pi_runner.sh --lab-interface=wlan0` |

Remediation configures `ORCHESTRATOR_URL` from the orchestrator DB/env (`http://192.168.20.95:5000` by default), not from the browser port you use to open the dashboard.

## Related docs

- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) — Pi setup and NetworkManager notes
- [DEPLOY_THIS.md](../app/DEPLOY_THIS.md) — Deploy commands
- [PEER_CONNECTIVITY_PLAN.md](PEER_CONNECTIVITY_PLAN.md) — Planned policy-test peer connectivity (separate feature)
