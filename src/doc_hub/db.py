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
#   - The unique constraint is (corpus_id, snapshot_id, content_hash).
# NOTE: _CHUNKS_DDL constant is replaced by _chunks_ddl() function below.

_VERSIONS_DDL = """
CREATE TABLE IF NOT EXISTS doc_versions (
    corpus_id         text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    snapshot_id       text NOT NULL,
    source_version    text NOT NULL,
    resolved_version  text,
    source_type       text NOT NULL,
    source_url        text NOT NULL,
    fetch_strategy    text NOT NULL,
    fetch_config_hash text NOT NULL,
    url_set_hash      text,
    content_hash      text NOT NULL,
    fetched_at        timestamptz NOT NULL,
    indexed_at        timestamptz,
    total_chunks      int DEFAULT 0,
    enabled           boolean DEFAULT true,
    metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (corpus_id, snapshot_id)
)
"""

_VERSION_ALIASES_DDL = """
CREATE TABLE IF NOT EXISTS doc_version_aliases (
    corpus_id   text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    alias       text NOT NULL,
    snapshot_id text NOT NULL,
    updated_at  timestamptz DEFAULT now(),
    PRIMARY KEY (corpus_id, alias),
    FOREIGN KEY (corpus_id, snapshot_id) REFERENCES doc_versions(corpus_id, snapshot_id) ON DELETE CASCADE
)
"""

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
    snapshot_id text NOT NULL DEFAULT 'legacy',
    source_version text NOT NULL DEFAULT 'latest',
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
    UNIQUE (corpus_id, snapshot_id, doc_path)
)
"""

_DOCUMENTS_INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS doc_documents_corpus_id_idx
    ON doc_documents (corpus_id);

CREATE INDEX IF NOT EXISTS doc_documents_parent_id_idx
    ON doc_documents (parent_id);

CREATE INDEX IF NOT EXISTS doc_documents_corpus_sort_order_idx
    ON doc_documents (corpus_id, snapshot_id, sort_order);

CREATE INDEX IF NOT EXISTS doc_documents_corpus_path_idx
    ON doc_documents (corpus_id, snapshot_id, doc_path text_pattern_ops);
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

_LEGACY_CHUNKS_VERSION_COLUMNS_DDL = """
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS snapshot_id text NOT NULL DEFAULT 'legacy';
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS source_version text NOT NULL DEFAULT 'latest';
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS fetched_at timestamptz
"""

_LEGACY_DOCUMENTS_VERSION_COLUMNS_DDL = """
ALTER TABLE doc_documents ADD COLUMN IF NOT EXISTS snapshot_id text NOT NULL DEFAULT 'legacy';
ALTER TABLE doc_documents ADD COLUMN IF NOT EXISTS source_version text NOT NULL DEFAULT 'latest'
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
    ON doc_chunks (corpus_id, snapshot_id, content_hash);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_snapshot_idx
    ON doc_chunks (corpus_id, snapshot_id);

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
    snapshot_id text NOT NULL DEFAULT 'legacy',
    source_version text NOT NULL DEFAULT 'latest',
    fetched_at timestamptz,
    section_path text NOT NULL,
    heading_level smallint NOT NULL,
    start_line   int NOT NULL DEFAULT 0,
    end_line     int NOT NULL DEFAULT 0,
    char_count   int NOT NULL,
    category     text NOT NULL,
    UNIQUE (corpus_id, snapshot_id, content_hash)
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


async def _execute_statements(conn: asyncpg.Connection, statements: str) -> None:
    for stmt in statements.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            await conn.execute(stmt)


