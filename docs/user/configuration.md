# Configuration Reference

All environment variables recognized by doc-hub, their defaults, and how they interact.

The unified `doc-hub` CLI loads environment in this order before dispatching commands:

1. Existing process environment
2. `.env` in the current working directory / repo root
3. A global env file at `~/.local/share/doc-hub/env` (or `{DOC_HUB_DATA_DIR}/env` if you override the data root)

That means explicit shell exports still win, repo-local `.env` files still work for clone-based development, and a global `doc-hub` install can still run from anywhere on the machine.

---

## Environment variable reference

| Variable | Default | Required | Description |
|---|---|---|---|
| `DOC_HUB_DATABASE_URL` | — | No¹ | Full PostgreSQL connection string (overrides all `PG*` vars) |
| `PGHOST` | `localhost` | No | PostgreSQL host |
| `PGPORT` | `5432` | No | PostgreSQL port |
| `PGDATABASE` | `doc_hub` | No | PostgreSQL database name |
| `PGUSER` | `postgres` | No | PostgreSQL user |
| `PGPASSWORD` | — | **Yes**¹ | PostgreSQL password — no default, raises `RuntimeError` if unset |
| `GEMINI_API_KEY` | — | **Yes** | Gemini API key for embeddings — raised on first embed call |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | No | Gemini embedding model name |
| `GEMINI_EMBEDDING_DIM` | `768` | No | Gemini output dimensionality |
| `DOC_HUB_VECTOR_DIM` | `768` | No | Vector column size in PostgreSQL — must match embedder dimensions |
| `DOC_HUB_EMBED_SLEEP` | `65.0` | No | Seconds to sleep between embedding batches (rate-limit pacing) |
| `DOC_HUB_DATA_DIR` | See below | No | Override data root directory |
| `XDG_DATA_HOME` | — | No | XDG base data dir — used if `DOC_HUB_DATA_DIR` is not set |
| `DOC_HUB_EVAL_DIR` | See below | No | Override eval file directory |
| `DOC_HUB_CLEAN_MODEL` | — | When cleaning | Model slug for the LLM cleaning endpoint |
| `DOC_HUB_CLEAN_API_KEY` | — | When cleaning | API key for the LLM cleaning endpoint |
| `DOC_HUB_CLEAN_BASE_URL` | — | When cleaning | Base URL for the OpenAI-compatible API |
| `DOC_HUB_CLEAN_PROMPT` | Built-in default | No | System prompt override for the cleaning LLM |
| `LOGLEVEL` | — | No | Set to `DEBUG` for verbose output from `doc-hub docs search` and `doc-hub pipeline eval` |

¹ Either `DOC_HUB_DATABASE_URL` or `PGPASSWORD` must be set. If neither is set, `_build_dsn()` raises `RuntimeError`.

---

## Database connection

### Resolution order

`_build_dsn()` in `db.py` resolves the connection string in this order:

1. **Explicit DSN argument** — passed programmatically (not applicable from the CLI)
2. **`DOC_HUB_DATABASE_URL`** — a complete PostgreSQL connection string
3. **Individual `PG*` variables** — assembled into a URL with safe defaults

```bash
# Option A: full connection string
export DOC_HUB_DATABASE_URL="postgresql://myuser:mypass@localhost:5432/doc_hub"

# Option B: individual variables
export PGHOST=localhost
export PGPORT=5432
export PGDATABASE=doc_hub
export PGUSER=postgres
export PGPASSWORD=mypassword
```

### Variable details

**`DOC_HUB_DATABASE_URL`**
Full PostgreSQL connection string. Takes precedence over all individual `PG*` variables. Useful for cloud-hosted databases where the provider supplies a single connection URL.

**`PGHOST`** (default: `localhost`)
PostgreSQL server hostname or IP address.

**`PGPORT`** (default: `5432`)
PostgreSQL server port. The standard PostgreSQL port is `5432`. If you run Docker with a non-standard host mapping (e.g. `-p 5433:5432`), set this to the host-side port — but the default `5432` matches the standard in-container port.

