# Plugin Authoring Guide

Complete guide for writing fetcher, parser, and embedder plugins for doc-hub.

**See also:** [`docs/dev/protocols-reference.md`](protocols-reference.md) for exact method signatures and type constraints.

---

## 1. Plugin System Overview

doc-hub has three plugin points corresponding to pipeline stages:

| Plugin type | Stage | Responsibility |
|-------------|-------|----------------|
| `Fetcher`   | fetch | Download/locate source files, write `.md` files to a directory |
| `Parser`    | parse | Read `.md` files, return a list of `Chunk` objects |
| `Embedder`  | embed | Convert text strings to float vectors |

**Structural typing — no inheritance required.** Plugins are plain Python classes that implement the methods and properties declared in `doc_hub.protocols`. Static type checkers (mypy, pyright) verify conformance at development time; `isinstance()` checks verify it at registration time.

Two registration mechanisms exist:

1. **Entry points** (`importlib.metadata`) — primary. Ship as a Python package with `[project.entry-points]` in `pyproject.toml`.
2. **Local plugin files** — secondary. Drop a `.py` file into `{data_root}/plugins/{fetchers,parsers,embedders}/`. No package install required.

Entry points take precedence on name collision.

---

## 2. Writing a Fetcher Plugin

### Protocol signature

```python
async def fetch(
    self,
    corpus_slug: str,
    fetch_config: dict[str, Any],
    output_dir: Path,
) -> Path:
```

Source: `src/doc_hub/protocols.py` — `Fetcher.fetch`

### Contract

**Must:**
- Write `.md` files into `output_dir` (the parse stage globs for `*.md`).
- Be idempotent — running twice must not corrupt state.
- Return the path to a directory containing `.md` files. Typically `output_dir`, but may be a different path for `local_dir`-style fetchers that return a pre-existing directory.

**Should:**
- Create `output_dir` with `output_dir.mkdir(parents=True, exist_ok=True)` before writing files.
- Write `manifest.json` to enable incremental sync and version provenance. New fetchers should prefer schema version 2 using helpers from `doc_hub.versions`.
- Read all strategy-specific settings from `fetch_config`; fail fast with a clear error on missing required keys.

**Must not:**
- Touch the database.
- Embed or chunk content.
- Modify files outside `output_dir`.

### Minimal example

Reference: `src/doc_hub/_builtins/fetchers/local_dir.py`

```python
from pathlib import Path
from typing import Any


class LocalDirFetcher:
    """Returns a pre-existing local directory of markdown files."""

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        path = Path(fetch_config["path"])
        if not path.is_dir():
            raise FileNotFoundError(f"Local dir not found: {path}")
        return path  # output_dir is ignored — return the source path directly
```

### Full example with manifest-based incremental sync

For versioned corpora, prefer `doc_hub.versions.snapshot_manifest_from_downloads()` and `write_snapshot_manifest()` over ad-hoc manifest JSON. Schema-version-2 manifests carry `source_version`, immutable `snapshot_id`, `fetched_at`, content/url-set hashes, and aliases such as `latest`.


Reference: `src/doc_hub/_builtins/fetchers/llms_txt.py` — `LlmsTxtFetcher`

```python
import json
import hashlib
from pathlib import Path
from typing import Any


class MyApiFetcher:
    """Fetches pages from a custom API and writes them as .md files."""

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        api_url: str = fetch_config["api_url"]
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load existing manifest to skip unchanged pages
        manifest_path = output_dir / "manifest.json"
        existing: dict[str, str] = {}
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text())
            existing = {f["filename"]: f["content_hash"] for f in data.get("files", []) if f.get("success")}

        results = []
        pages = await _fetch_page_list(api_url)  # your implementation

        for page in pages:
            filename = page["slug"] + ".md"
            content = page["markdown"]
            content_hash = hashlib.sha256(content.encode()).hexdigest()

            # Write only if new or changed
            outfile = output_dir / filename
            if existing.get(filename) != content_hash or not outfile.exists():
                outfile.write_text(content)

            results.append({"filename": filename, "success": True, "content_hash": content_hash})

        manifest_path.write_text(json.dumps({"files": results}, indent=2))
        return output_dir
```

