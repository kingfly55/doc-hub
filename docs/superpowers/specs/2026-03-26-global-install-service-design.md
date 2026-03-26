# Global Install and MCP Service Design

## Goal

Make `doc-hub` usable as a canonical command from anywhere on the machine, retire stale pre-unification command wrappers, standardize the user-level MCP systemd service on that global command, and provide one durable home-directory env file for database and Gemini credentials.

## Current problems

- The current shell in this repo has no DB or Gemini env loaded by default.
- The active user-level `doc-hub-mcp.service` points at an older external checkout and an older env source.
- `~/.local/bin` still exposes stale pre-unification commands (`doc-hub-search`, `doc-hub-pipeline`, `doc-hub-eval`, `doc-hub-sync-all`, `doc-hub-mcp`).
- The repository docs still assume a repo-local `.env` or ad-hoc shell exports for many flows.

## Approved design

### 1. Global command model

Use a user-level tool install so `doc-hub` is available on PATH from anywhere:

- install via `uv tool install --force <clone-or-git-url>`
- canonical executable remains `doc-hub`
- interactive use and the MCP service should both invoke `doc-hub`, not `uv run ...`

### 2. Global env file

Add first-class support for a durable user env file at:

- `~/.local/share/doc-hub/env`

Loading order should be:

1. existing process environment
2. local working-directory / repo `.env`
3. global `~/.local/share/doc-hub/env`

This keeps explicit shell exports highest priority while allowing repo-local overrides when working inside a clone, and still gives `doc-hub` a stable fallback when run from anywhere else.

### 3. Systemd service model

Update the user-level MCP service to:

- invoke `doc-hub serve mcp --transport sse --port 8340`
- stop referencing the old external checkout
- load credentials from the durable env file instead of a repo `.env`

The existing local service name may be preserved if that minimizes disruption.

### 4. Legacy command cleanup

The supported CLI surface is the unified `doc-hub` command. Old globally installed wrappers should be removed from PATH during the local repair flow.

### 5. Repository updates

The repository should document this install strategy in:

- `README.md`
- `docs/user/configuration.md`
- `docs/user/mcp-server.md`
- `.agent/install-manager/` operational docs and memory

## Code changes required

- Add a shared helper for loading the global env file in the unified CLI bootstrap path.
- Add tests proving the bootstrap order and global env path behavior.
- Keep existing repo-local `.env` behavior intact.

## Verification requirements

- focused tests for the CLI bootstrap behavior pass
- the updated docs remain accurate to the actual install path and service pattern
- `uv tool` reinstall leaves `doc-hub` on PATH and removes the stale wrappers
- the repaired `doc-hub-mcp.service` starts and points at the canonical command
- install-manager diagnostics reflect the new operational reality
