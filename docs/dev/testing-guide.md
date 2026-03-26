# Testing Guide

**Audience:** Developer / AI agent
**Source:** repo root

How to run tests, what markers exist, how to mock plugins, and how to write tests for new plugins.

---

## 1. Running Tests

### All unit tests (no external dependencies)

```bash
pytest tests/
```

### Integration tests only (requires live DB + `GEMINI_API_KEY`)

```bash
pytest tests/ -m integration
```

### Exclude integration tests

```bash
pytest tests/ -m "not integration"
```

---

## 2. Test Configuration

Defined in `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["integration: requires live DB and GEMINI_API_KEY"]
```

- **`asyncio_mode = "auto"`** — all `async def test_*` functions are detected and run automatically. No `@pytest.mark.asyncio` decorator required (though harmless to include).
- **`markers`** — registers the `integration` marker to suppress unknown-marker warnings.

### Dev dependencies (`[dependency-groups]` in `pyproject.toml`)

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.9",
]
```

---

## 3. Test Markers

### `@pytest.mark.integration`

```python
@pytest.mark.integration
async def test_real_db_indexing(pool):
    ...
```

Applied to tests that require:
- A running **PostgreSQL** instance with the **VectorChord** extension installed
- A valid **`GEMINI_API_KEY`** environment variable

Unmarked tests must run without any external dependencies. If a test needs a real DB or live API, it **must** carry `@pytest.mark.integration`.

---

## 4. Isolation: Registry Reset

The plugin registry (`doc_hub.discovery`) is a module-level singleton cached after first call. Tests that exercise plugin discovery or inject mock plugins must reset it before and after:

```python
import pytest
from doc_hub.discovery import reset_registry

@pytest.fixture(autouse=True)
def isolated_registry():
    reset_registry()
    yield
    reset_registry()
```

`reset_registry()` sets the global `_registry` variable to `None`, forcing the next `get_registry()` call to rebuild from scratch.

**When to use:**
- Any test that calls `get_registry()`
- Any test that patches `importlib.metadata.entry_points`
- Any test that loads local plugin files

---

## 5. Mocking the Embedder

Create a class that satisfies the `Embedder` protocol (`doc_hub.protocols`). The mock must implement all four properties and both async methods. Vectors returned by `embed_batch` and `embed_query` must have length equal to the `dimensions` property.

```python
from pathlib import Path
from typing import Any

class MockEmbedder:
    @property
    def model_name(self) -> str:
        return "mock-model"

    @property
    def dimensions(self) -> int:
        return 768

    @property
    def task_type_document(self) -> str:
        return "RETRIEVAL_DOCUMENT"

    @property
    def task_type_query(self) -> str:
        return "RETRIEVAL_QUERY"

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dimensions for _ in texts]

    async def embed_query(self, query: str) -> list[float]:
        return [0.0] * self.dimensions
```

Verify protocol conformance:

```python
from doc_hub.protocols import Embedder

assert isinstance(MockEmbedder(), Embedder)
```

For tests that use `embed_chunks()` directly, you also need to patch `get_vector_dim` to return the mock's dimensions, and patch the cache/output paths:

```python
from unittest.mock import patch, AsyncMock

with (
    patch("doc_hub.db.get_vector_dim", return_value=768),
    patch("doc_hub.embed.embeddings_cache_path", return_value=tmp_path / "cache.jsonl"),
    patch("doc_hub.embed.embedded_chunks_path", return_value=tmp_path / "embedded.jsonl"),
    patch("doc_hub.embed.asyncio.sleep"),  # skip rate-limit pauses
):
    result = await embed_chunks("my-corpus", chunks, MockEmbedder())
```

`get_vector_dim()` reads `DOC_HUB_VECTOR_DIM` (default 768). If your mock embedder uses a different dimension (e.g., 128), set the env var or patch `get_vector_dim` to match.

For lighter-weight mocking with `unittest.mock`:

```python
from unittest.mock import MagicMock, AsyncMock

embedder = MagicMock()
embedder.model_name = "test-model"
embedder.dimensions = 768
embedder.embed_batch = AsyncMock(return_value=[[0.1] * 768])
embedder.embed_query = AsyncMock(return_value=[0.1] * 768)
```

---

## 6. Mocking Fetchers

**Option A — inline mock class:**

```python
from pathlib import Path
from typing import Any

class MockFetcher:
    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "doc.md").write_text("# Title\n\nContent here.")
        return output_dir
```

**Option B — `LocalDirFetcher` with a temp path:**

```python
from doc_hub._builtins.fetchers.local_dir import LocalDirFetcher

# pre-populate tmp_path with .md files
(tmp_path / "doc.md").write_text("# Hello\n\nWorld.")

fetcher = LocalDirFetcher()
result = await fetcher.fetch("my-corpus", {"path": str(tmp_path)}, tmp_path / "out")
assert result == tmp_path  # local_dir returns the source path directly
```

**Option C — patch `get_registry()`:**

```python
from unittest.mock import MagicMock, patch

