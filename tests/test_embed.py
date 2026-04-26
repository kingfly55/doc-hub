"""Tests for doc_hub.embed — embedding, caching, L2 normalization.

Unit tests only — embedder plugin calls are mocked. No network access required.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_hub.embed import (
    BATCH_SIZE,
    EmbeddedChunk,
    append_to_cache,
    embed_chunks,
    l2_normalize,
    load_cache,
)
from doc_hub.parse import Chunk, derive_category


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_embedder(dims: int = 768, model: str = "test-model") -> MagicMock:
    """Build a mock Embedder that returns deterministic embeddings."""
    embedder = MagicMock()
    embedder.model_name = model
    embedder.dimensions = dims
    embedder.embed_batch = AsyncMock(return_value=[[0.1] * dims])
    embedder.embed_query = AsyncMock(return_value=[0.1] * dims)
    return embedder


def _make_chunk(
    source_file: str = "guide.md",
    content: str = "This is test content.",
    section_path: str = "Guide",
) -> Chunk:
    return Chunk(
        source_file=source_file,
        source_url="https://example.com/guide/",
        section_path=section_path,
        heading="Guide",
        heading_level=1,
        content=content,
        start_line=1,
        end_line=1 + content.count("\n"),
        char_count=len(content),
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        category=derive_category(source_file),
    )


def _make_embedding(dims: int = 768, seed: float = 1.0) -> list[float]:
    """Make a deterministic (non-normalized) embedding vector."""
    return [math.sin(i * seed) for i in range(dims)]


def _make_unit_embedding(dims: int = 768, seed: float = 1.0) -> list[float]:
    """Make a deterministic L2-normalized embedding vector."""
    return l2_normalize(_make_embedding(dims, seed))


# ---------------------------------------------------------------------------
# l2_normalize()
# ---------------------------------------------------------------------------


def test_l2_normalize_produces_unit_vector():
    """l2_normalize returns a vector with magnitude ~1.0."""
    vec = [1.0, 2.0, 3.0, 4.0]
    normalized = l2_normalize(vec)
    magnitude = math.sqrt(sum(x ** 2 for x in normalized))
    assert abs(magnitude - 1.0) < 1e-5


def test_l2_normalize_zero_vector_unchanged():
    """l2_normalize returns the zero vector unchanged (degenerate case)."""
    vec = [0.0, 0.0, 0.0]
    result = l2_normalize(vec)
    assert result == vec


def test_l2_normalize_returns_list_of_floats():
    """l2_normalize returns a list (not a numpy array)."""
    vec = [1.0, 0.0, 0.0]
    result = l2_normalize(vec)
    assert isinstance(result, list)
    assert all(isinstance(x, float) for x in result)


def test_l2_normalize_768_dims():
    """l2_normalize works correctly on 768-dimensional vectors."""
    vec = [math.sin(i) for i in range(768)]
    normalized = l2_normalize(vec)
    assert len(normalized) == 768
    magnitude = math.sqrt(sum(x ** 2 for x in normalized))
    assert abs(magnitude - 1.0) < 1e-4


# ---------------------------------------------------------------------------
# load_cache() / append_to_cache()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_cache_missing_file(tmp_path):
    """load_cache returns {} if the cache file doesn't exist."""
    result = await load_cache(tmp_path / "nonexistent.jsonl", "gemini-embedding-001", 768)
    assert result == {}


@pytest.mark.asyncio
async def test_load_cache_filters_by_model(tmp_path):
    """load_cache only returns entries with matching model."""
    cache_path = tmp_path / "cache.jsonl"
    entries = [
        {"content_hash": "hash1", "model": "gemini-embedding-001", "dimensions": 768, "embedding": [0.1]},
        {"content_hash": "hash2", "model": "old-model", "dimensions": 768, "embedding": [0.2]},
    ]
    with cache_path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")

    result = await load_cache(cache_path, "gemini-embedding-001", 768)
    assert "hash1" in result
    assert "hash2" not in result


