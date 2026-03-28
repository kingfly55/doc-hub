# Design: `doc-hub pipeline add` and `pipeline logs` Subcommands

**Date:** 2026-03-27

## Overview

Add two new subcommands under the existing `doc-hub pipeline` group:

1. **`pipeline add`** — Register a new corpus and run the indexing pipeline
2. **`pipeline logs`** — Show pipeline run logs for a corpus

These sit alongside the existing `pipeline run`, `pipeline sync-all`, and `pipeline eval`.

## `pipeline add`

### Interface

```
doc-hub pipeline add <name> --strategy <strategy> [strategy-specific flags] [--slug <slug>] [--no-index]
```

### Required arguments

| Argument | Description |
|----------|-------------|
| `name` | Human-readable corpus name (positional) |
| `--strategy` | Fetcher plugin name: `llms_txt`, `sitemap`, `git_repo`, `local_dir` |

### Global optional flags

| Flag | Description |
|------|-------------|
| `--slug` | Override auto-derived slug (default: slugify name, e.g. "Pydantic AI" -> `pydantic-ai`) |
| `--no-index` | Register the corpus only; skip pipeline run |

### Strategy-specific flags

Each strategy enforces its own required flags at the argparse level.

| Strategy | Required | Optional |
|----------|----------|----------|
| `llms_txt` | `--url` | `--url-pattern`, `--base-url`, `--workers`, `--retries` |
| `sitemap` | `--url` | |
| `git_repo` | `--url` | `--branch`, `--docs-dir` |
| `local_dir` | `--path` | |

### Behavior

1. Derive slug from name using simple slugification (lowercase, replace spaces/special chars with hyphens), unless `--slug` is provided.
2. Build `fetch_config` dict from strategy-specific flags.
3. Upsert corpus into DB via `db.upsert_corpus()`.
4. Unless `--no-index`, run the full pipeline via `run_pipeline()`.
5. Pipeline errors surface normally to stdout/stderr.

### Examples

```bash
doc-hub pipeline add "Pydantic AI" --strategy llms_txt --url https://ai.pydantic.dev/llms.txt
doc-hub pipeline add "FastAPI" --strategy sitemap --url https://fastapi.tiangolo.com/sitemap.xml
doc-hub pipeline add "Anthropic SDK" --strategy git_repo --url https://github.com/anthropics/anthropic-sdk-python.git --docs-dir docs
doc-hub pipeline add "My Docs" --strategy local_dir --path ./my-docs/
doc-hub pipeline add "Pydantic AI" --strategy llms_txt --url https://ai.pydantic.dev/llms.txt --slug pai --no-index
```

## `pipeline logs`

### Interface

```
doc-hub pipeline logs <slug>
```

### Required arguments

| Argument | Description |
|----------|-------------|
| `slug` | Corpus slug (positional) |

### Behavior

Show pipeline run logs for the given corpus to stdout. For now this is a simple pass-through of pipeline output — no log persistence or querying. Will be improved later.

## Implementation

### Files to change

- **Edit:** `src/doc_hub/cli/pipeline.py` — Add `add` and `logs` subcommands to `register_pipeline_group()`
- **Edit:** `src/doc_hub/cli/main.py` — No changes needed (pipeline group already registered)

### Reuse

- `db.upsert_corpus()` for corpus registration
- `run_pipeline()` for indexing
- `models.Corpus` dataclass for constructing the corpus object
- Existing `discovery.get_registry()` for validating strategy names

### No new modules or dependencies required.