**`PGDATABASE`** (default: `doc_hub`)
Name of the PostgreSQL database. `ensure_schema()` creates tables inside this database; the database itself must already exist.

**`PGUSER`** (default: `postgres`)
PostgreSQL user. Must have `CREATE TABLE`, `CREATE INDEX`, and `CREATE EXTENSION` privileges.

**`PGPASSWORD`** (no default — **required**)
PostgreSQL password. There is no default. If this variable is unset and `DOC_HUB_DATABASE_URL` is also unset, `_build_dsn()` raises:

```
RuntimeError: PGPASSWORD environment variable not set.
Set it directly or use DOC_HUB_DATABASE_URL for the full connection string.
```

### Special characters in credentials

When individual `PG*` variables are used, `_build_dsn()` encodes the user and password with `urllib.parse.quote_plus`. This handles special characters like `@`, `/`, `%`, `+`, and spaces. You do not need to pre-encode these values.

```bash
# This works even if the password contains @ or /
export PGPASSWORD="p@ss/word"
```

If you provide `DOC_HUB_DATABASE_URL` directly, you are responsible for percent-encoding any special characters in the user or password fields.

---

## Embedding configuration

### `GEMINI_API_KEY` (required)

The Gemini API key used by the built-in `GeminiEmbedder`. Get a free key at https://aistudio.google.com/apikey.

The key is read lazily — on the first embedding call, not at import time. The MCP server starts without it; the error surfaces only when a corpus is indexed or a search query requires embedding.

```bash
export GEMINI_API_KEY="AIza..."
```

### `GEMINI_EMBEDDING_MODEL` (default: `gemini-embedding-001`)

The Gemini model used by `GeminiEmbedder`. Changing this invalidates the embedding cache (cache entries are keyed by model name and dimensions, so stale entries are silently skipped).

```bash
export GEMINI_EMBEDDING_MODEL="gemini-embedding-001"
```

### `GEMINI_EMBEDDING_DIM` (default: `768`)

The output dimensionality requested from Gemini. Must match `DOC_HUB_VECTOR_DIM` (the vector column size in PostgreSQL). If these differ, `embed_chunks()` raises `ValueError` before any API calls are made.

### `DOC_HUB_VECTOR_DIM` (default: `768`)

Controls the `vector(N)` column type in `doc_chunks`. Read by `get_vector_dim()` in `db.py`.

`ensure_schema()` validates the existing column dimension against this value on every startup. If they differ, it raises:

```
RuntimeError: Existing doc_chunks table has vector(1536) but DOC_HUB_VECTOR_DIM=768.
To fix this, either:
  1. Set DOC_HUB_VECTOR_DIM=1536 to match the existing table, or
  2. DROP TABLE doc_chunks and let doc-hub recreate it with the new dimension.
     (This will delete all indexed data — re-index all corpora after.)
```

This variable must be a positive integer. Any other value causes `get_vector_dim()` to raise `ValueError`.

```bash
export DOC_HUB_VECTOR_DIM=768
```

### `DOC_HUB_EMBED_SLEEP` (default: `65.0`)

Seconds to sleep between embedding batches. The default of 65 seconds paces requests to stay within the Gemini free-tier rate limit of 100 requests per minute.

Set to `0` for embedders without rate limits, or reduce it if you have a paid Gemini quota.

```bash
export DOC_HUB_EMBED_SLEEP=0      # no rate limiting
export DOC_HUB_EMBED_SLEEP=5.0    # 5-second pause between batches
```

This variable overrides the `inter_batch_sleep` parameter in `embed_chunks()` (`embed.py:253`).

---

## LLM cleaning configuration

The `doc-hub pipeline clean` command and auto-clean during fetch use an OpenAI-compatible LLM to strip navigation, footers, and scraping artifacts from fetched markdown. These variables are only required when cleaning is triggered.

### `DOC_HUB_CLEAN_MODEL` (required when cleaning)

