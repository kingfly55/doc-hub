# CLI Reference

doc-hub ships five console scripts. This document covers every flag, output format, and exit code for each.

---

## `doc-hub-pipeline`

Run the fetch → parse → embed → index pipeline for a single corpus.

```
doc-hub-pipeline --corpus SLUG [options]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--corpus SLUG` | string | **required** | Corpus slug. Must exist in the `doc_corpora` table. |
| `--stage {fetch,parse,embed,index}` | choice | all stages | Run only this stage instead of the full pipeline. |
| `--clean` | flag | false | Wipe all local data for the corpus before starting (`shutil.rmtree` on the corpus directory). |
| `--skip-download` | flag | false | Skip the fetch step and re-use the existing `raw/` directory. |
| `--full-reindex` | flag | false | After upserting, delete DB rows whose `content_hash` is no longer in the current chunk set (removes stale chunks). |
| `--retry-failed` | flag | false | Retry only previously failed downloads. |
| `--workers N` | int | 20 | Download concurrency for the fetch stage. |
| `--retries N` | int | 3 | HTTP retry count per URL. |

### Corpus lookup

The corpus slug is resolved via `get_corpus()` against the `doc_corpora` table. If the slug is not found, the command prints an error and exits with code 1. Register a corpus first using the MCP `add_corpus_tool` or by inserting a row directly.

### Logging

Always logs at INFO level. Set `LOGLEVEL=DEBUG` in the environment to get DEBUG output from library code.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Corpus not found in `doc_corpora` |

### Examples

```bash
# Full pipeline for a corpus
doc-hub-pipeline --corpus pydantic-ai

# Fetch stage only
doc-hub-pipeline --corpus pydantic-ai --stage fetch

# Clean all local data then run a full pipeline with stale-row removal
doc-hub-pipeline --corpus pydantic-ai --clean --full-reindex

# Re-embed and re-index without re-downloading
doc-hub-pipeline --corpus pydantic-ai --skip-download --stage embed

# Increase download concurrency and retries
doc-hub-pipeline --corpus pydantic-ai --workers 40 --retries 5
```

---

## `doc-hub-search`

Hybrid vector + full-text search across indexed documentation.

```
doc-hub-search QUERY [options]
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
| `--language STR` | string | `english` | PostgreSQL text-search language configuration. Advanced tuning. Must be one of the 29 supported values (see below). |
| `--json` | flag | false | Output results as JSON instead of the default human-readable format. |

### Supported `--language` values

The language is validated against a whitelist to prevent SQL injection. Accepted values:

`arabic`, `armenian`, `basque`, `catalan`, `danish`, `dutch`, `english`, `finnish`, `french`, `german`, `greek`, `hindi`, `hungarian`, `indonesian`, `irish`, `italian`, `lithuanian`, `nepali`, `norwegian`, `portuguese`, `romanian`, `russian`, `serbian`, `simple`, `spanish`, `swedish`, `tamil`, `turkish`, `yiddish`

Passing an invalid language raises a `ValueError` before any query is executed.

### Logging

Logs at WARNING level by default. Set `LOGLEVEL=DEBUG` to enable verbose query logging (SQL parameters, similarity filter statistics).

### Exit codes

Always exits with code 0.

### Human-readable output format

```
Search results for: 'how do I handle retries?'
Corpus: pydantic-ai
----------------------------------------------------------------------

[1] Retries
    Corpus:     pydantic-ai
    Path:       pydantic-ai/agents/retries
    Category:   guide
    Lines:      142-178
    Similarity: 0.821  |  RRF Score: 0.03125
    URL:        https://docs.pydantic.ai/...
    Preview:    By default, if a tool call raises a ModelRetry...
```

### JSON output format

With `--json`, prints a JSON array. Each object has the following keys:

| Key | Type | Description |
|-----|------|-------------|
| `id` | int | Row ID in `doc_chunks` |
| `corpus_id` | string | Corpus slug |
| `heading` | string | Section heading |
| `section_path` | string | Hierarchical section path |
| `source_url` | string | Source URL of the document |
| `score` | float | RRF score (ranking transparency) |
| `similarity` | float | Cosine similarity to query |
| `category` | string | `api`, `guide`, `example`, `eval`, or `other` |
| `start_line` | int | 1-indexed start line in source file |
| `end_line` | int | 1-indexed end line (inclusive) |
| `content_preview` | string | First 200 characters of chunk content |

### Examples

```bash
# Basic search across all corpora
doc-hub-search "how do I handle retries?"

# Scoped to one corpus
doc-hub-search "how do I add middleware?" --corpus fastapi

# Filter to API reference only
doc-hub-search "Agent" --corpus pydantic-ai --category api --limit 10

# Multiple category filters
doc-hub-search "streaming" --category api --category guide

# Exclude examples, get more candidates
doc-hub-search "dependency injection" --exclude-category example --vector-limit 40

# Paginate results
doc-hub-search "tools" --limit 5 --offset 10

# Machine-readable output
doc-hub-search "validators" --corpus pydantic-ai --json

