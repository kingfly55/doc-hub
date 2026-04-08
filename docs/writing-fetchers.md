# Writing Fetcher Plugins for doc-hub

This guide explains how to implement a custom fetcher and register it with doc-hub's plugin system.

## How fetchers work

The pipeline has four stages: **fetch → parse → embed → index**. Fetchers are responsible for the first stage only: producing a directory of `.md` files. Everything downstream (parsing, embedding, indexing, search) is corpus-agnostic.

Fetchers are discovered at runtime via Python's `importlib.metadata` entry points (primary mechanism) or by dropping a `.py` file into `~/.local/share/doc-hub/plugins/fetchers/` (local plugin files).

## The Fetcher protocol

A fetcher is any class whose instances match the `Fetcher` protocol defined in `doc_hub.protocols`:

```python
from pathlib import Path

class MyFetcher:
    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict,
        output_dir: Path,
    ) -> Path:
        """Download docs and write .md files to output_dir.

        Args:
            corpus_slug: Unique corpus identifier (for logging/manifest paths).
            fetch_config: Dict from the corpus's fetch_config JSONB column.
                          Contains strategy-specific settings (e.g. URL, auth token).
            output_dir:   Directory where .md files should be written.
                          Created if it does not exist.

        Returns:
            Path to the output directory (same as output_dir).
        """
        ...
```

No inheritance from a base class is required — structural typing only.

## Registering via entry points (recommended)

The standard way to ship a fetcher is as a Python package with an entry point declaration.

### 1. Implement the fetcher class

```python
# my_wiki_fetcher/fetcher.py
from pathlib import Path

class WikiApiFetcher:
    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict,
        output_dir: Path,
    ) -> Path:
        api_url = fetch_config["api_url"]
        space = fetch_config["space"]
        output_dir.mkdir(parents=True, exist_ok=True)
        # ... fetch pages, convert to markdown, write to output_dir ...
        return output_dir
```

### 2. Declare the entry point in `pyproject.toml`

```toml
[project.entry-points."doc_hub.fetchers"]
wiki_api = "my_wiki_fetcher.fetcher:WikiApiFetcher"
```

### 3. Install the package

```bash
pip install -e .   # or: uv pip install -e .
```

**Important:** Entry points are discovered via `importlib.metadata`, which reads installed package metadata. The package must be (re)installed after any change to `[project.entry-points]` for the new fetcher to appear.

### 4. Register a corpus using your fetcher

```json
{
  "tool": "add_corpus_tool",
  "args": {
    "slug": "my-wiki",
    "name": "My Wiki",
    "strategy": "wiki_api",
    "config": {"api_url": "https://wiki.example.com/api", "space": "DOCS"}
  }
}
```

Or directly in SQL:

```sql
INSERT INTO doc_corpora (slug, name, fetch_strategy, fetch_config)
VALUES (
  'my-wiki', 'My Wiki', 'wiki_api',
  '{"api_url": "https://wiki.example.com/api", "space": "DOCS"}'::jsonb
);
```

## Registering as a local plugin file

For quick prototyping without creating a full package:

1. Create `~/.local/share/doc-hub/plugins/fetchers/my_fetcher.py`
2. Decorate your class with `@fetcher_plugin("name")`:

```python
from doc_hub.discovery import fetcher_plugin
from pathlib import Path

@fetcher_plugin("my_local_fetcher")
class MyLocalFetcher:
    async def fetch(self, corpus_slug, fetch_config, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        # ...
        return output_dir
```

Local plugin files are loaded on each `get_registry()` call. Entry points take precedence over local files on name collision.

## Testing protocol conformance

Use `isinstance()` with the `@runtime_checkable` protocol to verify your fetcher conforms:

```python
from doc_hub.protocols import Fetcher
from my_wiki_fetcher.fetcher import WikiApiFetcher

assert isinstance(WikiApiFetcher(), Fetcher), "WikiApiFetcher does not conform to Fetcher protocol"
```

Note: `@runtime_checkable` only checks method *names*, not signatures. Static type checkers (mypy, pyright) catch signature mismatches at development time.

## Fetcher contract

A fetcher **must**:

1. **Implement `async def fetch(self, corpus_slug, fetch_config, output_dir) -> Path`** matching the protocol.
2. **Write `.md` files to `output_dir`** — the parse stage scans for `*.md` files.
3. **Be idempotent** — running twice should not corrupt state.

A fetcher **should**:

4. **Create `output_dir`** if it doesn't exist (`output_dir.mkdir(parents=True, exist_ok=True)`).
5. **Read config from `fetch_config`** — fail fast with a clear error if required keys are missing.
6. **Write `manifest.json`** — enables incremental sync so only new/changed URLs are downloaded on subsequent runs. See the built-in `LlmsTxtFetcher` for an example implementation using `load_manifest`, `compute_manifest_diff`, and `write_manifest` from `doc_hub._builtins.fetchers.llms_txt`.

A fetcher **must not**:

7. **Touch the database** — fetchers are pure I/O.
8. **Embed or chunk content** — that's the parse and embed stages' job.
9. **Modify files outside `output_dir`**.

## Built-in fetchers

doc-hub ships four built-in fetchers registered via entry points:

| Name | Class | Description |
|------|-------|-------------|
| `llms_txt` | `LlmsTxtFetcher` | Downloads pages listed in an `llms.txt` index file. Supports `url_suffix` (e.g. `".md"`) for sites that list bare URLs but serve pages with an extension. Supports `url_excludes` / `url_exclude_pattern` for skipping paths. |
| `direct_url` | `DirectUrlFetcher` | Downloads one or more URLs directly as markdown files. Useful for monolithic docs (e.g. `llms-full.txt`). Config keys: `url` (single) or `urls` (list), optional `filenames` map. |
| `local_dir` | `LocalDirFetcher` | Copies/links a local directory of markdown files |
| `sitemap` | `SitemapFetcher` | Crawls a sitemap XML and downloads pages as markdown. Supports `url_prefix` for subdirectory inclusion and `url_excludes` / `url_exclude_pattern` for path exclusion. |
| `git_repo` | `GitRepoFetcher` | Clones a git repository and extracts markdown files |

> **Shared helper**: both `llms_txt` and `sitemap` use `doc_hub._builtins.fetchers.url_filter.build_exclude_filter()` to compile exclusion rules. If you're authoring a new web-based fetcher with similar needs, reuse that helper for consistent semantics (see `url_filter.py` for the full contract).

The authoritative protocol documentation is in `doc_hub/protocols.py`. This guide is a quickstart — refer to the protocol docstrings for the full contract.
