# Installation State

Use this file to track the current installed shape of a doc-hub deployment.

## Record when

- initial install is completed
- install path changes
- virtualenv / uv workflow changes
- DB target changes
- MCP service wiring changes
- major CLI deployment assumptions change

## Template

```md
### YYYY-MM-DD
- Install root: /absolute/path
- Invocation model: uv sync + source .venv/bin/activate
- Canonical CLI: doc-hub
- Database target: local docker / cloud / other
- DB connection mode: PG* vars or DOC_HUB_DATABASE_URL
- MCP mode: stdio / systemd SSE / streamable-http / not configured
- Notes: any important non-default assumptions
```

## Current known baseline

### 2026-03-26
- Install root: /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub
- Invocation model: local clone with `uv sync` and unified `doc-hub` CLI
- Canonical CLI: `doc-hub`
- Database target used for verification: local VectorChord PostgreSQL on `localhost:5433`
- DB connection mode used for verification: `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`
- MCP mode observed on this machine: systemd-managed SSE service exists outside this repo and appears to point at an older external install path
- Notes: this file should be updated after any fresh installation or deployment repair
