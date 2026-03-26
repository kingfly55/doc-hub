# doc-hub Documentation Plan

This plan defines every document to be written, the sections each must contain,
the source files a writing agent must read, and the specific items to cover.

Two documentation sets: **User Documentation** (install-and-use audience) and
**Developer Documentation** (agent-first, for humans and AI agents working on
doc-hub internals).

---

## 1. User Documentation (`docs/user/`)

---

### 1.1 `docs/user/getting-started.md`

**Audience:** User (new to doc-hub, not reading source code)

**Purpose:** Walk a user from zero to their first successful search query.

**Sections:**

1. **Prerequisites**
   - Python >= 3.11 (pyproject.toml requires-python), uv or pip
   - PostgreSQL with VectorChord extension
   - Gemini API key (free tier)

2. **Install doc-hub**
   - `uv sync --package doc-hub` (monorepo context)
   - `pip install doc-hub` (standalone context)
   - Verify with `doc-hub-search --help`

3. **Start PostgreSQL with VectorChord**
   - Docker one-liner: `docker run -d --name vchord-postgres -e POSTGRES_PASSWORD=... -p 5433:5432 tensorchord/vchord-postgres:latest`
   - Explain the `vchord` extension is created automatically by `ensure_schema()`

4. **Configure environment variables**
   - Minimum required: `PGPASSWORD`, `GEMINI_API_KEY`
   - Recommended `.env` file at repo root (loaded via `python-dotenv`)
   - Full table of connection vars: `DOC_HUB_DATABASE_URL`, `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`
   - Cross-reference to `docs/user/configuration.md` for all vars

5. **Register your first corpus**
   - Via MCP `add_corpus_tool` or direct SQL INSERT into `doc_corpora`
   - Example: register pydantic-ai with `llms_txt` strategy, providing `url` and `url_pattern` in config JSON

6. **Run the pipeline**
   - `doc-hub-pipeline --corpus pydantic-ai`
   - Explain what happens at each stage (fetch, parse, embed, index)
   - Expected output / logs

7. **Run your first search**
   - `doc-hub-search "how do I define a tool?" --corpus pydantic-ai`
   - Explain output columns: heading, corpus, path, category, similarity, RRF score, URL, preview
   - `--json` flag for structured output

8. **Next steps**
   - Point to CLI reference, MCP server setup, evaluation guide

**Key source files to reference:**
- `pyproject.toml` (dependencies, scripts, requires-python)
- `db.py` (connection string resolution in `_build_dsn`, `ensure_schema`)
- `pipeline.py` (`main()` CLI, stage descriptions)
- `search.py` (`main()` CLI output format)
- `mcp_server.py` (`add_corpus_tool` args)
- `README.md` (setup section for Docker command, env var table)

**Specific items to cover:**
- `_build_dsn()` resolution order: explicit DSN arg → `DOC_HUB_DATABASE_URL` → individual `PG*` vars
- `PGPASSWORD` has NO default (RuntimeError if not set)
- `PGPORT` default is `5432` (NOT 5433 — README uses 5433 because it maps host 5433 → container 5432)
- `ensure_schema()` creates extension `vchord CASCADE`, tables `doc_corpora`, `doc_chunks`, `doc_index_meta`, and all indexes idempotently
- The five console scripts: `doc-hub-pipeline`, `doc-hub-search`, `doc-hub-mcp`, `doc-hub-eval`, `doc-hub-sync-all`

---

### 1.2 `docs/user/configuration.md`

**Audience:** User

**Purpose:** Comprehensive reference for all environment variables and path resolution.

**Sections:**

1. **Environment variable reference table**
   - Every env var, its default, and what it controls

2. **Database connection**
   - `DOC_HUB_DATABASE_URL` — full PostgreSQL connection string (takes precedence)
   - `PGHOST` (default: `localhost`)
   - `PGPORT` (default: `5432`)
   - `PGDATABASE` (default: `doc_hub`)
   - `PGUSER` (default: `postgres`)
   - `PGPASSWORD` (NO default — required)
   - Resolution order in `_build_dsn()`: explicit arg → `DOC_HUB_DATABASE_URL` → individual PG* vars
   - URL encoding of user/password for special characters

3. **Embedding configuration**
   - `GEMINI_API_KEY` — required, get at https://aistudio.google.com/apikey
   - `GEMINI_EMBEDDING_MODEL` (default: `gemini-embedding-001`)
   - `GEMINI_EMBEDDING_DIM` (default: `768`)
   - `DOC_HUB_VECTOR_DIM` (default: `768`) — must match embedder dimensions
   - `DOC_HUB_EMBED_SLEEP` (default: `65.0`) — inter-batch sleep seconds

4. **Data directory resolution**
   - `DOC_HUB_DATA_DIR` (explicit override)
   - `XDG_DATA_HOME/doc-hub` (if `XDG_DATA_HOME` set)
   - `~/.local/share/doc-hub` (XDG default)
   - Directory layout: `{data_root}/{slug}/raw/`, `{data_root}/{slug}/chunks/`, `{data_root}/plugins/`

5. **Eval directory**
   - `DOC_HUB_EVAL_DIR` (explicit override)
   - `{data_root}/eval/` (default)

6. **Logging**
   - `LOGLEVEL` — set to `DEBUG` for verbose output (used by `search.py` and `eval.py`)
   - Pipeline always logs at INFO level

7. **`.env` file support**
   - All CLI entry points call `load_dotenv()` on startup
   - Place `.env` at repo root or working directory

**Key source files to reference:**
- `paths.py` (`data_root()`, `plugins_dir()`, `corpus_dir()`, `raw_dir()`, `chunks_dir()`, `manifest_path()`, `embedded_chunks_path()`, `embeddings_cache_path()`)
- `db.py` (`_build_dsn()`, `get_vector_dim()`)
- `embed.py` (`DOC_HUB_EMBED_SLEEP` override on line 253)
- `eval.py` (`_eval_dir()`, `DOC_HUB_EVAL_DIR`)
- `_builtins/embedders/gemini.py` (`GEMINI_API_KEY`, `GEMINI_EMBEDDING_MODEL`, `GEMINI_EMBEDDING_DIM`)

