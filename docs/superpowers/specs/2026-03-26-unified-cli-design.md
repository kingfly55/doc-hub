# Unified `doc-hub` CLI Design

## Goal

Replace the current multi-executable CLI surface with one canonical command:

```bash
doc-hub docs browse ...
doc-hub docs read ...
doc-hub docs search ...

doc-hub pipeline run ...
doc-hub pipeline sync-all ...
doc-hub pipeline eval ...

doc-hub serve mcp ...
```

The new CLI should become the single source of truth for all operational doc-hub usage.

## Non-goals

This design does not add new product surface for corpus lifecycle management or plugin/fetcher authoring. Those are noted as future considerations only.

## Current State

The repository currently exposes seven separate console scripts via `pyproject.toml`:

- `doc-hub-pipeline`
- `doc-hub-search`
- `doc-hub-mcp`
- `doc-hub-eval`
- `doc-hub-sync-all`
- `doc-hub-browse`
- `doc-hub-read`

Each script points at a separate module-level `main()` or `*_main()` entrypoint. This works, but it fragments the user and agent experience and spreads CLI concerns across multiple modules.

## Chosen Architecture

Create a first-class CLI package under `src/doc_hub/cli/` and make it the only canonical command surface.

### Package structure

```text
src/doc_hub/cli/
  __init__.py
  main.py
  shared.py
  docs.py
  pipeline.py
  serve.py
```

### Responsibilities

- `main.py`
  - builds the top-level `doc-hub` parser
  - registers the command groups
  - dispatches to the selected handler
- `docs.py`
  - owns `docs browse`, `docs read`, and `docs search`
- `pipeline.py`
  - owns `pipeline run`, `pipeline sync-all`, and `pipeline eval`
- `serve.py`
  - owns `serve mcp`
- `shared.py`
  - owns common bootstrap behavior such as `load_dotenv()`, logging setup, and any parser helper utilities shared across command groups

## Boundary Rules

The new CLI package owns command parsing and command grouping.

The existing non-CLI modules remain responsible for operational behavior:

- `src/doc_hub/search.py` retains search logic
- `src/doc_hub/pipeline.py` retains pipeline execution logic
- `src/doc_hub/browse.py` retains browse/read operational helpers or gets split so those helpers live in a non-entrypoint-friendly location
- `src/doc_hub/mcp_server.py` retains MCP server construction and runtime behavior
- `src/doc_hub/eval.py` retains evaluation behavior

The CLI package should call reusable functions in those modules. It should not duplicate business logic.

## Command Taxonomy

### `doc-hub docs`

Documentation-consumption commands:

- `doc-hub docs browse`
- `doc-hub docs read`
- `doc-hub docs search`

Rationale: all three commands are ways of navigating or querying documentation content.

### `doc-hub pipeline`

Index lifecycle and quality commands:

- `doc-hub pipeline run`
- `doc-hub pipeline sync-all`
- `doc-hub pipeline eval`

Rationale: these commands manage or validate indexed corpora rather than consume documentation directly.

### `doc-hub serve`

Integration-serving commands:

- `doc-hub serve mcp`

Rationale: starting the MCP server is a serving/integration concern, not a docs interaction or a pipeline operation.

## Migration Plan

This design assumes there are effectively no external users to preserve. The old standalone console scripts will be removed rather than kept as aliases.

### `pyproject.toml`

Replace the current seven scripts with one canonical entrypoint:

```toml
[project.scripts]
doc-hub = "doc_hub.cli.main:main"
```

The old entries should be removed.

## Implementation Strategy

### Step 1: Build the CLI package

Create the `doc_hub.cli` package and define the top-level parser tree with nested subparsers.

### Step 2: Extract reusable command handlers where needed

Refactor existing modules so the CLI package can invoke them cleanly without relying on old standalone `main()` shapes.

Examples:

- search should expose a reusable CLI-facing handler or parser-independent execution function
- pipeline should expose reusable entry functions for `run` and `sync-all`
- eval should expose a reusable handler callable from `pipeline eval`
- MCP server should expose a reusable handler callable from `serve mcp`
- browse/read helpers should be callable from `docs browse` and `docs read` without making `browse.py` the architectural owner of the CLI

### Step 3: Remove old standalone script ownership

Once all command paths are wired into `doc-hub`, the old script entrypoints in `pyproject.toml` should be removed. Module-level legacy `main()` functions may remain temporarily if they reduce refactor risk, but they should no longer define the public CLI architecture.

## UX Requirements

### Help output

The new CLI must provide a clean discoverable help tree:

- `doc-hub --help`
- `doc-hub docs --help`
- `doc-hub docs browse --help`
- `doc-hub pipeline --help`
- `doc-hub serve --help`

The help text should make the taxonomy obvious.

### Behavioral parity

The new commands must preserve the current behavior of the existing tools:

- same operational semantics
- same major flags
- same JSON shapes
- same large-document threshold behavior
- same pipeline stage behavior
- same MCP transport behavior

This is a CLI consolidation and architecture cleanup, not a product behavior redesign.

## Testing Strategy

### Required tests

1. Parser and dispatch tests for the new unified CLI
2. Regression coverage showing each new subcommand delegates to the correct existing operational behavior
3. Updated CLI docs and entrypoint checks
4. Full suite verification after migration

### Specific checks

- `doc-hub docs browse` behaves like the old browse command
- `doc-hub docs read` behaves like the old read command
- `doc-hub docs search` behaves like the old search command
- `doc-hub pipeline run` preserves current pipeline options and semantics
- `doc-hub pipeline sync-all` preserves current sync-all semantics
- `doc-hub pipeline eval` preserves current eval semantics
- `doc-hub serve mcp` preserves current MCP server semantics
- `pyproject.toml` exposes only the canonical `doc-hub` entrypoint

## Documentation Changes

Update user docs so the unified CLI becomes the sole documented command surface.

At minimum:

- `README.md`
- `docs/user/cli-reference.md`
- `docs/user/mcp-server.md`
- any getting-started examples that currently use the split commands

Documentation should stop presenting the old separate executables as the primary interface.

## Future Considerations

These are intentionally out of scope for this design, but should be reconsidered later:

1. Add a `corpora` command group for corpus lifecycle management
   - possible commands: `list`, `add`, `update`, `disable`
2. Revisit plugin/fetcher registration UX from the CLI
3. Consider whether a deeper application-service layer is warranted later if doc-hub grows beyond the current command surface

## Why This Architecture Was Chosen

Several alternatives were considered:

- a thin unified router over the existing command modules
- a full app-service layer plus CLI adapters
- a command-registry system with per-command modules

The selected design is the best current fit because it:

- creates a real first-class CLI architecture
- keeps the taxonomy clean and discoverable
- avoids preserving the old split-command seams as the long-term design
- avoids introducing a heavier application framework before it is clearly needed
- preserves existing operational logic instead of rewriting core behavior

## Acceptance Criteria

This design is complete when:

1. `doc-hub` is the only documented CLI entrypoint
2. the command hierarchy matches the approved taxonomy
3. old standalone script entrypoints are removed from `pyproject.toml`
4. all unified subcommands preserve current behavior
5. user-facing docs are updated to the new structure
6. the full test suite passes after the migration
