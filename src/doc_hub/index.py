"""Index pipeline for doc-hub: upsert embedded chunks into PostgreSQL.

Reads EmbeddedChunk objects (output of the embed stage), upserts them into
the shared ``doc_chunks`` table scoped by ``corpus_id``, and updates per-corpus
metadata in ``doc_index_meta`` and ``doc_corpora``.

Ported from ``pydantic_ai_docs/index.py`` with the following changes:
- Sync psycopg  →  async asyncpg
- Single corpus table  →  shared ``doc_chunks`` table with ``corpus_id`` FK
- Uses ``$1, $2, ...`` asyncpg placeholders (not ``%s`` psycopg placeholders)
- Uses explicit ``async with conn.transaction():`` (asyncpg has no autocommit toggle)
- Row-by-row ``conn.execute()`` loop inside one transaction (asyncpg executemany()
  does NOT support ON CONFLICT DO UPDATE — see phase plan for details)
- Per-corpus advisory lock via ``pg_advisory_xact_lock(hashtext($1))``
- All upsert/delete/stats operations scoped by ``corpus_id``
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

import asyncpg

from doc_hub.db import update_corpus_stats
from doc_hub.embed import EmbeddedChunk
from doc_hub.models import Corpus

log = logging.getLogger(__name__)

# Batch size controls how often progress is logged during the upsert loop.
# The actual DB writes are all inside a single transaction.
BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class IndexResult:
    """Statistics from a single index run."""

    inserted: int
    """Number of newly inserted rows (INSERT, not UPDATE)."""

    updated: int
    """Number of existing rows that were updated (ON CONFLICT DO UPDATE)."""

    deleted: int
    """Number of stale rows deleted (only non-zero when full=True)."""

    total: int
    """Total rows in doc_chunks for this corpus after the run."""


# ---------------------------------------------------------------------------
# Core upsert logic
# ---------------------------------------------------------------------------


async def upsert_chunks(
    pool: asyncpg.Pool,
    corpus: Corpus,
    chunks: list[EmbeddedChunk],
    *,
    full: bool = False,
    embedder_model: str = "",
    embedder_dims: int = 0,
) -> IndexResult:
    """Upsert embedded chunks into ``doc_chunks`` for a specific corpus.

    Each chunk is inserted or updated using ``ON CONFLICT (corpus_id,
    content_hash) DO UPDATE``.  All writes happen inside a single transaction
    that also holds a per-corpus advisory lock to prevent concurrent indexing
    of the same corpus.

    When *full* is True, rows belonging to this corpus whose ``content_hash``
    is **not** in the current chunk set are deleted (stale cleanup).  Rows from
    other corpora are never touched.

    Args:
        pool:   asyncpg connection pool.
        corpus: The corpus being indexed (provides ``slug``).
        chunks: L2-normalised embedded chunks from the embed stage.
        full:   If True, delete stale rows for this corpus after upsert.

    Returns:
        :class:`IndexResult` with counts of inserted, updated, and deleted rows
        plus the post-run total for this corpus.
    """
    if not chunks:
        log.warning("[%s] upsert_chunks called with empty chunk list — nothing to do", corpus.slug)
        total = await pool.fetchval(
            "SELECT count(*) FROM doc_chunks WHERE corpus_id = $1",
            corpus.slug,
        )
        return IndexResult(inserted=0, updated=0, deleted=0, total=int(total or 0))

    inserted = 0
    updated = 0
    deleted = 0
    current_hashes: list[str] = []

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Acquire a per-corpus advisory lock scoped to this transaction.
            # This serialises concurrent index operations on the same corpus
            # (e.g., overlapping MCP refresh + cron sync).
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                corpus.slug,
            )
            log.debug("[%s] Advisory lock acquired", corpus.slug)

            total_chunks = len(chunks)
            n_batches = (total_chunks + BATCH_SIZE - 1) // BATCH_SIZE

            for batch_num, batch_start in enumerate(range(0, total_chunks, BATCH_SIZE), start=1):
                batch = chunks[batch_start : batch_start + BATCH_SIZE]

                for chunk in batch:
                    current_hashes.append(chunk.content_hash)

                    # Format embedding vector as a Postgres literal string.
                    # asyncpg doesn't natively send Python lists as vector —
                    # the cast ``::vector`` in the SQL handles the conversion.
                    emb_str = "[" + ",".join(str(v) for v in chunk.embedding) + "]"

                    # asyncpg.execute() returns a status string like:
                    #   'INSERT 0 1'  — new row inserted
                    #   'INSERT 0 0'  — conflict, DO UPDATE fired (oid=0, rows=1 in theory)
                    #
                    # NOTE: Postgres always reports 'INSERT 0 1' for the ON CONFLICT
                    # DO UPDATE path as well (the UPDATE branch still returns one
                    # "inserted" row in the command tag). To distinguish true inserts
                    # from updates we use a RETURNING clause: xmax=0 means INSERT.
                    rows = await conn.fetch(
                        """
                        INSERT INTO doc_chunks (
                            corpus_id, content_hash, content, heading, source_file,
                            source_url, section_path, heading_level, start_line,
                            end_line, char_count, category, embedding
                        ) VALUES (
                            $1, $2, $3, $4, $5,
                            $6, $7, $8, $9,
                            $10, $11, $12, $13::vector
                        )
                        ON CONFLICT (corpus_id, content_hash) DO UPDATE SET
                            content       = EXCLUDED.content,
                            heading       = EXCLUDED.heading,
                            source_file   = EXCLUDED.source_file,
                            source_url    = EXCLUDED.source_url,
                            section_path  = EXCLUDED.section_path,
                            heading_level = EXCLUDED.heading_level,
                            start_line    = EXCLUDED.start_line,
                            end_line      = EXCLUDED.end_line,
                            char_count    = EXCLUDED.char_count,
                            category      = EXCLUDED.category,
                            embedding     = EXCLUDED.embedding
                        RETURNING (xmax = 0) AS is_insert
                        """,
                        corpus.slug,
                        chunk.content_hash,
                        chunk.content,
                        chunk.heading,
                        chunk.source_file,
                        chunk.source_url,
                        chunk.section_path,
                        chunk.heading_level,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.char_count,
                        chunk.category,
                        emb_str,
                    )
                    if rows and rows[0]["is_insert"]:
                        inserted += 1
                    else:
                        updated += 1

                log.info(
                    "[%s] Index batch %d/%d: %d rows processed (%d/%d total)",
                    corpus.slug,
                    batch_num,
                    n_batches,
                    len(batch),
                    min(batch_start + len(batch), total_chunks),
                    total_chunks,
                )

            # ---------------------------------------------------------------- #
            # Full mode: delete stale rows for THIS corpus only                 #
            # ---------------------------------------------------------------- #
            if full and current_hashes:
                result = await conn.execute(
                    """
                    DELETE FROM doc_chunks
                    WHERE corpus_id = $1
                      AND content_hash != ALL($2::text[])
                    """,
                    corpus.slug,
                    current_hashes,
                )
                # asyncpg execute() returns a status string like 'DELETE 5'
                deleted = _parse_command_count(result)
                log.info("[%s] Full mode: deleted %d stale chunks", corpus.slug, deleted)
            elif full:
                log.warning(
                    "[%s] --full requested but chunk list is empty — skipping stale deletion",
                    corpus.slug,
                )

    # -------------------------------------------------------------------- #
    # Post-transaction: update corpus stats + metadata                     #
    # -------------------------------------------------------------------- #
    total = await pool.fetchval(
        "SELECT count(*) FROM doc_chunks WHERE corpus_id = $1",
        corpus.slug,
    )
    total_int = int(total or 0)

    await update_corpus_stats(pool, corpus.slug, total_int)
    await _write_meta(pool, corpus.slug, total_int, embedder_model, embedder_dims)

    log.info(
        "[%s] Index complete: inserted=%d, updated=%d, deleted=%d, total=%d",
        corpus.slug,
        inserted,
        updated,
        deleted,
        total_int,
    )
    return IndexResult(inserted=inserted, updated=updated, deleted=deleted, total=total_int)


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


async def _write_meta(
    pool: asyncpg.Pool,
    corpus_slug: str,
    total_chunks: int,
    embedder_model: str,
    embedder_dims: int,
) -> None:
    """Write per-corpus metadata rows to ``doc_index_meta``.

    Keys written:
    - ``last_indexed_at``     — ISO 8601 timestamp (UTC)
    - ``total_chunks``        — chunk count as a string
    - ``embedding_model``     — embedder model name snapshot
    - ``embedding_dimensions`` — vector dimension snapshot

    The embedder model and dims are snapshots of what was actually used
    during this index run (the plugin registry could change between runs).

    All keys are scoped by ``corpus_id`` using the ``(corpus_id, key)``
    primary key.

    Args:
        pool: asyncpg connection pool.
        corpus_slug: Corpus slug (used as corpus_id in doc_index_meta).
        total_chunks: Total chunk count after the index run.
        embedder_model: Model name from the embedder plugin.
        embedder_dims: Vector dimensions from the embedder plugin.
    """
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    rows: list[tuple[str, str]] = [
        ("last_indexed_at", now_iso),
        ("total_chunks", str(total_chunks)),
        ("embedding_model", embedder_model),
        ("embedding_dimensions", str(embedder_dims)),
    ]

    async with pool.acquire() as conn:
        for key, value in rows:
            await conn.execute(
                """
                INSERT INTO doc_index_meta (corpus_id, key, value, updated_at)
                VALUES ($1, $2, $3, now())
                ON CONFLICT (corpus_id, key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_at = now()
                """,
                corpus_slug,
                key,
                value,
            )

    log.debug("[%s] Metadata updated (%d keys)", corpus_slug, len(rows))


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


async def verify_index(
    pool: asyncpg.Pool,
    corpus: Corpus,
    chunks: list[EmbeddedChunk],
) -> None:
    """Run a quick vector smoke-test to confirm the index is functional.

    Queries the top-3 nearest neighbours for the first chunk's embedding and
    asserts at least one row is returned.  This catches missing extension,
    wrong vector dimensions, or empty tables.

    Args:
        pool:   asyncpg connection pool.
        corpus: The corpus that was just indexed.
        chunks: The chunks that were indexed (need at least one for the probe).

    Raises:
        AssertionError: If the smoke-test query returns no results.
    """
    if not chunks:
        log.warning("[%s] verify_index: no chunks to probe — skipping smoke test", corpus.slug)
        return

    probe = chunks[0].embedding
    probe_str = "[" + ",".join(str(v) for v in probe) + "]"

    rows = await pool.fetch(
        """
        SELECT id, heading
        FROM doc_chunks
        WHERE corpus_id = $1
        ORDER BY embedding <=> $2::vector
        LIMIT 3
        """,
        corpus.slug,
        probe_str,
    )

    assert len(rows) > 0, (
        f"[{corpus.slug}] Vector smoke-test query returned no results — "
        "index may be empty or the vector extension is not working correctly."
    )

    log.info(
        "[%s] Smoke test passed: top result id=%d, heading=%r",
        corpus.slug,
        rows[0]["id"],
        rows[0]["heading"],
    )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _parse_command_count(status: str) -> int:
    """Parse the row-count from an asyncpg execute() status string.

    asyncpg returns status strings like ``'DELETE 5'``, ``'INSERT 0 1'``,
    ``'UPDATE 3'``.  The last space-separated token is always the affected
    row count.

    Args:
        status: asyncpg command status string.

    Returns:
        Integer row count, or 0 if parsing fails.
    """
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0
