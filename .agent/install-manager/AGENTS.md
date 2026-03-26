# Agent Instructions for Install Manager

When working inside `.agent/install-manager/`, treat this area as the operational cockpit for a real doc-hub installation.

## Goals

Help operators and users:
- install doc-hub from a clone
- verify the environment and services
- diagnose failures systematically
- repair issues safely

## Preferred workflow

1. Read `README.md` for the map
2. Run read-only diagnostics first:
   - `scripts/check-env.sh`
   - `scripts/check-db.sh`
   - `scripts/check-mcp.sh`
3. Use `diagnostics/common-failures.md` to map symptoms to likely causes
4. Only then move to `repair/` playbooks

## Safety rules

- Do not drop data, reset schemas, or reindex everything without confirming the blast radius
- Prefer read-only checks and targeted repairs first
- When a command mutates installation state, explain what it changes
- Treat local Docker containers, systemd units, and cloud DBs as real operator assets

## Important repo-specific facts

- Canonical CLI is `doc-hub`
- Main operational commands:
  - `doc-hub pipeline run --corpus <slug>`
  - `doc-hub pipeline sync-all`
  - `doc-hub pipeline eval ...`
  - `doc-hub docs search ...`
  - `doc-hub docs browse ...`
  - `doc-hub docs read ...`
  - `doc-hub serve mcp ...`
- Database schema is initialized by `ensure_schema()` in `src/doc_hub/db.py`
- Vector dimension mismatches are load-bearing and must be diagnosed before repair

## Repair posture

Use the least-destructive fix that addresses the verified root cause.
