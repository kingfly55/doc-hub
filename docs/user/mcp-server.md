# MCP Server

doc-hub exposes six tools to LLM agents via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) using [FastMCP](https://github.com/jlowin/fastmcp). The server manages a shared PostgreSQL connection pool across all tool invocations and supports three transport modes.

---

## Overview

The MCP server (`mcp_server.py`) exposes these tools:

| Tool | Description |
|---|---|
| `search_docs_tool` | Hybrid vector + full-text search across indexed documentation |
| `list_corpora_tool` | List all registered corpora with status |
| `add_corpus_tool` | Register a new corpus or update an existing one |
| `refresh_corpus_tool` | Re-run the full fetch → parse → embed → index → tree pipeline for a corpus |
| `browse_corpus_tool` | Browse the persisted document hierarchy for a corpus |
| `get_document_tool` | Read a document or section, with outline mode for large documents |

On startup, the server creates an asyncpg connection pool and runs `ensure_schema()` to verify the database schema. On shutdown, the pool is closed. The `GEMINI_API_KEY` is checked lazily — the server starts and serves `search_docs_tool`, `list_corpora_tool`, and `add_corpus_tool` requests without it. The key is only required when a search query or corpus refresh triggers an embedding call.

---

## Transport modes

### stdio (default)

The server is spawned once per session by the MCP client (Claude Desktop, Claude Code, or any stdio-capable client). Communication happens over stdin/stdout. There is no network port.

```bash
doc-hub serve mcp
```

Use this for Claude Desktop and Claude Code integrations. It is the simplest configuration — no service management required.

### SSE

The server runs as a persistent HTTP service. Clients connect to `http://<host>:<port>/sse`. This is suitable for running as a systemd service that multiple agents can reach simultaneously.

```bash
doc-hub serve mcp --transport sse --port 8340
```

### streamable-http

The newer MCP HTTP transport. Uses the same host/port arguments as SSE.

```bash
doc-hub serve mcp --transport streamable-http --port 8340
```

---

## Starting the server

```bash
# stdio — spawned per session (default)
doc-hub serve mcp

# SSE — persistent HTTP service on localhost:8340
doc-hub serve mcp --transport sse --port 8340

# SSE on a custom host and port
doc-hub serve mcp --transport sse --host 0.0.0.0 --port 9000

# streamable-http
doc-hub serve mcp --transport streamable-http --port 8340
```

**CLI flags:**

| Flag | Default | Description |
|---|---|---|
| `--transport` | `stdio` | Transport protocol: `stdio`, `sse`, or `streamable-http` |
| `--host` | `127.0.0.1` | Bind address for SSE/HTTP transports |
| `--port` | `8340` | Port for SSE/HTTP transports |

The `--host` and `--port` flags are ignored when `--transport stdio` is used.

---

## Claude Desktop configuration

Add one of the following blocks to your Claude Desktop `claude_desktop_config.json`.

### stdio (spawn per session)

Claude Desktop spawns the server automatically when a session starts. Use this if you have not set up a persistent service.

```json
{
  "mcpServers": {
    "doc-hub": {
      "command": "doc-hub",
      "args": ["serve", "mcp"]
    }
  }
}
```

For a global install, put your database and Gemini settings in `~/.local/share/doc-hub/env` so the spawned command works from anywhere. You only need an explicit `"env"` block here if you want this MCP client to override the shared machine-level defaults. See [Configuration Reference](configuration.md) for all supported variables.

### SSE (connect to running service)

Use this if the server is already running (e.g. as a systemd service). Claude Desktop connects to the existing process instead of spawning a new one.

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

---

## Claude Code configuration

### Using the `--mcp` flag

Pass the MCP config directly when launching Claude Code:

```bash
claude --mcp '{"mcpServers":{"doc-hub":{"command":"doc-hub","args":["serve","mcp"]}}}'
```

### Using `settings.json`

Add the server to `.claude/settings.json` in your project or to the global settings file (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "doc-hub": {
      "command": "doc-hub",
      "args": ["serve", "mcp"]
    }
  }
}
```

For an already-running SSE service:

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

---

## Running as a systemd service

For persistent access by any agent on the machine, run the server under systemd as a user service.

### Unit file

```bash
cat > ~/.config/systemd/user/doc-hub-serve-mcp.service << 'EOF'
[Unit]
Description=doc-hub MCP Server (SSE on :8340)
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=%h
ExecStart=%h/.local/bin/doc-hub serve mcp --transport sse --port 8340
Restart=always
RestartSec=10
Environment=HOME=%h
EnvironmentFile=%h/.local/share/doc-hub/env

