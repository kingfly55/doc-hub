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