async def _migrate_legacy_chunks_unique_constraint(conn: asyncpg.Connection) -> None:
    constraint_name = await conn.fetchval(
        """
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE t.relname = 'doc_chunks'
          AND n.nspname = current_schema()
          AND c.contype = 'u'
          AND pg_get_constraintdef(c.oid) = 'UNIQUE (corpus_id, content_hash)'
        LIMIT 1
        """
    )
    if constraint_name:
        await conn.execute(f'ALTER TABLE doc_chunks DROP CONSTRAINT "{constraint_name}"')

    has_version_constraint = await conn.fetchval(
        """
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE t.relname = 'doc_chunks'
          AND n.nspname = current_schema()
          AND c.contype = 'u'
          AND pg_get_constraintdef(c.oid) = 'UNIQUE (corpus_id, snapshot_id, content_hash)'
        LIMIT 1
        """
    )
    if not has_version_constraint:
        await conn.execute(
            """
            ALTER TABLE doc_chunks
            ADD CONSTRAINT doc_chunks_corpus_snapshot_content_hash_key
            UNIQUE (corpus_id, snapshot_id, content_hash)
            """
        )


async def _migrate_legacy_documents_unique_constraint(conn: asyncpg.Connection) -> None:
    constraint_name = await conn.fetchval(
        """
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE t.relname = 'doc_documents'
          AND n.nspname = current_schema()
          AND c.contype = 'u'
          AND pg_get_constraintdef(c.oid) = 'UNIQUE (corpus_id, doc_path)'
        LIMIT 1
        """
    )
    if constraint_name:
        await conn.execute(f'ALTER TABLE doc_documents DROP CONSTRAINT "{constraint_name}"')

    has_version_constraint = await conn.fetchval(
        """
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE t.relname = 'doc_documents'
          AND n.nspname = current_schema()
          AND c.contype = 'u'
          AND pg_get_constraintdef(c.oid) = 'UNIQUE (corpus_id, snapshot_id, doc_path)'
        LIMIT 1
        """
    )
    if not has_version_constraint:
        await conn.execute(
            """
            ALTER TABLE doc_documents
            ADD CONSTRAINT doc_documents_corpus_snapshot_doc_path_key
            UNIQUE (corpus_id, snapshot_id, doc_path)
            """
        )


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

        await conn.execute(_VERSIONS_DDL)
        log.debug("doc_versions table ensured.")

        await conn.execute(_VERSION_ALIASES_DDL)
        log.debug("doc_version_aliases table ensured.")

        await conn.execute(_chunks_ddl())
        log.debug("doc_chunks table ensured.")
        await _execute_statements(conn, _LEGACY_CHUNKS_VERSION_COLUMNS_DDL)
        log.debug("legacy doc_chunks version columns migrated if needed.")
        await _migrate_legacy_chunks_unique_constraint(conn)
        log.debug("legacy doc_chunks unique constraint migrated if needed.")

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
        await _execute_statements(conn, _LEGACY_DOCUMENTS_VERSION_COLUMNS_DDL)
        log.debug("legacy doc_documents version columns migrated if needed.")
        await _migrate_legacy_documents_unique_constraint(conn)
        log.debug("legacy doc_documents unique constraint migrated if needed.")

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


async def delete_corpus(pool: asyncpg.Pool, slug: str) -> bool:
    """Delete a corpus and all its associated data from the database.

    Child rows in doc_chunks, doc_index_meta, and doc_documents are removed
    automatically via ON DELETE CASCADE.

    Args:
        pool: asyncpg connection pool.
        slug: Corpus slug to delete.

    Returns:
        True if a row was deleted, False if the slug was not found.
    """
    result = await pool.execute("DELETE FROM doc_corpora WHERE slug = $1", slug)
    return result == "DELETE 1"


async def upsert_doc_version(pool: asyncpg.Pool, version) -> None:
    await pool.execute(
        """
        INSERT INTO doc_versions (
            corpus_id, snapshot_id, source_version, resolved_version, source_type,
            source_url, fetch_strategy, fetch_config_hash, url_set_hash, content_hash,
            fetched_at, indexed_at, total_chunks, enabled, metadata
        ) VALUES (
            $1, $2, $3, $4, $5,
            $6, $7, $8, $9, $10,
            $11::timestamptz, $12::timestamptz, $13, $14, $15::jsonb
        )
        ON CONFLICT (corpus_id, snapshot_id) DO UPDATE SET
            source_version = EXCLUDED.source_version,
            resolved_version = EXCLUDED.resolved_version,
            source_type = EXCLUDED.source_type,
            source_url = EXCLUDED.source_url,
            fetch_strategy = EXCLUDED.fetch_strategy,
            fetch_config_hash = EXCLUDED.fetch_config_hash,
            url_set_hash = EXCLUDED.url_set_hash,
            content_hash = EXCLUDED.content_hash,
            fetched_at = EXCLUDED.fetched_at,
            indexed_at = EXCLUDED.indexed_at,
            total_chunks = EXCLUDED.total_chunks,
            enabled = EXCLUDED.enabled,
            metadata = EXCLUDED.metadata
        """,
        version.corpus_id,
        version.snapshot_id,
        version.source_version,
        version.resolved_version,
        version.source_type,
        version.source_url,
        version.fetch_strategy,
        version.fetch_config_hash,
        version.url_set_hash,
        version.content_hash,
        version.fetched_at,
        version.indexed_at,
        version.total_chunks,
        version.enabled,
        json.dumps(version.metadata),
    )


