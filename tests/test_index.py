"""Tests for doc_hub.index — the index pipeline.

Unit tests only — no live DB required.  DB calls are mocked via asyncpg
Pool/Connection mocks.  Tests verify:

- IndexResult dataclass fields
- _parse_command_count() helper
- upsert_chunks() sets corpus_id correctly
- upsert_chunks() updates insert/update counters (xmax-based detection)
- upsert_chunks() handles empty chunk list gracefully
- upsert_chunks() full=True deletes stale rows and parses DELETE count
- upsert_chunks() leaves other-corpus rows untouched (corpus_id scoping)
- upsert_chunks() acquires advisory lock
- _write_meta() writes expected keys
- verify_index() passes when rows returned, raises on empty result
- verify_index() skips gracefully when chunk list is empty
- run_index() in pipeline.py loads from embedded_chunks.jsonl when no chunks given
- run_index() passes embedded_chunks directly when provided
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from doc_hub.embed import EmbeddedChunk
from doc_hub.index import (
    BATCH_SIZE,
    IndexResult,
    _parse_command_count,
    _write_meta,
    upsert_chunks,
    verify_index,
)
from doc_hub.models import Corpus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_corpus(slug: str = "test-corpus") -> Corpus:
    return Corpus(
        slug=slug,
        name="Test Corpus",
        fetch_strategy="llms_txt",
        fetch_config={"url": "https://example.com/llms.txt"},
    )


def _make_embedded_chunk(
    content_hash: str = "abc123",
    heading: str = "Test Heading",
    content: str = "Test content here.",
    embedding: list[float] | None = None,
) -> EmbeddedChunk:
    if embedding is None:
        embedding = [0.1] * 768
    return EmbeddedChunk(
        source_file="test.md",
        source_url="https://example.com/test",
        section_path="Docs > Test",
        heading=heading,
        heading_level=2,
        content=content,
        start_line=1,
        end_line=1 + content.count("\n"),
        char_count=len(content),
        content_hash=content_hash,
        category="guide",
        embedding=embedding,
    )


def _make_mock_pool() -> MagicMock:
    """Return a mock asyncpg Pool with a usable acquire() context manager."""
    pool = MagicMock()
    conn = AsyncMock()
    # acquire() returns an async context manager that yields conn
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)

    # transaction() returns an async context manager
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    return pool, conn


# ---------------------------------------------------------------------------
# IndexResult
# ---------------------------------------------------------------------------


def test_index_result_fields():
    """IndexResult has the expected fields."""
    r = IndexResult(inserted=5, updated=3, deleted=1, total=10)
    assert r.inserted == 5
    assert r.updated == 3
    assert r.deleted == 1
    assert r.total == 10


def test_index_result_is_dataclass():
    """IndexResult is a dataclass (asdict works)."""
    r = IndexResult(inserted=1, updated=2, deleted=3, total=4)
    d = asdict(r)
    assert d == {"inserted": 1, "updated": 2, "deleted": 3, "total": 4}


# ---------------------------------------------------------------------------
# _parse_command_count
# ---------------------------------------------------------------------------


def test_parse_command_count_delete():
    assert _parse_command_count("DELETE 7") == 7


def test_parse_command_count_insert():
    assert _parse_command_count("INSERT 0 1") == 1


def test_parse_command_count_update():
    assert _parse_command_count("UPDATE 3") == 3


def test_parse_command_count_zero():
    assert _parse_command_count("DELETE 0") == 0


def test_parse_command_count_invalid():
    assert _parse_command_count("") == 0


def test_parse_command_count_none():
    assert _parse_command_count(None) == 0


# ---------------------------------------------------------------------------
# upsert_chunks — empty list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_chunks_empty_list():
    """upsert_chunks returns zeros when called with an empty chunk list."""
    corpus = _make_corpus()
    pool, conn = _make_mock_pool()
    pool.fetchval = AsyncMock(return_value=0)

    result = await upsert_chunks(pool, corpus, [])

    assert result.inserted == 0
    assert result.updated == 0
    assert result.deleted == 0
    assert result.total == 0
    # No advisory lock or DB writes should occur
    conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# upsert_chunks — insert vs update detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_chunks_counts_inserts(tmp_path):
    """upsert_chunks increments inserted when xmax=0 (new row)."""
    corpus = _make_corpus()
    chunk = _make_embedded_chunk()
    pool, conn = _make_mock_pool()

    # conn.fetch returns a row with is_insert=True (new row)
    conn.fetch = AsyncMock(return_value=[{"is_insert": True}])
    # conn.execute used for advisory lock
    conn.execute = AsyncMock(return_value="SELECT 1")
    # pool.fetchval for total_chunks count
    pool.fetchval = AsyncMock(return_value=1)

    with (
        patch("doc_hub.index.update_corpus_stats", new=AsyncMock()),
        patch("doc_hub.index._write_meta", new=AsyncMock()),
    ):
        result = await upsert_chunks(pool, corpus, [chunk])

    assert result.inserted == 1
    assert result.updated == 0


@pytest.mark.asyncio
async def test_upsert_chunks_counts_updates(tmp_path):
    """upsert_chunks increments updated when xmax != 0 (existing row updated)."""
    corpus = _make_corpus()
    chunk = _make_embedded_chunk()
    pool, conn = _make_mock_pool()

    # conn.fetch returns a row with is_insert=False (existing row updated)
    conn.fetch = AsyncMock(return_value=[{"is_insert": False}])
    conn.execute = AsyncMock(return_value="SELECT 1")
    pool.fetchval = AsyncMock(return_value=1)

    with (
        patch("doc_hub.index.update_corpus_stats", new=AsyncMock()),
        patch("doc_hub.index._write_meta", new=AsyncMock()),
    ):
        result = await upsert_chunks(pool, corpus, [chunk])

    assert result.inserted == 0
    assert result.updated == 1


# ---------------------------------------------------------------------------
# upsert_chunks — advisory lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_chunks_acquires_advisory_lock():
    """upsert_chunks calls pg_advisory_xact_lock with the corpus slug."""
    corpus = _make_corpus(slug="lock-test")
    chunk = _make_embedded_chunk()
    pool, conn = _make_mock_pool()

    conn.fetch = AsyncMock(return_value=[{"is_insert": True}])
    conn.execute = AsyncMock(return_value="SELECT 1")
    pool.fetchval = AsyncMock(return_value=1)

    with (
        patch("doc_hub.index.update_corpus_stats", new=AsyncMock()),
        patch("doc_hub.index._write_meta", new=AsyncMock()),
    ):
        await upsert_chunks(pool, corpus, [chunk])

    # The first execute() call should be the advisory lock
    first_execute_call = conn.execute.call_args_list[0]
    sql_arg = first_execute_call[0][0]
    assert "pg_advisory_xact_lock" in sql_arg
    # The corpus slug should be passed as a parameter
    assert first_execute_call[0][1] == "lock-test"


# ---------------------------------------------------------------------------
# upsert_chunks — full mode (stale deletion)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_chunks_full_mode_deletes_stale():
    """full=True issues a DELETE with corpus_id scoping."""
    corpus = _make_corpus()
    chunk = _make_embedded_chunk()
    pool, conn = _make_mock_pool()

    # Track execute calls: first is advisory lock, second is DELETE
    execute_responses = ["SELECT 1", "DELETE 2"]
    execute_call_count = [0]

    async def mock_execute(*args, **kwargs):
        resp = execute_responses[min(execute_call_count[0], len(execute_responses) - 1)]
        execute_call_count[0] += 1
        return resp

    conn.execute = mock_execute
    conn.fetch = AsyncMock(return_value=[{"is_insert": True}])
    pool.fetchval = AsyncMock(return_value=1)

    with (
        patch("doc_hub.index.update_corpus_stats", new=AsyncMock()),
        patch("doc_hub.index._write_meta", new=AsyncMock()),
    ):
        result = await upsert_chunks(pool, corpus, [chunk], full=True)

    assert result.deleted == 2


@pytest.mark.asyncio
async def test_upsert_chunks_full_mode_scopes_delete_by_corpus():
    """full=True DELETE statement includes corpus_id = $1 (not cross-corpus)."""
    corpus = _make_corpus(slug="corpus-a")
    chunk = _make_embedded_chunk()
    pool, conn = _make_mock_pool()

    delete_sqls = []

    async def capture_execute(sql, *args, **kwargs):
        if "DELETE" in sql.upper():
            delete_sqls.append((sql, args))
        return "DELETE 0"

    conn.execute = capture_execute
    conn.fetch = AsyncMock(return_value=[{"is_insert": True}])
    pool.fetchval = AsyncMock(return_value=1)

    with (
        patch("doc_hub.index.update_corpus_stats", new=AsyncMock()),
        patch("doc_hub.index._write_meta", new=AsyncMock()),
    ):
        await upsert_chunks(pool, corpus, [chunk], full=True)

    assert len(delete_sqls) == 1
    delete_sql, delete_args = delete_sqls[0]
    # Must scope by corpus_id
    assert "corpus_id" in delete_sql
    # First positional arg to DELETE must be the corpus slug
    assert delete_args[0] == "corpus-a"


@pytest.mark.asyncio
async def test_upsert_chunks_no_full_no_delete():
    """full=False (default) must not issue any DELETE statement."""
    corpus = _make_corpus()
    chunk = _make_embedded_chunk()
    pool, conn = _make_mock_pool()

    delete_called = []

    async def capture_execute(sql, *args, **kwargs):
        if "DELETE" in sql.upper():
            delete_called.append(sql)
        return "SELECT 1"

    conn.execute = capture_execute
    conn.fetch = AsyncMock(return_value=[{"is_insert": True}])
    pool.fetchval = AsyncMock(return_value=1)

    with (
        patch("doc_hub.index.update_corpus_stats", new=AsyncMock()),
        patch("doc_hub.index._write_meta", new=AsyncMock()),
    ):
        await upsert_chunks(pool, corpus, [chunk], full=False)

    assert delete_called == [], "DELETE should not be called when full=False"


# ---------------------------------------------------------------------------
# upsert_chunks — corpus stats update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_chunks_calls_update_corpus_stats():
    """upsert_chunks calls update_corpus_stats with the post-run total."""
    corpus = _make_corpus()
    chunk = _make_embedded_chunk()
    pool, conn = _make_mock_pool()

    conn.fetch = AsyncMock(return_value=[{"is_insert": True}])
    conn.execute = AsyncMock(return_value="SELECT 1")
    pool.fetchval = AsyncMock(return_value=42)

    captured_stats = {}

    async def capture_stats(pool_, slug, total):
        captured_stats["slug"] = slug
        captured_stats["total"] = total

    with (
        patch("doc_hub.index.update_corpus_stats", side_effect=capture_stats),
        patch("doc_hub.index._write_meta", new=AsyncMock()),
    ):
        result = await upsert_chunks(pool, corpus, [chunk])

    assert captured_stats["slug"] == corpus.slug
    assert captured_stats["total"] == 42
    assert result.total == 42


# ---------------------------------------------------------------------------
# upsert_chunks — embedding format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_chunks_formats_embedding_as_vector_string():
    """Embedding is passed as a '[0.1,0.2,...]' string to $11::vector."""
    corpus = _make_corpus()
    embedding = [0.25, 0.5, 0.75]
    chunk = _make_embedded_chunk(embedding=embedding)
    pool, conn = _make_mock_pool()

    captured_fetch_calls = []

    async def capture_fetch(sql, *args):
        captured_fetch_calls.append((sql, args))
        return [{"is_insert": True}]

    conn.fetch = capture_fetch
    conn.execute = AsyncMock(return_value="SELECT 1")
    pool.fetchval = AsyncMock(return_value=1)

    with (
        patch("doc_hub.index.update_corpus_stats", new=AsyncMock()),
        patch("doc_hub.index._write_meta", new=AsyncMock()),
    ):
        await upsert_chunks(pool, corpus, [chunk])

    assert len(captured_fetch_calls) == 1
    _, args = captured_fetch_calls[0]
    # The 13th positional arg (index 12) should be the embedding string
    emb_arg = args[12]
    assert emb_arg.startswith("[")
    assert emb_arg.endswith("]")
    # Verify the values are present
    assert "0.25" in emb_arg
    assert "0.5" in emb_arg
    assert "0.75" in emb_arg


# ---------------------------------------------------------------------------
# _write_meta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_meta_writes_expected_keys():
    """_write_meta writes last_indexed_at, total_chunks, embedding_model, embedding_dimensions."""
    pool, conn = _make_mock_pool()

    written_keys = []

    async def capture_execute(sql, *args):
        if "doc_index_meta" in sql:
            written_keys.append(args[1])  # key is $2 = args[1]
        return "INSERT 0 1"

    conn.execute = capture_execute

    await _write_meta(pool, "my-corpus", total_chunks=100, embedder_model="gemini-embedding-001", embedder_dims=768)

    assert "last_indexed_at" in written_keys
    assert "total_chunks" in written_keys
    assert "embedding_model" in written_keys
    assert "embedding_dimensions" in written_keys


@pytest.mark.asyncio
async def test_write_meta_scopes_by_corpus_id():
    """_write_meta passes corpus_slug as $1 to every INSERT."""
    pool, conn = _make_mock_pool()

    corpus_ids_seen = set()

    async def capture_execute(sql, *args):
        if "doc_index_meta" in sql:
            corpus_ids_seen.add(args[0])  # corpus_id is $1 = args[0]
        return "INSERT 0 1"

    conn.execute = capture_execute

    await _write_meta(pool, "my-corpus", total_chunks=10, embedder_model="", embedder_dims=0)

    assert corpus_ids_seen == {"my-corpus"}


# ---------------------------------------------------------------------------
# verify_index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_index_passes_when_rows_returned():
    """verify_index does not raise when pool.fetch returns at least one row."""
    corpus = _make_corpus()
    chunk = _make_embedded_chunk()
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"id": 1, "heading": "Test"}])

    # Should not raise
    await verify_index(pool, corpus, [chunk])
    pool.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_verify_index_raises_on_empty_result():
    """verify_index raises AssertionError when no rows are returned."""
    corpus = _make_corpus()
    chunk = _make_embedded_chunk()
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])

    with pytest.raises(AssertionError, match="smoke-test"):
        await verify_index(pool, corpus, [chunk])


@pytest.mark.asyncio
async def test_verify_index_skips_when_no_chunks():
    """verify_index skips the query when chunk list is empty."""
    corpus = _make_corpus()
    pool = MagicMock()
    pool.fetch = AsyncMock()

    # Should not raise and should not call pool.fetch
    await verify_index(pool, corpus, [])
    pool.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_verify_index_scopes_query_by_corpus_id():
    """verify_index passes corpus.slug as the first parameter to the query."""
    corpus = _make_corpus(slug="scoped-corpus")
    chunk = _make_embedded_chunk()
    pool = MagicMock()

    captured_args = []

    async def capture_fetch(sql, *args):
        captured_args.extend(args)
        return [{"id": 1, "heading": "h"}]

    pool.fetch = capture_fetch

    await verify_index(pool, corpus, [chunk])

    assert captured_args[0] == "scoped-corpus"


# ---------------------------------------------------------------------------
# pipeline.run_index — integration with pipeline stage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_run_index_raises_if_no_jsonl(tmp_path):
    """run_index raises FileNotFoundError if embedded_chunks.jsonl doesn't exist."""
    from doc_hub.pipeline import run_index

    corpus = _make_corpus()

    with patch("doc_hub.pipeline.chunks_dir", return_value=tmp_path):
        with pytest.raises(FileNotFoundError, match="embedded_chunks.jsonl"):
            await run_index(corpus)


