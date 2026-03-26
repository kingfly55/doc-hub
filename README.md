# doc-hub

Multi-corpus documentation search engine with hybrid vector + full-text retrieval, exposed via MCP (Model Context Protocol) for use by LLM agents.

## What it does

doc-hub fetches documentation from various sources, parses it into semantically meaningful chunks, embeds them with a pluggable embedder, indexes them in PostgreSQL with VectorChord, and serves hybrid search (vector KNN + BM25 full-text, fused via RRF) through an MCP server or CLI.

The plugin system lets you add new fetchers, parsers, and embedders without modifying doc-hub itself.

## Install

doc-hub is installed from GitHub (not published to PyPI).

```bash
# As an isolated CLI tool (recommended — puts doc-hub-* commands on your PATH)
uv tool install git+https://github.com/kingfly55/doc-hub.git
# or: pipx install git+https://github.com/kingfly55/doc-hub.git

# Or from a local clone
git clone https://github.com/kingfly55/doc-hub.git && cd doc-hub
uv sync            # creates .venv and installs everything
source .venv/bin/activate
```

Verify:

```bash
doc-hub-search --help
```

## Requirements

- Python >= 3.11
- PostgreSQL with [VectorChord](https://github.com/tensorchord/VectorChord) extension
- Gemini API key (free tier works) — or a custom embedder plugin

## Quick start

### 1. Start PostgreSQL with VectorChord

```bash
docker run -d --name vchord-postgres \
  -e POSTGRES_PASSWORD=mypassword \
  -p 5432:5432 \
  tensorchord/vchord-postgres:latest
```

### 2. Set environment variables

```bash
export GEMINI_API_KEY="your-key-here"
export PGHOST=localhost PGPORT=5432 PGUSER=postgres PGPASSWORD=mypassword PGDATABASE=postgres
```

Or create a `.env` file in your working directory — doc-hub uses `python-dotenv` and will auto-load it.

You can also set a single connection string:

```bash
export DOC_HUB_DATABASE_URL="postgresql://postgres:mypassword@localhost:5432/postgres"
```

### 3. Index your first corpus

```bash
doc-hub-pipeline --corpus pydantic-ai
```

### 4. Search

```bash
doc-hub-search "how do I handle retries?" --corpus pydantic-ai
```

## CLI reference

| Script | Description |
|--------|-------------|
| `doc-hub-pipeline` | Run the fetch → parse → embed → index pipeline for a corpus |
| `doc-hub-search` | Hybrid search CLI |
| `doc-hub-mcp` | Start the MCP server |
| `doc-hub-eval` | Evaluate retrieval quality |
| `doc-hub-sync-all` | Run the pipeline for all enabled corpora |

See [docs/user/cli-reference.md](docs/user/cli-reference.md) for full flags and examples.

## MCP server

Exposes four tools via FastMCP. Supports stdio (default), SSE, and streamable-http transports.

```bash
# stdio (default — for Claude Desktop, Claude Code)
doc-hub-mcp

# SSE (persistent service)
doc-hub-mcp --transport sse --port 8340

# streamable-http
doc-hub-mcp --transport streamable-http --port 8340
```

### Claude Desktop / Claude Code configuration

**stdio** (spawned per session):

```json
{
  "mcpServers": {
    "doc-hub": {
      "command": "doc-hub-mcp",
      "env": { "GEMINI_API_KEY": "<key>" }
    }
  }
}
```

**SSE** (connect to running service):

```json
{
  "mcpServers": {
    "doc-hub": {
      "type": "sse",
      "url": "http://localhost:8340/sse"
    }
  }
}
```

See [docs/user/mcp-server.md](docs/user/mcp-server.md) for transport details and systemd service setup.

### Running as a systemd service

```bash
cat > ~/.config/systemd/user/doc-hub-mcp.service << 'EOF'
[Unit]
Description=doc-hub MCP Server (SSE on :8340)
After=network.target postgresql.service

[Service]
Type=simple
ExecStart=doc-hub-mcp --transport sse --port 8340
Restart=always
RestartSec=10
Environment=HOME=%h
Environment=GEMINI_API_KEY=your-key-here
Environment=PGHOST=localhost
Environment=PGPASSWORD=mypassword

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now doc-hub-mcp.service
```

## Plugin system

doc-hub uses a plugin architecture based on Python entry points. Three plugin types:

| Plugin Type | Entry Point Group | Protocol |
|-------------|-------------------|----------|
| Fetcher | `doc_hub.fetchers` | `doc_hub.protocols.Fetcher` |
| Parser | `doc_hub.parsers` | `doc_hub.protocols.Parser` |
| Embedder | `doc_hub.embedders` | `doc_hub.protocols.Embedder` |

**Built-in plugins:** `llms_txt` fetcher, `local_dir` fetcher, `markdown` parser, `gemini` embedder.

See [docs/dev/plugin-authoring.md](docs/dev/plugin-authoring.md) for a complete guide to writing plugins.

## As a library

```python
import asyncio
from doc_hub.search import search_docs
from doc_hub.db import create_pool

async def main():
    pool = await create_pool()
    results = await search_docs("how do I define a tool?", pool=pool, corpus="pydantic-ai")
    for r in results:
        print(f"{r.heading} (sim={r.similarity:.3f})")
    await pool.close()

asyncio.run(main())
```

## Data storage

doc-hub stores local data (raw downloads, chunk caches, embedding caches) in an XDG-compliant directory:

1. `DOC_HUB_DATA_DIR` env var (explicit override)
2. `$XDG_DATA_HOME/doc-hub` if `XDG_DATA_HOME` is set
3. `~/.local/share/doc-hub` (default)

```
~/.local/share/doc-hub/
├── {corpus-slug}/
│   ├── raw/                       # downloaded .md files + manifest.json
│   └── chunks/
│       ├── chunks.jsonl           # parsed chunks
│       ├── embedded_chunks.jsonl  # chunks with embedding vectors
│       └── embeddings_cache.jsonl # embedding cache (keyed by content hash)
└── plugins/                       # local plugin files (alternative to entry points)
    ├── fetchers/
    ├── parsers/
    └── embedders/
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | Required for the built-in Gemini embedder |
| `DOC_HUB_DATABASE_URL` | — | Full connection string (overrides PG* vars) |
| `PGHOST` | `localhost` | PostgreSQL host |
| `PGPORT` | `5432` | PostgreSQL port |
| `PGDATABASE` | `postgres` | Database name |
| `PGUSER` | `postgres` | Database user |
| `PGPASSWORD` | — | Database password (required) |
| `DOC_HUB_DATA_DIR` | `~/.local/share/doc-hub` | Override data directory |
| `DOC_HUB_EVAL_DIR` | `{data_root}/eval/` | Override eval directory |
| `LOGLEVEL` | — | Set to `DEBUG` for verbose output |

## Testing

```bash
# Unit tests (no DB or API key needed)
pytest tests/

# Integration tests (requires live DB + GEMINI_API_KEY)
pytest tests/ -m integration
```

## Documentation

- **Users:** [Getting Started](docs/user/getting-started.md) · [Configuration](docs/user/configuration.md) · [CLI Reference](docs/user/cli-reference.md) · [MCP Server](docs/user/mcp-server.md) · [Evaluation](docs/user/evaluation.md) · [Cloud Database](docs/user/cloud-database.md)
- **Developers:** [AGENTS.md](AGENTS.md) · [Architecture](ARCHITECTURE.md) · [Plugin Authoring](docs/dev/plugin-authoring.md) · [Protocols](docs/dev/protocols-reference.md) · [Database Schema](docs/dev/database-schema.md) · [Testing](docs/dev/testing-guide.md) · [Search Internals](docs/dev/search-internals.md)