async def list_doc_versions(pool: asyncpg.Pool, corpus_id: str, *, enabled_only: bool = True):
    query = """
        SELECT v.*, a.aliases
        FROM doc_versions v
        LEFT JOIN (
            SELECT corpus_id, snapshot_id, array_agg(alias ORDER BY alias) AS aliases
            FROM doc_version_aliases
            GROUP BY corpus_id, snapshot_id
        ) a ON a.corpus_id = v.corpus_id AND a.snapshot_id = v.snapshot_id
        WHERE v.corpus_id = $1
    """
    if enabled_only:
        query += " AND v.enabled = true"
    query += " ORDER BY v.fetched_at DESC, v.source_version"
    rows = await pool.fetch(query, corpus_id)
    return rows


async def get_doc_version(pool: asyncpg.Pool, corpus_id: str, selector: str):
    resolved = await resolve_version_selector(pool, corpus_id, selector)
    if resolved is None:
        return None
    return await pool.fetchrow(
        "SELECT * FROM doc_versions WHERE corpus_id = $1 AND snapshot_id = $2",
        corpus_id,
        resolved,
    )


async def upsert_version_alias(pool: asyncpg.Pool, corpus_id: str, alias: str, snapshot_id: str) -> None:
    await pool.execute(
        """
        INSERT INTO doc_version_aliases (corpus_id, alias, snapshot_id, updated_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (corpus_id, alias) DO UPDATE SET
            snapshot_id = EXCLUDED.snapshot_id,
            updated_at = now()
        """,
        corpus_id,
        alias,
        snapshot_id,
    )


async def get_default_snapshot_id(pool: asyncpg.Pool, corpus_id: str) -> str:
    latest = await resolve_version_selector(pool, corpus_id, "latest")
    if latest:
        return latest
    newest = await pool.fetchval(
        """
        SELECT snapshot_id
        FROM doc_versions
        WHERE corpus_id = $1 AND enabled = true
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        corpus_id,
    )
    return str(newest) if newest else "legacy"


async def resolve_version_selector(pool: asyncpg.Pool, corpus_id: str, selector: str) -> str | None:
    alias_snapshot = await pool.fetchval(
        "SELECT snapshot_id FROM doc_version_aliases WHERE corpus_id = $1 AND alias = $2",
        corpus_id,
        selector,
    )
    if alias_snapshot:
        return str(alias_snapshot)

    snapshot = await pool.fetchval(
        "SELECT snapshot_id FROM doc_versions WHERE corpus_id = $1 AND snapshot_id = $2",
        corpus_id,
        selector,
    )
    if snapshot:
        return str(snapshot)

    source_snapshot = await pool.fetchval(
        """
        SELECT snapshot_id
        FROM doc_versions
        WHERE corpus_id = $1 AND source_version = $2 AND enabled = true
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        corpus_id,
        selector,
    )
    return str(source_snapshot) if source_snapshot else None


async def update_version_stats(
    pool: asyncpg.Pool,
    corpus_id: str,
    snapshot_id: str,
    total_chunks: int,
) -> None:
    await pool.execute(
        """
        UPDATE doc_versions
        SET indexed_at = now(),
            total_chunks = $3
        WHERE corpus_id = $1 AND snapshot_id = $2
        """,
        corpus_id,
        snapshot_id,
        total_chunks,
    )


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
