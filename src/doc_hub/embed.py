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
import time as _time
from collections import deque
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from doc_hub.parse import Chunk, embedding_input
from doc_hub.paths import embeddings_cache_path, embedded_chunks_path

if TYPE_CHECKING:
    from doc_hub.protocols import Embedder

log = logging.getLogger(__name__)

# Gemini batchEmbedContents hard limit is 100 texts per request.
BATCH_SIZE = 100

# Default rate-limit budgets (conservative for Gemini free tier).
# Free tier: 100 RPM, ~250k TPM.  We leave headroom for search queries.
DEFAULT_RPM = 80
DEFAULT_TPM = 200_000


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
    snapshot_id: str = "legacy"
    source_version: str = "latest"
    fetched_at: str | None = None
    embedding: list[float] = field(default_factory=list)

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
            snapshot_id=chunk.snapshot_id,
            source_version=chunk.source_version,
            fetched_at=chunk.fetched_at,
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
# Sliding-window rate limiter
# ---------------------------------------------------------------------------


def _estimate_tokens(texts: list[str]) -> int:
    """Estimate token count for a batch of texts.

    Uses chars/4 as a rough approximation (standard heuristic for
    English text with most tokenizers). Slightly overestimates, which
    is preferable for rate-limiting purposes.
    """
    return sum(len(t) for t in texts) // 4


class RateLimiter:
    """Sliding-window rate limiter for RPM and TPM.

    Tracks requests and estimated tokens over a rolling 60-second window.
    Before each batch, ``acquire()`` checks whether sending the batch
    would exceed either budget. If so, it sleeps exactly long enough
    for old entries to expire from the window.

    The embedder plugin's own retry-on-429 logic remains as a safety net
    for cases where the token estimate is off.

    Args:
        rpm: Maximum requests per minute (default: 80).
        tpm: Maximum tokens per minute (default: 200_000).
    """

    def __init__(self, rpm: int = DEFAULT_RPM, tpm: int = DEFAULT_TPM) -> None:
        self.rpm = rpm
        self.tpm = tpm
        self._request_times: deque[float] = deque()
        self._token_usage: deque[tuple[float, int]] = deque()

    def _prune(self, now: float) -> tuple[int, int]:
        """Remove expired entries and return (current_rpm, current_tpm)."""
        cutoff = now - 60.0
        while self._request_times and self._request_times[0] < cutoff:
            self._request_times.popleft()
        while self._token_usage and self._token_usage[0][0] < cutoff:
            self._token_usage.popleft()
        current_rpm = len(self._request_times)
        current_tpm = sum(tokens for _, tokens in self._token_usage)
        return current_rpm, current_tpm

    async def acquire(self, estimated_tokens: int) -> None:
        """Wait until a request consuming ``estimated_tokens`` can be sent."""
        while True:
            now = _time.monotonic()
            current_rpm, current_tpm = self._prune(now)

            rpm_ok = current_rpm < self.rpm
            tpm_ok = current_tpm + estimated_tokens <= self.tpm

            if rpm_ok and tpm_ok:
                self._request_times.append(now)
                self._token_usage.append((now, estimated_tokens))
                return

            # Calculate how long to wait.
            wait = 0.0
            if not rpm_ok and self._request_times:
                # Wait for the oldest request to expire from the window.
                wait = max(wait, self._request_times[0] + 60.0 - now)
            if not tpm_ok:
                # Walk the token window to find when enough tokens expire.
                needed = current_tpm + estimated_tokens - self.tpm
                accumulated = 0
                for ts, tokens in self._token_usage:
                    accumulated += tokens
                    if accumulated >= needed:
                        wait = max(wait, ts + 60.0 - now)
                        break

            wait = max(wait, 0.5)  # minimum wait to avoid busy-spinning
            log.info(
                "Rate limiter: waiting %.1fs (RPM: %d/%d, TPM: ~%dk/%dk)",
                wait,
                current_rpm, self.rpm,
                current_tpm // 1000, self.tpm // 1000,
            )
            await asyncio.sleep(wait)


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
    snapshot_id: str | None = None,
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

    Rate limiting is handled by a sliding-window ``RateLimiter`` that
    tracks RPM and estimated TPM usage over a rolling 60-second window.
    Batches are sent as fast as the budget allows — no fixed sleep.
    Configure via environment variables:
        DOC_HUB_EMBED_RPM   — requests/minute budget (default: 80)
        DOC_HUB_EMBED_TPM   — tokens/minute budget   (default: 200000)

    The function uses embedding_input(chunk) from parse.py to construct
    the text sent to the embedder (prepends document/section context).

    Args:
        corpus_slug: Corpus slug (for paths and logging).
        chunks: Parsed chunks from the parse stage.
        embedder: An Embedder protocol instance.
        batch_size: Items per API request (default 100, API max).

    Returns:
        List of EmbeddedChunk objects with L2-normalized embeddings.

    Raises:
        ValueError: If embedder dimensions don't match deployment config.
    """
    from doc_hub.db import get_vector_dim  # noqa: PLC0415

    # Read rate-limit config from env (allows override without code change)
    rpm = int(os.getenv("DOC_HUB_EMBED_RPM", str(DEFAULT_RPM)))
    tpm = int(os.getenv("DOC_HUB_EMBED_TPM", str(DEFAULT_TPM)))
    batch_size = int(os.getenv("DOC_HUB_EMBED_BATCH_SIZE", str(batch_size)))

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
    cache_path = embeddings_cache_path(corpus_slug, snapshot_id=snapshot_id)
    output_path = embedded_chunks_path(corpus_slug, snapshot_id=snapshot_id)

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
        log.info(
            "[%s] Embedding %d new chunks in %d batches (RPM budget: %d, TPM budget: %dk)",
            corpus_slug, n_to_embed, n_batches, rpm, tpm // 1000,
        )

        # Ensure cache file parent directory exists
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        rate_limiter = RateLimiter(rpm=rpm, tpm=tpm)

        for batch_num, batch_start in enumerate(range(0, n_to_embed, batch_size), start=1):
            batch_indices = to_embed_indices[batch_start: batch_start + batch_size]
            batch_chunks = [chunks[i] for i in batch_indices]

            # Build embedding input texts using embedding_input() from parse.py.
            # This prepends "Document: {name} | Section: {path}\n\n" before content,
            # which is critical for embedding quality — must NOT use raw chunk.content.
            texts = [embedding_input(chunk) for chunk in batch_chunks]

            # Wait for rate-limit budget before sending the request.
            estimated_tokens = _estimate_tokens(texts)
            await rate_limiter.acquire(estimated_tokens)

            log.info(
                "[%s] Batch %d/%d: embedding %d chunks (~%dk tokens)",
                corpus_slug,
                batch_num,
                n_batches,
                len(texts),
                estimated_tokens // 1000,
            )

            raw_embeddings = await embedder.embed_batch(texts)

            # Normalize and cache each embedding
            for chunk, raw_emb in zip(batch_chunks, raw_embeddings):
                normalized = l2_normalize(raw_emb)
                cache[chunk.content_hash] = normalized
                await append_to_cache(cache_path, chunk.content_hash, normalized, model, dimensions)

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