@pytest.mark.asyncio
async def test_load_cache_filters_by_dimensions(tmp_path):
    """load_cache only returns entries with matching dimensions."""
    cache_path = tmp_path / "cache.jsonl"
    entries = [
        {"content_hash": "hash1", "model": "gemini-embedding-001", "dimensions": 768, "embedding": [0.1]},
        {"content_hash": "hash2", "model": "gemini-embedding-001", "dimensions": 1536, "embedding": [0.2]},
    ]
    with cache_path.open("w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")

    result = await load_cache(cache_path, "gemini-embedding-001", 768)
    assert "hash1" in result
    assert "hash2" not in result


@pytest.mark.asyncio
async def test_append_to_cache_writes_entry(tmp_path):
    """append_to_cache appends a valid JSON entry to the cache file."""
    cache_path = tmp_path / "cache.jsonl"
    embedding = [0.1, 0.2, 0.3]
    await append_to_cache(cache_path, "myhash", embedding, "gemini-embedding-001", 768)

    assert cache_path.exists()
    data = json.loads(cache_path.read_text().strip())
    assert data["content_hash"] == "myhash"
    assert data["model"] == "gemini-embedding-001"
    assert data["dimensions"] == 768
    assert data["embedding"] == embedding


@pytest.mark.asyncio
async def test_append_to_cache_includes_model_and_dimensions(tmp_path):
    """append_to_cache stores model and dimensions for cache validation."""
    cache_path = tmp_path / "cache.jsonl"
    await append_to_cache(cache_path, "h1", [0.5], "my-model", 512)
    data = json.loads(cache_path.read_text().strip())
    assert data["model"] == "my-model"
    assert data["dimensions"] == 512


@pytest.mark.asyncio
async def test_cache_roundtrip(tmp_path):
    """Entries written by append_to_cache are readable by load_cache."""
    cache_path = tmp_path / "cache.jsonl"
    embedding = l2_normalize([1.0, 2.0, 3.0])
    await append_to_cache(cache_path, "abc123", embedding, "gemini-embedding-001", 768)
    result = await load_cache(cache_path, "gemini-embedding-001", 768)
    assert "abc123" in result
    assert result["abc123"] == embedding


# ---------------------------------------------------------------------------
# embed_chunks() — dimension validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_chunks_dimension_mismatch_raises(tmp_path):
    """embed_chunks raises ValueError when embedder dims don't match deployment."""
    embedder = _make_mock_embedder(dims=1536)
    chunk = _make_chunk()

    with (
        patch("doc_hub.db.get_vector_dim", return_value=768),
        patch("doc_hub.embed.embeddings_cache_path", return_value=tmp_path / "cache.jsonl"),
        patch("doc_hub.embed.embedded_chunks_path", return_value=tmp_path / "embedded.jsonl"),
    ):
        with pytest.raises(ValueError, match="1536-dim"):
            await embed_chunks("test-corpus", [chunk], embedder)


@pytest.mark.asyncio
async def test_embed_chunks_dimension_mismatch_error_message(tmp_path):
    """embed_chunks dimension mismatch error mentions the model name."""
    embedder = _make_mock_embedder(dims=1536, model="openai-text-3-large")
    chunk = _make_chunk()

    with (
        patch("doc_hub.db.get_vector_dim", return_value=768),
        patch("doc_hub.embed.embeddings_cache_path", return_value=tmp_path / "cache.jsonl"),
        patch("doc_hub.embed.embedded_chunks_path", return_value=tmp_path / "embedded.jsonl"),
    ):
        with pytest.raises(ValueError, match="openai-text-3-large"):
            await embed_chunks("test-corpus", [chunk], embedder)


