# doc-hub Plugin Architecture Transformation

## Problem Statement

doc-hub is a multi-corpus documentation search engine (fetch → parse → embed → index → search) currently structured as a monolithic package. Adding a new documentation source requires editing files inside doc-hub itself (enum, dispatch table, SQL CHECK constraint). The goal is to transform doc-hub into a **stable framework** that never changes when new fetchers, parsers, or embedders are added. These extensions live in external packages that plug into doc-hub via Python's standard entry point mechanism.

Simultaneously, the package must be extractable from its current UV monorepo into a **standalone pip-installable repository** with proper home-directory data storage and cloud-friendly database configuration.

## Current State

The package lives at `packages/doc-hub/` in a UV workspace monorepo. Key files:

- `src/doc_hub/models.py` — `FetchStrategy` StrEnum (4 hardcoded values), `Corpus` dataclass
- `src/doc_hub/fetchers.py` — `FETCHERS` dispatch dict mapping enum → async function. Only `llms_txt` is implemented; 3 stubs raise NotImplementedError.
- `src/doc_hub/parse.py` — Hardcoded markdown heading-split parser. Not pluggable.
- `src/doc_hub/embed.py` — Hardcoded Gemini `gemini-embedding-001` (768-dim). Not pluggable.
- `src/doc_hub/paths.py` — `_find_repo_root()` walks up looking for `[tool.uv.workspace]`. Crashes outside monorepo.
- `src/doc_hub/db.py` — asyncpg pool, DDL with `CHECK (fetch_strategy IN ('llms_txt', ...))` constraint, CRUD helpers.
- `src/doc_hub/search.py` — Hybrid vector + FTS search. No page browsing.
- `src/doc_hub/index.py` — PostgreSQL upsert with advisory locks.
- `src/doc_hub/pipeline.py` — Orchestration: fetch → parse → embed → index. Hardcoded stage dispatch.
- `src/doc_hub/mcp_server.py` — 4 MCP tools (search, list_corpora, add_corpus, refresh_corpus). No page browsing tools.
- `src/doc_hub/eval.py` — Retrieval quality evaluation.
- `pyproject.toml` — hatchling build, Python >=3.13, 5 console scripts, 14 dependencies.

Database: PostgreSQL with VectorChord extension. Three tables: `doc_corpora`, `doc_chunks` (vector(768)), `doc_index_meta`.

## Architecture Decisions

### Plugin System: Hybrid Entry Points + Local Files (Approach C)

**Primary mechanism:** Python `importlib.metadata.entry_points()` with three groups:
- `doc_hub.fetchers` — async functions that download docs and produce .md files
- `doc_hub.parsers` — functions that convert raw files into `Chunk` objects
- `doc_hub.embedders` — classes/objects that embed text into vectors

**Secondary mechanism:** Scan `{data_root}/plugins/{fetchers,parsers,embedders}/*.py` for decorated functions. Entry points take precedence on name collision.

**Plugin contracts:** Python `typing.Protocol` classes in `doc_hub.protocols`. Plugins don't inherit — they match the structural type.

**Built-in plugins:** The existing llms_txt fetcher, markdown parser, and Gemini embedder ship inside doc-hub as built-in entry points (the pytest model — batteries included, but overridable). They live in `doc_hub._builtins/` subpackage. This is the recommended approach, but the adversarial review should evaluate whether companion packages are better.

### Database: PostgreSQL with VectorChord (unchanged)

Stay with PostgreSQL + VectorChord. No migration to SQLite. But:
- Clean up connection defaults (standard port 5432, no default password)
- Add `DOC_HUB_DATABASE_URL` env var for connection string override
- Remove hardcoded `CHECK` constraint on `fetch_strategy`
- Document cloud PostgreSQL setup (Neon, Supabase, Railway, self-hosted VectorChord)

### Data Storage: XDG-compliant Home Directory

Replace `_find_repo_root()` with:
1. `DOC_HUB_DATA_DIR` env var (explicit override)
2. `XDG_DATA_HOME/doc-hub` if set
3. `~/.local/share/doc-hub` default

Remove all monorepo path assumptions.

### Page Browsing: New Query Functions + MCP Tools

Add three capabilities missing from the current search-only interface:
- `list_pages(corpus)` → all unique source_url/source_file in a corpus
- `get_page(corpus, source_url)` → all chunks for a page, ordered by start_line
- `get_page_toc(corpus, source_url)` → heading hierarchy for a page

Three corresponding new MCP tools.

## Constraints

1. **The plan is architecture only — no code is written.** The adversarial pipeline produces a refined plan document, not implementation.
2. **PostgreSQL + VectorChord is non-negotiable.** Do not propose SQLite or alternative vector stores.
3. **The existing hybrid search (vector KNN + FTS + RRF) must not change.** It works well. The plugin system wraps around it, not through it.
4. **Backward compatibility with existing indexed data is NOT required.** This is a greenfield rewrite of the architecture. Existing corpora will be re-indexed.
5. **Python >=3.11 target** (down from >=3.13). StrEnum is available since 3.11.
6. **The plan must address the full picture:** discovery, protocols, path resolution, DB schema changes, page browsing, MCP tools, package metadata, built-in plugins, documentation for plugin authors.

## Open Questions for Adversarial Review

These are intentionally left under-specified for the adversarial rounds to resolve:

1. **Built-in plugins vs companion packages.** Should llms_txt/markdown/gemini ship inside doc-hub or as separate first-party packages? Evaluate tradeoffs: install simplicity vs separation of concerns. The lean is built-in.

2. **Parser boundary.** Currently parse.py does: heading split → chunk size optimization (merge tiny, split mega) → dedup → category derivation. How much of this is "parser" vs "core pipeline"? Proposal: parsers produce raw chunks from source files, the core pipeline handles size optimization and dedup. But the adversarial review should stress-test this.

3. **Vector dimensions across embedding models.** The `doc_chunks` table has `vector(768)` hardcoded. If someone uses OpenAI (1536-dim) or MiniLM (384-dim), this breaks. Options:
   - Per-corpus dimension stored in `doc_corpora`, table uses max dimension with zero-padding (wasteful)
   - `ALTER TABLE` when a new dimension is needed (dangerous)
   - Separate tables per dimension (complex)
   - Store dimension in corpus config, validate at embed time, require all corpora use same dimensions (simplest but limiting)
   The adversarial review should pick the right tradeoff.

4. **Plugin configuration schema.** Fetchers currently get config via `corpus.fetch_config` (a JSONB dict). Should plugins declare their config schema (e.g., via a Pydantic model or TypedDict) so doc-hub can validate config at registration time? Or keep it as a freeform dict?

5. **Plugin lifecycle hooks.** Should plugins be able to hook into pipeline events beyond their stage? E.g., a fetcher that wants to run post-index cleanup, or an embedder that needs startup/shutdown. Or keep it simple — plugins are pure functions, no lifecycle.

## Milestones

(To be generated by the init stage)
