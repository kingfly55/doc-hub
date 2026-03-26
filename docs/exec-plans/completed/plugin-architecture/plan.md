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
- `pyproject.toml` — hatchling build, Python >=3.13, 5 console scripts, 7 runtime dependencies (asyncpg, google-genai, numpy, aiohttp, pgvector, python-dotenv, mcp).

Database: PostgreSQL with VectorChord extension. Three tables: `doc_corpora`, `doc_chunks` (vector(768)), `doc_index_meta`.

## Architecture Decisions

### Plugin System: Hybrid Entry Points + Local Files (Approach C)

**Primary mechanism:** Python `importlib.metadata.entry_points()` with three groups:
- `doc_hub.fetchers` — classes with `async def fetch(self, corpus_slug, fetch_config, output_dir) -> Path`
- `doc_hub.parsers` — classes with `def parse(self, input_dir, *, corpus_slug, base_url) -> list[Chunk]`
- `doc_hub.embedders` — classes with properties (`model_name`, `dimensions`, `task_type_document`, `task_type_query`) and methods (`embed_batch`, `embed_query`)

**Secondary mechanism:** Scan `{data_root}/plugins/{fetchers,parsers,embedders}/*.py` for decorated classes. Entry points take precedence on name collision.

**Plugin contracts:** Python `typing.Protocol` classes in `doc_hub.protocols`. Plugins don't inherit — they match the structural type via `@runtime_checkable` isinstance checks at discovery time.

**Built-in plugins:** The existing llms_txt fetcher, markdown parser, and Gemini embedder ship inside doc-hub as built-in entry points (batteries included, but overridable). They live in `doc_hub._builtins/` subpackage.

**Important: Entry points require package reinstall.** After adding or changing `[project.entry-points]` in `pyproject.toml`, the package must be reinstalled (`pip install -e .` or `uv sync`) for `importlib.metadata.entry_points()` to discover them. This is a Python packaging requirement, not a doc-hub limitation. Document this in the plugin author guide.

**Entry point registration in `pyproject.toml`:** Built-in plugins MUST be registered in the `[project.entry-points]` section. Without this, the discovery engine won't find them. Example:
```toml
[project.entry-points."doc_hub.fetchers"]
llms_txt = "doc_hub._builtins.fetchers.llms_txt:LlmsTxtFetcher"
local_dir = "doc_hub._builtins.fetchers.local_dir:LocalDirFetcher"
sitemap = "doc_hub._builtins.fetchers.sitemap:SitemapFetcher"
git_repo = "doc_hub._builtins.fetchers.git_repo:GitRepoFetcher"

[project.entry-points."doc_hub.parsers"]
markdown = "doc_hub._builtins.parsers.markdown:MarkdownParser"

[project.entry-points."doc_hub.embedders"]
gemini = "doc_hub._builtins.embedders.gemini:GeminiEmbedder"
```

### Database: PostgreSQL with VectorChord (unchanged)

Stay with PostgreSQL + VectorChord. No migration to SQLite. But:
- Clean up connection defaults (standard port 5432, no default password)
- Add `DOC_HUB_DATABASE_URL` env var for connection string override
- URL-encode password in `_build_dsn()` via `urllib.parse.quote_plus()` to handle special characters (`@`, `/`, `%`, etc.)
- Remove hardcoded `CHECK` constraint on `fetch_strategy`
- Document cloud PostgreSQL setup (Neon, Supabase, Railway, self-hosted VectorChord)

### Data Storage: XDG-compliant Home Directory

Replace `_find_repo_root()` with:
1. `DOC_HUB_DATA_DIR` env var (explicit override)
2. `XDG_DATA_HOME/doc-hub` if set
3. `~/.local/share/doc-hub` default

Remove all monorepo path assumptions.

### Page Browsing: Deferred to Post-Plugin-Architecture

