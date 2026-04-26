# doc-hub Architecture

Multi-corpus documentation search engine. Indexes docs from any source into
PostgreSQL, then serves hybrid semantic + keyword search via an MCP server.

---

## 1. Domain Overview

```
Source docs  →  Fetcher plugin  →  Parser plugin  →  Embedder plugin  →  PostgreSQL  →  Search
```

**Pipeline stages** (in order):
1. **fetch** — download/locate source files, write `.md` files to `raw/`
2. **parse** — split files into `Chunk` objects, write `chunks.jsonl`
3. **embed** — embed chunks into vectors, write `embedded_chunks.jsonl`
4. **index** — upsert embedded chunks into `doc_chunks` (PostgreSQL)
5. **search** — hybrid KNN + BM25 + RRF over indexed chunks

**Plugin points**: Fetcher, Parser, Embedder — each is a pluggable, discoverable class.

**MCP server**: Exposes four tools (`search_docs_tool`, `list_corpora_tool`, `add_corpus_tool`, `refresh_corpus_tool`) to LLMs via the Model Context Protocol.

---

## 2. Data Flow Diagram

```
  ┌──────────────┐
  │  Source docs │  (web, git repo, local dir, llms.txt)
  └──────┬───────┘
         │
         ▼  [fetchers.py → Fetcher plugin]
  ┌──────────────────────────────────┐
  │  {data_root}/{slug}/raw/*.md     │  raw files + manifest.json
  └──────┬───────────────────────────┘
         │
         ▼  [parse.py → Parser plugin → size optimization → dedup → category]
  ┌──────────────────────────────────────────────────────┐
  │  {data_root}/{slug}/chunks/chunks.jsonl              │  list[Chunk]
  └──────┬───────────────────────────────────────────────┘
         │
         ▼  [embed.py → Embedder plugin → l2_normalize → cache]
  ┌──────────────────────────────────────────────────────┐
  │  {data_root}/{slug}/chunks/embedded_chunks.jsonl     │  list[EmbeddedChunk]
  │  {data_root}/{slug}/chunks/embeddings_cache.jsonl    │  cache (keyed by hash+model+dims)
  └──────┬───────────────────────────────────────────────┘
         │
         ▼  [index.py → upsert_chunks → pg_advisory_xact_lock]
  ┌──────────────────────────────────────────────────────┐
  │  PostgreSQL: doc_chunks (vector + tsvector)          │
  │              doc_corpora (registry)                  │
  │              doc_index_meta (key/value metadata)     │
  └──────┬───────────────────────────────────────────────┘
         │
         ▼  [search.py → embed query → vector KNN + BM25 → RRF]
  ┌──────────────────┐
  │  list[SearchResult]  │
  └──────────────────┘
         │
         ▼  [mcp_server.py → FastMCP tools]
  ┌──────────────────┐
  │  LLM / Client    │
  └──────────────────┘
```

---

## 3. Module Map

| Module | Path | Responsibility |
|---|---|---|
| `pipeline.py` | `src/doc_hub/pipeline.py` | Orchestrator: `run_fetch`, `run_parse`, `run_embed`, `run_index`, `run_pipeline`, `sync_all` |
| `mcp_server.py` | `src/doc_hub/mcp_server.py` | FastMCP server: 4 tools, lifespan pool management |
| `search.py` | `src/doc_hub/search.py` | Hybrid search: `search_docs()`, `SearchConfig`, `SearchResult`, RRF SQL |
| `eval.py` | `src/doc_hub/eval.py` | Retrieval quality eval: P@5, MRR |
| `index.py` | `src/doc_hub/index.py` | DB upsert: `upsert_chunks()`, advisory locks, `IndexResult` |
| `embed.py` | `src/doc_hub/embed.py` | Embedding orchestration: `embed_chunks()`, `l2_normalize()`, JSONL cache |
| `parse.py` | `src/doc_hub/parse.py` | Parse pipeline: `parse_docs()`, `Chunk` dataclass, `derive_category()`, merge/split |
| `fetchers.py` | `src/doc_hub/fetchers.py` | Fetch dispatch: routes to Fetcher plugin by name |
| `db.py` | `src/doc_hub/db.py` | DB pool, schema DDL, CRUD helpers, JSONB codec |
| `models.py` | `src/doc_hub/models.py` | `Corpus` dataclass |
| `protocols.py` | `src/doc_hub/protocols.py` | `Fetcher`, `Parser`, `Embedder` protocols (`@runtime_checkable`) |
| `discovery.py` | `src/doc_hub/discovery.py` | Plugin discovery: entry points + local files, `PluginRegistry`, `get_registry()` |
| `paths.py` | `src/doc_hub/paths.py` | Path resolution: `data_root()`, `raw_dir()`, `chunks_dir()`, etc. |
| `_builtins/` | `src/doc_hub/_builtins/` | Built-in plugins: `LlmsTxtFetcher`, `LocalDirFetcher`, `SitemapFetcher`, `GitRepoFetcher`, `MarkdownParser`, `GeminiEmbedder` |

