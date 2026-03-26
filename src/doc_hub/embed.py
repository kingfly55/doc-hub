"""Core embed pipeline orchestration for doc-hub.

Reads chunks from a list of Chunk objects (output of parse stage), checks
content hashes against a per-corpus local embedding cache, batch-embeds only
new/changed chunks using the provided Embedder plugin, L2-normalizes all
vectors, and writes embedded_chunks.jsonl.

Embedder-specific logic (API calls, retry, rate limits) lives in embedder
plugins (e.g. doc_hub._builtins.embedders.gemini.GeminiEmbedder).

Core responsibilities:
- Caching (keyed by content_hash + model_name + dimensions)
- L2 normalization of all output vectors
- Batch orchestration and rate-limit pacing
- Writing embedded_chunks.jsonl
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from doc_hub.parse import Chunk, embedding_input
from doc_hub.paths import embeddings_cache_path, embedded_chunks_path

if TYPE_CHECKING:
    from doc_hub.protocols import Embedder

log = logging.getLogger(__name__)

BATCH_SIZE = 50          # Items per API request (default).


# ---------------------------------------------------------------------------
# EmbeddedChunk dataclass
# ---------------------------------------------------------------------------


@dataclass
class EmbeddedChunk:
    """A Chunk with an attached embedding vector."""

    source_file: str
    source_url: str
    section_path: str
    heading: str
    heading_level: int
    content: str
    start_line: int
    end_line: int
    char_count: int
    content_hash: str
    category: str
    embedding: list[float]

    @classmethod
    def from_chunk(cls, chunk: Chunk, embedding: list[float]) -> "EmbeddedChunk":
        """Construct from a Chunk and its embedding vector."""
        return cls(
            source_file=chunk.source_file,
            source_url=chunk.source_url,
            section_path=chunk.section_path,
            heading=chunk.heading,
            heading_level=chunk.heading_level,
            content=chunk.content,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            char_count=chunk.char_count,
            content_hash=chunk.content_hash,
            category=chunk.category,
            embedding=embedding,
        )


# ---------------------------------------------------------------------------
# L2 normalization
# ---------------------------------------------------------------------------


def l2_normalize(vec: list[float]) -> list[float]:
    """Return an L2-normalized copy of the input vector.

    All embedding vectors MUST be L2-normalized before storage. Without
    normalization, cosine distance (the <=> operator in pgvector) gives
    incorrect results.

    Args:
        vec: Raw embedding vector from the embedder plugin.

    Returns:
        L2-normalized vector as a list of floats.
    """
    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return vec  # degenerate case — leave unchanged
    return (arr / norm).tolist()


# ---------------------------------------------------------------------------
# Cache I/O (wrapped in asyncio.to_thread for async rewrite)
# ---------------------------------------------------------------------------


def _load_cache_sync(cache_path: Path, model: str, dimensions: int) -> dict[str, list[float]]:
    """Synchronous implementation of cache loading.

    Only entries that match the given model and dimensions are returned.
    Stale entries (different model or dimensions) are silently skipped.

    Args:
        cache_path: Path to the embeddings cache JSONL file.
        model: Expected embedding model name.
        dimensions: Expected embedding dimensions.

    Returns:
        Dict mapping content_hash -> embedding vector.
    """
    cache: dict[str, list[float]] = {}
    if not cache_path.exists():
        return cache

    for line in cache_path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("model") == model and entry.get("dimensions") == dimensions:
            cache[entry["content_hash"]] = entry["embedding"]

    return cache


async def load_cache(cache_path: Path, model: str, dimensions: int) -> dict[str, list[float]]:
    """Load embeddings cache from JSONL file (async wrapper).

    Only entries that match the given model and dimensions are returned.
    Stale entries (different model or dimensions) are silently skipped.
    This prevents using stale embeddings after a model change.

    Args:
        cache_path: Path to the embeddings cache JSONL file.
        model: Expected embedding model name (for cache validation).
        dimensions: Expected embedding dimensions (for cache validation).

    Returns:
        Dict mapping content_hash -> embedding vector.
    """
    return await asyncio.to_thread(_load_cache_sync, cache_path, model, dimensions)


def _append_to_cache_sync(
    cache_path: Path,
    content_hash: str,
    embedding: list[float],
    model: str,
    dimensions: int,
) -> None:
    """Synchronous implementation of cache append."""
    entry = {
        "content_hash": content_hash,
        "model": model,
        "dimensions": dimensions,
        "embedding": embedding,
    }
    with cache_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


async def append_to_cache(
    cache_path: Path,
    content_hash: str,
    embedding: list[float],
    model: str,
    dimensions: int,
) -> None:
    """Append a single embedding entry to the cache file (async wrapper).

    Args:
        cache_path: Path to the embeddings cache JSONL file.
        content_hash: SHA-256 hash of the chunk content.
        embedding: The embedding vector to cache.
        model: Embedding model name (stored for cache validation).
        dimensions: Embedding dimensions (stored for cache validation).
    """
    await asyncio.to_thread(
        _append_to_cache_sync,
        cache_path,
        content_hash,
        embedding,
        model,
        dimensions,
    )


# ---------------------------------------------------------------------------
# Main embedding logic
# ---------------------------------------------------------------------------


async def embed_chunks(
    corpus_slug: str,
    chunks: list[Chunk],
    embedder: "Embedder",
    *,
    batch_size: int = BATCH_SIZE,
    inter_batch_sleep: float = 65.0,
) -> list[EmbeddedChunk]:
    """Embed parsed chunks using the provided embedder plugin.

    Core responsibilities:
    1. Validate embedder dimensions match deployment config
    2. Load per-corpus embedding cache
    3. Identify chunks needing embedding (cache miss)
    4. Call embedder.embed_batch() for each batch
    5. L2-normalize all vectors
    6. Update cache
    7. Write embedded_chunks.jsonl

    The function uses embedding_input(chunk) from parse.py to construct
    the text sent to the embedder (prepends document/section context).

    Args:
        corpus_slug: Corpus slug (for paths and logging).
        chunks: Parsed chunks from the parse stage.
        embedder: An Embedder protocol instance.
        batch_size: Items per API request (default 50).
        inter_batch_sleep: Seconds to sleep between batches (default 65).
            Set to 0 for embedders without rate limits.
            Can be overridden via DOC_HUB_EMBED_SLEEP environment variable.

    Returns:
        List of EmbeddedChunk objects with L2-normalized embeddings.

    Raises:
        ValueError: If embedder dimensions don't match deployment config.
    """
    import time as _time  # noqa: PLC0415

    from doc_hub.db import get_vector_dim  # noqa: PLC0415

    # Read inter_batch_sleep from env var (allows override without code change)
    inter_batch_sleep = float(os.getenv("DOC_HUB_EMBED_SLEEP", str(inter_batch_sleep)))

    # Dimension validation
    deployment_dim = get_vector_dim()
    if embedder.dimensions != deployment_dim:
        raise ValueError(
            f"Embedder '{embedder.model_name}' produces {embedder.dimensions}-dim "
            f"vectors, but this deployment is configured for {deployment_dim}-dim "
            f"(DOC_HUB_VECTOR_DIM={deployment_dim}). All corpora in a deployment "
            f"must use the same embedding dimensions."
        )

    t_start = _time.time()

    model = embedder.model_name
    dimensions = embedder.dimensions
    cache_path = embeddings_cache_path(corpus_slug)
    output_path = embedded_chunks_path(corpus_slug)

    total = len(chunks)
    log.info("[%s] Embedding %d chunks (model=%s, dims=%d)", corpus_slug, total, model, dimensions)

    # --- Load cache ---
    cache = await load_cache(cache_path, model, dimensions)
    log.info("[%s] Loaded %d cached embeddings", corpus_slug, len(cache))

    # --- Identify chunks that need embedding ---
    to_embed_indices: list[int] = []
    for i, chunk in enumerate(chunks):
        if chunk.content_hash not in cache:
            to_embed_indices.append(i)

    n_cached = total - len(to_embed_indices)
    n_to_embed = len(to_embed_indices)
    log.info(
        "[%s] %d cache hits, %d chunks to embed (%.1f%% cached)",
        corpus_slug,
        n_cached,
        n_to_embed,
        100.0 * n_cached / total if total else 0,
    )

    if n_to_embed > 0:
        n_batches = (n_to_embed + batch_size - 1) // batch_size
        log.info("[%s] Embedding %d new chunks in %d batches...", corpus_slug, n_to_embed, n_batches)

        # Ensure cache file parent directory exists
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        for batch_num, batch_start in enumerate(range(0, n_to_embed, batch_size), start=1):
            batch_indices = to_embed_indices[batch_start: batch_start + batch_size]
            batch_chunks = [chunks[i] for i in batch_indices]

            # Build embedding input texts using embedding_input() from parse.py.
            # This prepends "Document: {name} | Section: {path}\n\n" before content,
            # which is critical for embedding quality — must NOT use raw chunk.content.
            texts = [embedding_input(chunk) for chunk in batch_chunks]

            log.info(
                "[%s] Batch %d/%d: embedding %d chunks",
                corpus_slug,
                batch_num,
                n_batches,
                len(texts),
            )

            raw_embeddings = await embedder.embed_batch(texts)

            # Normalize and cache each embedding
            for chunk, raw_emb in zip(batch_chunks, raw_embeddings):
                normalized = l2_normalize(raw_emb)
                cache[chunk.content_hash] = normalized
                await append_to_cache(cache_path, chunk.content_hash, normalized, model, dimensions)

            # Sleep between batches to respect rate limits.
            # Default: 65s (Gemini free tier: 100 req/min).
            # Override via DOC_HUB_EMBED_SLEEP env var.
            if batch_num < n_batches:
                log.info(
                    "[%s] Rate-limit pause: sleeping %.1fs before next batch...",
                    corpus_slug,
                    inter_batch_sleep,
                )
                await asyncio.sleep(inter_batch_sleep)

        log.info("[%s] Embedding complete. Cache now has %d entries.", corpus_slug, len(cache))
    else:
        log.info("[%s] 0 chunks to embed — all served from cache", corpus_slug)

    # --- Build output list ---
    embedded: list[EmbeddedChunk] = []
    for chunk in chunks:
        embedding = l2_normalize(cache[chunk.content_hash])
        embedded.append(EmbeddedChunk.from_chunk(chunk, embedding))

    # --- Write output file ---
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_output() -> None:
        with output_path.open("w", encoding="utf-8") as f:
            for ec in embedded:
                f.write(json.dumps(asdict(ec)) + "\n")

    await asyncio.to_thread(_write_output)

    elapsed = _time.time() - t_start
    log.info(
        "[%s] Wrote %d embedded chunks to %s (%.1fs elapsed)",
        corpus_slug,
        total,
        output_path,
        elapsed,
    )
    log.info(
        "[%s] Summary: total=%d, cache_hits=%d, api_calls=%d, batches=%d, time=%.1fs",
        corpus_slug,
        total,
        n_cached,
        n_to_embed,
        (n_to_embed + batch_size - 1) // batch_size if n_to_embed else 0,
        elapsed,
    )
    return embedded