Key points from `LlmsTxtFetcher`:
- `load_manifest(output_dir)` returns `{filename: {"url": ..., "content_hash": ...}}` for successful entries.
- `compute_manifest_diff(upstream_urls, existing_manifest)` returns `(new_urls, removed_filenames)`.
- `write_manifest(results, output_dir)` writes the updated manifest.
- These helpers are in `doc_hub._builtins.fetchers.llms_txt` — import them directly or implement your own equivalent.

---

## 3. Writing a Parser Plugin

### Protocol signature

```python
def parse(
    self,
    input_dir: Path,
    *,
    corpus_slug: str,
    base_url: str,
) -> list[Chunk]:
```

Source: `src/doc_hub/protocols.py` — `Parser.parse`

**`parse()` is synchronous, not async.**

`corpus_slug` and `base_url` are keyword-only arguments (note the `*`).

### Contract

**Must:**
- Return a list of `Chunk` objects with **the core content fields** set (see below).
- Set `category = ""` — the core pipeline calls `derive_category(source_file)` from `parse.py` to fill this in. Setting a non-empty value here will be overwritten only if the field is empty; do not set it.
- Compute `content_hash = hashlib.sha256(content.encode()).hexdigest()` for each chunk.
- Set `char_count = len(content)`.

**Must not:**
- Merge or split chunks by size — `parse_docs()` in `parse.py` does this.
- Deduplicate chunks — `parse_docs()` deduplicates by `content_hash`.
- Derive `category` — leave it as `""`.

### What the core pipeline does after `parse()`

Executed in `parse_docs()` (`src/doc_hub/parse.py`):

1. Fills `category` via `derive_category(source_file)` for all chunks where `category == ""`.
2. **Merges tiny chunks** — `_merge_tiny_chunks(chunks, min_chars=500)`: chunks under 500 chars are appended to their predecessor (same `source_file` only).
3. **Splits mega chunks** — `_split_mega_chunks(chunks, max_chars=2500, target=1000)`: chunks over 2500 chars are split at paragraph boundaries into ~1000-char pieces.
4. **Deduplicates** by `content_hash`.
5. Writes `chunks.jsonl`.

### `Chunk` dataclass — the core content fields

Source: `src/doc_hub/parse.py`

```python
@dataclass
class Chunk:
    source_file: str    # Original filename, e.g. "models__openai.md"
    source_url: str     # Original URL from manifest, or "" if unknown
    section_path: str   # Heading hierarchy, e.g. "Configuration > API Keys"
    heading: str        # The section heading text
    heading_level: int  # 1-6 (ATX headings); 0 for preamble/no-heading content
    content: str        # Full section text including the heading line
    start_line: int     # 1-indexed line number in source file
    end_line: int       # 1-indexed last line number (inclusive)
    char_count: int     # len(content)
    content_hash: str   # hashlib.sha256(content.encode()).hexdigest()
    category: str       # MUST be "" — core pipeline derives this
    snapshot_id: str = "legacy"
    source_version: str = "latest"
    fetched_at: str | None = None
```

### `derive_category()` rules

Source: `src/doc_hub/parse.py` — `derive_category(source_file: str) -> str`

Applied in priority order against the lowercased filename (`.md` suffix removed):

| Match condition | Category |
|-----------------|----------|
| contains `"api"` or `"reference"` | `"api"` |
| contains `"example"` or `"tutorial"` | `"example"` |
| contains `"eval"` | `"eval"` |
| contains any of: `install`, `config`, `migration`, `quickstart`, `getting-started`, `getting_started`, `setup`, `guide`, `how-to`, `howto`, `changelog`, `contributing`, `readme` | `"guide"` |
| anything else | `"other"` |

### `embedding_input()` format

Source: `src/doc_hub/parse.py` — `embedding_input(chunk: Chunk) -> str`

The core pipeline calls `embedding_input(chunk)` to construct the text sent to the embedder:

```
"Document: {doc_name} | Section: {section_path}\n\n{content}"
```

where `doc_name` replaces `__` with `/` and strips the `.md` suffix from `source_file`. This prefix is critical for embedding quality — do not bypass it in a custom embedder.