The model slug to use for cleaning requests. Any model available at your configured endpoint works.

```bash
export DOC_HUB_CLEAN_MODEL="gpt-4o-mini"
```

### `DOC_HUB_CLEAN_API_KEY` (required when cleaning)

API key for the OpenAI-compatible endpoint.

```bash
export DOC_HUB_CLEAN_API_KEY="sk-..."
```

### `DOC_HUB_CLEAN_BASE_URL` (required when cleaning)

Base URL for the API. Works with any OpenAI-compatible endpoint (OpenAI, OpenRouter, local LLMs, etc.).

```bash
export DOC_HUB_CLEAN_BASE_URL="https://api.openai.com/v1"
```

### `DOC_HUB_CLEAN_PROMPT` (optional)

Override the system prompt sent to the LLM. When unset, a built-in prompt is used that strips navigation, footers, breadcrumbs, and scraping artifacts while preserving all documentation content verbatim.

```bash
export DOC_HUB_CLEAN_PROMPT="Your custom cleaning instructions here"
```

---

## Data directory resolution

### `data_root()` resolution order

`data_root()` in `paths.py` resolves the base data directory in this order:

1. **`DOC_HUB_DATA_DIR`** — explicit override (supports `~` expansion)
2. **`$XDG_DATA_HOME/doc-hub`** — if `XDG_DATA_HOME` is set
3. **`~/.local/share/doc-hub`** — XDG default

```bash
# Use a custom location
export DOC_HUB_DATA_DIR="/mnt/data/doc-hub"

# Or rely on XDG
export XDG_DATA_HOME="/mnt/data"
# → data root becomes /mnt/data/doc-hub
```

The data root directory is **not created automatically**. Callers that write files create it with `mkdir(parents=True, exist_ok=True)`.

### Directory layout

```
{data_root}/
  {slug}/
    raw/                    # Fetched .md files + manifest.json
    chunks/
      embedded_chunks.jsonl     # Output of embed stage
      embeddings_cache.jsonl    # Cache keyed by (content_hash, model, dimensions)
  plugins/                  # Local plugin .py files (fetchers/, parsers/, embedders/)
```

For a corpus with slug `pydantic-ai`, all files live under `{data_root}/pydantic-ai/`.

---

## Eval directory

`_eval_dir()` in `eval.py` resolves the evaluation file directory:

1. **`DOC_HUB_EVAL_DIR`** — explicit override
2. **`{data_root}/eval/`** — default

```bash
export DOC_HUB_EVAL_DIR="/home/user/my-evals"
```

Eval files must be named `{corpus-slug}.json` (e.g., `pydantic-ai.json`). See the [evaluation guide](evaluation.md) for file format details.

---

## Logging

**`LOGLEVEL`**

Set to `DEBUG` to enable verbose output from `doc-hub docs search` and `doc-hub pipeline eval`. Pipeline commands continue to log at `INFO` level regardless of this variable.

```bash
LOGLEVEL=DEBUG doc-hub docs search "how do I define a tool?" --corpus pydantic-ai
```

---

## `.env` file support

All CLI entry points call `load_dotenv()` at startup (via `python-dotenv`). Place a `.env` file in your repo root or working directory:

```bash
# .env
PGPASSWORD=mypassword
PGDATABASE=doc_hub
GEMINI_API_KEY=AIza...
DOC_HUB_VECTOR_DIM=768
DOC_HUB_EMBED_SLEEP=65.0
```

Variables already in your environment take precedence over `.env` values.

For a global install that should work from any directory, create a durable env file under doc-hub's home-directory data root:

```bash
mkdir -p ~/.local/share/doc-hub
cat > ~/.local/share/doc-hub/env <<'EOF'
PGHOST=localhost
PGPORT=5433
PGUSER=postgres
PGPASSWORD=your-password
PGDATABASE=postgres
GEMINI_API_KEY=your-key
EOF
```

If you set `DOC_HUB_DATA_DIR`, the fallback file becomes `{DOC_HUB_DATA_DIR}/env` instead.