# ---------------------------------------------------------------------------
# embed_chunks() — main embedding function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_chunks_cache_hit_skips_api_call(tmp_path):
    """embed_chunks skips embedder.embed_batch for chunks already in the cache."""
    chunk = _make_chunk(content="Cached content.")
    embedder = _make_mock_embedder()

    cache_path = tmp_path / "chunks" / "embeddings_cache.jsonl"
    cache_path.parent.mkdir(parents=True)
    cached_embedding = _make_unit_embedding(768)
    entry = {
        "content_hash": chunk.content_hash,
        "model": "test-model",
        "dimensions": 768,
        "embedding": cached_embedding,
    }
    cache_path.write_text(json.dumps(entry) + "\n")

    with (
        patch("doc_hub.db.get_vector_dim", return_value=768),
        patch("doc_hub.embed.embeddings_cache_path", return_value=cache_path),
        patch("doc_hub.embed.embedded_chunks_path", return_value=tmp_path / "chunks" / "embedded_chunks.jsonl"),
    ):
        result = await embed_chunks("test-corpus", [chunk], embedder)
        embedder.embed_batch.assert_not_called()

    assert len(result) == 1
    assert result[0].content_hash == chunk.content_hash


@pytest.mark.asyncio
async def test_embed_chunks_output_path_written(tmp_path):
    """embed_chunks writes to embedded_chunks_path(corpus_slug)."""
    chunk = _make_chunk(content="Test content.")
    embedder = _make_mock_embedder()
    embedder.embed_batch = AsyncMock(return_value=[[0.1] * 768])
    expected_output = tmp_path / "chunks" / "embedded_chunks.jsonl"

    with (
        patch("doc_hub.db.get_vector_dim", return_value=768),
        patch("doc_hub.embed.embeddings_cache_path", return_value=tmp_path / "chunks" / "cache.jsonl"),
        patch("doc_hub.embed.embedded_chunks_path", return_value=expected_output),
        patch("doc_hub.embed.asyncio.sleep"),
    ):
        await embed_chunks("test-corpus", [chunk], embedder)

    assert expected_output.exists()


@pytest.mark.asyncio
async def test_embed_chunks_l2_normalization_applied(tmp_path):
    """embed_chunks applies L2 normalization to all embeddings."""
    chunk = _make_chunk(content="Normalize me.")
    raw_embedding = [2.0] * 768  # magnitude = sqrt(768 * 4) = large
    embedder = _make_mock_embedder()
    embedder.embed_batch = AsyncMock(return_value=[raw_embedding])

    with (
        patch("doc_hub.db.get_vector_dim", return_value=768),
        patch("doc_hub.embed.embeddings_cache_path", return_value=tmp_path / "chunks" / "cache.jsonl"),
        patch("doc_hub.embed.embedded_chunks_path", return_value=tmp_path / "chunks" / "embedded.jsonl"),
        patch("doc_hub.embed.asyncio.sleep"),
    ):
        result = await embed_chunks("test-corpus", [chunk], embedder)

    assert len(result) == 1
    emb = result[0].embedding
    magnitude = math.sqrt(sum(x ** 2 for x in emb))
    assert abs(magnitude - 1.0) < 1e-4, f"Embedding not normalized: magnitude={magnitude}"


@pytest.mark.asyncio
async def test_embed_chunks_rate_limit_sleep_between_batches(tmp_path):
    """embed_chunks sleeps between batches (rate limiting)."""
    num_chunks = BATCH_SIZE + 1
    chunks = [_make_chunk(content=f"Content {i}") for i in range(num_chunks)]
    for i, c in enumerate(chunks):
        c.content_hash = hashlib.sha256(f"Content {i}".encode()).hexdigest()

    embedder = _make_mock_embedder()
    embedder.embed_batch = AsyncMock(return_value=[[0.1] * 768 for _ in range(BATCH_SIZE + 1)])

    sleep_calls = []

    async def mock_sleep(secs):
        sleep_calls.append(secs)

    with (
        patch("doc_hub.db.get_vector_dim", return_value=768),
        patch("doc_hub.embed.embeddings_cache_path", return_value=tmp_path / "cache.jsonl"),
        patch("doc_hub.embed.embedded_chunks_path", return_value=tmp_path / "embedded.jsonl"),
        patch("doc_hub.embed.asyncio.sleep", side_effect=mock_sleep),
    ):
        await embed_chunks("test-corpus", chunks, embedder)

    assert sleep_calls == []


