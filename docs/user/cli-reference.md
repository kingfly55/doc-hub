# CLI Reference

The canonical command surface for doc-hub is a single executable:

```bash
doc-hub ...
```

For a concise local reference after install, use `man doc-hub`. If your shell has not picked up the installed manpath yet, use `doc-hub man` to print the bundled manpage directly.

The command tree is organized into three groups plus a top-level manual command:

- `doc-hub man` — print the built-in manual page
- `doc-hub docs ...`
- `doc-hub pipeline ...`
- `doc-hub serve ...`

---

## `doc-hub man`

Print the built-in manual page.

```bash
doc-hub man
```

Use this when you want the same concise reference content as `man doc-hub` but your shell environment has not picked up the installed manpath yet.

---

## `doc-hub docs list`

List registered corpora.

```bash
doc-hub docs list [options]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json` | flag | false | Emit machine-readable corpus records. |

### Examples

```bash
# List corpora
doc-hub docs list

# Machine-readable output
doc-hub docs list --json
```

---

## `doc-hub docs browse`

Browse the persisted document hierarchy for a corpus.

```bash
doc-hub docs browse CORPUS [options]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `corpus` | string | **required** | Corpus slug to browse. |
| `--path PATH` | string | none | Restrict output to this document subtree path. |
| `--depth N` | int | none | Maximum depth below the selected root path. |
| `--json` | flag | false | Emit raw JSON tree nodes instead of rendered text. |

### Output

Human-readable mode prints the corpus slug followed by an indented preorder tree. Group nodes are marked with `[group]`. Concrete documents include a stable short document ID in brackets plus total character count and section count. Use that short ID with `doc-hub docs read` when you do not want to type the full path.

### Examples

```bash
# Browse the whole corpus
doc-hub docs browse pydantic-ai

# Browse just one subtree
doc-hub docs browse pydantic-ai --path api

# Limit subtree depth
doc-hub docs browse pydantic-ai --path api --depth 1

# Use the short ID shown in browse output with read
# Example browse line: Install [abc123] 12,345 chars  3 sections
doc-hub docs read pydantic-ai abc123

# Machine-readable output
doc-hub docs browse pydantic-ai --json
```

---

## `doc-hub docs read`

Read a document or a specific section from a corpus.

```bash
doc-hub docs read CORPUS DOC_PATH_OR_ID [options]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `corpus` | string | **required** | Corpus slug containing the document. |
| `doc_path` | string | **required** | Document path or short document ID from `doc-hub docs browse`. |
| `--section SECTION_PATH` | string | none | Restrict output to one section and its descendants. |
| `--json` | flag | false | Emit the same structured payload shape as the MCP read tool. |

### Examples

```bash
# Read a document
doc-hub docs read pydantic-ai agents

# Read one section and its descendants
doc-hub docs read pydantic-ai agents --section "Agents > Tools"

# Machine-readable output
doc-hub docs read pydantic-ai agents --json
```

---

## `doc-hub docs search`

Hybrid vector + full-text search across indexed documentation.

```bash
doc-hub docs search --corpus SLUG [--corpus SLUG ...] QUERY [options]
```

### Arguments and flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `query` | string | **required** | Search query (positional). |
| `--corpus SLUG` | string (repeatable) | **required** | Restrict results to one or more corpus slugs. Repeat the flag to search multiple corpora. |
| `--category CATEGORY` | string (repeatable) | no filter | Include only results in this category. Repeatable: `--category api --category guide`. Valid values: `api`, `guide`, `example`, `eval`, `other`. |
| `--exclude-category CATEGORY` | string (repeatable) | no filter | Exclude results in this category. Repeatable. Same valid values as `--category`. |
| `--limit N` | int | 5 | Maximum number of results to return. |
| `--offset N` | int | 0 | Skip the first N results (pagination). |
| `--min-similarity FLOAT` | float | 0.55 | Minimum cosine similarity threshold. Applied in Python after SQL execution, not in the SQL WHERE clause. Results below this value are dropped. |
| `--source-url-prefix STR` | string | no filter | Restrict results to source URLs starting with this string. |
| `--section-path-prefix STR` | string | no filter | Restrict results to section paths starting with this string. |
| `--vector-limit N` | int | 20 | KNN candidate pool size. Advanced tuning. |
| `--text-limit N` | int | 10 | BM25 candidate pool size. Advanced tuning. |
| `--rrfk N` | int | 60 | Reciprocal Rank Fusion k constant. Advanced tuning. |
| `--language STR` | string | `english` | PostgreSQL text-search language configuration. Advanced tuning. Must be one of the supported values. |
| `--json` | flag | false | Output results as JSON instead of the default human-readable format. |

