# Doc-Hub Search Skill Design

**Date:** 2026-03-27
**Location:** `~/.claude/skills/doc-hub-search/SKILL.md`
**Approach:** Single SKILL.md (Approach A)

## Purpose

A Claude Code skill that teaches Claude when and how to use the `doc-hub` CLI to search external documentation during coding sessions. When Claude encounters questions about external libraries, frameworks, or APIs that can't be answered from the repository's own files, it should search doc-hub's indexed corpora instead of relying on potentially stale training knowledge.

## Trigger Conditions

The skill fires when **all** of the following are true:

1. Claude is working in a code repository
2. A question arises about something **external to the repo** — libraries, frameworks, APIs, configuration patterns
3. The answer is not available by reading the repo's own files

**Trigger examples:**
- External library API usage (function signatures, config options, parameters)
- Framework patterns that may have changed since training cutoff
- Dependency configuration or integration details

**Non-triggers:**
- Questions about code in the current repo
- Pure logic/algorithm questions with no library dependency
- User explicitly directs to a different source

## Workflow

1. **Discover** — `doc-hub docs list` to see available corpora
2. **Match** — Identify which corpus/corpora maps to the question
3. **Search** — `doc-hub docs search --corpus <slug> "<query>"` with appropriate filters
4. **Drill down** (if needed) — `doc-hub docs browse` or `doc-hub docs read` for deeper context
5. **Apply** — Use retrieved information to answer or write code

If no relevant corpus exists, note this to the user and fall back to training knowledge.

## CLI Surface (Concise Reference)

### `doc-hub docs list`
Discover available corpora. Use `--json` for machine-readable output.

### `doc-hub docs search --corpus SLUG QUERY`
Hybrid vector + full-text search. Key flags:
- `--corpus SLUG` (required, repeatable) — which corpora to search
- `--limit N` (default 5) — max results
- `--category CATEGORY` (repeatable) — filter to: api, guide, example, eval, other
- `--exclude-category CATEGORY` (repeatable) — exclude categories
- `--min-similarity FLOAT` (default 0.55) — cosine similarity threshold
- `--source-url-prefix STR` — filter by source URL prefix
- `--section-path-prefix STR` — filter by section path prefix
- `--json` — JSON output

### `doc-hub docs browse CORPUS`
Browse document tree. Key flags:
- `--path PATH` — start at a specific path in the tree
- `--depth N` — limit tree depth
- `--json` — JSON output

### `doc-hub docs read CORPUS DOC_PATH_OR_ID`
Read a specific document or section. Key flags:
- `--section SECTION` — read a specific section
- `--json` — JSON output

### `doc-hub docs man`
Print the full manpage. Use as a self-help escape hatch when confused about CLI syntax.

## Escape Hatch

If Claude is unsure about any command syntax or available flags, it should run `doc-hub docs man` to get the authoritative CLI reference.

## Assumptions

- `doc-hub` is installed and the database is running. No availability checks needed.
- Corpora are discovered dynamically via `doc-hub docs list`, never hardcoded.

## Design Decisions

- **Single file:** The CLI surface is small enough to fit concisely in one SKILL.md.
- **No defensive checks:** Assume doc-hub is available. Handle errors if they happen.
- **Dynamic discovery:** Never assume which corpora exist. Always check.
- **Man page escape hatch:** Keeps the skill concise while providing a runtime fallback for edge cases.
