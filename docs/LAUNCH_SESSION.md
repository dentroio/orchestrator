# Launch Session

The **Launch…** button opens a menu to scope lab traffic before orchestration runs. Use it to control **how many** identities are active, **which types** (users vs IoT), **which personas**, and **which runners** participate.

## Grouped campaigns (recommended for Clarion verification)

Run **one persona group per launch**, then **Verify last launch** before moving on. This is usually better than one big mixed run when you want to know whether Clarion grouped Sales vs Finance correctly.

| Order | Preset | Purpose |
|-------|--------|---------|
| 1–4 | **Group: Sales / Finance / Engineering / IT** | Pi + matching Windows for that dept only; verify persona alignment in Clarion |
| 5 | **Population: All users (Pi rotation)** | Cycle all 802.1X users; build toward **50** endpoints in Clarion |
| 6 | **Population: IoT / MAB** | wlan Pis only; separate IoT clusters |
| Optional | **Anchors: Windows only** | 4 stable PCs — baseline only, not scale |

Toolbar shows **Clarion endpoints: N / target** while the modal is open. When **N ≥ target**, run bootstrap/clustering in Clarion, then filter ground truth by `launch_id`.

Set **`clarion_api_url`** in orchestrator config (default `http://192.168.30.2:5000/api`) if your Clarion API is elsewhere.

## Dashboard

1. Click **Launch…** in the toolbar.
2. Choose **Identity type**: Users only (802.1X), IoT only (MAB), or both.
3. Optionally check **Personas** (Sales, Finance, Badge Reader, …). If none are checked, all personas matching the identity type are allowed.
4. Set **Max concurrent Pi sessions** (`0` = unlimited). This caps how many identities are active at once across Pi runners.
5. Check **Runners** to include (Pi and Windows). Uncheck hosts you want idle.
6. **Include Windows runners** — Windows PCs do not rotate users; each host uses the **logged-in AD user** mapped in the identity DB (see below).
7. **Refresh preview** shows matching identity counts per runner and a `launch_id`.
8. Click **Launch session** to start orchestration with that profile.

The toolbar **Cycle once** checkbox is mirrored in the launch modal.

## Windows runners

| Pi runners | Windows runners |
|------------|-----------------|
| Rotate through identities from the DB (802.1X / MAB on lab interface) | **No rotation** — persona comes from whoever is logged into that PC |
| Respect launch profile filters and max concurrent | Included when checked; agent polls `/api/windows-hosts/<id>/plan` |
| Ground truth row per Pi session start | Windows traffic is driven by the plan API (persona from logged-in user) |

Configure each Windows runner in **Configuration** with `runner_type: windows`, `fallback_persona`, and `persona_set` for URL/policy resolution. The four lab groups (Sales, Finance, Engineering, IT) map to one user per Windows host.

## Correlation with Clarion

Each Pi session start appends a row to **`ground_truth_log.csv`** with:

| Column | Purpose |
|--------|---------|
| `launch_id` | UUID for this Launch session — filter in Clarion validation |
| `identity_kind` | `user` or `iot` |
| `auth` | `dot1x` or `mab` |
| `username` | AD username when present |
| `runner` / `runner_type` | Which host ran the session |
| `persona`, `os`, `device_mac`, `device_name` | Expected endpoint attributes |
| `expected_destinations` | URLs/services for the session |
| `session_duration_seconds`, `scheduled_end_timestamp` | Time window |

Compare `launch_id` and identity fields to what Clarion observed in flows and identity tables.

## API

### Preview

```http
POST /api/launch/preview
Content-Type: application/json

{
  "launch_profile": {
    "identity_kinds": ["users"],
    "personas": ["Finance"],
    "runner_names": ["pi-runner-2", "win-runner-2"],
    "max_concurrent": 4,
    "include_windows": true,
    "auto_start_runners": true
  }
}
```

### Start with profile

```http
POST /api/start
Content-Type: application/json

{
  "cycle_once": false,
  "launch_profile": { ... }
}
```

Response includes `launch_id` and `preview` summary.

### Status while running

`GET /api/status` includes `launch_id` and `launch_profile` when orchestration is active.

## Examples

| Goal | Settings |
|------|----------|
| Only AD users, no IoT | Identity type: **Users only** |
| Only MAB devices | Identity type: **IoT only** |
| Finance on two Pis | Personas: **Finance**; Runners: **pi-runner-2**, **pi-runner-3** |
| At most 3 dot1x sessions at once | Max concurrent: **3** |
| Pis only (no Windows) | Uncheck Windows runners or disable **Include Windows runners** |

## Related

- [RUNNER_PREFLIGHT_AUDIT.md](RUNNER_PREFLIGHT_AUDIT.md) — interface checks before launch
- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) — runner setup