### Example parser class

Reference: `src/doc_hub/_builtins/parsers/markdown.py` — `MarkdownParser`

```python
import hashlib
from pathlib import Path

from doc_hub.parse import Chunk


class MyCustomParser:
    """Parser for a custom documentation format."""

    def parse(
        self,
        input_dir: Path,
        *,
        corpus_slug: str,
        base_url: str,
    ) -> list[Chunk]:
        chunks: list[Chunk] = []

        for md_file in sorted(input_dir.glob("*.md")):
            if md_file.name.startswith("_"):
                continue  # skip internal files like _llms.txt

            text = md_file.read_text(errors="replace")
            lines = text.splitlines()

            # Example: treat entire file as one chunk (no heading split)
            content = text.strip()
            content_hash = hashlib.sha256(content.encode()).hexdigest()

            chunks.append(Chunk(
                source_file=md_file.name,
                source_url="",          # no manifest — leave empty
                section_path=md_file.stem,
                heading=md_file.stem,
                heading_level=0,
                content=content,
                start_line=1,
                end_line=len(lines),
                char_count=len(content),
                content_hash=content_hash,
                category="",            # INTENTIONALLY EMPTY — core fills this
            ))

        return chunks
```

For heading-based splitting, study `MarkdownParser._split_into_chunks()` in `src/doc_hub/_builtins/parsers/markdown.py`. It handles:
- ATX headings (`#` through `######`) detected outside fenced code blocks.
- Preamble content before the first heading (emitted as `heading_level=0`).
- Hierarchical `section_path` built from a stack of `(level, title)` pairs joined by `" > "`.
- Manifest loading via `_load_manifest(input_dir)` → `{filename: url}` to populate `source_url`.

---

## 4. Writing an Embedder Plugin

### Protocol signatures

Source: `src/doc_hub/protocols.py` — `Embedder`

```python
@property
def model_name(self) -> str: ...          # unique model ID, part of cache key
@property
def dimensions(self) -> int: ...          # output vector length (e.g. 768, 1536)
@property
def task_type_document(self) -> str: ...  # e.g. "RETRIEVAL_DOCUMENT", or "" if N/A
@property
def task_type_query(self) -> str: ...     # e.g. "RETRIEVAL_QUERY", or "" if N/A

async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
async def embed_query(self, query: str) -> list[float]: ...
```

Both methods are **async**.

### Contract

**Must:**
- Return one vector per input text in `embed_batch`. `len(return_value) == len(texts)`.
- Return vectors of length `== self.dimensions`.
- Use `task_type_document` when calling `embed_batch`, and `task_type_query` when calling `embed_query`. Return `""` for either if the underlying API doesn't support task types.
- `dimensions` must match the `DOC_HUB_VECTOR_DIM` deployment configuration (default 768). The core pipeline validates this before embedding starts.

**Must not:**
- L2-normalize output vectors — `l2_normalize()` in `embed.py` handles this.
- Cache embeddings — the core pipeline manages an embedding cache keyed by `(content_hash, model_name, dimensions)`.
- Batch internally — the core pipeline calls `embed_batch()` with pre-sized batches (default 50 items).

### What the core pipeline does

Executed in `embed_chunks()` (`src/doc_hub/embed.py`):

1. Validates `embedder.dimensions == get_vector_dim()` (`DOC_HUB_VECTOR_DIM`).
2. Loads the per-corpus embedding cache from `embeddings_cache.jsonl` (keyed by `content_hash + model_name + dimensions`).
3. Calls `embedding_input(chunk)` to build the text for each cache-miss chunk.
4. Calls `embedder.embed_batch(texts)` in batches of 100 (default, Gemini API max).
5. L2-normalizes every returned vector via `l2_normalize()`.
6. Appends normalized vectors to cache.
7. Uses a sliding-window rate limiter (`DOC_HUB_EMBED_RPM`/`DOC_HUB_EMBED_TPM`) to pace batches — waits only as long as needed.
8. Calls `embedder.embed_query(query)` during search (not indexing).

### Embedding cache JSONL format

Each line in `embeddings_cache.jsonl`:

```json
{"content_hash": "abc123...", "model": "gemini-embedding-001", "dimensions": 768, "embedding": [0.1, -0.3, ...]}
```

Changing `model_name` or `dimensions` invalidates all cached entries for that model (stale entries are silently skipped on load).

### `l2_normalize()` implementation

Source: `src/doc_hub/embed.py`

```python
import numpy as np

def l2_normalize(vec: list[float]) -> list[float]:
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return vec
    return (arr / norm).tolist()
```

### Example embedder class

Reference: `src/doc_hub/_builtins/embedders/gemini.py` — `GeminiEmbedder`

```python
import asyncio
import os
from typing import Any


class MyEmbedder:
    """Embedder using a hypothetical REST API."""

    def __init__(self) -> None:
        self._client: Any = None
        # Read config from env vars — do not hard-code credentials
        self._model = os.environ.get("MY_EMBED_MODEL", "my-model-v1")
        self._dim = int(os.environ.get("MY_EMBED_DIM", "768"))

    def _get_client(self) -> Any:
        # Lazy init — defer until first use so env vars can be set after import
        if self._client is None:
            api_key = os.environ.get("MY_API_KEY")
            if not api_key:
                raise RuntimeError("MY_API_KEY not set")
            from my_embed_sdk import Client
            self._client = Client(api_key=api_key)
        return self._client

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dim

    @property
    def task_type_document(self) -> str:
        return ""  # this API does not support task types

    @property
    def task_type_query(self) -> str:
        return ""

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed texts. Core pipeline handles batching, caching, L2 normalization."""
        client = self._get_client()
        # Wrap synchronous SDK calls in asyncio.to_thread
        response = await asyncio.to_thread(client.embed, texts=texts, model=self._model)
        return [emb.vector for emb in response.embeddings]

    async def embed_query(self, query: str) -> list[float]:
        client = self._get_client()
        response = await asyncio.to_thread(client.embed, texts=[query], model=self._model)
        return response.embeddings[0].vector
```

Key patterns from `GeminiEmbedder`:
- Lazy client init in `_get_client()` — avoids import-time side effects, respects env vars set after import.
- Retry logic with rate-limit discrimination: distinguish per-minute (429/PerMinute → wait 65s) from per-day (429/PerDay → wait 300s) from transient errors (exponential backoff).
- `asyncio.to_thread()` for synchronous SDK calls.

---

## 5. Registering via Entry Points (Recommended)

Entry points are discovered via `importlib.metadata` at startup. The package must be installed for its entry points to appear.

### Entry point group names

| Plugin type | Group name |
|-------------|-----------|
| Fetcher     | `doc_hub.fetchers` |
| Parser      | `doc_hub.parsers` |
| Embedder    | `doc_hub.embedders` |

### `pyproject.toml` example

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/my_doc_hub_plugin"]

[project]
name = "my-doc-hub-plugin"
version = "0.1.0"
dependencies = ["doc-hub"]

[project.entry-points."doc_hub.fetchers"]
my_api = "my_doc_hub_plugin.fetcher:MyApiFetcher"

[project.entry-points."doc_hub.parsers"]
my_format = "my_doc_hub_plugin.parser:MyFormatParser"

[project.entry-points."doc_hub.embedders"]
my_model = "my_doc_hub_plugin.embedder:MyEmbedder"
```

Format: `name = "package.module:ClassName"`

Classes are instantiated with no args — `entry_point.load()()`. Configuration must come from environment variables or lazy initialization.

### Install

```bash
pip install -e .
# or
uv pip install -e .
```

**Important:** Entry points are read from installed package metadata. After any change to `[project.entry-points]`, reinstall the package. The registry is cached in memory; in a running process, call `reset_registry()` from `doc_hub.discovery` to force reload.

---

## 6. Registering as a Local Plugin File

For quick prototyping without creating a full package.

### Directory structure

```
{data_root}/plugins/
    fetchers/
        my_fetcher.py
    parsers/
        my_parser.py
    embedders/
        my_embedder.py
