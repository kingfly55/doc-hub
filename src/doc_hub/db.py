"""Database connection pool and schema management for doc-hub.

This module provides:
- create_pool(): async connection pool backed by asyncpg, with JSONB codec
- ensure_schema(): idempotent DDL to create all doc-hub tables and indexes

IMPORTANT: asyncpg does NOT auto-serialize Python dicts to/from JSONB.
The pool registers a custom codec so that Python dicts round-trip through
JSONB columns transparently (no manual json.dumps/loads needed at call sites).
"""

from __future__ import annotations

import json
import logging
import os

import asyncpg

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

# doc_corpora: the registry of documentation corpora
_CORPORA_DDL = """
CREATE TABLE IF NOT EXISTS doc_corpora (
    slug            text PRIMARY KEY,
    name            text NOT NULL,
    fetch_strategy  text NOT NULL,
    parser          text NOT NULL DEFAULT 'markdown',
    embedder        text NOT NULL DEFAULT 'gemini',
    fetch_config    jsonb NOT NULL,
    enabled         boolean DEFAULT true,
    last_indexed_at timestamptz,
    total_chunks    int DEFAULT 0
)
"""

# doc_chunks: the main chunks table with corpus_id FK.
# IMPORTANT:
#   - heading and content columns are listed BEFORE the tsv generated column
#     so that Postgres can compute the generated expression on CREATE TABLE.
#   - tsv uses weighted tsvector: heading→weight A, content→weight B.
#   - The unique constraint is (corpus_id, content_hash) — not just content_hash.
# NOTE: _CHUNKS_DDL constant is replaced by _chunks_ddl() function below.

# doc_index_meta: per-corpus key/value metadata (timestamps, counts, etc.)
_META_DDL = """
CREATE TABLE IF NOT EXISTS doc_index_meta (
    corpus_id  text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    key        text NOT NULL,
    value      text NOT NULL,
    updated_at timestamptz DEFAULT now(),
    PRIMARY KEY (corpus_id, key)
)
"""

_DOCUMENTS_DDL = """
CREATE TABLE IF NOT EXISTS doc_documents (
    id serial PRIMARY KEY,
    corpus_id text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    doc_path text NOT NULL,
    title text NOT NULL,
    source_url text NOT NULL DEFAULT '',
    source_file text NOT NULL DEFAULT '',
    parent_id int REFERENCES doc_documents(id) ON DELETE SET NULL,
    depth smallint NOT NULL DEFAULT 0,
    sort_order int NOT NULL DEFAULT 0,
    is_group boolean NOT NULL DEFAULT false,
    total_chars int NOT NULL DEFAULT 0,
    section_count int NOT NULL DEFAULT 0,
    UNIQUE (corpus_id, doc_path)
)
"""

_DOCUMENTS_INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS doc_documents_corpus_id_idx
    ON doc_documents (corpus_id);

CREATE INDEX IF NOT EXISTS doc_documents_parent_id_idx
    ON doc_documents (parent_id);

CREATE INDEX IF NOT EXISTS doc_documents_corpus_sort_order_idx
    ON doc_documents (corpus_id, sort_order);

CREATE INDEX IF NOT EXISTS doc_documents_corpus_path_idx
    ON doc_documents (corpus_id, doc_path text_pattern_ops);
"""

_CHUNKS_DOCUMENT_ID_DDL = """
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS document_id int REFERENCES doc_documents(id) ON DELETE SET NULL
"""

_CHUNKS_DOCUMENT_ID_INDEX = """
CREATE INDEX IF NOT EXISTS doc_chunks_document_id_idx ON doc_chunks (document_id)
"""

_LEGACY_CORPORA_PARSER_DDL = """
ALTER TABLE doc_corpora ADD COLUMN IF NOT EXISTS parser text NOT NULL DEFAULT 'markdown'
"""

_LEGACY_CORPORA_EMBEDDER_DDL = """
ALTER TABLE doc_corpora ADD COLUMN IF NOT EXISTS embedder text NOT NULL DEFAULT 'gemini'
"""

# Indexes — all idempotent via IF NOT EXISTS.
# Notes:
# - GIN indexes do NOT support composite (corpus_id, tsv) keys. Use a GIN on
#   tsv alone plus a separate B-tree on corpus_id for corpus-scoped FTS.
# - LIKE-prefix indexes use text_pattern_ops for locale-independent scans.
_INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS doc_chunks_corpus_id_idx
    ON doc_chunks (corpus_id);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_tsv_idx
    ON doc_chunks USING gin (tsv);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_category_idx
    ON doc_chunks (corpus_id, category);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_hash_idx
    ON doc_chunks (corpus_id, content_hash);

CREATE INDEX IF NOT EXISTS doc_chunks_source_url_idx
    ON doc_chunks (source_url text_pattern_ops);

CREATE INDEX IF NOT EXISTS doc_chunks_section_path_idx
    ON doc_chunks (section_path text_pattern_ops);

CREATE INDEX IF NOT EXISTS doc_chunks_heading_level_idx
    ON doc_chunks (heading_level);
"""