---

## 4. Module Dependency Graph

```
                        ┌─────────────────────────────────┐
                        │  INTERFACE LAYER                │
                        │                                 │
                  ┌─────┴──────┐    ┌─────────────┐      │
                  │ pipeline.py│    │ mcp_server.py│      │
                  └─────┬──────┘    └──────┬──────┘      │
                        │                  │              │
              ┌─────────┴───────┐   ┌──────┴─────┐       │
              │                 │   │            │        │
              ▼                 ▼   ▼            ▼        │
         fetchers.py         parse.py        search.py  eval.py
              │               │    │            │         │
              │               ▼    ▼            │         │
              │           paths.py  embed.py ◄──┘         │
              │                       │                   │
              │               ┌───────┴───────────┐       │
              │               ▼                   ▼       │
              │           index.py            db.py ◄─────┘
              │               │
              │               ▼
              │           models.py
              │
              ▼
          discovery.py
              │
              ▼
          protocols.py
              │
              ▼
           parse.py  (for Chunk type)
```

**Precise import chains** (top-level imports only; lazy imports noted separately):

| Module | Imports from doc_hub |
|---|---|
| `pipeline.py` | `fetchers`, `models`, `paths` (top-level); `parse`, `embed`, `db`, `index`, `discovery` (lazy) |
| `mcp_server.py` | `db`, `pipeline`, `search`, `models` |
| `search.py` | `asyncpg` (top-level); `embed` (l2_normalize), `discovery` (lazy) |
| `eval.py` | `db`, `search` |
| `index.py` | `db` (update_corpus_stats), `embed` (EmbeddedChunk), `models` (Corpus) |
| `embed.py` | `parse` (Chunk, embedding_input), `paths`; `db` (get_vector_dim, lazy) |
| `parse.py` | `paths`; `discovery` (lazy inside parse_docs) |
| `fetchers.py` | `discovery` (get_registry) |
| `discovery.py` | `protocols` (Fetcher, Parser, Embedder) |
| `protocols.py` | `parse` (Chunk) |
| `db.py` | _(no doc_hub imports)_ |
| `models.py` | _(no doc_hub imports)_ |
| `paths.py` | `models` (TYPE_CHECKING only) |

---

## 5. Layer Rules

Four layers with strict import rules:

### Foundation Layer
`protocols.py`, `models.py`, `paths.py`, `db.py`, `discovery.py`

- Minimal cross-dependencies.
- `protocols.py` imports only `parse.py` (for `Chunk` type).
- `discovery.py` imports `protocols.py`.
- `paths.py` and `db.py` have no doc_hub imports.
- **MUST NOT** import: `pipeline.py`, `mcp_server.py`, `search.py`, `eval.py`.

### Plugin Layer
`_builtins/`, external plugin packages

- **MAY import**: `protocols.py` (for protocol reference), `parse.py` (for `Chunk`), `discovery.py` (for decorators).
- **MUST NOT import**: `db.py`, `index.py`, `pipeline.py`.
- Plugins must be instantiable with no args.

### Core Pipeline Layer
`parse.py`, `embed.py`, `index.py`, `fetchers.py`

- **MAY import**: `protocols.py`, `discovery.py`, `models.py`, `paths.py`, `db.py`.
- **MUST NOT import**: `mcp_server.py`, `search.py`, `eval.py`.

### Interface Layer
`search.py`, `mcp_server.py`, `eval.py`, `pipeline.py`

- **MAY import**: anything in core, foundation, or plugin layers.
- `pipeline.py` is the orchestrator — it calls `run_fetch`, `run_parse`, `run_embed`, `run_index` in sequence.
- `mcp_server.py` uses `FastMCP` lifespan for shared `asyncpg.Pool`; does not hold a shared embedder.