[Install]
WantedBy=default.target
EOF
```

This assumes `doc-hub` was installed with `uv tool install` and that `~/.local/share/doc-hub/env` contains the required database and Gemini settings.

### Enable and start

```bash
systemctl --user daemon-reload
systemctl --user enable --now doc-hub-serve-mcp.service
```

### Check status and logs

```bash
systemctl --user status doc-hub-serve-mcp.service
journalctl --user -u doc-hub-serve-mcp.service -f
```

Once running, connect via `http://localhost:8340/sse`.

---

## Tool reference

### `search_docs_tool`

Search indexed documentation using hybrid vector + full-text retrieval. Searches all corpora by default.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | required | Natural language or keyword search query |
| `corpus` | `str \| None` | `None` | Corpus slug to restrict search (e.g. `"pydantic-ai"`). `None` searches all corpora. |
| `categories` | `list[str] \| None` | `None` | Filter to these categories (e.g. `["api", "guide"]`). Valid values: `api`, `guide`, `example`, `eval`, `other`. |
| `limit` | `int` | `5` | Maximum number of results to return |
| `max_content_chars` | `int` | `800` | Truncate the `content` field of each result to this many characters |
| `version` | `str \| None` | `None` | Strictly search this version for `corpus`. Mutually exclusive with `versions` and `all_versions`. |
| `versions` | `list[str] \| None` | `None` | Strictly search these versions for `corpus`. |
| `all_versions` | `bool` | `False` | Search every indexed version for `corpus`. Only use for migration/history questions. |

**Returns:** `list[dict]`

Each dict has the following keys:

| Key | Type | Description |
|---|---|---|
| `heading` | `str` | Section heading |
| `section_path` | `str` | Full heading path (e.g. `"Agents > Running Agents > stream_text"`) |
| `content` | `str` | Chunk content, truncated to `max_content_chars` |
| `source_url` | `str` | URL or file path of the source document |
| `corpus_id` | `str` | Slug of the corpus this chunk belongs to |
| `source_version` | `str` | Source version label for the searched snapshot |
| `snapshot_id` | `str` | Immutable snapshot identifier that was searched |
| `scope` | `dict \| None` | Machine-readable searched/available version metadata when a corpus was scoped |
| `score` | `float` | RRF score rounded to 4 decimal places (higher = better rank) |
| `similarity` | `float` | Cosine similarity to the query vector, rounded to 3 decimal places |
| `category` | `str` | Chunk category: `api`, `guide`, `example`, `eval`, or `other` |
| `start_line` | `int` | 1-indexed start line in the source file |
| `end_line` | `int` | 1-indexed end line (inclusive) in the source file |

Results are sorted by RRF score descending. Results with cosine similarity below 0.55 are filtered out before being returned (this threshold is applied in Python after the SQL query, not in SQL).

**Example:**

```json
{
  "query": "how do I define a tool?",
  "corpus": "pydantic-ai",
  "categories": ["api", "guide"],
  "limit": 3
}
```

---

### `list_corpora_tool`

List all registered documentation corpora, including disabled ones.

**Parameters:** none

**Returns:** `list[dict]`

Each dict has the following keys:

| Key | Type | Description |
|---|---|---|
| `slug` | `str` | Unique corpus identifier |
| `name` | `str` | Human-readable corpus name |
| `strategy` | `str` | Fetcher plugin name (e.g. `"llms_txt"`, `"local_dir"`) |
| `enabled` | `bool` | Whether the corpus is active |
| `total_chunks` | `int` | Number of indexed chunks |
| `last_indexed_at` | `str \| None` | ISO timestamp of last successful index run, or `None` |
| `versions` | `list[dict]` | Available indexed snapshots with `source_version`, `snapshot_id`, and chunk counts |