Page browsing (list_pages, get_page, get_page_toc + 3 new MCP tools) is a valuable feature but is orthogonal to the plugin architecture transformation. It requires no plugin-system changes — just new SQL queries and MCP tool registrations. **Deferring to a follow-up milestone** keeps the plugin transformation focused and reduces risk. The SQL queries and MCP tools can be added in a single follow-up milestone after the plugin architecture is stable.

## Constraints

1. **The plan is architecture only — no code is written.** The adversarial pipeline produces a refined plan document, not implementation.
2. **PostgreSQL + VectorChord is non-negotiable.** Do not propose SQLite or alternative vector stores.
3. **The existing hybrid search (vector KNN + FTS + RRF) must not change.** It works well. The plugin system wraps around it, not through it.
4. **Backward compatibility with existing indexed data is NOT required.** This is a greenfield rewrite of the architecture. Existing corpora will be re-indexed.
5. **Python >=3.11 target** (down from >=3.13). StrEnum is available since 3.11. The `pyproject.toml` `requires-python` must be updated from `>=3.13` to `>=3.11`. This also means `dict[str, Any]` and `list[str]` type hints in runtime code must use `from __future__ import annotations` (already present in all modules).
6. **The plan must address the full picture:** discovery, protocols, path resolution, DB schema changes, MCP tools, package metadata, built-in plugins, documentation for plugin authors.

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

   **Corollary — same embedder for cross-corpus search:** Even with matching dimensions, different embedding models produce incompatible vector spaces. If corpus A uses Gemini and corpus B uses OpenAI, cross-corpus search would embed the query with one model but compare against vectors from both — producing meaningless similarity scores for the mismatched corpus. **All corpora in a deployment should use the same embedder.** The `embedder` column on `doc_corpora` is for per-corpus override flexibility, but cross-corpus search only works correctly when all corpora share the same embedder. Document this clearly.

4. **Plugin configuration schema.** Fetchers currently get config via `corpus.fetch_config` (a JSONB dict). Should plugins declare their config schema (e.g., via a Pydantic model or TypedDict) so doc-hub can validate config at registration time? Or keep it as a freeform dict?

5. **Plugin lifecycle hooks.** Should plugins be able to hook into pipeline events beyond their stage? E.g., a fetcher that wants to run post-index cleanup, or an embedder that needs startup/shutdown. Or keep it simple — plugins are pure functions, no lifecycle.

## Plugin Author Workflow Summary

A developer writing a new plugin (e.g., a custom fetcher for a wiki API) follows these steps:

1. **Choose a delivery mechanism:**
   - **Entry point (recommended):** Create a Python package with a `pyproject.toml` entry point. Install the package in the same environment as doc-hub.
   - **Local plugin file (quick prototyping):** Drop a `.py` file in `~/.local/share/doc-hub/plugins/fetchers/` with a `@fetcher_plugin("name")` decorator.

2. **Implement the protocol:** Write a class whose methods match one of the protocols in `doc_hub.protocols` (Fetcher, Parser, or Embedder). No inheritance needed — just matching method signatures.

3. **Register:**
   - For entry points: add a `[project.entry-points."doc_hub.fetchers"]` section in your package's `pyproject.toml`, then `pip install -e .` (or `uv sync`).
   - For local files: the `@fetcher_plugin("name")` decorator handles registration automatically on next `get_registry()` call.

4. **Use:** Register a corpus with `fetch_strategy="my_plugin_name"` via MCP `add_corpus_tool` or SQL INSERT, then run the pipeline.

5. **Test:** Instantiate your plugin class directly in tests. For protocol conformance: `assert isinstance(MyPlugin(), Fetcher)`.

## First-Time Standalone Install Experience

After this transformation, a new user installs doc-hub with:

