# Install Manager Design

## Goal

Create a dedicated `.agent/install-manager/` area that serves as the operational home base for cloned doc-hub installations. It should support both humans and agents performing setup, verification, diagnosis, and repair of a local or hosted doc-hub deployment.

## Intended Use

This area is for operational management of a real doc-hub installation, not end-user product usage.

Typical workflows:

- a developer clones the repo and asks an agent to set up doc-hub
- an operator wants to verify database or MCP health
- a doc-hub user reports a problem and an agent needs to diagnose the installation
- an operator needs a safe repair playbook for schema drift, env issues, indexing problems, or MCP service problems

## Scope

The install manager will include:

- agent instructions for working inside the install-manager area
- install and environment setup runbooks
- database initialization and status guidance
- health checks and diagnostics runbooks
- repair and recovery playbooks
- lightweight helper scripts for common checks

## Structure

```text
.agent/
  install-manager/
    README.md
    AGENTS.md
    install/
      clone-setup.md
      environment.md
      services.md
    database/
      initialization.md
      status.md
      backup-restore.md
    diagnostics/
      health-checks.md
      common-failures.md
      commands.md
    repair/
      recovery-playbooks.md
      reset-reindex.md
    scripts/
      check-env.sh
      check-db.sh
      check-mcp.sh
```

## Responsibilities

### `README.md`
- explain what the install manager is
- give the navigation map
- explain when to start here instead of the user docs

### `AGENTS.md`
- provide local agent operating instructions
- define the recommended troubleshooting flow
- define what to verify before suggesting a repair
- distinguish safe local checks from risky or destructive actions

### `install/`
- how to set up from a clone
- Python / uv setup
- how the unified CLI is installed and invoked
- environment variable setup
- MCP service setup options

### `database/`
- local Docker + VectorChord setup
- cloud DB notes and connection verification
- schema initialization behavior
- corpus/index state inspection
- backup/restore notes where appropriate

### `diagnostics/`
- exact check commands for environment, DB, MCP, corpus status, and indexing state
- symptom-to-cause mapping
- known failure patterns and what evidence to gather first

### `repair/`
- safe repair runbooks
- when to rebuild tree only
- when to reindex one corpus
- when to sync all corpora
- how to handle schema drift and vector-dimension mismatch safely

### `scripts/`
Small helper scripts only for common read-only status checks.

Proposed scripts:
- `check-env.sh` — print required env state and config summary
- `check-db.sh` — verify DB connectivity, extension availability, and key table presence
- `check-mcp.sh` — verify MCP service command or endpoint health

These scripts should be diagnostic-first and avoid destructive behavior.

## Design Principles

1. **Agent-first navigation**
   - an agent should be able to enter `.agent/install-manager/` and quickly find the right runbook
2. **Operational clarity**
   - setup, diagnostics, and repair should be separated rather than mixed together
3. **Low-risk defaults**
   - prefer read-only checks first
   - repairs should clearly call out risk levels
4. **Exact commands**
   - every runbook should include concrete commands, expected results, and common failure interpretations
5. **No duplicate product docs unless operationally necessary**
   - reference existing user docs where appropriate, but consolidate operational guidance here

## Unified CLI Documentation Cleanup

As part of this work, finish remaining stale unified CLI doc updates outside the install-manager area where they affect operations or agent guidance.

Known items to update:

- `AGENTS.md`
- `ARCHITECTURE.md`
- `docs/fix-incremental-sync.md`
- `docs/sync-behavior.feature`
- `docs/user/mcp-server.md` malformed Claude Code config example
- systemd service naming references in user docs / README if we choose to rename them for consistency

Historical exec-plan references can remain unchanged.

## Acceptance Criteria

This design is complete when:

1. `.agent/install-manager/` exists with the approved structure
2. `README.md` and `AGENTS.md` make the area usable by an agent without extra context
3. install, DB, diagnostics, and repair runbooks are present and concrete
4. helper scripts exist for env, DB, and MCP checks
5. remaining non-historical unified CLI doc gaps are fixed
6. the new scripts are executable and behave as documented