### Examples

```bash
# Search one corpus
doc-hub docs search --corpus fastapi "how do I add middleware?"

# Search multiple corpora
doc-hub docs search --corpus pydantic-ai --corpus fastapi "retry middleware"

# Filter to API reference only
doc-hub docs search --corpus pydantic-ai "Agent" --category api --limit 10

# Machine-readable output
doc-hub docs search --corpus pydantic-ai "validators" --json
```

---

## `doc-hub pipeline run`

Run the fetch → parse → embed → index → tree pipeline for a single corpus.

```bash
doc-hub pipeline run --corpus SLUG [options]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--corpus SLUG` | string | **required** | Corpus slug. Must exist in the `doc_corpora` table. |
| `--stage {fetch,parse,embed,index,tree}` | choice | all stages | Run only this stage instead of the full pipeline. |
| `--clean` | flag | false | Wipe all local data for the corpus before starting (`shutil.rmtree` on the corpus directory). |
| `--skip-download` | flag | false | Skip the fetch step and re-use the existing `raw/` directory. |
| `--full-reindex` | flag | false | After upserting, delete DB rows whose `content_hash` is no longer in the current chunk set. |
| `--retry-failed` | flag | false | Retry only previously failed downloads. |
| `--workers N` | int | 20 | Download concurrency for the fetch stage. |
| `--retries N` | int | 3 | HTTP retry count per URL. |

### Examples

```bash
# Full pipeline for a corpus
doc-hub pipeline run --corpus pydantic-ai

# Fetch stage only
doc-hub pipeline run --corpus pydantic-ai --stage fetch

# Rebuild only the persisted document tree
doc-hub pipeline run --corpus pydantic-ai --stage tree
```

---

## `doc-hub pipeline add`

Register a new documentation corpus and run the indexing pipeline.

```bash
doc-hub pipeline add <name> --strategy {llms_txt,sitemap,git_repo,local_dir} [options]
```

### Arguments and flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `name` | string | **required** | Human-readable corpus name (positional). |
| `--strategy` | choice | **required** | Fetcher plugin. Choices: `llms_txt`, `sitemap`, `git_repo`, `local_dir`. |
| `--url URL` | string | none | URL for `llms_txt`, `sitemap`, or `git_repo` strategies. |
| `--path PATH` | string | none | Local directory path for the `local_dir` strategy. |
| `--slug SLUG` | string | slugified name | Override the auto-derived slug. |
| `--no-index` | flag | false | Register the corpus only; skip the pipeline run. |
| `--url-pattern PATTERN` | string | none | URL pattern filter (llms_txt only). |
| `--base-url URL` | string | none | Base URL override (llms_txt only). |
| `--workers N` | int | 20 | Download concurrency (llms_txt only). |
| `--retries N` | int | 3 | HTTP retry count per URL (llms_txt only). |
| `--branch BRANCH` | string | none | Git branch to check out (git_repo only). |
| `--docs-dir DIR` | string | none | Subdirectory containing docs (git_repo only). |

### Examples

```bash
# Register and index a corpus from an llms.txt file
doc-hub pipeline add "Pydantic AI" --strategy llms_txt --url https://ai.pydantic.dev/llms.txt

# Register a local directory corpus without running the pipeline
doc-hub pipeline add "My Docs" --strategy local_dir --path ./my-docs --no-index
```

---

## `doc-hub pipeline clean`

