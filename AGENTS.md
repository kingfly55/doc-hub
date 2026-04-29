# doc-hub

Multi-corpus documentation search engine. Fetches, parses, embeds, and indexes documentation into PostgreSQL (pgvector + BM25), then exposes hybrid search via an MCP server.

Build backend: `hatchling` with `packages = ["src/doc_hub"]`.

## Module Map

| Module | Responsibility |
|---|---|
| `protocols.py` | `@runtime_checkable` plugin protocols: `Fetcher`, `Parser`, `Embedder` |
| `discovery.py` | Plugin registry; entry point + local `{data_root}/plugins/` file discovery |
| `models.py` | `Corpus` dataclass; `Corpus.from_row()` constructs from asyncpg Record or dict |
| `paths.py` | XDG-aware data directory resolution (`get_data_root() -> Path`) |
| `db.py` | asyncpg pool creation, DDL, JSONB codec registration, CRUD helpers |
| `fetchers.py` | Fetcher dispatch — looks up plugin by `corpus.fetch_strategy`, calls `fetch()` |
| `parse.py` | `Chunk` dataclass; parse pipeline: size optimization, dedup by hash, category derivation |
| `embed.py` | `EmbeddedChunk`; embedding cache keyed by `(content_hash, model, dimensions)`; L2 normalization; batch orchestration |
| `index.py` | PostgreSQL upsert with advisory locks; `IndexResult` |
| `search.py` | Hybrid search: vector KNN + BM25 + RRF (k=60); `SearchResult`, `SearchConfig` |
| `pipeline.py` | Full pipeline orchestration: fetch → parse → embed → index; `sync_all` |
| `mcp_server.py` | FastMCP server with 4 tools; `AppState` lifespan |
| `eval.py` | Retrieval evaluation: P@N, MRR; `TestQuery`, `EvalReport` |
| `_builtins/` | Built-in plugins — fetchers: `llms_txt`, `local_dir`, `sitemap`, `git_repo`; parsers: `markdown`; embedders: `gemini` |

## Quick Reference

```bash
# Tests (unit)
pytest tests/

# Tests (integration — requires live DB + GEMINI_API_KEY)
pytest tests/ -m integration

# Lint
ruff check src/

# CLI entry points
doc-hub pipeline run --corpus <slug> [--stage fetch|parse|embed|index|tree] [--clean] [--skip-download] [--full-reindex]
doc-hub docs list --json
doc-hub docs search --corpus <slug> "<query>" --json --schema v2
doc-hub docs browse <slug> --json
doc-hub docs read <slug> <doc_id> --json
doc-hub serve mcp          # start MCP server
doc-hub pipeline eval      # run retrieval eval
doc-hub pipeline sync-all  # index all enabled corpora
```

pytest config: `asyncio_mode = "auto"` — all async tests run automatically.

## Entry Point Groups

| Group | Purpose |
|---|---|
| `doc_hub.fetchers` | Fetcher plugins |
| `doc_hub.parsers` | Parser plugins |
| `doc_hub.embedders` | Embedder plugins |

## Key Conventions

- All async I/O uses `asyncpg` (not psycopg).
- Plugin discovery: `importlib.metadata` entry points (primary) + local `{data_root}/plugins/*.py` files (secondary).
- `@runtime_checkable` protocols — `isinstance()` checks method **names only**, not signatures. Static type checkers (mypy/pyright) enforce full conformance.
- Structural typing: plugins do NOT inherit from protocol classes.
- Embedders must NOT cache, normalize, or batch internally — the core pipeline owns all of that.
- Parsers must NOT derive category — set `category = ""` and let the pipeline handle it.

## Deep Dives

- [Architecture](ARCHITECTURE.md) — system design, data flow, DB schema overview
- [Plugin Authoring](docs/dev/plugin-authoring.md) — how to write and register Fetcher/Parser/Embedder plugins
- [Protocol Reference](docs/dev/protocols-reference.md) — full method signatures and contracts
- [Database Schema](docs/dev/database-schema.md) — table definitions, indexes, JSONB columns
- [Testing Guide](docs/dev/testing-guide.md) — unit vs integration, fixtures, markers
- [Search Internals](docs/dev/search-internals.md) — RRF algorithm, BM25 config, vector index tuning

## Execution Plans & Scripts

- [Plugin Architecture Plan](docs/exec-plans/completed/plugin-architecture/plan.md) — completed plan for the plugin architecture transformation (milestones 1–8)
- `scripts/pipeline.py` — adversarial implementation pipeline (plan → refine → implement)
- `scripts/doc-pipeline.py` — documentation generation pipeline (plan → write)

## Where to Look

| Task | File |
|---|---|
| Add a new fetcher plugin | `protocols.py` (contract), `_builtins/fetchers/` (examples), `docs/dev/plugin-authoring.md` |
| Add a new embedder | `protocols.py` (contract), `_builtins/embedders/gemini.py` (example) |
| Change chunking logic | `parse.py` |
| Change search ranking | `search.py` (`SearchConfig`, RRF weights) |
| Add an MCP tool | `mcp_server.py` |
| Modify DB schema | `db.py` (DDL strings) |
| Add a corpus | Insert row into `doc_corpora` via `db.py` CRUD helpers |
| Tune embedding batch size | `embed.py` |
| Add eval queries | `eval.py` (`TestQuery`) |
| Understand why architecture was designed this way | `docs/exec-plans/completed/plugin-architecture/` |
| Run the implementation pipeline | `scripts/pipeline.py --help` |
| Regenerate documentation | `scripts/doc-pipeline.py --help` |