```bash
pip install doc-hub  # or: uv pip install doc-hub

# Start PostgreSQL with VectorChord
docker run -d --name doc-hub-pg \
  -e POSTGRES_PASSWORD=mypassword \
  -e POSTGRES_DB=doc_hub \
  -p 5432:5432 \
  tensorchord/vchord-postgres:latest

# Set required environment variables
export PGPASSWORD=mypassword
export GEMINI_API_KEY="your-key-here"
# Optional: export DOC_HUB_DATA_DIR=/custom/path (default: ~/.local/share/doc-hub)
# Optional: export DOC_HUB_DATABASE_URL="postgresql://postgres:mypassword@localhost:5432/doc_hub"

# Register a corpus and run the pipeline
doc-hub-mcp  # starts MCP server; OR use CLI:
doc-hub-pipeline --corpus pydantic-ai
```

The plan must ensure this works without a UV workspace, without `_find_repo_root()`, and with clear error messages at every failure point.

## Error Messages for Common Mistakes

The plan must specify clear, actionable error messages for these common scenarios:

| Scenario | Where | Error Message |
|----------|-------|---------------|
| Unknown fetcher plugin name | `PluginRegistry.get_fetcher()` | `Unknown fetcher: 'xyz'. Available fetchers: ['git_repo', 'llms_txt', 'local_dir', 'sitemap']` |
| Unknown parser plugin name | `PluginRegistry.get_parser()` | `Unknown parser: 'xyz'. Available parsers: ['markdown']` |
| Unknown embedder plugin name | `PluginRegistry.get_embedder()` | `Unknown embedder: 'xyz'. Available embedders: ['gemini']` |
| Embedder dimension mismatch | `embed_chunks()` | `Embedder 'openai-text-3-large' produces 1536-dim vectors, but this deployment is configured for 768-dim (DOC_HUB_VECTOR_DIM=768). All corpora in a deployment must use the same embedding dimensions.` |
| Missing GEMINI_API_KEY | `GeminiEmbedder._get_client()` | `GEMINI_API_KEY environment variable not set. Get a free key at https://aistudio.google.com/apikey` |
| Missing PGPASSWORD | `_build_dsn()` | `PGPASSWORD environment variable not set. Set it directly or use DOC_HUB_DATABASE_URL for the full connection string.` |
| Plugin doesn't conform to protocol | `_load_entry_points()` | `Skipping fetcher entry point 'xyz': instance 'BadClass' does not conform to Fetcher protocol` |
| Plugin import error (broken install) | `_load_entry_points()` | `Failed to load fetcher entry point 'xyz': ModuleNotFoundError: No module named 'some_dep'` (logged as exception, plugin skipped, does not crash) |
| Duplicate entry point name (two packages) | `_load_entry_points()` | `Fetcher entry point 'xyz' already registered (from package 'pkg-a'). Skipping duplicate from entry point 'xyz' in this scan.` (first-loaded wins, warning logged) |
| Plugin `__init__` raises (e.g. missing API key) | `_load_entry_points()` | `Failed to load embedder entry point 'openai': RuntimeError: OPENAI_API_KEY not set` (logged as exception, plugin skipped) |
| Corpus references uninstalled plugin | `run_pipeline()` → `get_fetcher()` | `KeyError: Unknown fetcher: 'wiki_api'. Available fetchers: ['git_repo', 'llms_txt', 'local_dir', 'sitemap']. If you just installed a plugin, restart the process or call reset_registry().` |
| Vector dim mismatch: existing table vs env | `ensure_schema()` | `RuntimeError: Existing doc_chunks table has vector(768) but DOC_HUB_VECTOR_DIM=1536. Drop and recreate the table, or set DOC_HUB_VECTOR_DIM=768 to match.` |
| Password with special chars in DSN | `_build_dsn()` | Passwords are URL-encoded via `urllib.parse.quote_plus()`. Use `DOC_HUB_DATABASE_URL` for passwords with exotic characters. |
| No `data_root()` outside monorepo (pre-M3) | `_find_repo_root()` | Removed entirely — `data_root()` uses XDG paths, never fails |