```

`data_root` defaults to `~/.local/share/doc-hub/` (platform-specific; defined in `src/doc_hub/paths.py`).

Files whose names start with `_` are skipped.

### Decorator API

Source: `src/doc_hub/discovery.py`

```python
from doc_hub.discovery import fetcher_plugin, parser_plugin, embedder_plugin
```

The `_PLUGIN_ATTR = "_doc_hub_plugin"` attribute is set on the class by the decorator. Discovery scans all classes in the file and registers those that have this attribute.

### Fetcher local plugin example

```python
# {data_root}/plugins/fetchers/my_fetcher.py
from pathlib import Path
from typing import Any

from doc_hub.discovery import fetcher_plugin


@fetcher_plugin("my_source")
class MySourceFetcher:
    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        # ... fetch logic ...
        return output_dir
```

### Parser local plugin example

```python
# {data_root}/plugins/parsers/my_parser.py
from pathlib import Path

from doc_hub.discovery import parser_plugin
from doc_hub.parse import Chunk


@parser_plugin("my_format")
class MyFormatParser:
    def parse(
        self,
        input_dir: Path,
        *,
        corpus_slug: str,
        base_url: str,
    ) -> list[Chunk]:
        # ... parsing logic ...
        return chunks
```

### Embedder local plugin example

```python
# {data_root}/plugins/embedders/my_embedder.py
from doc_hub.discovery import embedder_plugin


@embedder_plugin("my_model")
class MyEmbedder:
    @property
    def model_name(self) -> str: return "my-model-v1"

    @property
    def dimensions(self) -> int: return 768

    @property
    def task_type_document(self) -> str: return ""

    @property
    def task_type_query(self) -> str: return ""

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...

    async def embed_query(self, query: str) -> list[float]:
        ...
```

### Precedence

If a local plugin name collides with an entry point name, the entry point wins and the local plugin is skipped with a warning.

---

## 7. Testing Plugins

### Protocol conformance check

```python
from doc_hub.protocols import Fetcher, Parser, Embedder

assert isinstance(MyApiFetcher(), Fetcher)
assert isinstance(MyFormatParser(), Parser)
assert isinstance(MyEmbedder(), Embedder)
```

**Caveat:** `@runtime_checkable` + `isinstance()` only checks that required method/attribute **names** exist on the class. A method with the wrong signature (e.g. `fetch(self)` instead of `fetch(self, corpus_slug, fetch_config, output_dir)`) passes `isinstance()` but fails at call time. Use mypy or pyright for full signature validation.

### Testing a fetcher

```python
import tempfile
from pathlib import Path
import pytest
from my_plugin.fetcher import MyApiFetcher


@pytest.mark.asyncio
async def test_fetcher_writes_md_files():
    fetcher = MyApiFetcher()
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "raw"
        result = await fetcher.fetch(
            corpus_slug="test-corpus",
            fetch_config={"api_url": "https://example.com/api"},
            output_dir=output_dir,
        )
        md_files = list(result.glob("*.md"))
        assert len(md_files) > 0, "Fetcher must write at least one .md file"
        # Verify idempotency
        result2 = await fetcher.fetch(
            corpus_slug="test-corpus",
            fetch_config={"api_url": "https://example.com/api"},
            output_dir=output_dir,
        )
        assert result2 == result
```

### Testing a parser

```python
import hashlib
import tempfile
from pathlib import Path
from my_plugin.parser import MyFormatParser
from doc_hub.parse import Chunk


def test_parser_returns_valid_chunks():
    parser = MyFormatParser()
    with tempfile.TemporaryDirectory() as tmpdir:
        input_dir = Path(tmpdir)
        (input_dir / "guide.md").write_text("# Getting Started\n\nSome content here.\n")

        chunks = parser.parse(input_dir, corpus_slug="test", base_url="https://example.com/")

        assert len(chunks) > 0
        for chunk in chunks:
            assert isinstance(chunk, Chunk)
            assert chunk.source_file != ""
            assert chunk.char_count == len(chunk.content)
            assert chunk.content_hash == hashlib.sha256(chunk.content.encode()).hexdigest()
            assert chunk.category == "", "Parser must not set category — leave as empty string"
            assert chunk.heading_level in range(0, 7)
