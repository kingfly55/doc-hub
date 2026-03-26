# Getting Started with doc-hub

This guide walks you from zero to your first successful search query.

---

## 1. Prerequisites

- **Python >= 3.11**
- **uv** (recommended) or **pip**
- **Docker** (for running PostgreSQL with VectorChord)
- **Gemini API key** — free tier works; get one at [Google AI Studio](https://aistudio.google.com/apikey)

---

## 2. Install doc-hub

doc-hub is installed from GitHub (not published to PyPI). Choose whichever method fits your workflow:

### Option A: Install as an isolated CLI tool

Puts all `doc-hub-*` commands on your PATH without managing a virtual environment:

```bash
# Using uv (recommended)
uv tool install git+https://github.com/kingfly55/doc-hub.git

# Or using pipx
pipx install git+https://github.com/kingfly55/doc-hub.git
```

### Option B: Install from a local clone (recommended for development)

```bash
git clone https://github.com/kingfly55/doc-hub.git && cd doc-hub
uv sync            # creates .venv and installs all dependencies (including dev)
source .venv/bin/activate
```

The CLI scripts are available whenever the virtual environment is active.

### Option C: Run without installing

```bash
uvx --from git+https://github.com/kingfly55/doc-hub.git doc-hub-search --help
```

### Verify installation

```bash
doc-hub-search --help
```

This confirms all five console scripts are available:

| Script | Description |
|--------|-------------|
| `doc-hub-pipeline` | Run the fetch → parse → embed → index pipeline for a corpus |
| `doc-hub-search` | Hybrid search CLI |
| `doc-hub-mcp` | Start the MCP server |
| `doc-hub-eval` | Evaluate retrieval quality |
| `doc-hub-sync-all` | Run the pipeline for all enabled corpora |

---

## 3. Start PostgreSQL with VectorChord

doc-hub requires PostgreSQL with the [VectorChord](https://github.com/tensorchord/VectorChord) extension. The easiest way is the official Docker image:

```bash
docker run -d \
  --name vchord-postgres \
  -e POSTGRES_PASSWORD=mysecretpassword \
  -p 5433:5432 \
  tensorchord/vchord-postgres:latest
```

This maps **host port 5433** to **container port 5432**. When you set `PGPORT` for this configuration, use `5433` (see the env table below).

The VectorChord extension itself does **not** need to be installed manually. When doc-hub connects on first use, `ensure_schema()` runs `CREATE EXTENSION IF NOT EXISTS vchord CASCADE` automatically, along with creating all required tables and indexes.

---

## 4. Configure Environment Variables

### Minimum required

```bash
export PGPASSWORD=mysecretpassword   # must match -e POSTGRES_PASSWORD above
export GEMINI_API_KEY=your-key-here
```

`PGPASSWORD` has **no default**. If it is not set, doc-hub raises:

```
RuntimeError: PGPASSWORD environment variable not set.
Set it directly or use DOC_HUB_DATABASE_URL for the full connection string.
```

`GEMINI_API_KEY` is required for the embed and search stages. It is read directly by the Gemini client.

### Recommended: `.env` file

Create a `.env` file at the repo root. All doc-hub commands load it automatically via `python-dotenv`:

```dotenv
GEMINI_API_KEY=your-key-here
PGHOST=localhost
PGPORT=5433
PGUSER=postgres
PGPASSWORD=mysecretpassword
PGDATABASE=postgres
```

### Full environment variable reference

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | Required for embedding and search |
| `DOC_HUB_DATABASE_URL` | — | Full PostgreSQL connection string; overrides all `PG*` vars |
| `PGHOST` | `localhost` | PostgreSQL host |
| `PGPORT` | `5432` | PostgreSQL port (use `5433` if using the Docker example above) |
| `PGDATABASE` | `doc_hub` | Database name |
| `PGUSER` | `postgres` | Database user |
| `PGPASSWORD` | **no default** | Database password — must be set |

**Connection string resolution order** (implemented in `_build_dsn()`):

1. Explicit `dsn` argument passed in code
2. `DOC_HUB_DATABASE_URL` environment variable
3. Individual `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` variables

For all configuration options, see `docs/user/configuration.md`.

---

## 5. Register Your First Corpus

A corpus is a named documentation source registered in the `doc_corpora` table. You must register a corpus before running the pipeline.

### Option A: Via MCP `add_corpus_tool`

If you have the MCP server running, call `add_corpus_tool` with:

```
slug:     "pydantic-ai"
name:     "Pydantic AI"
strategy: "llms_txt"
config:   {"url": "https://ai.pydantic.dev/llms.txt"}
```

The `parser` and `embedder` arguments default to `"markdown"` and `"gemini"` and can be omitted.

### Option B: Direct SQL INSERT

```sql
INSERT INTO doc_corpora (slug, name, fetch_strategy, fetch_config, parser, embedder, enabled)
VALUES (
    'pydantic-ai',
    'Pydantic AI',
    'llms_txt',
    '{"url": "https://ai.pydantic.dev/llms.txt"}',
    'markdown',
    'gemini',
    true
);
```

### `llms_txt` strategy config fields

| Key | Required | Description |
|-----|----------|-------------|
| `url` | Yes | URL to the `llms.txt` manifest file |
| `url_pattern` | No | Regex to extract doc URLs from the manifest. Auto-derived from `url` if omitted. |
| `base_url` | No | Base URL for filename generation. Auto-derived from `url` if omitted. |
| `workers` | No | Download concurrency (default: 20) |
| `retries` | No | Per-URL HTTP retry count (default: 3) |

---

## 6. Run the Pipeline

```bash
doc-hub-pipeline --corpus pydantic-ai
```

This runs all four stages in sequence:

| Stage | What happens |
|-------|-------------|
| **1. Fetch** | Downloads the `llms.txt` manifest, extracts doc URLs, fetches each `.md` file. Output lands in `data/pydantic-ai/raw/`. An incremental manifest tracks what has changed since the last run. |
| **2. Parse** | Splits each markdown file by headings. Applies a two-pass chunk-size optimization (merges chunks under 500 chars, splits chunks over 2500 chars). Deduplicates by SHA-256 content hash. Output: `data/pydantic-ai/chunks/chunks.jsonl`. |
| **3. Embed** | Sends each chunk to Gemini (`gemini-embedding-001`, 768-dim vectors), L2-normalizes the result. Uses a per-corpus cache to skip already-embedded chunks (free-tier rate limit: 100 RPM). Output: `data/pydantic-ai/chunks/embedded_chunks.jsonl`. |
| **4. Index** | Upserts chunks into the `doc_chunks` PostgreSQL table scoped by `corpus_id`. Updates `doc_corpora` stats. Builds GIN and VectorChord indexes. |

`ensure_schema()` runs automatically at the start of the index stage, creating the `vchord` extension and all tables if they don't exist yet.

### Running individual stages

```bash
doc-hub-pipeline --corpus pydantic-ai --stage fetch
doc-hub-pipeline --corpus pydantic-ai --stage parse
doc-hub-pipeline --corpus pydantic-ai --stage embed
doc-hub-pipeline --corpus pydantic-ai --stage index
```

### Other useful flags

```bash
# Re-use previously fetched files (skip download)
doc-hub-pipeline --corpus pydantic-ai --skip-download

# Delete stale DB rows (chunks no longer in the corpus)
doc-hub-pipeline --corpus pydantic-ai --full-reindex

# Wipe all local data for the corpus first, then re-run
doc-hub-pipeline --corpus pydantic-ai --clean
```

### Expected log output

```
INFO: [pydantic-ai] === STEP 1: Fetch ===
INFO: [pydantic-ai] Fetching llms.txt from https://ai.pydantic.dev/llms.txt
INFO: [pydantic-ai] Found 142 unique URLs
INFO: [pydantic-ai] Fetch complete → data/pydantic-ai/raw
INFO: [pydantic-ai] === STEP 2: Parse (parser=markdown) ===
INFO: [pydantic-ai] Parse complete → 1847 chunks
INFO: [pydantic-ai] === STEP 3: Embed (embedder=gemini) ===
INFO: [pydantic-ai] Embed complete → 1847 embedded chunks
INFO: [pydantic-ai] === STEP 4: Index ===
INFO: [pydantic-ai] Upsert complete: inserted=1847, updated=0, deleted=0, total=1847
INFO: [pydantic-ai] Pipeline done in 143.2s
```

The embed stage is the slowest on first run due to Gemini API rate limits. Subsequent runs use the cache and are much faster.

---

## 7. Run Your First Search

```bash
doc-hub-search "how do I define a tool?" --corpus pydantic-ai
```

### Output format

```
Search results for: 'how do I define a tool?'
Corpus: pydantic-ai
──────────────────────────────────────────────────────────────────────

[1] Tools
    Corpus:     pydantic-ai
    Path:       api-reference/agent/tools
    Category:   api
    Lines:      1-48
    Similarity: 0.821  |  RRF Score: 0.03125
    URL:        https://ai.pydantic.dev/tools/
    Preview:    Tools are functions that an agent can call during a run...
```

| Field | Description |
|-------|-------------|
| **Heading** | The markdown heading of the matching section |
| **Corpus** | Corpus slug (`corpus_id`) |
| **Path** | `section_path` — hierarchical path within the source file |
| **Category** | `api`, `guide`, `example`, `eval`, or `other` |
| **Lines** | Source line range in the original file |
| **Similarity** | Cosine similarity of the chunk embedding vs. the query embedding (0–1). Results below 0.55 are filtered out by default. |
| **RRF Score** | Reciprocal Rank Fusion score combining vector KNN rank and BM25 full-text rank |
| **URL** | Source URL for the chunk |
| **Preview** | First 200 characters of the chunk content |

### JSON output

```bash
doc-hub-search "how do I define a tool?" --corpus pydantic-ai --json
```

Returns an array of objects with keys: `id`, `corpus_id`, `heading`, `section_path`, `source_url`, `score`, `similarity`, `category`, `start_line`, `end_line`, `content_preview`.

### Other search flags

```bash
# Search without corpus filter (all indexed corpora)
doc-hub-search "retry logic"

# Filter by category
doc-hub-search "Agent" --corpus pydantic-ai --category api

# Return more results
doc-hub-search "streaming" --corpus pydantic-ai --limit 10

# Lower the similarity threshold to get more results
doc-hub-search "streaming" --corpus pydantic-ai --min-similarity 0.4
```

---

## 8. Next Steps

- **CLI reference** — full flag documentation for all five console scripts
- **MCP server setup** — integrate doc-hub with Claude Code or any MCP client via `doc-hub-mcp`
- **Evaluation** — measure retrieval quality with `doc-hub-eval` using hand-curated test queries
- **Custom fetchers** — see `docs/writing-fetchers.md` to index documentation from non-standard sources
- **Configuration** — see `docs/user/configuration.md` for the full environment variable reference