## Failure Modes and Edge Cases

The plugin architecture introduces new failure modes that the implementation must handle gracefully. This section documents the expected behavior for each scenario.

### Broken Plugin (Import Error, Wrong Signature)

**At discovery time:** If a plugin's entry point cannot be loaded (e.g., missing dependency, syntax error in module), the exception is caught and logged in `_load_entry_points()`. The plugin is skipped — it does NOT appear in the registry, and it does NOT crash the process. Other plugins continue loading normally.

**At call time:** If a plugin was loaded successfully at discovery time but fails during execution (e.g., `fetch()` raises because an API is unreachable), the exception propagates to the pipeline caller. The pipeline should log the error and continue to the next corpus in `sync_all()`.

**Protocol mismatch at discovery:** If a loaded class does not satisfy `isinstance(instance, Protocol)` (missing method names), it is skipped with a warning. Note that `@runtime_checkable` only checks method *names*, not signatures — a class with `def fetch(self)` (wrong arity) will pass the isinstance check but fail at call time with `TypeError`. This is acceptable; static type checkers catch signature mismatches at development time.

### Two Plugins Register the Same Name

**Two entry point packages with the same name:** `importlib.metadata.entry_points(group=group)` may return multiple entries with the same `.name` from different installed packages. The discovery engine iterates in the order returned by `importlib.metadata` and registers the first one successfully loaded. If a name is already registered when a second entry point with the same name is encountered, the second is skipped with a warning: `"Fetcher entry point 'xyz' already registered — skipping duplicate"`.

**Entry point vs. local file collision:** Entry points are loaded first (primary mechanism). Local plugin files are loaded second. If a local plugin file registers a name that already exists in the registry (from an entry point), the local file is skipped with a warning: `"Local fetcher plugin 'xyz' skipped — name already registered (entry point takes precedence)"`.

### Corpus References a Non-Installed Plugin

**At registration time (`add_corpus_tool`):** Soft validation — if the named plugin is not in the registry, a warning is logged but the corpus is still registered. This allows registering corpora before their plugin packages are installed.

**At pipeline execution time:** When the pipeline calls `get_registry().get_fetcher(corpus.fetch_strategy)` and the name is not found, `KeyError` is raised with an actionable message listing available plugins and an install hint. The pipeline aborts for that corpus. In `sync_all()`, the error is caught per-corpus and does not prevent other corpora from syncing.

### Concurrent Sync Operations

**Within a single process:** `sync_all()` processes corpora sequentially (not concurrently), so there is no contention on the plugin registry or file system within one process.

**Across multiple processes:** Two processes (e.g., a cron job and an MCP refresh) may run the pipeline for the same corpus simultaneously. The per-corpus advisory lock in `upsert_chunks()` (`pg_advisory_xact_lock(hashtext(slug))`) serializes the database write phase. The fetch/parse/embed stages write to the same file paths, so concurrent execution may produce corrupted intermediate files. **Resolution:** The advisory lock only protects the DB upsert. To be fully safe, the fetch stage should also acquire a file-based lock (e.g., `{corpus_dir}/.lock`). However, this is an existing limitation, not introduced by the plugin system, and is deferred to a future hardening milestone.

**Plugin registry thread safety:** The `_registry` global singleton is not thread-safe, but this is acceptable because asyncio is single-threaded. The registry is populated once at first access and then read-only. `reset_registry()` is only used in tests.

### Vector Dimension Mismatch

**Scenario 1: Embedder produces wrong dimensions for the deployment.** The embed pipeline validates `embedder.dimensions == get_vector_dim()` before embedding any chunks. Mismatch raises `ValueError` with a clear message. This is checked in Milestone 7's `embed_chunks()`.

