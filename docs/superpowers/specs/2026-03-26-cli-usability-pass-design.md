# doc-hub CLI Usability Pass Design

**Date:** 2026-03-26

## Goal

Improve the day-to-day usability of the unified `doc-hub` CLI by adding a built-in manpage display command, making search explicitly corpus-scoped while still allowing multi-corpus searches, and making browse/read navigation faster with short document IDs.

## Scope

This design covers four linked changes:

1. Add a built-in command that prints the concise bundled manpage/help text directly in the terminal.
2. Change docs search so it always requires one or more corpora and supports repeated `--corpus` flags.
3. Add stable short document IDs to browse output and allow read to accept those IDs.
4. Update install-manager docs and operational memory to reflect the new supported workflow and the verified MANPATH fallback notes.

It does not add automatic shell mutation by the CLI, does not restore legacy wrapper executables, and does not change MCP configuration behavior.

## Requirements

### Built-in manpage command

- Add a first-class CLI command under the unified tree.
- Recommended surface: `doc-hub docs man`.
- Print the concise bundled manpage content directly in the terminal.
- Mention it in the normal help/docs surface as the repo-supported fallback when `man doc-hub` is not yet wired into the shell.
- Reuse the bundled manpage as the source of truth rather than creating a second divergent text blob.

### Search behavior

- `doc-hub docs search` must require at least one `--corpus`.
- Support multiple corpora via repeated `--corpus` flags.
- Follow standard CLI conventions: repeatable flags instead of ad hoc delimiters.
- Preserve existing filters and output modes where practical.
- Update search internals so results can be restricted to a set of corpora instead of a single optional corpus.

### Browse/read short IDs

- Browse output should show a short identifier next to each concrete document.
- IDs should be short, stable, and deterministic across commands.
- IDs should not be session-local.
- `doc-hub docs read` should accept either the existing document path or one of those IDs.
- Group nodes should not receive IDs.
- JSON browse output should include the short ID for concrete documents.

### Install-manager refresh

- Update install-manager docs to mention the built-in manpage command and the shell MANPATH note.
- Record the machine-local MANPATH change in operational memory because it was a verified local repair.
- Keep the install-manager guidance factual and operational.

## Existing state

- `src/doc_hub/cli/docs.py` registers `browse`, `read`, `list`, and `search`.
- `man/doc-hub.1` exists and is packaged into `share/man/man1/doc-hub.1`.
- `src/doc_hub/search.py` currently accepts a single optional `--corpus` and allows search across all corpora.
- `src/doc_hub/documents.py` already returns structured document tree rows keyed by `doc_path`.
- `src/doc_hub/browse.py` renders browse output from those rows and reads documents by `doc_path`.
- Install-manager docs currently mention `man doc-hub` only indirectly, and operational memory does not yet record the shell MANPATH fix.

## Design decisions

### 1. Add `doc-hub docs man`

The new command will live under the docs group as:

```bash
doc-hub docs man
```

Behavior:
- Read the bundled `doc-hub.1` source from the installed package or repository path.
- Render it in a plain terminal-friendly form.
- Keep it simple: correctness and availability matter more than perfect roff rendering.

This command becomes the repo-supported fallback when `man doc-hub` is not discoverable yet in the user shell.

### 2. Require one-or-more corpora for search

Search will move from optional single-corpus filtering to required multi-value filtering:

```bash
doc-hub docs search --corpus pydantic-ai --corpus fastapi "retry logic"
```

Behavior:
- At least one `--corpus` is required.
- Multiple `--corpus` flags are allowed.
- Internal SQL/filtering should use an array-based corpus predicate.
- Human-readable output should show the requested corpus set clearly.

### 3. Add deterministic short document IDs

Each concrete document will expose a short ID derived deterministically from stable document metadata, with six lowercase alphanumeric characters as the target format.

Preferred basis:
- corpus slug + canonical `doc_path`

Behavior:
- Browse text output prints the short ID beside each concrete document.
- Browse JSON output includes a `doc_id` field.
- Read accepts either a canonical path or a short ID and resolves IDs through the same deterministic mapping.

This avoids session-local state and keeps the ID stable as long as the corpus slug and doc path stay stable.

### 4. Refresh install-manager docs and memory

Update:
- `.agent/install-manager/install/clone-setup.md`
- `.agent/install-manager/install/environment.md`
- `.agent/install-manager/diagnostics/commands.md`
- `.agent/install-manager/memory/installation-state.md`
- `.agent/install-manager/memory/resolved-incidents.md`

Focus:
- mention `doc-hub docs man` as the built-in fallback
- mention that bare `man doc-hub` may require the uv tool man dir in `MANPATH`
- record that this machine was updated to include that man dir in shell startup files

## Testing and verification strategy

- TDD for `docs man` command routing/output.
- TDD for required repeatable `--corpus` behavior in search parser and search execution.
- TDD for browse short IDs and read-by-ID behavior.
- Focused test runs for each subsystem before broader regressions.
- Full suite verification after all tasks are complete.
- End-to-end CLI checks from the worktree install.

## Risks and constraints

- Roff is not a good direct terminal format, so `docs man` should use a pragmatic plain-text conversion instead of trying to emulate `man` perfectly.
- Six-character IDs can theoretically collide, so the implementation should detect collisions within a corpus and extend deterministically only if needed.
- Requiring `--corpus` changes current CLI behavior, so docs/tests must be updated in the same change.

## Success criteria

- `doc-hub docs man` exists and prints useful bundled help text.
- `doc-hub docs search` requires at least one corpus and accepts repeated `--corpus` flags.
- Browse output shows short IDs and read accepts those IDs.
- Install-manager docs/memory reflect the new supported workflow and the verified MANPATH notes.
- All changes are tested, verified, committed, pushed, merged, and installed locally.