def get_vector_dim() -> int:
    """Return the configured vector dimension for this deployment.

    Reads DOC_HUB_VECTOR_DIM env var (default: 768).
    Used by the embed pipeline to validate embedder compatibility.

    Raises:
        ValueError: If DOC_HUB_VECTOR_DIM is set but not a positive integer.
    """
    raw = os.getenv("DOC_HUB_VECTOR_DIM", "768")
    try:
        dim = int(raw)
    except ValueError:
        raise ValueError(
            f"DOC_HUB_VECTOR_DIM must be a positive integer, got {raw!r}"
        ) from None
    if dim <= 0:
        raise ValueError(
            f"DOC_HUB_VECTOR_DIM must be a positive integer, got {dim}"
        )
    return dim


def _chunks_ddl() -> str:
    """Generate doc_chunks DDL with the configured vector dimension."""
    dim = get_vector_dim()
    return f"""
CREATE TABLE IF NOT EXISTS doc_chunks (
    id           serial PRIMARY KEY,
    corpus_id    text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    content_hash text NOT NULL,
    heading      text NOT NULL,
    content      text NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (
                     setweight(to_tsvector('english', heading), 'A') ||
                     setweight(to_tsvector('english', content), 'B')
                 ) STORED,
    embedding    vector({dim}) NOT NULL,
    source_file  text NOT NULL,
    source_url   text NOT NULL,
    section_path text NOT NULL,
    heading_level smallint NOT NULL,
    start_line   int NOT NULL DEFAULT 0,
    end_line     int NOT NULL DEFAULT 0,
    char_count   int NOT NULL,
    category     text NOT NULL,
    UNIQUE (corpus_id, content_hash)
)
"""


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

def _build_dsn(dsn: str | None = None) -> str:
    """Return DSN from argument or environment variables.

    Resolution order:
    1. Explicit dsn argument
    2. DOC_HUB_DATABASE_URL env var (full connection string)
    3. Individual PG* env vars with safe defaults

    Env vars:
      DOC_HUB_DATABASE_URL — full PostgreSQL connection string
      PGHOST     (default: localhost)
      PGPORT     (default: 5432)  # standard PostgreSQL port
      PGDATABASE (default: doc_hub)
      PGUSER     (default: postgres)
      PGPASSWORD (NO default — must be set explicitly)
    """
    if dsn:
        return dsn

    env_url = os.getenv("DOC_HUB_DATABASE_URL")
    if env_url:
        return env_url

    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    dbname = os.getenv("PGDATABASE", "doc_hub")
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD")
    if not password:
        raise RuntimeError(
            "PGPASSWORD environment variable not set. "
            "Set it directly or use DOC_HUB_DATABASE_URL for the full connection string. "
            "Note: PGPASSWORD no longer has a default value (previously defaulted to 'pydantic-docs')."
        )
    # URL-encode user and password to handle special characters (@, /, %, etc.)
    from urllib.parse import quote_plus
    return f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{dbname}"


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Per-connection init callback: register JSONB codec.

    asyncpg does NOT auto-serialize Python dicts to/from JSONB. The codec
    must be registered on each individual connection (not the pool object).
    Using this as the `init` callback in create_pool ensures every new
    connection in the pool gets the codec.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def create_pool(dsn: str | None = None) -> asyncpg.Pool:
    """Create and return an asyncpg connection pool.

    Pool config: min_size=1, max_size=10 (matching pydantic-ai-docs defaults).

    Registers a JSONB codec via the `init` callback so that Python dicts
    round-trip through JSONB columns transparently on every connection.
    Without this, asyncpg raises TypeError or returns raw JSON strings.
    """
    resolved_dsn = _build_dsn(dsn)

    pool = await asyncpg.create_pool(
        resolved_dsn,
        min_size=1,
        max_size=10,
        init=_init_connection,
    )

    log.debug("asyncpg pool created (dsn=%s)", resolved_dsn)
    return pool