@pytest.mark.asyncio
async def test_pipeline_run_index_loads_from_jsonl(tmp_path):
    """run_index loads EmbeddedChunks from embedded_chunks.jsonl when not provided."""
    from doc_hub.pipeline import run_index

    corpus = _make_corpus()
    chunk = _make_embedded_chunk()

    # Write a minimal embedded_chunks.jsonl
    jsonl_path = tmp_path / "embedded_chunks.jsonl"
    with jsonl_path.open("w") as f:
        f.write(json.dumps(asdict(chunk)) + "\n")

    captured = {}

    async def mock_upsert(pool, corp, chunks, **kwargs):
        captured["chunks"] = chunks
        return IndexResult(inserted=1, updated=0, deleted=0, total=1)

    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

    # pipeline.py uses local imports inside run_index; patch the source modules
    with (
        patch("doc_hub.pipeline.chunks_dir", return_value=tmp_path),
        patch("doc_hub.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("doc_hub.db.ensure_schema", new=AsyncMock()),
        patch("doc_hub.index.upsert_chunks", side_effect=mock_upsert),
        patch("doc_hub.index.verify_index", new=AsyncMock()),
    ):
        await run_index(corpus)

    assert len(captured["chunks"]) == 1
    assert captured["chunks"][0].content_hash == chunk.content_hash


@pytest.mark.asyncio
async def test_pipeline_run_index_uses_provided_chunks():
    """run_index uses provided embedded_chunks without reading from disk."""
    from doc_hub.pipeline import run_index

    corpus = _make_corpus()
    chunk = _make_embedded_chunk()

    captured = {}

    async def mock_upsert(pool, corp, chunks, **kwargs):
        captured["chunks"] = chunks
        return IndexResult(inserted=1, updated=0, deleted=0, total=1)

    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

    with (
        patch("doc_hub.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("doc_hub.db.ensure_schema", new=AsyncMock()),
        patch("doc_hub.index.upsert_chunks", side_effect=mock_upsert),
        patch("doc_hub.index.verify_index", new=AsyncMock()),
    ):
        await run_index(corpus, embedded_chunks=[chunk])

    assert len(captured["chunks"]) == 1
    assert captured["chunks"][0].content_hash == chunk.content_hash


@pytest.mark.asyncio
async def test_pipeline_run_index_passes_full_reindex():
    """run_index passes full_reindex flag to upsert_chunks as full=True."""
    from doc_hub.pipeline import run_index

    corpus = _make_corpus()
    chunk = _make_embedded_chunk()

    captured = {}

    async def mock_upsert(pool, corp, chunks, **kwargs):
        captured["full"] = kwargs.get("full")
        return IndexResult(inserted=1, updated=0, deleted=0, total=1)

    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

    with (
        patch("doc_hub.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("doc_hub.db.ensure_schema", new=AsyncMock()),
        patch("doc_hub.index.upsert_chunks", side_effect=mock_upsert),
        patch("doc_hub.index.verify_index", new=AsyncMock()),
    ):
        await run_index(corpus, full_reindex=True, embedded_chunks=[chunk])

    assert captured["full"] is True


@pytest.mark.asyncio
async def test_pipeline_run_index_closes_pool_on_error():
    """run_index closes the DB pool even if upsert_chunks raises."""
    from doc_hub.pipeline import run_index

    corpus = _make_corpus()
    chunk = _make_embedded_chunk()

    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

    with (
        patch("doc_hub.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("doc_hub.db.ensure_schema", new=AsyncMock()),
        patch("doc_hub.index.upsert_chunks", new=AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await run_index(corpus, embedded_chunks=[chunk])

    mock_pool.close.assert_called_once()


# ---------------------------------------------------------------------------
# Pipeline threading: embed → index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_pipeline_threads_embedded_chunks_to_index():
    """run_pipeline passes embedded_chunks from run_embed directly to run_index."""
    from doc_hub.pipeline import run_pipeline

    corpus = _make_corpus()
    fake_embedded = [_make_embedded_chunk()]

    captured_index_kwargs = {}

    async def mock_run_index(corp, *, full_reindex=False, embedded_chunks=None, pool=None, embedder=None):
        captured_index_kwargs["embedded_chunks"] = embedded_chunks

    with (
        patch("doc_hub.pipeline.run_fetch", new=AsyncMock()),
        patch("doc_hub.pipeline.run_parse", new=AsyncMock(return_value=[])),
        patch("doc_hub.pipeline.run_embed", new=AsyncMock(return_value=fake_embedded)),
        patch("doc_hub.pipeline.run_index", side_effect=mock_run_index),
    ):
        await run_pipeline(corpus)

    assert captured_index_kwargs["embedded_chunks"] is fake_embedded