**Specific items to cover:**
- `data_root()` resolution: `DOC_HUB_DATA_DIR` → `XDG_DATA_HOME/doc-hub` → `~/.local/share/doc-hub`
- `get_vector_dim()` reads `DOC_HUB_VECTOR_DIM` (default 768), raises ValueError if not positive int
- `ensure_schema()` validates existing vector column dimension against `DOC_HUB_VECTOR_DIM` and raises RuntimeError on mismatch
- `_build_dsn()` uses `urllib.parse.quote_plus` for user/password
- GeminiEmbedder reads `GEMINI_API_KEY` lazily on first use (not at import time)
- `DOC_HUB_EMBED_SLEEP` overrides `inter_batch_sleep` parameter in `embed_chunks()`

---

### 1.3 `docs/user/cli-reference.md`

**Audience:** User

**Purpose:** Complete reference for all 5 console scripts with every flag, examples, and exit codes.

**Sections:**

1. **`doc-hub-pipeline`**
   - Description: Run the fetch → parse → embed → index pipeline for a corpus
   - Flags:
     - `--corpus SLUG` (required) — corpus slug, must exist in `doc_corpora`
     - `--stage {fetch,parse,embed,index}` — run only this stage (default: all)
     - `--clean` — wipe all local data for the corpus before starting (`shutil.rmtree` on `corpus_dir`)
     - `--skip-download` — skip fetch, re-use existing `raw/` directory
     - `--full-reindex` — delete stale DB rows after upsert (rows whose content_hash no longer in current set)
     - `--retry-failed` — retry previously failed downloads
     - `--workers N` (default: 20) — download concurrency
     - `--retries N` (default: 3) — HTTP retry count per URL
   - Exit codes: 0 success, 1 if corpus not found in DB
   - Examples: full pipeline, single stage, clean + full reindex

2. **`doc-hub-search`**
   - Description: Hybrid vector + full-text search
   - Flags:
     - `query` (positional, required) — search query string
     - `--corpus SLUG` — filter to one corpus (default: search all)
     - `--category CATEGORY` (repeatable via `action="append"`) — include filter: `api`, `guide`, `example`, `eval`, `other`
     - `--exclude-category CATEGORY` (repeatable) — exclude filter
     - `--limit N` (default: 5) — max results
     - `--offset N` (default: 0) — skip first N results (pagination)
     - `--min-similarity FLOAT` (default: 0.55) — cosine similarity threshold
     - `--source-url-prefix STR` — restrict to URLs starting with this
     - `--section-path-prefix STR` — restrict to section paths starting with this
     - `--vector-limit N` (default: 20) — KNN candidate pool (advanced)
     - `--text-limit N` (default: 10) — BM25 candidate pool (advanced)
     - `--rrfk N` (default: 60) — RRF k constant (advanced)
     - `--language STR` (default: english) — PostgreSQL text-search language config
     - `--json` — output results as JSON
   - Exit codes: 0 always
   - Output format: numbered results with heading, corpus, path, category, lines, similarity, RRF score, URL, preview
   - JSON output format: array of objects with keys `id`, `corpus_id`, `heading`, `section_path`, `source_url`, `score`, `similarity`, `category`, `start_line`, `end_line`, `content_preview`

3. **`doc-hub-mcp`**
   - Description: Start the MCP server
   - Flags:
     - `--transport {stdio,sse,streamable-http}` (default: stdio)
     - `--host STR` (default: 127.0.0.1)
     - `--port N` (default: 8340)
   - Exit codes: 0 on normal shutdown
   - Examples: stdio, SSE, streamable-http with custom port

4. **`doc-hub-eval`**
   - Description: Evaluate retrieval quality with Precision@N and MRR
   - Flags:
     - `--corpus SLUG` (mutually exclusive with `--all`)
     - `--all` — run evals for all corpora with eval files
     - `--limit N` (default: 5) — results per query (N in Precision@N)
     - `--verbose` — show per-query hit/miss details
     - `--output PATH` — write JSON report to file
     - `--min-precision FLOAT` (default: 0.80) — P@N pass threshold
     - `--min-mrr FLOAT` (default: 0.60) — MRR pass threshold
   - Default behavior (no --corpus, no --all): runs all available evals
   - Exit codes: 0 if all evals pass, 1 if any fail or no evals found
   - Output format: report with queries run, hits, P@N, MRR, failed queries, status PASS/FAIL

5. **`doc-hub-sync-all`**
   - Description: Run full pipeline for every enabled corpus in the DB
   - Flags: none
   - Exit codes: 0 on completion
   - Output: per-corpus summary (new, updated, removed, total) or FAILED

**Key source files to reference:**
- `pipeline.py` (`_build_arg_parser()`, `main()`, `sync_all_main()`)
- `search.py` (`main()`, all argparse definitions)
- `mcp_server.py` (`_parse_args()`, `main()`)
- `eval.py` (`main()`, argparse definitions, `DEFAULT_PRECISION_THRESHOLD`, `DEFAULT_MRR_THRESHOLD`)

**Specific items to cover:**
- All `argparse` flag names, types, defaults, and help text — extracted verbatim from source
- `doc-hub-pipeline` requires corpus to exist in `doc_corpora` table (resolved via `get_corpus()`)
- `doc-hub-search` and `doc-hub-eval` use `LOGLEVEL=DEBUG` for verbose logging
- `doc-hub-pipeline` and `doc-hub-sync-all` always log at INFO
- `doc-hub-eval` default behavior: runs all corpora (the `elif args.all or True:` pattern means default = all)
- `doc-hub-search` `--category` uses `action="append"` so multiple categories are specified as `--category api --category guide`
- `SearchConfig` language validation against `VALID_PG_LANGUAGES` whitelist (29 languages listed in search.py)
- `doc-hub-sync-all` catches per-corpus errors and continues to next corpus

---

### 1.4 `docs/user/mcp-server.md`

**Audience:** User

**Purpose:** Running the MCP server, all 4 tools with parameters, transport modes, Claude Desktop integration.

**Sections:**

1. **Overview**
   - FastMCP server exposing 4 tools for LLM agents
   - Shared DB pool via lifespan context

2. **Transport modes**
   - **stdio** (default): spawned per session, used by Claude Code / Claude Desktop
   - **SSE**: persistent HTTP service on `host:port/sse`
   - **streamable-http**: newer MCP HTTP transport

3. **Starting the server**
   - `doc-hub-mcp` (stdio)
   - `doc-hub-mcp --transport sse --port 8340`
   - `doc-hub-mcp --transport streamable-http --port 8340`