# Debug mode
LOGLEVEL=DEBUG doc-hub-search "Agent" --corpus pydantic-ai
```

---

## `doc-hub-mcp`

Start the doc-hub MCP server, which exposes search and corpus management tools to LLMs via the Model Context Protocol.

```
doc-hub-mcp [options]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--transport {stdio,sse,streamable-http}` | choice | `stdio` | Transport protocol. |
| `--host STR` | string | `127.0.0.1` | Bind address for SSE and streamable-http transports. Ignored for stdio. |
| `--port N` | int | 8340 | Port for SSE and streamable-http transports. Ignored for stdio. |

### MCP tools exposed

- `search_docs_tool` — hybrid vector + full-text search
- `list_corpora_tool` — list all registered corpora
- `add_corpus_tool` — register a new corpus
- `refresh_corpus_tool` — re-run the full pipeline for a corpus

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Normal shutdown |

### Examples

```bash
# stdio — for Claude Code / Claude Desktop (default)
doc-hub-mcp

# SSE transport on default port 8340
doc-hub-mcp --transport sse

# SSE on a custom port
doc-hub-mcp --transport sse --port 9000

# Streamable HTTP
doc-hub-mcp --transport streamable-http --port 8340

# Bind to all interfaces (for Docker / remote access)
doc-hub-mcp --transport sse --host 0.0.0.0 --port 8340
```

### MCP client configuration

**stdio (spawn per session):**
```json
{
  "mcpServers": {
    "doc-hub": {
      "command": "uv",
      "args": ["run", "--package", "doc-hub", "doc-hub-mcp"],
      "env": { "GEMINI_API_KEY": "<key>" }
    }
  }
}
```

**SSE (connect to running service):**
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

## `doc-hub-eval`

Evaluate retrieval quality using hand-curated test queries. Reports Precision@N and Mean Reciprocal Rank (MRR).

```
doc-hub-eval [--corpus SLUG | --all] [options]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--corpus SLUG` | string | — | Evaluate this corpus only. Mutually exclusive with `--all`. |
| `--all` | flag | — | Run evals for all corpora that have eval files. Mutually exclusive with `--corpus`. |
| `--limit N` | int | 5 | Results per query. This is the N in Precision@N. |
| `--verbose` | flag | false | Show per-query hit/miss details during the run. |
| `--output PATH` | string | none | Write the JSON evaluation report to this file. When multiple corpora are evaluated, writes a JSON array; for a single corpus, writes a single object. |
| `--min-precision FLOAT` | float | 0.80 | Minimum Precision@N required to pass. |
| `--min-mrr FLOAT` | float | 0.60 | Minimum MRR required to pass. |

### Default behavior

Running `doc-hub-eval` with no arguments (no `--corpus`, no `--all`) behaves the same as `--all`: it runs evals for every corpus that has an eval file. This is because the argument parser uses the pattern `elif args.all or True:`, which means the all-corpora branch is always taken when `--corpus` is not specified.

### Eval file discovery

Eval files are JSON files at `{eval_dir}/{corpus_slug}.json`. The eval directory is resolved in this order:

1. `DOC_HUB_EVAL_DIR` environment variable
2. `{data_root}/eval/` (XDG data directory fallback)

### Logging

Logs at WARNING level by default. Set `LOGLEVEL=DEBUG` to enable verbose logging.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | All evaluated corpora passed both thresholds |
| 1 | Any corpus failed a threshold, or no eval files were found |

### Output format

Each corpus produces a report block:

```
========================================
RETRIEVAL QUALITY EVALUATION — PYDANTIC-AI
========================================
Queries run:      25
Hits in top-5:    22
Precision@5:      0.880
MRR:              0.743

STATUS: PASS ✓  (P@5=0.880 >= 0.8, MRR=0.743 >= 0.6)
```

If `--verbose` is set, each query result is printed inline:

```
  [01/25] [q-agents-tools] ✓ HIT  RR=1.000  sim=0.834  rank 1
         Query: how do I define a tool?
         Top:   'Tools' (path: pydantic-ai/tools/overview)
```

If a corpus fails:

```
STATUS: FAIL ✗
  ✗ P@5=0.600 < 0.800 (need 0.200 more)
  ✗ MRR=0.500 < 0.600 (need 0.100 more)
```

Failed queries (no relevant result in the top N) are listed by query ID and text.

### Examples

```bash
# Eval a single corpus
doc-hub-eval --corpus pydantic-ai

# Eval all corpora with eval files
doc-hub-eval --all

# Equivalent to --all (default behavior)
doc-hub-eval

# Verbose output + JSON report
doc-hub-eval --corpus pydantic-ai --verbose --output report.json

# Lower thresholds for a new corpus
doc-hub-eval --corpus fastapi --min-precision 0.70 --min-mrr 0.50

# Debug mode
LOGLEVEL=DEBUG doc-hub-eval --corpus pydantic-ai
```

---

## `doc-hub-sync-all`

Run the full pipeline for every enabled corpus in the database.

```
doc-hub-sync-all
```

### Flags

None.

### Behavior

1. Opens a DB pool and ensures the schema is up to date.
2. Queries all corpora with `enabled = true` in `doc_corpora`.
3. Runs the full fetch → parse → embed → index pipeline for each corpus in sequence.
4. If a corpus fails, the error is caught and logged, and processing continues with the next corpus.
5. Prints a summary table when all corpora have been processed.

### Logging

Always logs at INFO level.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | All corpora processed (including corpora that failed — errors are caught per-corpus) |

### Output

```
Doc-Hub Sync Summary:
  pydantic-ai: 312 new, 0 updated, 0 removed (total: 312)
  fastapi: FAILED — ConnectionRefusedError(...)
  openai: 0 new, 5 updated, 0 removed (total: 287)
```

Each line shows: `{slug}: {inserted} new, {updated} updated, {deleted} removed (total: {total})`, or `FAILED — {error}` if the corpus raised an exception.