mock_registry = MagicMock()
mock_registry.get_fetcher.return_value = MockFetcher()

with patch("doc_hub.discovery.get_registry", return_value=mock_registry):
    # code under test that calls get_registry().get_fetcher(...)
    ...
```

---

## 7. Mocking the Database

For unit tests, mock `asyncpg.Pool` and `asyncpg.Connection` via `unittest.mock`:

```python
from unittest.mock import AsyncMock, MagicMock

mock_conn = AsyncMock()
mock_conn.fetch.return_value = []
mock_conn.fetchrow.return_value = None
mock_conn.execute.return_value = "INSERT 0 1"

mock_pool = MagicMock()
mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
```

For integration tests, create a real pool and schema:

```python
import asyncpg
from doc_hub.db import create_pool, ensure_schema

@pytest.mark.integration
async def test_with_real_db():
    pool = await create_pool()
    await ensure_schema(pool)
    # ... test body ...
    await pool.close()
```

---

## 8. Testing New Plugins

### Fetcher

```python
import pytest
from pathlib import Path
from doc_hub.protocols import Fetcher

async def test_my_fetcher(tmp_path):
    fetcher = MyFetcher()

    # Protocol conformance
    assert isinstance(fetcher, Fetcher)

    # Functional: must write .md files to output_dir
    result = await fetcher.fetch(
        corpus_slug="test",
        fetch_config={"url": "https://example.com/llms.txt"},
        output_dir=tmp_path / "raw",
    )

    md_files = list(result.glob("*.md"))
    assert len(md_files) > 0

    # Optional: manifest.json for incremental sync
    # manifest = result / "manifest.json"
    # assert manifest.exists()
```

### Parser

```python
import hashlib
import pytest
from pathlib import Path
from doc_hub.protocols import Parser
from doc_hub.parse import Chunk

def test_my_parser(tmp_path):
    parser = MyParser()

    # Protocol conformance
    assert isinstance(parser, Parser)

    # Prepare input
    (tmp_path / "guide.md").write_text("# Getting Started\n\nInstall the package.\n")

    chunks = parser.parse(
        tmp_path,
        corpus_slug="test",
        base_url="https://example.com/",
    )

    assert isinstance(chunks, list)
    assert len(chunks) > 0

    for chunk in chunks:
        assert isinstance(chunk, Chunk)
        # All fields must be set
        assert chunk.source_file != ""
        assert chunk.content != ""
        assert chunk.char_count == len(chunk.content)
        assert chunk.content_hash == hashlib.sha256(chunk.content.encode()).hexdigest()
        # category MUST be "" — core pipeline fills it
        assert chunk.category == ""
        assert chunk.heading_level in range(0, 7)
```

### Embedder

```python
import pytest
from doc_hub.protocols import Embedder

async def test_my_embedder():
    embedder = MyEmbedder()

    # Protocol conformance
    assert isinstance(embedder, Embedder)

    # Properties
    assert isinstance(embedder.model_name, str) and embedder.model_name
    assert isinstance(embedder.dimensions, int) and embedder.dimensions > 0

    # embed_batch: one vector per input, length == dimensions
    texts = ["hello world", "second text"]
    vectors = await embedder.embed_batch(texts)
    assert len(vectors) == len(texts)
    for v in vectors:
        assert len(v) == embedder.dimensions

    # embed_query: single vector
    q_vec = await embedder.embed_query("search query")
    assert len(q_vec) == embedder.dimensions
```

---

## 9. Integration Test Requirements

Integration tests require:

| Requirement | Environment variable |
|---|---|
| PostgreSQL with VectorChord | `PGPASSWORD` or `DOC_HUB_DATABASE_URL` |
| Gemini embedding API | `GEMINI_API_KEY` |

Integration tests may create tables, insert data, and drop tables. Run them in an isolated database or against a dedicated test instance.

The vector dimension of the running schema must match the embedder being tested. If the schema was created with `DOC_HUB_VECTOR_DIM=768` and you are testing a 1536-dimension embedder, `embed_chunks()` will raise `ValueError` before any API calls are made.

---

## 10. Key Import Reference

| Symbol | Import path |
|---|---|
| `reset_registry()` | `from doc_hub.discovery import reset_registry` |
| `get_registry()` | `from doc_hub.discovery import get_registry` |
| `Fetcher`, `Parser`, `Embedder` protocols | `from doc_hub.protocols import Fetcher, Parser, Embedder` |
| `Chunk` dataclass | `from doc_hub.parse import Chunk` |
| `embed_chunks()` | `from doc_hub.embed import embed_chunks` |
| `l2_normalize()` | `from doc_hub.embed import l2_normalize` |
| `get_vector_dim()` | `from doc_hub.db import get_vector_dim` |
| `LocalDirFetcher` | `from doc_hub._builtins.fetchers.local_dir import LocalDirFetcher` |
