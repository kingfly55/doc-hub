# CLI Reference

The canonical command surface for doc-hub is a single executable:

```bash
doc-hub ...
```

For a concise local reference after install, use `man doc-hub`.

The command tree is organized into three groups:

- `doc-hub docs ...`
- `doc-hub pipeline ...`
- `doc-hub serve ...`

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

Human-readable mode prints the corpus slug followed by an indented preorder tree. Group nodes are marked with `[group]`. Concrete documents include total character count and section count.

### Examples

```bash
# Browse the whole corpus
doc-hub docs browse pydantic-ai

# Browse just one subtree
doc-hub docs browse pydantic-ai --path api

# Limit subtree depth
doc-hub docs browse pydantic-ai --path api --depth 1

# Machine-readable output
doc-hub docs browse pydantic-ai --json
```

---

## `doc-hub docs read`

Read a document or a specific section from a corpus.

```bash
doc-hub docs read CORPUS DOC_PATH [options]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `corpus` | string | **required** | Corpus slug containing the document. |
| `doc_path` | string | **required** | Document path to read. |
| `--section SECTION_PATH` | string | none | Restrict output to one section and its descendants. |
| `--force` | flag | false | Force full content output for large documents. |
| `--json` | flag | false | Emit the same structured payload shape as the MCP read tool. |

### Large-document behavior

If the selected document exceeds 20,000 characters and neither `--force` nor `--section` is provided, the command prints an outline instead of the full body. Use `--section` to read one section or `--force` to print everything.

### Examples

```bash
# Read a document
doc-hub docs read pydantic-ai agents

# Read one section and its descendants
doc-hub docs read pydantic-ai agents --section "Agents > Tools"

# Force full output for a large document
doc-hub docs read pydantic-ai agents --force

# Machine-readable output
doc-hub docs read pydantic-ai agents --json
```

---

## `doc-hub docs search`

Hybrid vector + full-text search across indexed documentation.

```bash
doc-hub docs search QUERY [options]
```

### Arguments and flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `query` | string | **required** | Search query (positional). |
| `--corpus SLUG` | string | search all | Restrict results to this corpus slug. |
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
# Basic search across all corpora
doc-hub docs search "how do I handle retries?"

# Scoped to one corpus
doc-hub docs search "how do I add middleware?" --corpus fastapi

# Filter to API reference only
doc-hub docs search "Agent" --corpus pydantic-ai --category api --limit 10

# Machine-readable output
doc-hub docs search "validators" --corpus pydantic-ai --json
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