```

### Testing an embedder

```python
import pytest
from my_plugin.embedder import MyEmbedder
from doc_hub.protocols import Embedder


@pytest.mark.asyncio
async def test_embedder_protocol_and_dimensions():
    embedder = MyEmbedder()
    assert isinstance(embedder, Embedder)

    texts = ["Hello world", "Another document"]
    vectors = await embedder.embed_batch(texts)

    assert len(vectors) == len(texts)
    for vec in vectors:
        assert len(vec) == embedder.dimensions, (
            f"embed_batch returned {len(vec)}-dim vector, "
            f"expected {embedder.dimensions}"
        )

    query_vec = await embedder.embed_query("search query")
    assert len(query_vec) == embedder.dimensions
```

### Resetting the registry in tests

Source: `src/doc_hub/discovery.py` — `reset_registry()`

The plugin registry is a global singleton. Call `reset_registry()` between tests that manipulate `get_registry()`:

```python
import pytest
from doc_hub.discovery import reset_registry, get_registry


@pytest.fixture(autouse=True)
def clear_registry():
    reset_registry()
    yield
    reset_registry()


def test_custom_plugin_registered():
    # Override plugins dir to point at a test fixture directory
    registry = get_registry(plugins_dir=Path("tests/fixtures/plugins"))
    assert "my_source" in registry.list_fetchers()
```

### Mock embedder for pipeline integration tests

When testing pipeline stages that require an embedder but you don't want to hit a real API:

```python
class MockEmbedder:
    """Minimal Embedder implementation for testing."""

    @property
    def model_name(self) -> str:
        return "mock-model"

    @property
    def dimensions(self) -> int:
        return 768  # must match DOC_HUB_VECTOR_DIM in test env

    @property
    def task_type_document(self) -> str:
        return ""

    @property
    def task_type_query(self) -> str:
        return ""

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self.dimensions for _ in texts]

    async def embed_query(self, query: str) -> list[float]:
        return [0.1] * self.dimensions
```

Set `DOC_HUB_VECTOR_DIM=768` in the test environment to match `MockEmbedder.dimensions`. If the dimension mismatch check in `embed_chunks()` raises `ValueError`, verify that env var is set correctly.

---

## 8. Registry API Reference

Source: `src/doc_hub/discovery.py`

### `get_registry(*, plugins_dir=None) -> PluginRegistry`

Returns the global plugin registry, building it on first call. Loads entry points first, then local plugin files. Result is cached — subsequent calls return the same instance.

### `reset_registry() -> None`

Clears the global `_registry` singleton. Next call to `get_registry()` rebuilds from scratch. Used in tests and after installing new plugins in a running process.

### `PluginRegistry.get_fetcher(name: str) -> Fetcher`

Raises `KeyError` with a message listing available fetcher names if `name` is not registered.

### `PluginRegistry.get_parser(name: str) -> Parser`

Raises `KeyError` with available parser names if not found.

### `PluginRegistry.get_embedder(name: str) -> Embedder`

Raises `KeyError` with available embedder names if not found.

### `PluginRegistry.list_fetchers() -> list[str]`
### `PluginRegistry.list_parsers() -> list[str]`
### `PluginRegistry.list_embedders() -> list[str]`

Return sorted lists of registered plugin names.

---

## 9. Built-in Plugins

| Name | Class | File | Type |
|------|-------|------|------|
| `llms_txt` | `LlmsTxtFetcher` | `_builtins/fetchers/llms_txt.py` | Fetcher |
| `local_dir` | `LocalDirFetcher` | `_builtins/fetchers/local_dir.py` | Fetcher |
| `sitemap` | `SitemapFetcher` | `_builtins/fetchers/sitemap.py` | Fetcher |
| `git_repo` | `GitRepoFetcher` | `_builtins/fetchers/git_repo.py` | Fetcher |
| `markdown` | `MarkdownParser` | `_builtins/parsers/markdown.py` | Parser |
| `gemini` | `GeminiEmbedder` | `_builtins/embedders/gemini.py` | Embedder |

All built-in plugins are registered via entry points in `pyproject.toml` under the `doc_hub.fetchers`, `doc_hub.parsers`, and `doc_hub.embedders` groups.
