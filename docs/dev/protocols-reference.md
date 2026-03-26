# Protocols Reference

**Source:** `src/doc_hub/protocols.py`
**Chunk dataclass source:** `src/doc_hub/parse.py`

Three `@runtime_checkable` protocols in `doc_hub.protocols` define the plugin contracts for doc-hub. Plugins use **structural typing** — no inheritance from these classes is required or expected. Static type checkers (mypy, pyright) enforce full conformance at development time. `isinstance()` checks only verify attribute/method name existence.

---

## Module header

```python
from __future__ import annotations  # PEP 604 union syntax in annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from doc_hub.parse import Chunk
```

---

## `Fetcher` protocol

```python
@runtime_checkable
class Fetcher(Protocol):
    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path: ...
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `corpus_slug` | `str` | Unique corpus identifier. Used for logging only. |
| `fetch_config` | `dict[str, Any]` | Strategy-specific configuration dict from the `fetch_config` JSONB column in `doc_corpora`. Contents vary by fetcher. |
| `output_dir` | `Path` | Destination directory for fetched files. **May not exist.** The fetcher must call `output_dir.mkdir(parents=True, exist_ok=True)` before writing. |

### Return value

`Path` — directory containing `.md` files ready for the parse stage. May be `output_dir` itself (typical case) or a different pre-existing path (for `local_dir`-style fetchers that simply return the source directory without copying).

### Contract

- Must write `.md` files to the returned directory.
- Must be idempotent (safe to call repeatedly; use `manifest.json` for incremental sync).
- Must NOT touch the database.
- Must NOT invoke the parser or embedder.

---

## `Parser` protocol

```python
@runtime_checkable
class Parser(Protocol):
    def parse(
        self,
        input_dir: Path,
        *,
        corpus_slug: str,
        base_url: str,
    ) -> list[Chunk]: ...
```

**`parse()` is synchronous (not async).**

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `input_dir` | `Path` | Directory containing source files to parse. |
| `corpus_slug` | `str` | Corpus identifier. Keyword-only. Used for logging. |
| `base_url` | `str` | Base URL for reconstructing `source_url` from filenames (e.g. `"https://ai.pydantic.dev/"`). Keyword-only. Fallback when no `manifest.json` is present; the built-in `MarkdownParser` prefers the manifest. |

### Return value

`list[Chunk]` — raw chunks before size optimization. Every `Chunk` in the list must have all 11 fields set (see below).

### `Chunk` dataclass

Defined in `doc_hub.parse`. Imported into `doc_hub.protocols` via `from doc_hub.parse import Chunk`.

```python
@dataclass
class Chunk:
    source_file: str      # Original filename, e.g. "models__openai.md"
    source_url: str       # Original URL from manifest, or "" if unknown
    section_path: str     # Heading hierarchy, e.g. "Configuration > API Keys"
    heading: str          # Section heading text
    heading_level: int    # 1–6; use 0 for preamble/no-heading content
    content: str          # Full section text including the heading line
    start_line: int       # 1-indexed line number in source file
    end_line: int         # 1-indexed last line number (inclusive)
    char_count: int       # len(content)
    content_hash: str     # hashlib.sha256(content.encode()).hexdigest()
    category: str         # MUST be "" — core pipeline derives this
```

### Field requirements

| Field | Type | Constraint |
|-------|------|------------|
| `source_file` | `str` | Filename only, no path components. |
| `source_url` | `str` | Full URL or `""` if unavailable. |
| `section_path` | `str` | `" > "`-delimited heading chain from root to current section. |
| `heading` | `str` | Text of the heading for this section. |
| `heading_level` | `int` | `1`–`6`; `0` for preamble (content before any heading). |
| `content` | `str` | Full section text, including the heading line. |
| `start_line` | `int` | 1-indexed. |
| `end_line` | `int` | 1-indexed, inclusive. |
| `char_count` | `int` | Must equal `len(content)`. |
| `content_hash` | `str` | `hashlib.sha256(content.encode()).hexdigest()` — used as embed cache key. |
| `category` | `str` | **Must be `""`** (empty string). Parsers must not derive categories. |

### What the core pipeline does after `parse()` returns

These operations are performed by `parse.py`, not the parser plugin:

1. **Category derivation** — `derive_category(source_file)` fills the empty `category` field.
2. **Merge tiny chunks** — chunks with `char_count < 500` are merged into their predecessor within the same file.
3. **Split mega chunks** — chunks with `char_count > 2500` are split at paragraph boundaries, targeting ~1000 chars per sub-chunk.
4. **Deduplication** — exact-content duplicates removed by `content_hash`.

---

## `Embedder` protocol

```python
@runtime_checkable
class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    @property
    def task_type_document(self) -> str: ...

    @property
    def task_type_query(self) -> str: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, query: str) -> list[float]: ...
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `model_name` | `str` | Unique model identifier. Part of the embedding cache key. Changing this string invalidates all cached embeddings for this embedder. |
| `dimensions` | `int` | Output vector dimensionality (e.g. `768`, `1536`, `384`). Must match the `DOC_HUB_VECTOR_DIM` deployment configuration. |
| `task_type_document` | `str` | Task type hint passed to the API when embedding document chunks (e.g. `"RETRIEVAL_DOCUMENT"`). Return `""` if not applicable. |
| `task_type_query` | `str` | Task type hint passed to the API when embedding search queries (e.g. `"RETRIEVAL_QUERY"`). Return `""` if not applicable. |

### `embed_batch(self, texts: list[str]) -> list[list[float]]`

Embed a batch of pre-formatted text strings.

- `texts`: List of strings already formatted by `embedding_input()` (core pipeline responsibility).
- Returns a list of vectors, **one per input**, each of length `self.dimensions`.
- Must NOT L2-normalize output — the core pipeline calls `l2_normalize()` after.
- Must NOT cache internally — the core pipeline manages the JSONL embedding cache.
- Must NOT split the input into sub-batches — the core pipeline manages batch sizing.
- On API error: raise an exception. The core pipeline handles retry logic.

### `embed_query(self, query: str) -> list[float]`

Embed a single search query string.

- Uses `task_type_query` (not `task_type_document`).
- Returns a single vector of length `self.dimensions`.
- Called during search, not during indexing.

### What the core pipeline handles

The embedder plugin is responsible only for making the API call. Everything else is owned by `embed.py`:

- **Cache lookup/write** — JSONL cache keyed by `(content_hash, model, dimensions)`.
- **L2 normalization** — `l2_normalize()` applied to every vector before indexing.
- **Batch orchestration** — splits chunks into batches, calls `embed_batch()`, reassembles.
- **Rate-limit pacing** — inter-batch sleep.
- **JSONL output** — writes `embedded_chunks.jsonl`.

---

## Runtime checking caveats

`@runtime_checkable` + `isinstance()` only verifies that the required method and attribute **names exist** on the object. It does not check signatures, return types, or property descriptors.

```python
from doc_hub.protocols import Fetcher

class BadFetcher:
    async def fetch(self):  # wrong arity — missing required parameters
        return Path(".")

isinstance(BadFetcher(), Fetcher)  # True — isinstance passes!
# But BadFetcher().fetch(slug, config, dir) → TypeError at call time
```

Use mypy or pyright for full protocol conformance checking:

```bash
mypy src/
```

Quick conformance smoke test (name-only, not signature):

```python
assert isinstance(MyFetcher(), Fetcher)
assert isinstance(MyParser(), Parser)
assert isinstance(MyEmbedder(), Embedder)
```