---

### `add_corpus_tool`

Register a new documentation corpus or update an existing one with the same slug.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `slug` | `str` | required | Unique identifier (e.g. `"fastapi"`, `"langchain"`) |
| `name` | `str` | required | Human-readable name (e.g. `"FastAPI"`) |
| `strategy` | `str` | required | Fetcher plugin name (e.g. `"llms_txt"`, `"local_dir"`) |
| `config` | `dict` | required | Strategy-specific configuration (stored as JSONB). Contents depend on the fetcher plugin. |
| `parser` | `str` | `"markdown"` | Parser plugin name |
| `embedder` | `str` | `"gemini"` | Embedder plugin name |

**Returns:** `dict`

```json
{"status": "registered", "slug": "<slug>"}
```

**Plugin validation:** The tool checks whether `strategy`, `parser`, and `embedder` are registered in the plugin registry. If any are not found, a warning is logged but the corpus is still registered. The tool does not error on unknown plugin names — they may be installed before the pipeline runs. If a plugin is missing when `refresh_corpus_tool` is called, the pipeline will fail at that point.

If a corpus with the given `slug` already exists, it is updated (upserted) with the new values.

**Example:**

```json
{
  "slug": "fastapi",
  "name": "FastAPI",
  "strategy": "llms_txt",
  "config": {
    "url": "https://fastapi.tiangolo.com/llms.txt",
    "url_pattern": "https://fastapi.tiangolo.com/"
  }
}
```

---

### `refresh_corpus_tool`

Re-run the full pipeline for a corpus: fetch → parse → embed → index → tree.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `slug` | `str` | required | Slug of the corpus to refresh |
| `full` | `bool` | `False` | If `True`, delete stale chunks that are no longer present in the current fetch |

**Returns:** `dict`

On success:

```json
{
  "status": "complete",
  "slug": "<slug>",
  "chunks_indexed": 1240,
  "inserted": 42,
  "updated": 5,
  "deleted": 0
}
```

On failure:

```json
{"error": "Corpus 'unknown-slug' not found"}
```

```json
{"error": "Corpus 'my-corpus' is disabled"}
```

The tool checks that the corpus exists and is enabled before running the pipeline. If the corpus is disabled, it returns an error dict rather than throwing. To re-enable a disabled corpus, use `add_corpus_tool` to upsert it with the desired configuration (the `Corpus` model defaults `enabled` to `True`).

When `full=False` (the default), the index stage only inserts and updates chunks — it does not delete chunks that were removed from the source. Use `full=True` for a clean sync that removes stale content. The tree stage runs after indexing so document hierarchy and browse/read surfaces stay in sync with the latest indexed chunks.

---

### `browse_corpus_tool`

Browse the persisted document hierarchy for a corpus.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `corpus` | `str` | required | Corpus slug to browse |
| `path` | `str \| None` | `None` | Optional subtree root path |
| `depth` | `int \| None` | `None` | Optional maximum relative depth below `path` |
| `version` | `str \| None` | `None` | Version selector. Defaults to the corpus default/latest snapshot. |

**Returns:** `dict`

The payload includes `corpus`, `snapshot_id`, and `documents`. Each document-tree node has fields such as `doc_path`, `title`, `source_url`, `depth`, `is_group`, `total_chars`, `section_count`, and `children_count`. The list is already in preorder for direct rendering.

---

### `get_document_tool`

Read a document or one section of a document.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `corpus` | `str` | required | Corpus slug containing the document |
| `doc_path` | `str` | required | Document path to read |
| `version` | `str \| None` | `None` | Version selector. Defaults to the corpus default/latest snapshot. |

**Returns:** `dict`

- Full mode: `mode`, `doc_path`, `title`, `content`, `source_url`, `snapshot_id`, `total_chars`, `section_count`
- Missing document: `{"error": "Document '<doc_path>' not found in corpus '<corpus>' at snapshot '<snapshot_id>'"}`