**Scenario 2: Existing `doc_chunks` table was created with a different dimension.** `CREATE TABLE IF NOT EXISTS` does NOT alter an existing table — it silently preserves the old schema. If someone changes `DOC_HUB_VECTOR_DIM` after initial table creation, the table still has the old `vector(N)`. `ensure_schema()` MUST detect this by querying `pg_attribute` for the actual column type and comparing against `get_vector_dim()`. On mismatch, raise `RuntimeError` with instructions to either drop/recreate the table or set the env var to match. See Milestone 4 for implementation details.

### Backward Compatibility

**Existing data:** Not required (Constraint 4). The transformation is a greenfield rewrite. Existing corpora must be re-indexed after the transformation.

**Existing env vars:** The `PGPORT` default changes from `5433` to `5432` and `PGPASSWORD` default changes from `pydantic-docs` to no default (required). The `_build_dsn()` error message for missing `PGPASSWORD` should mention: `"Note: PGPASSWORD no longer has a default value. Previously it defaulted to 'pydantic-docs'."` Users of the existing Docker setup (`-p 5433:5432`) must set `PGPORT=5433` explicitly or update their Docker port mapping.

**Existing imports:** Code that imports `FetchStrategy` or `FETCHERS` from their current locations will break after Milestone 5. This is expected — the transformation changes internal APIs. No compatibility shims.

## Milestones

| # | Name | Dependencies | Key Deliverables |
|---|------|-------------|------------------|
| 1 | Protocol Definitions | None | `protocols.py` with `Fetcher`, `Parser`, `Embedder` protocols |
| 2 | Plugin Discovery Engine | M1 | `discovery.py` with entry-point + local-file discovery, `PluginRegistry` |
| 3 | XDG-Compliant Data Storage | None | Rewrite `paths.py`: remove `_find_repo_root()`, add XDG resolution chain |
| 4 | Database Config Cleanup & Schema Changes | None | Remove CHECK constraint, add `DOC_HUB_DATABASE_URL`, parameterize vector dim, add `parser`/`embedder` columns to DDL and `Corpus`, remove `migrate_from_legacy()`. **Do NOT remove `embedding_model` from `Corpus` dataclass** — it is still used by `index.py:_write_meta()` until M7. |
| 5 | Core Models Refactor & Built-in Fetcher Plugins | M1, M2, M4 | Remove `FetchStrategy` enum, move fetchers to `_builtins/`, register as entry points in `pyproject.toml` |
| 6 | Parser Pluggability | M1, M2, M3, M5 | Extract markdown parser to `_builtins/parsers/`, split `parse.py` into core pipeline + plugin. **Depends on M3** because `parse_docs()` calls `chunks_dir(corpus_slug)` with a string. |
| 7 | Embedder Pluggability | M1, M2, M3, M4, M5 | Extract Gemini embedder to `_builtins/embedders/`, refactor `embed.py` into core orchestration. Remove `embedding_model` from `Corpus`. **Depends on M3** because `embed_chunks()` calls `embeddings_cache_path(corpus_slug)` with a string. |
| 8 | Pipeline Integration & Packaging | M3–M7 | Wire plugins through pipeline + MCP server, update `pyproject.toml` (entry points, `requires-python = ">=3.11"`), rewrite `docs/writing-fetchers.md` as plugin author guide, update README for standalone install |

Milestones 1–3 are independent and can be worked in parallel. Milestones 5–7 depend on 1+2 and can be parallelized after those complete (but M6 and M7 also depend on M3 for string-accepting path helpers). Milestone 8 is the integration milestone that brings everything together.

**Critical dependency note for implementation agents:** Milestones 5, 6, and 7 each add entry-point registrations to `pyproject.toml`. Since only M8 does the final packaging, each of M5/M6/M7 should add their entry-point sections to `pyproject.toml` immediately. The package must be reinstalled (`uv sync` or `pip install -e .`) after each milestone for entry-point discovery to work in verification steps.

