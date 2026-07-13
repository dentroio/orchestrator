# Lab Project Boundaries

This directory defines the target project layout for `lab/` so we can split and move components safely.

Current state:
- Canonical project sources now live under `lab/projects/*/app/`.
- Legacy `lab/` paths remain available as compatibility symlinks.
- Deploy scripts continue to function from repo root or `lab/`.

## Projects

- `orchestrator/` - orchestrator, runner agent, assignment flow, validation.
- `cmdb/` - standalone CMDB backend/frontend and integration docs.
- `gateway/` - lab-specific gateway config and cert automation.
- `mab-registration/` - MAB registration demo app and deployment.
- `traffic-simulation/` - persona simulation helpers and traffic generators.
- `PROJECT_FILE_OWNERSHIP.md` - current files mapped to project owners and move targets.

## Migration Strategy

1. Keep canonical sources under `lab/projects/*/app/`.
2. Maintain compatibility symlinks at legacy `lab/` paths.
3. Gradually update docs/scripts to canonical paths.
4. Remove compatibility symlinks after one stable release cycle.

## Immediate goal

Use these docs as the source of truth for what belongs in each project while keeping current scripts operational.

## Migration status

- `orchestrator` migrated to `lab/projects/orchestrator/app/`.
- `traffic-simulation` migrated to `lab/projects/traffic-simulation/app/`.
- `gateway` migrated to `lab/projects/gateway/app/clarion-gateway/`.
- `cmdb` migrated to `lab/projects/cmdb/app/cmdb/`.
- `mab-registration` migrated to `lab/projects/mab-registration/app/`.