async def _migrate_legacy_corpora_schema(conn: asyncpg.Connection) -> None:
    parser_exists = await conn.fetchval(
        """
        SELECT 1
        FROM pg_attribute
        WHERE attrelid = 'doc_corpora'::regclass
          AND attname = 'parser'
          AND NOT attisdropped
        """
    )
    if not parser_exists:
        await conn.execute(_LEGACY_CORPORA_PARSER_DDL)

    embedder_exists = await conn.fetchval(
        """
        SELECT 1
        FROM pg_attribute
        WHERE attrelid = 'doc_corpora'::regclass
          AND attname = 'embedder'
          AND NOT attisdropped
        """
    )
    if not embedder_exists:
        await conn.execute(_LEGACY_CORPORA_EMBEDDER_DDL)

    check_name = await conn.fetchval(
        """
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'doc_corpora'::regclass
          AND contype = 'c'
          AND conname = 'doc_corpora_fetch_strategy_check'
        """
    )
    if check_name:
        await conn.execute(f"ALTER TABLE doc_corpora DROP CONSTRAINT IF EXISTS {check_name}")


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

async def ensure_schema(pool: asyncpg.Pool) -> None:
    """Create all doc-hub tables and indexes if they don't exist.

    Uses the VectorChord extension (vchord).
    This function is idempotent — safe to call on every startup.

    IMPORTANT: After creating/verifying the doc_chunks table, this
    function checks that the existing vector column dimension matches
    DOC_HUB_VECTOR_DIM. If they differ, it raises RuntimeError with
    instructions — because CREATE TABLE IF NOT EXISTS silently
    preserves the old schema, the mismatch would otherwise cause
    cryptic INSERT failures later.
    """
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vchord CASCADE")
        log.debug("VectorChord extension ensured.")

        await conn.execute(_CORPORA_DDL)
        log.debug("doc_corpora table ensured.")
        await _migrate_legacy_corpora_schema(conn)
        log.debug("legacy doc_corpora schema migrated if needed.")

        await conn.execute(_chunks_ddl())
        log.debug("doc_chunks table ensured.")

        # Validate existing vector dimension matches configured dimension.
        # CREATE TABLE IF NOT EXISTS preserves the old schema, so if
        # DOC_HUB_VECTOR_DIM changed after initial creation, the table
        # still has the old dimension. Detect this and fail fast.
        existing_dim = await conn.fetchval(
            """
            SELECT atttypmod
            FROM pg_attribute
            WHERE attrelid = 'doc_chunks'::regclass
              AND attname = 'embedding'
              AND NOT attisdropped
            """
        )
        # atttypmod for vector(N) is N. If the column doesn't exist
        # or has no typmod, existing_dim will be None or -1.
        configured_dim = get_vector_dim()
        if existing_dim is not None and existing_dim > 0 and existing_dim != configured_dim:
            raise RuntimeError(
                f"Existing doc_chunks table has vector({existing_dim}) but "
                f"DOC_HUB_VECTOR_DIM={configured_dim}. To fix this, either:\n"
                f"  1. Set DOC_HUB_VECTOR_DIM={existing_dim} to match the existing table, or\n"
                f"  2. DROP TABLE doc_chunks and let doc-hub recreate it with the new dimension.\n"
                f"     (This will delete all indexed data — re-index all corpora after.)"
            )

        await conn.execute(_META_DDL)
        log.debug("doc_index_meta table ensured.")

        await conn.execute(_DOCUMENTS_DDL)
        log.debug("doc_documents table ensured.")

        await conn.execute(_CHUNKS_DOCUMENT_ID_DDL)
        log.debug("doc_chunks.document_id column ensured.")

        for stmt in _INDEXES_DDL.strip().split("\n\n"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(stmt)

        for stmt in _DOCUMENTS_INDEXES_DDL.strip().split("\n\n"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(stmt)
        log.debug("doc_documents indexes ensured.")

        await conn.execute(_CHUNKS_DOCUMENT_ID_INDEX)
        log.debug("doc_chunks.document_id index ensured.")

    log.info("doc-hub schema verified / created.")


# ---------------------------------------------------------------------------
# Corpus CRUD helpers
# ---------------------------------------------------------------------------
# NOTE: asyncpg does NOT auto-serialize Python dicts to/from JSONB.
# The pool registers a custom JSONB codec via _init_connection so that Python
# dicts round-trip transparently. If the pool was created WITHOUT that codec,
# you must pass json.dumps(corpus.fetch_config) manually.
#
# upsert_corpus() below passes json.dumps() explicitly as a belt-and-suspenders
# safety measure — it is idempotent even if the codec is registered.
# ---------------------------------------------------------------------------


async def get_corpus(pool: asyncpg.Pool, slug: str):
    """Fetch a single corpus by slug.

    Returns:
        A :class:`~doc_hub.models.Corpus` if found, or ``None``.
    """
    from doc_hub.models import Corpus  # local import to avoid circular at module level

    row = await pool.fetchrow("SELECT * FROM doc_corpora WHERE slug = $1", slug)
    return Corpus.from_row(row) if row else None


async def list_corpora(pool: asyncpg.Pool, enabled_only: bool = True):
    """Return all registered corpora.

    Args:
        enabled_only: When ``True`` (default), only return rows where
                      ``enabled = true``.

    Returns:
        List of :class:`~doc_hub.models.Corpus` instances.
    """
    from doc_hub.models import Corpus  # local import

    query = "SELECT * FROM doc_corpora"
    if enabled_only:
        query += " WHERE enabled = true"
    rows = await pool.fetch(query)
    return [Corpus.from_row(r) for r in rows]


async def upsert_corpus(pool: asyncpg.Pool, corpus) -> None:
    """Insert or update a corpus row.

    Uses ``ON CONFLICT (slug) DO UPDATE`` to handle re-registration without
    errors.  The ``last_indexed_at`` and ``total_chunks`` columns are NOT
    updated here — use :func:`update_corpus_stats` for those.

    Args:
        pool:   asyncpg connection pool.
        corpus: A :class:`~doc_hub.models.Corpus` instance to persist.
    """
    await pool.execute(
        """
        INSERT INTO doc_corpora (slug, name, fetch_strategy, fetch_config,
                                 parser, embedder, enabled)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        ON CONFLICT (slug) DO UPDATE SET
            name            = EXCLUDED.name,
            fetch_strategy  = EXCLUDED.fetch_strategy,
            fetch_config    = EXCLUDED.fetch_config,
            parser          = EXCLUDED.parser,
            embedder        = EXCLUDED.embedder,
            enabled         = EXCLUDED.enabled
        """,
        corpus.slug,
        corpus.name,
        corpus.fetch_strategy,
        json.dumps(corpus.fetch_config),  # explicit json.dumps for safety
        corpus.parser,
        corpus.embedder,
        corpus.enabled,
    )
    log.debug("Upserted corpus: %s", corpus.slug)


async def update_corpus_fetch_config(
    pool: asyncpg.Pool, slug: str, fetch_config: dict,
) -> None:
    """Update just the fetch_config JSONB column for a corpus.

    Used by ``pipeline clean`` to persist ``clean: true`` so that future
    fetches automatically apply the LLM cleaning step.

    Args:
        pool:         asyncpg connection pool.
        slug:         Corpus slug (primary key).
        fetch_config: New fetch_config dict to store.
    """
    await pool.execute(
        "UPDATE doc_corpora SET fetch_config = $1::jsonb WHERE slug = $2",
        json.dumps(fetch_config),
        slug,
    )
    log.debug("Updated fetch_config for corpus: %s", slug)


async def update_corpus_stats(pool: asyncpg.Pool, slug: str, total_chunks: int) -> None:
    """Update post-indexing stats for a corpus.

    Sets ``last_indexed_at = now()`` and ``total_chunks = total_chunks``.

    Args:
        pool:         asyncpg connection pool.
        slug:         Corpus slug (primary key).
        total_chunks: Number of chunks produced by the last index run.
    """
    await pool.execute(
        """
        UPDATE doc_corpora
        SET last_indexed_at = now(),
            total_chunks    = $2
        WHERE slug = $1
        """,
        slug,
        total_chunks,
    )
    log.debug("Updated stats for corpus %s: total_chunks=%d", slug, total_chunks)