---

## 6. Plugin Boundary

### Three Plugin Points

| Plugin type | Protocol | Entry point group | Method signature |
|---|---|---|---|
| Fetcher | `doc_hub.protocols.Fetcher` | `doc_hub.fetchers` | `async def fetch(self, corpus_slug, fetch_config, output_dir) -> Path` |
| Parser | `doc_hub.protocols.Parser` | `doc_hub.parsers` | `def parse(self, input_dir, *, corpus_slug, base_url) -> list[Chunk]` |
| Embedder | `doc_hub.protocols.Embedder` | `doc_hub.embedders` | `async def embed_batch(texts) -> list[list[float]]`, `async def embed_query(query) -> list[float]` |

### What Plugins Do vs. What Core Handles

**Plugins are responsible for:**
- Fetcher: fetching/locating source files, writing `.md` files to `output_dir`, writing optional `manifest.json`.
- Parser: converting `.md` files to raw `Chunk` objects with core content fields set; `category` MUST be `""`. Snapshot/version provenance is filled from manifest metadata by the pipeline when absent.
- Embedder: calling the embedding API, returning raw (non-normalized) vectors.

**Core pipeline handles (plugins must NOT do these):**
- `parse.py`: merge tiny chunks (< 500 chars), split mega chunks (> 2500 chars), dedup by content hash, category derivation via `derive_category()`.
- `embed.py`: JSONL cache (keyed by `content_hash + model_name + dimensions`), L2 normalization via `l2_normalize()`, batch orchestration, inter-batch sleep for rate limits.

### Discovery Mechanism

1. **Entry points** (primary): loaded via `importlib.metadata.entry_points(group=...)`.
2. **Local plugin files** (secondary): scanned from `{data_root}/plugins/{fetchers,parsers,embedders}/*.py`.
3. On name collision, entry point wins.
4. Plugins validated via `isinstance(instance, Protocol)` at registration (checks method names, not signatures).
5. Registry cached as global `_registry`; call `reset_registry()` to clear (for tests).

**Built-in entry points** (from `pyproject.toml`):
```toml
[project.entry-points."doc_hub.fetchers"]
llms_txt  = "doc_hub._builtins.fetchers.llms_txt:LlmsTxtFetcher"
local_dir = "doc_hub._builtins.fetchers.local_dir:LocalDirFetcher"
sitemap   = "doc_hub._builtins.fetchers.sitemap:SitemapFetcher"
git_repo  = "doc_hub._builtins.fetchers.git_repo:GitRepoFetcher"

[project.entry-points."doc_hub.parsers"]
markdown = "doc_hub._builtins.parsers.markdown:MarkdownParser"

[project.entry-points."doc_hub.embedders"]
gemini = "doc_hub._builtins.embedders.gemini:GeminiEmbedder"
```

---

## 7. Data Stores

### Filesystem (`paths.py`)

Data root resolution order (from `data_root()`):
1. `DOC_HUB_DATA_DIR` env var
2. `$XDG_DATA_HOME/doc-hub`
3. `~/.local/share/doc-hub`

```
{data_root}/
  {slug}/
    raw/               # Legacy fetched .md files + manifest.json
    chunks/            # Legacy chunks/embeddings cache
    versions/
      {snapshot_id}/
        raw/               # Versioned .md files + manifest.json
        chunks/
          chunks.jsonl
          embedded_chunks.jsonl
          embeddings_cache.jsonl
  plugins/
    fetchers/*.py      # Local fetcher plugin files
    parsers/*.py       # Local parser plugin files
    embedders/*.py     # Local embedder plugin files
```

### PostgreSQL

Five core tables, created idempotently by `ensure_schema()` (`db.py`):

| Table | Purpose |
|---|---|
| `doc_corpora` | Corpus registry: slug, name, strategy, parser, embedder, fetch_config (JSONB), enabled |
| `doc_versions` | Immutable documentation snapshots per corpus, including source version, snapshot hash, fetch provenance, and indexed chunk count |
| `doc_version_aliases` | Mutable aliases such as `latest` pointing to a corpus snapshot |
| `doc_chunks` | All indexed chunks: content, heading, embedding `vector({dim})`, tsv tsvector, corpus_id FK, snapshot/version metadata |
| `doc_index_meta` | Per-corpus key/value: last_indexed_at, total_chunks, embedding_model, embedding_dimensions |