4. **Claude Desktop configuration**
   - stdio JSON config: `{"mcpServers": {"doc-hub": {"command": "uv", "args": ["run", "--package", "doc-hub", "doc-hub-mcp"], "env": {"GEMINI_API_KEY": "<key>"}}}}`
   - SSE JSON config: `{"mcpServers": {"doc-hub": {"type": "sse", "url": "http://localhost:8340/sse"}}}`

5. **Claude Code configuration**
   - Using `--mcp` flag or MCP config in `settings.json`

6. **Running as a systemd service**
   - Unit file template (from README)
   - Enable/start commands, status check, log viewing

7. **Tool reference: `search_docs_tool`**
   - Parameters: `query` (str, required), `corpus` (str|None), `categories` (list[str]|None), `limit` (int, default 5), `max_content_chars` (int, default 800)
   - Return: list of dicts with keys `heading`, `section_path`, `content`, `source_url`, `corpus_id`, `score`, `similarity`, `category`, `start_line`, `end_line`
   - Content is truncated to `max_content_chars`

8. **Tool reference: `list_corpora_tool`**
   - Parameters: none
   - Return: list of dicts with keys `slug`, `name`, `strategy`, `enabled`, `total_chunks`, `last_indexed_at`

9. **Tool reference: `add_corpus_tool`**
   - Parameters: `slug` (str), `name` (str), `strategy` (str), `config` (dict), `parser` (str, default "markdown"), `embedder` (str, default "gemini")
   - Return: `{"status": "registered", "slug": "<slug>"}`
   - Soft validation: warns if plugin not registered but does not error

10. **Tool reference: `refresh_corpus_tool`**
    - Parameters: `slug` (str), `full` (bool, default false)
    - Return: `{"status": "complete", "slug": ..., "chunks_indexed": ..., "inserted": ..., "updated": ..., "deleted": ...}` or `{"error": "..."}`
    - Runs full pipeline (fetch → parse → embed → index)
    - Returns error if corpus not found or disabled

**Key source files to reference:**
- `mcp_server.py` (all tool definitions, `_parse_args()`, lifespan, `AppState`, `DEFAULT_PORT`)
- `search.py` (`search_docs()` parameters for understanding what `search_docs_tool` delegates to)

**Specific items to cover:**
- `DEFAULT_PORT = 8340`
- `AppState` dataclass with `pool: asyncpg.Pool`
- Lifespan creates pool + ensures schema on startup, closes pool on shutdown
- GEMINI_API_KEY check is deferred (lazy on first embed call) — server starts without it
- `_search_tool_impl` truncates content to `max_content_chars`, rounds score to 4 decimal places, similarity to 3
- `_add_corpus_impl` soft-validates plugin names via `get_registry()` — warns but doesn't error
- `_refresh_corpus_impl` checks corpus exists and is enabled before running pipeline
- The server host for non-stdio transports: `server.settings.host = args.host`

---

### 1.5 `docs/user/evaluation.md`

**Audience:** User

**Purpose:** How to write eval files, run evaluations, and interpret Precision@5 and MRR.

**Sections:**

1. **What is retrieval evaluation?**
   - Measures how well search returns relevant results for known queries
   - Two metrics: Precision@N (hit rate in top N) and MRR (Mean Reciprocal Rank)

2. **Eval file format**
   - JSON array of test query objects
   - Each object requires: `id` (str), `query` (str), plus at least one of `expected_headings` (list[str]) or `expected_section_paths` (list[str])
   - Optional fields: `min_similarity` (float, default 0.55), `notes` (str)
   - Example eval file

3. **Eval file location**
   - `DOC_HUB_EVAL_DIR` env var (explicit override)
   - `{data_root}/eval/` (default, from `_eval_dir()`)
   - File naming: `{corpus-slug}.json` (e.g. `eval/pydantic-ai.json`)

4. **Running evaluations**
   - `doc-hub-eval --corpus pydantic-ai`
   - `doc-hub-eval --all`
   - `doc-hub-eval` (default: runs all available)
   - `doc-hub-eval --corpus pydantic-ai --verbose --output report.json`
   - `doc-hub-eval --corpus fastapi --min-precision 0.70 --min-mrr 0.50`

5. **Understanding the output**
   - Report sections: queries run, hits in top-N, Precision@N, MRR
   - Failed queries list (no relevant result in top N)
   - Low similarity queries (top result below `min_similarity`)
   - PASS/FAIL status with threshold comparison

6. **Matching logic**
   - A result is a "hit" if heading matches any `expected_headings` (case-insensitive substring) OR section_path contains any `expected_section_paths` (case-insensitive substring)
   - `_is_hit_single()` checks both `expected_headings` and `expected_section_paths`
   - Reciprocal Rank: `1 / rank_of_first_hit`, or 0.0 if no hit

7. **Metrics explained**
   - **Precision@N**: `hits / total` — fraction of queries where top-N contained a relevant result
   - **MRR**: mean of `1/rank_of_first_hit` across all queries — rewards results appearing higher
   - Default thresholds: P@5 >= 0.80, MRR >= 0.60

8. **JSON report format**
   - `to_dict()` output: corpus, total, hits, precision_at_n, mrr, n, failed_queries, low_similarity_queries, passed, thresholds
   - Single corpus → object, multiple corpora → array

9. **Tips for writing good eval files**
   - Cover different query types: keyword, natural language, API-specific
   - Use specific `expected_headings` that match actual indexed headings
   - Use `expected_section_paths` for broader matching
   - Start with ~20-30 queries per corpus

**Key source files to reference:**
- `eval.py` (all dataclasses, `evaluate()`, `load_test_queries()`, `_is_hit_single()`, `_reciprocal_rank()`, `print_report()`, `main()`)

