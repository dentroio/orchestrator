# Lab Project File Ownership (Migration Complete)

This map records canonical project locations plus legacy compatibility paths.

## Orchestrator

Canonical location:
- `lab/projects/orchestrator/app/`

Compatibility paths:
- `lab/lab_orchestrator.py`
- `lab/orchestrator_web.py`
- `lab/runner_agent.py`
- `lab/auto_lab_runner.py`
- `lab/validator_engine.py`
- `lab/db.py`
- `lab/set_runner_interface.py`
- `lab/deploy_orchestrator.sh`
- `lab/deploy_runner.sh`
- `lab/configure_clarion_runner.sh`
- `lab/clarion-runner.service`
- `lab/DEPLOY_THIS.md`
- `lab/RUNNER_ARCHITECTURE.md`

## Traffic Simulation

Canonical location:
- `lab/projects/traffic-simulation/app/`

Compatibility paths:
- `lab/traffic_gen.py`
- `lab/identity_switcher.py`
- `lab/http_persona_spoofer.py`
- `lab/dhcp_fingerprint_inject.py`
- `lab/iot_backend_mock.py`
- `lab/add_ot_devices.py`
- `lab/add_ot_devices_additional.py`

## CMDB

Canonical location:
- `lab/projects/cmdb/app/cmdb/`

Compatibility path:
- `lab/cmdb`

## Lab Gateway

Canonical location:
- `lab/projects/gateway/app/clarion-gateway/`

Compatibility path:
- `lab/clarion-gateway`

## MAB Registration

Canonical location:
- `lab/projects/mab-registration/app/`

Compatibility path:
- `lab/mab-registration`

## Shared Documentation and Planning

Keep at lab root for now:
- `README.md`
- `QUICK_START.md`
- `DEPLOYMENT_GUIDE.md`
- `HOW_IT_WORKS.md`
- `LAB_MASTER_PLAN.md`
- `UPDATES.md`

Reason:
- These are cross-project operator docs and should remain a top-level entry point.

## Next cleanup pass

1. Update docs and scripts to canonical project paths only.
2. Verify no remaining tooling relies on legacy `lab/` entry points.
3. Remove compatibility symlinks after one stable cycle.
