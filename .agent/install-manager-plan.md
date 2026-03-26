# Install Manager Implementation Plan

## Goal

Create a `.agent/install-manager/` operations area for setup, status checks, diagnostics, and repair of a cloned doc-hub installation, while also finishing the remaining non-historical unified CLI documentation cleanup.

## Architecture

The install-manager area will be organized as an operational cockpit for agents and humans. Documentation will be split by responsibility (install, database, diagnostics, repair), and lightweight shell scripts will provide read-only health checks for environment, database, and MCP status.

## Files to Create

```text
.agent/install-manager/
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

## Files to Update

- `README.md`
- `AGENTS.md`
- `ARCHITECTURE.md`
- `docs/fix-incremental-sync.md`
- `docs/sync-behavior.feature`
- `docs/user/mcp-server.md`

## Execution Outline

1. Create the install-manager folder and navigation docs
2. Add install/database/diagnostic/repair runbooks with exact commands
3. Add the three helper scripts and make them executable
4. Finish the remaining non-historical unified CLI documentation cleanup
5. Verify scripts and focused docs references
6. Run targeted tests if any code-facing docs or behavior assumptions changed
7. Commit the completed work
