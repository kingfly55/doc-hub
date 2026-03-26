# Install Manager

This area is the operational home base for managing a cloned doc-hub installation.

Use it when you need to:

- set up doc-hub from a clone
- configure environment variables and local services
- initialize or inspect the database
- check installation health
- diagnose problems reported by a user
- follow safe repair playbooks

## Navigation

- `AGENTS.md` — instructions for agents working in this area
- `install/` — clone setup, environment setup, and service setup
- `database/` — DB initialization, DB status, and backup/restore notes
- `diagnostics/` — health checks, common failures, and exact commands
- `repair/` — recovery playbooks and reset/reindex guidance
- `scripts/` — lightweight helper scripts for env, DB, and MCP checks

## Operating Model

1. Start with read-only checks first
2. Gather evidence before proposing fixes
3. Prefer the helper scripts for quick triage
4. Use the repair runbooks only after confirming the likely cause

## Quick Start

```bash
# Environment summary
./.agent/install-manager/scripts/check-env.sh

# Database connectivity and schema summary
./.agent/install-manager/scripts/check-db.sh

# MCP command / endpoint guidance
./.agent/install-manager/scripts/check-mcp.sh
```
