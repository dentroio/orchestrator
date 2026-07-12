# Runner Architecture: Client/Server, No SSH

## 1. User Identities Are the Master List

**The orchestrator is the single source of truth.** Identities (User Identities and IoT Devices) and runner configuration are stored in the server’s **SQLite DB** (`lab/clarion_lab.db`), not in JSON files. They are managed via the dashboard API.

- **Orchestrated runs:** For each session, the orchestrator picks an identity from the DB (by persona set), resolves access URLs from Connectivity, and **sends that identity to the runner** via the assignment API. The runner agent receives one assignment per session (identity, session_duration, access_urls, interface). The runner does **not** need a local identities file; it receives everything from the API.
- **Standalone runs:** If you run `auto_lab_runner.py` on a Pi without the orchestrator (no assignment), it can still use a local identities file for ad‑hoc testing. For normal lab use, the server is the only source of truth.

---

## 2. Client/Server Model (Current)

- **No SSH from orchestrator to runners.** The orchestrator never SSHs to Pis. Runners are **agents** that poll the server for work.
- **Config in DB:** Runners, identities, services, and connectivity policies are stored in SQLite. On first run, if `orchestrator_config.json` or `identities1.json` exist in `lab/`, they are imported once into the DB; after that, only the DB is used.
- **Flow:**
  1. **Orchestrator** (server): In its loop, when it’s time to assign an identity to runner R, it picks from the **master identity list** (DB), resolves access URLs, and sets **pending_assignment** for R. It does **not** SSH.
  2. **Runner agent** (systemd service on each Pi): Polls `GET /api/runner/assignment/<runner_id>`. If there is an assignment, it runs one session (identity_switcher + traffic_gen), reports telemetry, then calls `POST /api/runner/assignment/<runner_id>/ack`.
  3. **Orchestrator** on ack: Clears `pending_assignment` for that runner and assigns the next identity on the next loop iteration.

---

## 3. Implementation

- **Orchestrator:** All runners are agent-based. The loop only sets `pending_assignment`; there is no SSH or script mode. APIs: **GET /api/runner/assignment/<runner_id>**, **POST /api/runner/assignment/<runner_id>/ack**.
- **Runner:** **runner_agent.py** polls for assignments, runs one session via `auto_lab_runner.py`, then acks. Deploy with `deploy_runner.sh`. **clarion-runner.service** (systemd): copy to `/etc/systemd/system/`, set `ORCHESTRATOR_URL` and `RUNNER_ID`, then enable/start. See DEPLOYMENT_GUIDE and DEPLOY_THIS.md.

---

## 4. Summary

| Aspect | Current (client/server) |
|--------|--------------------------|
| Config & identities | SQLite DB on server; managed via dashboard |
| Session start | Agent polls API; no SSH |
| Runner process | Long-lived agent: poll → run session → ack |
| Runner identification | By name (`RUNNER_ID` in agent env); host/user optional for display only |

---

## 5. Windows runners (traffic only, no identity rotation)

Windows lab PCs do **not** use `GET /api/runner/assignment/<id>` or 802.1x identity switching. They run **`lab/windows_runner_agent.ps1`** (Scheduled Task or manual), which polls **`POST /api/windows-hosts/<runner_id>/plan`** and reports **`POST /api/runner/telemetry`**.

- **Runner config (DB / dashboard):** Set **`runner_type`** to **`windows`**, **`name`** to the same value as **`-RunnerId`** on the script, **`persona_set`** (and optional **`fallback_persona`**) so the orchestrator can resolve **`access_urls`** from connectivity policies.
- **`windows_mode`:** **`traffic`** (default when omitted for `runner_type: windows`) generates HTTP(S) to those URLs. **`discovery`** is telemetry only (no outbound lab traffic).
- **Orchestrator loop:** Pi-style **`pending_assignment`** is **not** applied to Windows runners; only the plan API drives their behavior.

Example PowerShell on the Windows host (adjust URL and id):

```powershell
powershell -ExecutionPolicy Bypass -File C:\path\to\windows_runner_agent.ps1 `
  -OrchestratorUrl "http://192.168.20.95:5000" -RunnerId "win-runner-1"
```