**Specific items to cover:**
- `TestQuery` dataclass fields: `id`, `query`, `expected_headings`, `expected_section_paths`, `min_similarity`, `notes`
- `QueryResult` dataclass fields: `query_id`, `query`, `hit`, `reciprocal_rank`, `top_similarity`, `below_sim_threshold`, `results`, `first_hit_rank`
- `EvalReport` dataclass and `to_dict()` method
- `DEFAULT_PRECISION_THRESHOLD = 0.80`, `DEFAULT_MRR_THRESHOLD = 0.60`
- `load_test_queries()` validation: requires `id` and `query`, plus at least one expectation
- `evaluate()` passes `min_similarity=0.0` to `search_docs()` during eval (scores all results, doesn't pre-filter)
- `list_eval_corpora()` scans `_eval_dir()` for `*.json` files, returns stems as corpus slugs
- Exit code 0 if all pass, 1 if any fail

---

### 1.6 `docs/user/cloud-database.md`

**Audience:** User

**Purpose:** Using hosted PostgreSQL (Neon, Supabase, Railway) with VectorChord.

**Sections:**

1. **Why a cloud database?**
   - Persistent data without local Docker
   - Shared across machines / CI environments

2. **Requirements**
   - Provider must support the `vchord` (VectorChord) extension OR `pgvector` (with adapter notes)
   - PostgreSQL 15+ recommended

3. **Connection string setup**
   - Set `DOC_HUB_DATABASE_URL` to provider's connection string
   - Or set individual `PG*` vars
   - Ensure `PGPASSWORD` is set (no default)

4. **Neon**
   - Connection string format
   - Enable VectorChord extension (if available) or pgvector
   - Pooling considerations (Neon uses pgbouncer — may need `?sslmode=require`)

5. **Supabase**
   - Connection string from project settings
   - pgvector is pre-installed; VectorChord availability
   - Direct connection vs pooled connection

6. **Railway**
   - Connection string from Railway dashboard
   - Extension installation

7. **Vector dimension considerations**
   - `DOC_HUB_VECTOR_DIM` must match across local and cloud
   - `ensure_schema()` dimension mismatch detection (raises RuntimeError with instructions)

8. **Migration from local to cloud**
   - Schema is created idempotently by `ensure_schema()`
   - Data must be re-indexed (`doc-hub-pipeline --corpus <slug>` or `doc-hub-sync-all`)

**Key source files to reference:**
- `db.py` (`_build_dsn()`, `ensure_schema()`, `get_vector_dim()`, dimension mismatch validation)

**Specific items to cover:**
- `_build_dsn()` URL encoding via `urllib.parse.quote_plus` for special chars in passwords
- `ensure_schema()` creates extension `vchord CASCADE` — the `CASCADE` installs `pgvector` as a dependency
- Dimension mismatch detection: queries `pg_attribute.atttypmod` for existing `embedding` column
- Pool config: `min_size=1, max_size=10`

---

## 2. Developer Documentation (agent-first)

---

### 2.1 `AGENTS.md` (project root: `packages/doc-hub/AGENTS.md`)

**Audience:** Developer / AI agent

**Purpose:** Short (~100 lines) table of contents. Entry point for agents. Progressive disclosure via links.

**Sections:**

1. **Quick start for agents**
   - How to run tests: `uv run --package doc-hub pytest packages/doc-hub/tests/`
   - Integration tests: `pytest -m integration` (requires live DB + GEMINI_API_KEY)

2. **Module map** (table)
   - `protocols.py` — Plugin protocols (Fetcher, Parser, Embedder)
   - `discovery.py` — Plugin registry, entry point + local file discovery
   - `models.py` — Corpus dataclass
   - `paths.py` — Data directory resolution (XDG)
   - `db.py` — asyncpg pool, DDL, JSONB codec, CRUD helpers
   - `fetchers.py` — Fetcher dispatch (routes to plugin)
   - `parse.py` — Chunk dataclass, parse pipeline (size optimization, dedup, category)
   - `embed.py` — EmbeddedChunk, embedding cache, L2 normalization, batch orchestration
   - `index.py` — PostgreSQL upsert with advisory locks, IndexResult
   - `search.py` — Hybrid search (vector KNN + BM25 + RRF), SearchResult, SearchConfig
   - `pipeline.py` — Pipeline orchestration (fetch→parse→embed→index), sync_all
   - `mcp_server.py` — FastMCP server, 4 tools, AppState lifespan
   - `eval.py` — Retrieval evaluation (P@N, MRR), TestQuery, EvalReport
   - `_builtins/` — Built-in plugins (fetchers: llms_txt, local_dir, sitemap, git_repo; parsers: markdown; embedders: gemini)

3. **Where to find deeper docs** (links)
   - Architecture: `ARCHITECTURE.md`
   - Plugin authoring: `docs/dev/plugin-authoring.md`
   - Protocol reference: `docs/dev/protocols-reference.md`
   - Database schema: `docs/dev/database-schema.md`
   - Testing guide: `docs/dev/testing-guide.md`
   - Search internals: `docs/dev/search-internals.md`

4. **Key conventions**
   - All async I/O uses asyncpg (not psycopg)
   - Plugin discovery via `importlib.metadata` entry points (primary) and local `{data_root}/plugins/` files (secondary)
   - Entry point groups: `doc_hub.fetchers`, `doc_hub.parsers`, `doc_hub.embedders`
   - `@runtime_checkable` protocols — `isinstance()` checks method NAMES only, not signatures
   - Structural typing (no inheritance required for plugins)

**Key source files to reference:**
- All source files (for the module map)
- `pyproject.toml` (entry point groups, test config)

**Specific items to cover:**
- pytest marker: `integration` — "requires live DB and GEMINI_API_KEY"
- `asyncio_mode = "auto"` in pytest config
- Entry point group names: `doc_hub.fetchers`, `doc_hub.parsers`, `doc_hub.embedders`
- Build backend: `hatchling` with `packages = ["src/doc_hub"]`

---

### 2.2 `ARCHITECTURE.md` (project root: `packages/doc-hub/ARCHITECTURE.md`)

**Audience:** Developer / AI agent

**Purpose:** Domain map, module dependency graph, layer rules, data flow, plugin boundaries.

**Sections:**

1. **Domain overview**
   - doc-hub is a multi-corpus documentation search engine
   - Pipeline: fetch → parse → embed → index → search
   - Plugin system for fetchers, parsers, and embedders
   - MCP server for LLM tool access

2. **Data flow diagram**
   - ASCII diagram showing: Source → Fetcher → `raw/` dir → Parser → `chunks.jsonl` → Embedder → `embedded_chunks.jsonl` → Index (PostgreSQL) → Search (hybrid KNN + BM25 + RRF) → Results
   - Label each transition with the module responsible

3. **Module dependency graph**
   - Show which modules import which
   - Key dependency chains:
     - `pipeline.py` → `fetchers.py`, `parse.py`, `embed.py`, `index.py`, `db.py`
     - `mcp_server.py` → `db.py`, `pipeline.py`, `search.py`, `models.py`
     - `fetchers.py` → `discovery.py` → `protocols.py`
     - `parse.py` → `paths.py`
     - `embed.py` → `parse.py`, `paths.py`
     - `index.py` → `db.py`, `embed.py`, `models.py`
     - `search.py` → `db.py`, `embed.py`, `discovery.py`

4. **Layer rules**
   - **Plugin layer** (`_builtins/`, external packages): May import `protocols.py`, `parse.py` (for Chunk), `discovery.py` (for decorators). Must NOT import `db.py`, `index.py`, `pipeline.py`.
   - **Core pipeline layer** (`parse.py`, `embed.py`, `index.py`, `fetchers.py`): May import `protocols.py`, `discovery.py`, `models.py`, `paths.py`, `db.py`. Must NOT import `mcp_server.py`, `search.py`, `eval.py`.
   - **Interface layer** (`search.py`, `mcp_server.py`, `eval.py`, `pipeline.py`): May import anything in core or plugin layer.
   - **Foundation layer** (`protocols.py`, `models.py`, `paths.py`, `db.py`, `discovery.py`): Minimal inter-dependencies. `protocols.py` imports only `parse.py` (for Chunk type). `discovery.py` imports `protocols.py`. `paths.py` and `db.py` are independent.

5. **Plugin boundary**
   - Three plugin points: Fetcher, Parser, Embedder
   - Protocols define the contract (structural typing, no inheritance)
   - Discovery: entry points first, local files second
   - Entry point precedence on name collision
   - Plugins instantiated with no args — configuration via env vars or lazy init

6. **Data stores**
   - **Filesystem**: `{data_root}/{slug}/raw/` (fetched files), `{data_root}/{slug}/chunks/` (JSONL files), `{data_root}/plugins/` (local plugins)
   - **PostgreSQL**: `doc_corpora` (registry), `doc_chunks` (indexed chunks with embeddings + tsvector), `doc_index_meta` (key-value metadata)
   - **JSONL cache**: `embeddings_cache.jsonl` keyed by (content_hash, model, dimensions)

7. **Concurrency model**
   - asyncio throughout, asyncpg for DB
   - Per-corpus advisory lock during index: `pg_advisory_xact_lock(hashtext(slug))`
   - Download concurrency bounded by semaphore (`workers` parameter)
   - Embed batching with inter-batch sleep for rate limits

**Key source files to reference:**
- All core modules for import analysis
- `db.py` (DDL for data store description)
- `discovery.py` (plugin discovery mechanism)
- `index.py` (advisory lock, concurrency)

**Specific items to cover:**
- `pipeline.py` is the orchestrator — it imports and calls run_fetch, run_parse, run_embed, run_index
- `mcp_server.py` uses `FastMCP` lifespan for shared `asyncpg.Pool`
- `sync_all()` in pipeline.py iterates enabled corpora, catches per-corpus errors
- Embedding cache keyed by `(content_hash, model, dimensions)` — changing model invalidates cache
- `parse.py` owns the `Chunk` dataclass (used by protocols, parsers, embed, index)
- `embed.py` owns `EmbeddedChunk` (used by index)
- L2 normalization happens in `embed.py`, NOT in embedder plugins
- Category derivation happens in `parse.py`, NOT in parser plugins

---

### 2.3 `docs/dev/plugin-authoring.md`

**Audience:** Developer / AI agent

**Purpose:** Complete guide to writing a fetcher, parser, or embedder plugin from scratch.

**Sections:**

1. **Plugin system overview**
   - Three plugin types: Fetcher, Parser, Embedder
   - Two registration mechanisms: entry points (primary) and local plugin files (secondary)
   - Structural typing — no inheritance required

2. **Writing a fetcher plugin**
   - Protocol signature: `async def fetch(self, corpus_slug: str, fetch_config: dict[str, Any], output_dir: Path) -> Path`
   - Contract: must write `.md` files to `output_dir`, be idempotent, must not touch DB or embed
   - Should create `output_dir` with `mkdir(parents=True, exist_ok=True)`
   - Should write `manifest.json` for incremental sync
   - Example: complete fetcher class
   - Reference: `LlmsTxtFetcher` for manifest pattern, `LocalDirFetcher` for minimal implementation

3. **Writing a parser plugin**
   - Protocol signature: `def parse(self, input_dir: Path, *, corpus_slug: str, base_url: str) -> list[Chunk]`
   - Note: synchronous method, NOT async
   - Must set ALL Chunk fields: `source_file`, `source_url`, `section_path`, `heading`, `heading_level`, `content`, `start_line`, `end_line`, `char_count`, `content_hash`, `category`
   - Category MUST be `""` (empty string) — core pipeline derives category
   - `content_hash` = `hashlib.sha256(content.encode()).hexdigest()`
   - The core pipeline handles: merge tiny chunks (< 500 chars), split mega chunks (> 2500 chars), dedup by hash, category derivation
   - Example: complete parser class
   - Reference: `MarkdownParser` for heading splitting, manifest loading

4. **Writing an embedder plugin**
   - Protocol properties: `model_name` (str), `dimensions` (int), `task_type_document` (str), `task_type_query` (str)
   - Protocol methods: `async embed_batch(self, texts: list[str]) -> list[list[float]]`, `async embed_query(self, query: str) -> list[float]`
   - Must NOT L2-normalize output — core pipeline handles normalization
   - Must NOT cache — core pipeline manages caching
   - Must NOT batch internally — core pipeline manages batching
   - `embed_batch` uses `task_type_document`, `embed_query` uses `task_type_query`
   - Return empty string for task types if not applicable
   - `dimensions` must match `DOC_HUB_VECTOR_DIM` deployment config
   - Example: complete embedder class
   - Reference: `GeminiEmbedder` for retry logic, lazy client init

5. **Registering via entry points (recommended)**
   - pyproject.toml entry point groups: `doc_hub.fetchers`, `doc_hub.parsers`, `doc_hub.embedders`
   - Format: `name = "package.module:ClassName"`
   - Classes must be instantiable with no args
   - Must reinstall package after changing entry points
   - Example pyproject.toml snippet

6. **Registering as a local plugin file**
   - Directory: `{data_root}/plugins/{fetchers,parsers,embedders}/*.py`
   - Files starting with `_` are skipped
   - Decorators: `@fetcher_plugin("name")`, `@parser_plugin("name")`, `@embedder_plugin("name")`
   - Import decorators from `doc_hub.discovery`
   - Entry points take precedence on name collision

7. **Testing plugins**
   - `isinstance()` check with `@runtime_checkable` protocol: `assert isinstance(MyPlugin(), Fetcher)`
   - Caveat: `isinstance` only checks method NAMES, not signatures — use mypy/pyright for full validation
   - Testing a fetcher: create temp dir, call `fetch()`, assert `.md` files written
   - Testing a parser: create temp dir with `.md` files, call `parse()`, assert Chunk list
   - Testing an embedder: mock API, call `embed_batch()`, check vector dimensions

**Key source files to reference:**
- `protocols.py` (all three protocols with full docstrings)
- `discovery.py` (`get_registry()`, `PluginRegistry`, entry point loading, local plugin loading, decorators)
- `_builtins/fetchers/llms_txt.py` (full fetcher implementation)
- `_builtins/fetchers/local_dir.py` (minimal fetcher)
- `_builtins/parsers/markdown.py` (full parser implementation)
- `_builtins/embedders/gemini.py` (full embedder implementation)
- `parse.py` (`Chunk` dataclass, `derive_category()`, `embedding_input()`)
- `embed.py` (`l2_normalize()`, cache logic)
- `docs/writing-fetchers.md` (existing fetcher guide — superseded by this doc)

**Specific items to cover:**
- `Fetcher.fetch()` full signature with all parameter types and return type
- `Parser.parse()` full signature — note `base_url` keyword-only arg
- `Embedder` has 4 properties + 2 methods
- `Chunk` dataclass all 11 fields with types
- `embedding_input()` format: `"Document: {doc_name} | Section: {section_path}\n\n{content}"`
- `derive_category()` rules: api, example, eval, guide, other
- `_merge_tiny_chunks()` threshold: 500 chars; `_split_mega_chunks()` threshold: 2500 chars, target: 1000 chars
- `l2_normalize()` uses numpy
- Embedding cache JSONL format: `{"content_hash": ..., "model": ..., "dimensions": ..., "embedding": [...]}`
- `_PLUGIN_ATTR = "_doc_hub_plugin"` attribute name for local plugin decorators
- `PluginRegistry.get_fetcher()`, `.get_parser()`, `.get_embedder()` raise `KeyError` with available names
- `reset_registry()` clears cached registry (for tests)

---

### 2.4 `docs/dev/protocols-reference.md`

**Audience:** Developer / AI agent

**Purpose:** Every protocol method, every parameter, every return type.

**Sections:**

1. **Overview**
   - Three `@runtime_checkable` protocols in `doc_hub.protocols`
   - Structural typing: plugins do NOT inherit from these classes
   - `isinstance()` checks method NAMES only, not signatures
   - Static type checkers enforce full conformance

2. **`Fetcher` protocol**
   - Method: `async def fetch(self, corpus_slug: str, fetch_config: dict[str, Any], output_dir: Path) -> Path`
   - Parameter `corpus_slug`: Unique corpus identifier, used for logging
   - Parameter `fetch_config`: Strategy-specific configuration dict from JSONB column
   - Parameter `output_dir`: Directory for fetched files; may not exist; fetcher creates it
   - Return: Path to directory containing `.md` files (may be `output_dir` or different path for `local_dir` style)
   - Contract summary

3. **`Parser` protocol**
   - Method: `def parse(self, input_dir: Path, *, corpus_slug: str, base_url: str) -> list[Chunk]`
   - SYNCHRONOUS (not async)
   - Parameter `input_dir`: Directory containing source files
   - Parameter `corpus_slug`: Corpus identifier for logging (keyword-only)
   - Parameter `base_url`: Base URL for reconstructing source URLs from filenames (keyword-only)
   - Return: List of `Chunk` objects with ALL 11 fields set
   - Chunk field requirements (all listed with types and constraints):
     - `source_file: str`
     - `source_url: str`
     - `section_path: str`
     - `heading: str`
     - `heading_level: int` (1-6, 0 for preamble)
     - `content: str`
     - `start_line: int` (1-indexed)
     - `end_line: int` (1-indexed, inclusive)
     - `char_count: int` = `len(content)`
     - `content_hash: str` = `hashlib.sha256(content.encode()).hexdigest()`
     - `category: str` — MUST be `""` (empty string)
   - What the core pipeline handles (not the parser): category derivation, merge tiny, split mega, dedup

4. **`Embedder` protocol**
   - Property `model_name -> str`: Unique model identifier, part of cache key
   - Property `dimensions -> int`: Output vector dimensionality (e.g. 768, 1536)
   - Property `task_type_document -> str`: Task type for document embedding (e.g. `"RETRIEVAL_DOCUMENT"`), empty string if N/A
   - Property `task_type_query -> str`: Task type for query embedding (e.g. `"RETRIEVAL_QUERY"`), empty string if N/A
   - Method: `async def embed_batch(self, texts: list[str]) -> list[list[float]]` — embed batch of texts, one vector per input, length == `dimensions`
   - Method: `async def embed_query(self, query: str) -> list[float]` — embed single query, uses `task_type_query`
   - What the core pipeline handles: caching, L2 normalization, batching, rate-limit pacing, writing JSONL

5. **Runtime checking caveats**
   - `@runtime_checkable` + `isinstance()` only verifies method/attribute existence
   - A class with `fetch(self)` (wrong arity) passes `isinstance()` but fails at call time
   - Use mypy/pyright for full conformance checking

**Key source files to reference:**
- `protocols.py` (the entire file — all three protocols with full docstrings)
- `parse.py` (`Chunk` dataclass definition)

**Specific items to cover:**
- Exact method signatures with type annotations (copy from source)
- All `Chunk` fields with their types and descriptions
- The `from __future__ import annotations` at top of protocols.py (PEP 604 unions)
- `from doc_hub.parse import Chunk` import in protocols.py
- `@runtime_checkable` decorator on each protocol
- Each protocol's full docstring content

---

### 2.5 `docs/dev/database-schema.md`

**Audience:** Developer / AI agent

**Purpose:** All tables, columns, indexes, constraints, DDL explained.

**Sections:**

1. **Overview**
   - Three tables: `doc_corpora`, `doc_chunks`, `doc_index_meta`
   - Extension: VectorChord (`vchord CASCADE` — installs pgvector as dependency)
   - Schema created idempotently by `ensure_schema()`

2. **`doc_corpora` table**
   - DDL (verbatim from `_CORPORA_DDL`)
   - Columns:
     - `slug text PRIMARY KEY`
     - `name text NOT NULL`
     - `fetch_strategy text NOT NULL`
     - `parser text NOT NULL DEFAULT 'markdown'`
     - `embedder text NOT NULL DEFAULT 'gemini'`
     - `fetch_config jsonb NOT NULL`
     - `enabled boolean DEFAULT true`
     - `last_indexed_at timestamptz`
     - `total_chunks int DEFAULT 0`
   - CRUD helpers: `get_corpus()`, `list_corpora()`, `upsert_corpus()`, `update_corpus_stats()`

3. **`doc_chunks` table**
   - DDL (verbatim from `_chunks_ddl()` with `{dim}` placeholder explained)
   - Columns:
     - `id serial PRIMARY KEY`
     - `corpus_id text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE`
     - `content_hash text NOT NULL`
     - `heading text NOT NULL`
     - `content text NOT NULL`
     - `tsv tsvector GENERATED ALWAYS AS (setweight(to_tsvector('english', heading), 'A') || setweight(to_tsvector('english', content), 'B')) STORED`
     - `embedding vector({dim}) NOT NULL`
     - `source_file text NOT NULL`
     - `source_url text NOT NULL`
     - `section_path text NOT NULL`
     - `heading_level smallint NOT NULL`
     - `start_line int NOT NULL DEFAULT 0`
     - `end_line int NOT NULL DEFAULT 0`
     - `char_count int NOT NULL`
     - `category text NOT NULL`
   - Constraints: `UNIQUE (corpus_id, content_hash)`
   - `ON DELETE CASCADE` from `doc_corpora` — deleting a corpus deletes all its chunks
   - Generated tsvector: heading weighted A, content weighted B

4. **`doc_index_meta` table**
   - DDL (verbatim from `_META_DDL`)
   - Columns:
     - `corpus_id text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE`
     - `key text NOT NULL`
     - `value text NOT NULL`
     - `updated_at timestamptz DEFAULT now()`
   - Primary key: `(corpus_id, key)`
   - Keys written by `_write_meta()`: `last_indexed_at`, `total_chunks`, `embedding_model`, `embedding_dimensions`

5. **Indexes**
   - `doc_chunks_corpus_id_idx` — B-tree on `corpus_id`
   - `doc_chunks_corpus_tsv_idx` — GIN on `tsv`
   - `doc_chunks_corpus_category_idx` — B-tree on `(corpus_id, category)`
   - `doc_chunks_corpus_hash_idx` — B-tree on `(corpus_id, content_hash)`
   - `doc_chunks_source_url_idx` — `text_pattern_ops` on `source_url` (LIKE prefix optimization)
   - `doc_chunks_section_path_idx` — `text_pattern_ops` on `section_path`
   - `doc_chunks_heading_level_idx` — B-tree on `heading_level`
   - Note: GIN indexes do NOT support composite keys — separate B-tree on corpus_id for scoped FTS

6. **Vector dimension configuration**
   - `DOC_HUB_VECTOR_DIM` env var (default 768) controls `vector({dim})` column
   - `get_vector_dim()` reads and validates
   - `ensure_schema()` checks existing dimension vs configured and raises RuntimeError on mismatch
   - Fix options: change env var to match existing, or DROP TABLE and re-index

7. **JSONB codec**
   - asyncpg does NOT auto-serialize Python dicts to/from JSONB
   - `_init_connection()` registers custom codec per connection via `set_type_codec("jsonb", ...)`
   - `upsert_corpus()` also passes `json.dumps()` as belt-and-suspenders safety

8. **Advisory locks**
   - `pg_advisory_xact_lock(hashtext(slug))` during index operations
   - Prevents concurrent indexing of the same corpus
   - Transaction-scoped — released on commit/rollback

**Key source files to reference:**
- `db.py` (all DDL constants, `_chunks_ddl()`, `ensure_schema()`, `_init_connection()`, CRUD helpers)
- `index.py` (upsert SQL, advisory lock, `_write_meta()`)

**Specific items to cover:**
- Verbatim DDL for all three tables
- Verbatim DDL for all seven indexes
- `_chunks_ddl()` is a function (not constant) because vector dimension is configurable
- `CREATE EXTENSION IF NOT EXISTS vchord CASCADE`
- asyncpg pool config: `min_size=1, max_size=10`
- The `xmax = 0` trick in RETURNING clause to distinguish INSERT vs UPDATE
- `_parse_command_count()` parses asyncpg status strings like `'DELETE 5'`

---

### 2.6 `docs/dev/testing-guide.md`

**Audience:** Developer / AI agent

**Purpose:** How to run tests, what markers exist, how to mock, how to test plugins.

**Sections:**

1. **Running tests**
   - `uv run --package doc-hub pytest packages/doc-hub/tests/`
   - Integration tests: `pytest -m integration` (requires live DB + GEMINI_API_KEY)
   - Excluding integration: `pytest -m "not integration"`

2. **Test configuration**
   - `asyncio_mode = "auto"` — all async test functions auto-detected
   - Marker: `integration` — "requires live DB and GEMINI_API_KEY"
   - Dev dependencies: `pytest>=8.0`, `pytest-asyncio>=0.24`, `ruff>=0.9`

3. **Test markers**
   - `@pytest.mark.integration` — tests that need a running PostgreSQL + VectorChord and `GEMINI_API_KEY`
   - Unmarked tests should run without external dependencies

4. **Mocking the database**
   - Mock `asyncpg.Pool` and `asyncpg.Connection` for unit tests
   - For integration tests: use real DB via `create_pool()` + `ensure_schema()`
   - `reset_registry()` to clear cached plugin registry between tests

5. **Mocking the embedder**
   - Create a mock class matching the `Embedder` protocol
   - Must implement: `model_name`, `dimensions`, `task_type_document`, `task_type_query`, `embed_batch()`, `embed_query()`
   - Return fixed vectors of correct dimensionality
   - Example mock embedder class

6. **Mocking fetchers**
   - Create temp directory with `.md` files
   - Mock `get_registry()` to return a fetcher that copies files to output_dir
   - Or use `LocalDirFetcher` with a temp path

7. **Testing new plugins**
   - Fetcher: create temp dir, call `fetch()`, assert `.md` files and optional `manifest.json`
   - Parser: create temp dir with `.md` content, call `parse()`, validate Chunk fields
   - Embedder: mock API client, call `embed_batch()`, check output dimensions and count
   - Protocol conformance: `assert isinstance(MyPlugin(), Protocol)`

8. **Integration test requirements**
   - PostgreSQL with VectorChord running and accessible
   - Environment variables set: `PGPASSWORD` (or `DOC_HUB_DATABASE_URL`), `GEMINI_API_KEY`
   - Tests may create/drop tables and insert data

**Key source files to reference:**
- `pyproject.toml` (`[tool.pytest.ini_options]`, `[dependency-groups]`)
- `discovery.py` (`reset_registry()`)
- `protocols.py` (for mock implementation reference)
- `_builtins/embedders/gemini.py` (for mock embedder example)
- `_builtins/fetchers/local_dir.py` (for simple test fetcher)

**Specific items to cover:**
- `asyncio_mode = "auto"` from pytest config
- `markers = ["integration: requires live DB and GEMINI_API_KEY"]`
- `reset_registry()` — clears global `_registry` singleton
- `get_vector_dim()` reads `DOC_HUB_VECTOR_DIM` — set in test env to match mock embedder dimensions
- Mock embedder must return vectors of length == `dimensions` property
- `Chunk.category` must be `""` when returned from parser (core pipeline fills it)
- `l2_normalize()` imported from `doc_hub.embed`

---

### 2.7 `docs/dev/search-internals.md`

**Audience:** Developer / AI agent

**Purpose:** How hybrid search works: vector KNN, BM25, RRF fusion, scoring.

**Sections:**

1. **Overview**
   - Hybrid search combining two retrieval methods
   - Vector KNN for semantic similarity
   - BM25 full-text search for keyword matching
   - Reciprocal Rank Fusion (RRF) to merge ranked lists

2. **Query embedding**
   - `_embed_query_async()` resolves embedder from registry (default: "gemini")
   - Uses `embed_query()` with `task_type_query` (vs `task_type_document` for indexing)
   - Result is L2-normalized via `l2_normalize()`
   - Cross-corpus search requires all corpora to use the same embedder

3. **Vector KNN search (CTE: `vector_results`)**
   - `SELECT ... ORDER BY embedding <=> $1::vector LIMIT {vector_limit}`
   - `<=>` is cosine distance operator (VectorChord/pgvector)
   - `1 - (embedding <=> $1::vector)` gives cosine similarity
   - `ROW_NUMBER()` assigns `vec_rank`
   - Default `vector_limit = 20` (candidate pool size)

4. **BM25 full-text search (CTE: `text_results`)**
   - `websearch_to_tsquery('{language}', $2)` converts query text to tsquery
   - `tsv @@ query` matches against the generated tsvector column
   - `ts_rank(tsv, query)` scores by BM25-like ranking
   - Weighted tsvector: heading = weight A, content = weight B
   - `ROW_NUMBER()` assigns `text_rank`
   - Default `text_limit = 10` (candidate pool size)

5. **Reciprocal Rank Fusion (RRF)**
   - `FULL OUTER JOIN vector_results v ON text_results t ON v.id = t.id`
   - RRF score = `COALESCE(1.0/(k + vec_rank), 0) + COALESCE(1.0/(k + text_rank), 0)`
   - Default `k = 60` (RRF constant)
   - Results ordered by `rrf_score DESC`

6. **Filters**
   - All filters use NULL-propagation pattern: `($N::type IS NULL OR column op $N)`
   - `corpus_id` filter ($3): scope to one corpus
   - `categories` include ($4): `category = ANY($4::text[])`
   - `exclude_categories` ($5): `category != ALL($5::text[])`
   - `source_url_prefix` ($6): `source_url LIKE $6 || '%' ESCAPE '\'`
   - `section_path_prefix` ($7): `section_path LIKE $7 || '%' ESCAPE '\'`
   - LIKE metacharacters escaped via `_escape_like()` (escapes `\`, `%`, `_`)

7. **Post-filtering**
   - `min_similarity` threshold applied in Python AFTER SQL execution (NOT in WHERE clause)
   - Reason: text-only results have `vec_similarity = 0` and would be incorrectly excluded
   - Default `min_similarity = 0.55`

8. **`SearchConfig` dataclass**
   - `vector_limit: int = 20`
   - `text_limit: int = 10`
   - `rrfk: int = 60`
   - `language: str = "english"`
   - Language validated against `VALID_PG_LANGUAGES` whitelist (29 PostgreSQL text-search configs)
   - SQL injection prevention: language is interpolated via f-string but validated

9. **`SearchResult` dataclass**
   - Fields: `id`, `corpus_id`, `heading`, `section_path`, `content`, `source_url`, `score` (RRF), `similarity` (cosine), `category`, `start_line`, `end_line`

10. **Sync wrapper**
    - `search_docs_sync()` wraps with `asyncio.run()` — only for non-async contexts (CLI)
    - Raises `RuntimeError` if called from within running event loop

**Key source files to reference:**
- `search.py` (entire file — `_build_hybrid_sql()`, `search_docs()`, `_embed_query_async()`, `SearchConfig`, `SearchResult`, `VALID_PG_LANGUAGES`, `_escape_like()`)
- `db.py` (tsvector DDL in `_chunks_ddl()`, GIN index)
- `embed.py` (`l2_normalize()`)

**Specific items to cover:**
- The full SQL template from `_build_hybrid_sql()` (verbatim)
- Bind parameter numbering: $1 through $9 with their types and meanings
- `VALID_PG_LANGUAGES` frozenset (29 entries — list all of them)
- `_escape_like()` escapes `\` → `\\`, `%` → `\%`, `_` → `\_`
- `vec_similarity` computation: `1 - (embedding <=> $1::vector)`
- Weighted tsvector: `setweight(to_tsvector('english', heading), 'A') || setweight(to_tsvector('english', content), 'B')`
- `FULL OUTER JOIN` ensures results from either method appear
- `COALESCE` handles NULL ranks (result only in one method)
- Pagination: `LIMIT $8 OFFSET $9`
- `search_docs()` full parameter list with types and defaults

---

## Document Dependency Order

Writing agents should produce documents in this order to maximize cross-references:

1. `docs/dev/database-schema.md` (foundational — referenced by many others)
2. `docs/dev/protocols-reference.md` (foundational — referenced by plugin authoring)
3. `docs/dev/search-internals.md` (self-contained)
4. `docs/dev/plugin-authoring.md` (references protocols + schema)
5. `docs/dev/testing-guide.md` (references all plugin types)
6. `ARCHITECTURE.md` (references all dev docs)
7. `AGENTS.md` (index pointing to all dev docs)
8. `docs/user/configuration.md` (foundational for user docs)
9. `docs/user/getting-started.md` (references configuration)
10. `docs/user/cli-reference.md` (standalone)
11. `docs/user/mcp-server.md` (references CLI)
12. `docs/user/evaluation.md` (references CLI)
13. `docs/user/cloud-database.md` (references configuration + schema)