Clean fetched markdown files for a corpus via an LLM. Strips navigation menus, footers, breadcrumbs, and other scraping artifacts while preserving documentation content.

```bash
doc-hub pipeline clean <slug>
```

### Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `slug` | string | **required** | Corpus slug (positional). |

### Behavior

1. Loads the corpus manifest and identifies files where `content_hash != clean_hash` (changed since last clean or never cleaned).
2. Sends each file through an OpenAI-compatible LLM to strip navigation, footers, and artifacts.
3. Writes cleaned content back to disk and updates the manifest with `clean_hash` values.
4. Sets `clean: true` in the corpus's `fetch_config` so future fetches auto-clean.

### Required environment variables

| Variable | Description |
|---|---|
| `DOC_HUB_CLEAN_MODEL` | Model slug (e.g. `gpt-4o-mini`, `claude-sonnet-4-20250514`) |
| `DOC_HUB_CLEAN_API_KEY` | API key for the endpoint |
| `DOC_HUB_CLEAN_BASE_URL` | Base URL (e.g. `https://api.openai.com/v1`) |
| `DOC_HUB_CLEAN_PROMPT` | Optional system prompt override (has a built-in default) |

### Examples

```bash
# Clean a corpus
doc-hub pipeline clean camoufox

# Future fetches for this corpus will auto-clean
doc-hub pipeline logs camoufox
```

---

## `doc-hub pipeline logs`

Run the pipeline for an existing corpus with visible log output.

```bash
doc-hub pipeline logs <slug>
```

### Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `slug` | string | **required** | Corpus slug (positional). |

### Examples

```bash
# Run the pipeline with visible logs
doc-hub pipeline logs pydantic-ai
```

---

## `doc-hub pipeline sync-all`

Run the full pipeline for every enabled corpus in the database.

```bash
doc-hub pipeline sync-all
```

### Behavior

1. Opens a DB pool and ensures the schema is up to date.
2. Queries all corpora with `enabled = true` in `doc_corpora`.
3. Runs the full fetch → parse → embed → index → tree pipeline for each corpus in sequence.
4. If a corpus fails, the error is caught and logged, and processing continues with the next corpus.
5. Prints a summary table when all corpora have been processed.

---

## `doc-hub pipeline eval`

Evaluate retrieval quality using hand-curated test queries. Reports Precision@N and Mean Reciprocal Rank (MRR).

```bash
doc-hub pipeline eval [--corpus SLUG | --all] [options]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--corpus SLUG` | string | — | Evaluate this corpus only. Mutually exclusive with `--all`. |
| `--all` | flag | — | Run evals for all corpora that have eval files. Mutually exclusive with `--corpus`. |
| `--limit N` | int | 5 | Results per query. This is the N in Precision@N. |
| `--verbose` | flag | false | Show per-query hit/miss details during the run. |
| `--output PATH` | string | none | Write the JSON evaluation report to this file. |
| `--min-precision FLOAT` | float | 0.80 | Minimum Precision@N required to pass. |
| `--min-mrr FLOAT` | float | 0.60 | Minimum MRR required to pass. |

### Examples

```bash
# Eval a single corpus
doc-hub pipeline eval --corpus pydantic-ai

# Eval all corpora with eval files
doc-hub pipeline eval --all
```

---

## `doc-hub serve mcp`

Start the doc-hub MCP server, which exposes search, corpus management, and document browse/read tools to LLMs via the Model Context Protocol.

```bash
doc-hub serve mcp [options]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--transport {stdio,sse,streamable-http}` | choice | `stdio` | Transport protocol. |
| `--host STR` | string | `127.0.0.1` | Bind address for SSE and streamable-http transports. Ignored for stdio. |
| `--port N` | int | 8340 | Port for SSE and streamable-http transports. Ignored for stdio. |

### Examples

```bash
# stdio — for Claude Code / Claude Desktop (default)
doc-hub serve mcp

# SSE transport on default port 8340
doc-hub serve mcp --transport sse

# Streamable HTTP
doc-hub serve mcp --transport streamable-http --port 8340
```
