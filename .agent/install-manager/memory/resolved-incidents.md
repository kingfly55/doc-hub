# Resolved Incidents

Use this file to record durable operational incidents and their fixes.

## Record when

- a repair uncovered a non-obvious root cause
- a service was misconfigured in a way likely to recur
- a DB mismatch required a specific safe repair path
- an install issue would help future diagnosis

## Template

```md
### YYYY-MM-DD — Short title
- Symptom: what the operator saw
- Root cause: verified cause
- Fix: what was changed
- Verification: what command/output proved resolution
- Follow-up: optional preventive note
```

## Current known incidents

### 2026-03-26 — Legacy doc_corpora schema drift blocked DB integration tests
- Symptom: DB integration tests failed against the live local Postgres even after credentials were configured
- Root cause: existing `doc_corpora` schema was older than current expectations, missing `parser` and `embedder` columns and still carrying the legacy `doc_corpora_fetch_strategy_check` constraint
- Fix: `ensure_schema()` was updated to migrate the legacy `doc_corpora` schema in place
- Verification: DB integration suite passed and later full-suite verification succeeded against the configured local Postgres
- Follow-up: if an installation reuses an old database, inspect schema drift before assuming env-only failure

### 2026-03-26 — Local user systemd MCP service pointed at an older external install
- Symptom: MCP service status showed a running service, but logs referenced a different external repository path and missing DB env in that service context
- Root cause: the user-level `doc-hub-mcp.service` unit was managing an older install outside this repository, with incomplete DB environment configuration
- Fix: no automatic repair was applied here; the issue was surfaced by install-manager diagnostics for operator follow-up
- Verification: `./.agent/install-manager/scripts/check-mcp.sh` reproduced the service status and log evidence
- Follow-up: when diagnosing MCP issues, always verify the service unit command path and env source, not just whether the unit is active

### 2026-03-26 — Global doc-hub install and MCP service were normalized to the unified CLI
- Symptom: this machine had no global `doc-hub` command, the user service still pointed at an older checkout, and the stale pre-unification wrappers had drifted from the supported CLI surface
- Root cause: the machine install and service wiring predated the unified CLI migration and still depended on repo-local paths and ad-hoc env configuration
- Fix: installed `doc-hub` globally via `uv tool install --force`, created `/home/joenathan/.local/share/doc-hub/env`, removed the stale wrapper commands from PATH, and rewrote `doc-hub-mcp.service` to call `/home/joenathan/.local/bin/doc-hub serve mcp --transport sse --port 8340` with `EnvironmentFile=/home/joenathan/.local/share/doc-hub/env`
- Verification: `command -v doc-hub`, `doc-hub --help`, and `systemctl --user status doc-hub-mcp.service --no-pager` all succeeded; service logs showed the repaired process serving on `127.0.0.1:8340`
- Follow-up: future install guidance should treat `~/.local/share/doc-hub/env` as the durable machine-wide env source for global installs

### 2026-03-26 — Installed manpage was present but `man doc-hub` still failed
- Symptom: `man doc-hub` failed even after the manpage had been packaged and installed with the global uv tool
- Root cause: the uv tool man directory existed under `/home/joenathan/.local/share/uv/tools/doc-hub/share/man`, but the shell environment did not include that directory on `MANPATH`
- Fix: added `MANPATH` export blocks in shell startup files so the uv tool man directory is discoverable, and added `doc-hub docs man` as a built-in fallback that prints the bundled manpage without depending on shell manpath configuration
- Verification: after sourcing the repaired login-shell config, `man doc-hub` succeeded; `doc-hub docs man` also printed the bundled reference text directly
- Follow-up: if `man doc-hub` fails on another machine, first check whether the tool-installed man directory is on `MANPATH`; use `doc-hub docs man` as the immediate fallback