@pytest.mark.asyncio
async def test_embed_chunks_uses_embedding_input_not_raw_content(tmp_path):
    """embed_chunks calls embedding_input() to build texts, not chunk.content."""
    chunk = _make_chunk(
        source_file="models__openai.md",
        content="OpenAI API configuration.",
        section_path="OpenAI > Configuration",
    )

    captured_texts = []
    embedder = _make_mock_embedder()

    async def mock_embed_batch(texts):
        captured_texts.extend(texts)
        return [[0.1] * 768 for _ in texts]

    embedder.embed_batch = mock_embed_batch

    with (
        patch("doc_hub.db.get_vector_dim", return_value=768),
        patch("doc_hub.embed.embeddings_cache_path", return_value=tmp_path / "cache.jsonl"),
        patch("doc_hub.embed.embedded_chunks_path", return_value=tmp_path / "embedded.jsonl"),
        patch("doc_hub.embed.asyncio.sleep"),
    ):
        await embed_chunks("test-corpus", [chunk], embedder)

    assert len(captured_texts) == 1
    # The text should contain the embedding_input prefix, not just raw content
    assert "Document:" in captured_texts[0]
    assert "Section:" in captured_texts[0]
    assert captured_texts[0] != chunk.content


@pytest.mark.asyncio
async def test_embed_chunks_returns_embedded_chunk_list(tmp_path):
    """embed_chunks returns a list of EmbeddedChunk objects."""
    chunk = _make_chunk(content="Some content.")
    embedder = _make_mock_embedder()

    with (
        patch("doc_hub.db.get_vector_dim", return_value=768),
        patch("doc_hub.embed.embeddings_cache_path", return_value=tmp_path / "cache.jsonl"),
        patch("doc_hub.embed.embedded_chunks_path", return_value=tmp_path / "embedded.jsonl"),
        patch("doc_hub.embed.asyncio.sleep"),
    ):
        result = await embed_chunks("test-corpus", [chunk], embedder)

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], EmbeddedChunk)


@pytest.mark.asyncio
async def test_embed_chunks_output_contains_embedding_field(tmp_path):
    """embed_chunks writes output JSONL with 'embedding' field."""
    chunk = _make_chunk(content="Some content.")
    output_path = tmp_path / "embedded.jsonl"
    embedder = _make_mock_embedder()

    with (
        patch("doc_hub.db.get_vector_dim", return_value=768),
        patch("doc_hub.embed.embeddings_cache_path", return_value=tmp_path / "cache.jsonl"),
        patch("doc_hub.embed.embedded_chunks_path", return_value=output_path),
        patch("doc_hub.embed.asyncio.sleep"),
    ):
        await embed_chunks("test-corpus", [chunk], embedder)

    data = json.loads(output_path.read_text().strip())
    assert "embedding" in data
    assert isinstance(data["embedding"], list)
    assert len(data["embedding"]) == 768


@pytest.mark.asyncio
async def test_embed_chunks_accepts_corpus_slug_string(tmp_path):
    """embed_chunks accepts a plain string corpus slug, not a Corpus object."""
    chunk = _make_chunk(content="Some content.")
    embedder = _make_mock_embedder()

    with (
        patch("doc_hub.db.get_vector_dim", return_value=768),
        patch("doc_hub.embed.embeddings_cache_path", return_value=tmp_path / "cache.jsonl"),
        patch("doc_hub.embed.embedded_chunks_path", return_value=tmp_path / "embedded.jsonl"),
        patch("doc_hub.embed.asyncio.sleep"),
    ):
        # Should not raise — corpus_slug is a str, not a Corpus object
        result = await embed_chunks("my-corpus-slug", [chunk], embedder)

    assert len(result) == 1


# ---------------------------------------------------------------------------
# BATCH_SIZE constant
# ---------------------------------------------------------------------------