Extension: `CREATE EXTENSION IF NOT EXISTS vchord CASCADE` (installs pgvector as dependency).

Vector dimension: `DOC_HUB_VECTOR_DIM` env var (default: 768). `_chunks_ddl()` is a function (not a constant) because the dimension is injected at runtime. `ensure_schema()` checks that the existing column dimension matches; raises `RuntimeError` on mismatch.

Full DDL: see [`docs/dev/database-schema.md`](docs/dev/database-schema.md).

### Embedding Cache JSONL

Per-corpus file at `{data_root}/{slug}/chunks/embeddings_cache.jsonl`.

Each line:
```json
{"content_hash": "<sha256>", "model": "<model_name>", "dimensions": 768, "embedding": [0.1, ...]}
```

Cache key is `(content_hash, model, dimensions)`. Changing `model_name` or `dimensions` invalidates existing cache entries (stale entries are silently skipped on load).

---

## 8. Key Data Types

Data flows through these types in order:

```
Corpus (models.py)
  └─ loaded from doc_corpora at pipeline start

DocVersion (versions.py)
  └─ immutable snapshot metadata: corpus_id, snapshot_id, source_version, content hash, fetched_at

Chunk (parse.py)
  ├─ produced by Parser plugin + parse.py post-processing
  ├─ source_file, source_url, section_path, heading, heading_level,
  │  content, start_line, end_line, char_count, content_hash, category,
  │  snapshot_id, source_version, fetched_at
  └─ written to chunks.jsonl

EmbeddedChunk (embed.py)
  ├─ Chunk fields + embedding: list[float] (L2-normalized)
  └─ written to embedded_chunks.jsonl with snapshot/version metadata

IndexResult (index.py)
  ├─ returned by upsert_chunks()
  └─ fields: inserted, updated, deleted, total

SearchResult (search.py)
  ├─ returned by search_docs()
  └─ fields: id, corpus_id, heading, section_path, content, source_url,
             score (RRF), similarity (cosine), category, start_line, end_line,
             source_file, doc_path, snapshot_id, source_version
```

**`embedding_input()` format** (`parse.py`):
```
"Document: {doc_name} | Section: {section_path}\n\n{content}"
```
where `doc_name` replaces `__` with `/` and strips `.md`. This prefix is critical for embedding quality — it must match exactly between indexing and query time.

---

## 9. Concurrency Model

- **All I/O is async** (`asyncio` throughout; no sync DB calls in the pipeline).
- **DB driver**: `asyncpg` (not psycopg). Pool: `min_size=1, max_size=10`.
- **JSONB codec**: registered per-connection via `_init_connection()` callback — asyncpg does not auto-serialize Python dicts to JSONB.
- **Advisory lock**: `pg_advisory_xact_lock(hashtext(slug))` inside `upsert_chunks()` — transaction-scoped, prevents concurrent indexing of the same corpus.
- **Download concurrency**: bounded by semaphore; controlled by `workers` parameter (default: `DEFAULT_WORKERS = 20`).
- **Embed batching**: `BATCH_SIZE = 100` items per API call (Gemini max); sliding-window rate limiter tracks RPM and TPM over 60s windows — no fixed sleep. Configure via `DOC_HUB_EMBED_RPM` (default: 80) and `DOC_HUB_EMBED_TPM` (default: 200000).
- **`sync_all()`**: iterates enabled corpora sequentially; per-corpus errors are caught and logged — one failed corpus does not stop the rest.

---

## 10. CLI Entry Points

Defined in `pyproject.toml` `[project.scripts]`:

| Command | Entry point | Description |
|---|---|---|
| `doc-hub` | `doc_hub.cli.main:main` | Unified CLI for docs, pipeline, and serve operations |

---

## 11. Further Reading

- [`docs/dev/database-schema.md`](docs/dev/database-schema.md) — full DDL, indexes, constraints, advisory locks
- [`docs/dev/protocols-reference.md`](docs/dev/protocols-reference.md) — all protocol method signatures and Chunk fields
- [`docs/dev/plugin-authoring.md`](docs/dev/plugin-authoring.md) — how to write and register fetcher/parser/embedder plugins
- [`docs/dev/search-internals.md`](docs/dev/search-internals.md) — hybrid SQL, RRF, bind parameters
- [`docs/dev/testing-guide.md`](docs/dev/testing-guide.md) — pytest markers, mocking, integration tests