**`embedding_model` removal sequencing:** The `embedding_model` column is removed from `doc_corpora` DDL in M4, but the `embedding_model` field on the `Corpus` dataclass must be kept until M7. This is because `index.py:_write_meta()` accesses `corpus.embedding_model` to write metadata to `doc_index_meta` (a separate table). M7 refactors `_write_meta()` to accept explicit `embedder_model` and `embedder_dims` parameters, at which point `embedding_model` and `embedding_dimensions` are removed from `Corpus`.

## Final File Inventory

After all 8 milestones are complete, the following files should exist in `src/doc_hub/`:

```
src/doc_hub/
├── __init__.py                         # unchanged
├── models.py                           # Corpus (no FetchStrategy, no embedding_model; has parser/embedder)
├── paths.py                            # XDG data_root(), plugins_dir(), Corpus|str helpers
├── db.py                               # DOC_HUB_DATABASE_URL, get_vector_dim(), no CHECK, no migrate_from_legacy
├── protocols.py                        # NEW: Fetcher, Parser, Embedder protocols
├── discovery.py                        # NEW: PluginRegistry, get_registry(), decorators
├── fetchers.py                         # Gutted: thin dispatch via registry, keeps DEFAULT_WORKERS/RETRIES
├── parse.py                            # Core pipeline: Chunk, merge/split/dedup/category, parse_docs()
├── embed.py                            # Core orchestration: EmbeddedChunk, cache, L2 norm, no Gemini imports
├── index.py                            # Mostly unchanged: _write_meta takes explicit embedder params
├── search.py                           # Uses embedder plugin for query embedding, no genai imports
├── pipeline.py                         # No gemini_client; resolves plugins from Corpus fields
├── mcp_server.py                       # No gemini_client in AppState; parser/embedder on add_corpus
├── eval.py                             # Updated _eval_dir to use data_root(); no gemini_client
├── _builtins/
│   ├── __init__.py                     # empty
│   ├── fetchers/
│   │   ├── __init__.py                 # empty
│   │   ├── llms_txt.py                 # LlmsTxtFetcher class + all helpers from old fetchers.py
│   │   ├── local_dir.py                # LocalDirFetcher class
│   │   ├── sitemap.py                  # SitemapFetcher stub
│   │   └── git_repo.py                 # GitRepoFetcher stub
│   ├── parsers/
│   │   ├── __init__.py                 # empty
│   │   └── markdown.py                 # MarkdownParser class
│   └── embedders/
│       ├── __init__.py                 # empty
│       └── gemini.py                   # GeminiEmbedder class
```

Test files:
```
tests/
├── test_protocols.py                   # NEW (M1)
├── test_discovery.py                   # NEW (M2)
├── test_paths.py                       # NEW or modified (M3)
├── test_db.py                          # modified (M4)
├── test_fetchers.py                    # modified (M5)
├── test_parse.py                       # modified (M6)
├── test_markdown_parser.py             # NEW (M6)
├── test_embed.py                       # modified (M7)
├── test_gemini_embedder.py             # NEW (M7)
├── test_search.py                      # modified (M7)
├── test_mcp_server.py                  # modified (M8)
├── test_index.py                       # modified (M8, if exists)
├── test_eval.py                        # modified (M8, if exists)
```

`pyproject.toml` final state includes:
```toml
requires-python = ">=3.11"

[project.entry-points."doc_hub.fetchers"]
llms_txt = "doc_hub._builtins.fetchers.llms_txt:LlmsTxtFetcher"
local_dir = "doc_hub._builtins.fetchers.local_dir:LocalDirFetcher"
sitemap = "doc_hub._builtins.fetchers.sitemap:SitemapFetcher"
git_repo = "doc_hub._builtins.fetchers.git_repo:GitRepoFetcher"

[project.entry-points."doc_hub.parsers"]
markdown = "doc_hub._builtins.parsers.markdown:MarkdownParser"

[project.entry-points."doc_hub.embedders"]
gemini = "doc_hub._builtins.embedders.gemini:GeminiEmbedder"
```