def test_batch_size_is_100():
    """BATCH_SIZE constant is 100 (Gemini batchEmbedContents hard limit)."""
    assert BATCH_SIZE == 100


# ---------------------------------------------------------------------------
# EmbeddedChunk dataclass
# ---------------------------------------------------------------------------


def test_embedded_chunk_from_chunk():
    """EmbeddedChunk.from_chunk() preserves all Chunk fields."""
    chunk = _make_chunk()
    embedding = [0.1] * 768
    ec = EmbeddedChunk.from_chunk(chunk, embedding)

    assert ec.source_file == chunk.source_file
    assert ec.source_url == chunk.source_url
    assert ec.section_path == chunk.section_path
    assert ec.heading == chunk.heading
    assert ec.heading_level == chunk.heading_level
    assert ec.content == chunk.content
    assert ec.start_line == chunk.start_line
    assert ec.char_count == chunk.char_count
    assert ec.content_hash == chunk.content_hash
    assert ec.category == chunk.category
    assert ec.embedding == embedding


# ---------------------------------------------------------------------------
# Pipeline integration: run_parse / run_embed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_parse_returns_chunks(tmp_path):
    """pipeline.run_parse returns a list of Chunk objects."""
    from doc_hub.pipeline import run_parse
    from doc_hub.parse import Chunk as ParseChunk
    from doc_hub.models import Corpus

    corpus = Corpus(
        slug="test",
        name="Test",
        fetch_strategy="llms_txt",
        fetch_config={"url": "https://example.com/llms.txt"},
    )
    raw_path = tmp_path / "raw"
    raw_path.mkdir()
    (raw_path / "doc.md").write_text("# Title\n\nContent here.")

    chunks_output = tmp_path / "chunks"

    with (
        patch("doc_hub.pipeline.raw_dir", return_value=raw_path),
        patch("doc_hub.parse.chunks_dir", return_value=chunks_output),
    ):
        result = await run_parse(corpus)

    assert isinstance(result, list)
    assert len(result) >= 1
    assert all(isinstance(c, ParseChunk) for c in result)


@pytest.mark.asyncio
async def test_run_embed_resolves_embedder_from_registry(tmp_path):
    """run_embed resolves the embedder from the plugin registry when none provided."""
    from doc_hub.pipeline import run_embed
    from doc_hub.models import Corpus

    corpus = Corpus(
        slug="test",
        name="Test",
        fetch_strategy="llms_txt",
        fetch_config={},
        embedder="gemini",
    )
    chunk = _make_chunk()
    embedded_result = [EmbeddedChunk.from_chunk(chunk, [0.1] * 768)]

    mock_embedder = _make_mock_embedder()
    mock_registry = MagicMock()
    mock_registry.get_embedder.return_value = mock_embedder

    with (
        patch("doc_hub.discovery.get_registry", return_value=mock_registry),
        patch("doc_hub.embed.embed_chunks", new=AsyncMock(return_value=embedded_result)),
    ):
        result = await run_embed(corpus, chunks=[chunk])

    mock_registry.get_embedder.assert_called_once_with("gemini")
    assert result == embedded_result


@pytest.mark.asyncio
async def test_run_embed_uses_provided_embedder(tmp_path):
    """run_embed uses a pre-provided embedder without touching the registry."""
    from doc_hub.pipeline import run_embed
    from doc_hub.models import Corpus

    corpus = Corpus(
        slug="test",
        name="Test",
        fetch_strategy="llms_txt",
        fetch_config={},
    )
    chunk = _make_chunk()
    embedded_result = [EmbeddedChunk.from_chunk(chunk, [0.1] * 768)]
    mock_embedder = _make_mock_embedder()

    with (
        patch("doc_hub.embed.embed_chunks", new=AsyncMock(return_value=embedded_result)),
    ):
        result = await run_embed(corpus, chunks=[chunk], embedder=mock_embedder)

    assert result == embedded_result
