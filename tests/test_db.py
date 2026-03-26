"""Tests for doc_hub.db — schema creation, migration, and pool behavior.

Integration tests (marked with @pytest.mark.integration) require a live
PostgreSQL instance. Unit tests do not require a DB.

Run integration tests with:
    pytest tests/test_db.py -m integration
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


def test_package_imports():
    """Verify that the doc_hub package can be imported."""
    import doc_hub  # noqa: F401

    assert doc_hub.__version__ == "0.1.0"


def test_db_module_imports():
    """Verify that doc_hub.db exports the required symbols."""
    from doc_hub import db

    assert hasattr(db, "create_pool")
    assert hasattr(db, "ensure_schema")
    assert hasattr(db, "get_vector_dim")
    assert hasattr(db, "_chunks_ddl")


def test_migrate_from_legacy_not_importable():
    """Verify migrate_from_legacy has been removed from db.py."""
    import doc_hub.db

    assert not hasattr(doc_hub.db, "migrate_from_legacy"), (
        "migrate_from_legacy still exists in db.py"
    )


def test_models_module_imports():
    """Verify that doc_hub.models exports Corpus (FetchStrategy removed in M5)."""
    from doc_hub.models import Corpus
    import doc_hub.models

    assert hasattr(doc_hub.models, "Corpus")
    assert not hasattr(doc_hub.models, "FetchStrategy"), "FetchStrategy removed in M5"


def test_corpus_from_row():
    """Verify Corpus.from_row correctly constructs a Corpus instance."""
    from doc_hub.models import Corpus

    row = {
        "slug": "pydantic-ai",
        "name": "Pydantic AI Docs",
        "fetch_strategy": "llms_txt",
        "fetch_config": {"url": "https://ai.pydantic.dev/llms.txt"},
        "embedding_model": "gemini-embedding-001",
        "parser": "markdown",
        "embedder": "gemini",
        "enabled": True,
        "last_indexed_at": None,
        "total_chunks": 0,
    }
    corpus = Corpus.from_row(row)
    assert corpus.slug == "pydantic-ai"
    assert corpus.fetch_strategy == "llms_txt"
    assert isinstance(corpus.fetch_strategy, str)
    assert corpus.fetch_config == {"url": "https://ai.pydantic.dev/llms.txt"}
    assert not hasattr(corpus, "embedding_model")  # removed in M7
    assert corpus.parser == "markdown"
    assert corpus.embedder == "gemini"


def test_corpus_from_row_missing_embedding_model():
    """Verify from_row works when embedding_model column is absent (removed in M7)."""
    from doc_hub.models import Corpus

    # Row without embedding_model (column removed in M7)
    row = {
        "slug": "test",
        "name": "Test",
        "fetch_strategy": "llms_txt",
        "fetch_config": {},
        "parser": "markdown",
        "embedder": "gemini",
        "enabled": True,
        "last_indexed_at": None,
        "total_chunks": 0,
    }
    corpus = Corpus.from_row(row)
    assert not hasattr(corpus, "embedding_model")  # removed in M7


def test_corpus_from_row_new_columns():
    """Verify from_row correctly reads parser and embedder columns."""
    from doc_hub.models import Corpus

    row = {
        "slug": "test",
        "name": "Test",
        "fetch_strategy": "git_repo",
        "fetch_config": {},
        "parser": "rst",
        "embedder": "openai",
        "enabled": True,
        "last_indexed_at": None,
        "total_chunks": 5,
    }
    corpus = Corpus.from_row(row)
    assert corpus.parser == "rst"
    assert corpus.embedder == "openai"


def test_build_dsn_uses_arg():
    """Verify _build_dsn returns the provided DSN unchanged."""
    from doc_hub.db import _build_dsn

    custom_dsn = "postgresql://myuser:mypass@myhost:5432/mydb"
    assert _build_dsn(custom_dsn) == custom_dsn


def test_build_dsn_uses_doc_hub_database_url(monkeypatch):
    """Verify DOC_HUB_DATABASE_URL env var is respected by _build_dsn."""
    monkeypatch.setenv("DOC_HUB_DATABASE_URL", "postgresql://user:pass@host:5432/mydb")
    monkeypatch.delenv("PGPASSWORD", raising=False)

    from doc_hub.db import _build_dsn

    assert _build_dsn() == "postgresql://user:pass@host:5432/mydb"


def test_build_dsn_doc_hub_database_url_takes_precedence(monkeypatch):
    """Verify DOC_HUB_DATABASE_URL takes precedence over PG* env vars."""
    monkeypatch.setenv("DOC_HUB_DATABASE_URL", "postgresql://override:override@override:9999/override")
    monkeypatch.setenv("PGPASSWORD", "somepass")
    monkeypatch.setenv("PGHOST", "other")

    from doc_hub.db import _build_dsn

    result = _build_dsn()
    assert result == "postgresql://override:override@override:9999/override"


def test_build_dsn_default_port_is_5432(monkeypatch):
    """Verify default port is 5432."""
    monkeypatch.delenv("DOC_HUB_DATABASE_URL", raising=False)
    monkeypatch.delenv("PGPORT", raising=False)
    monkeypatch.setenv("PGPASSWORD", "test")

    from doc_hub.db import _build_dsn

    dsn = _build_dsn()
    assert ":5432/" in dsn, f"Port not 5432: {dsn}"


def test_build_dsn_default_database_is_doc_hub(monkeypatch):
    """Verify default database name is doc_hub."""
    monkeypatch.delenv("DOC_HUB_DATABASE_URL", raising=False)
    monkeypatch.delenv("PGDATABASE", raising=False)
    monkeypatch.setenv("PGPASSWORD", "test")

    from doc_hub.db import _build_dsn

    dsn = _build_dsn()
    assert dsn.endswith("/doc_hub"), f"Database not doc_hub: {dsn}"


def test_build_dsn_requires_pgpassword(monkeypatch):
    """Verify _build_dsn raises RuntimeError when PGPASSWORD is not set."""
    monkeypatch.delenv("DOC_HUB_DATABASE_URL", raising=False)
    monkeypatch.delenv("PGPASSWORD", raising=False)

    from doc_hub.db import _build_dsn

    with pytest.raises(RuntimeError, match="PGPASSWORD"):
        _build_dsn()


def test_build_dsn_url_encodes_password(monkeypatch):
    """Verify _build_dsn URL-encodes passwords with special characters."""
    from urllib.parse import quote_plus

    monkeypatch.delenv("DOC_HUB_DATABASE_URL", raising=False)
    monkeypatch.delenv("PGPORT", raising=False)
    monkeypatch.setenv("PGPASSWORD", "p@ss/w%rd")

    from doc_hub.db import _build_dsn

    dsn = _build_dsn()
    encoded_pw = quote_plus("p@ss/w%rd")  # 'p%40ss%2Fw%25rd'
    assert encoded_pw in dsn, f"Password not URL-encoded in DSN: {dsn}"


def test_no_check_constraint_on_fetch_strategy():
    """Verify fetch_strategy has no CHECK constraint in _CORPORA_DDL."""
    from doc_hub.db import _CORPORA_DDL

    assert "CHECK" not in _CORPORA_DDL, "CHECK constraint still present in _CORPORA_DDL"


def test_ddl_has_parser_and_embedder_columns():
    """Verify parser and embedder columns exist in _CORPORA_DDL."""
    from doc_hub.db import _CORPORA_DDL

    assert "parser          text NOT NULL" in _CORPORA_DDL, "parser column missing from DDL"
    assert "embedder        text NOT NULL" in _CORPORA_DDL, "embedder column missing from DDL"
    assert "embedding_model" not in _CORPORA_DDL, (
        "embedding_model column should be removed from DDL"
    )


def test_get_vector_dim_default(monkeypatch):
    """Verify get_vector_dim returns 768 by default."""
    monkeypatch.delenv("DOC_HUB_VECTOR_DIM", raising=False)

    from doc_hub.db import get_vector_dim

    assert get_vector_dim() == 768


def test_get_vector_dim_from_env(monkeypatch):
    """Verify get_vector_dim respects DOC_HUB_VECTOR_DIM env var."""
    monkeypatch.setenv("DOC_HUB_VECTOR_DIM", "1536")

    from doc_hub.db import get_vector_dim

    assert get_vector_dim() == 1536


def test_get_vector_dim_invalid_string(monkeypatch):
    """Verify get_vector_dim raises ValueError for non-integer DOC_HUB_VECTOR_DIM."""
    monkeypatch.setenv("DOC_HUB_VECTOR_DIM", "abc")

    from doc_hub.db import get_vector_dim

    with pytest.raises(ValueError, match="positive integer"):
        get_vector_dim()


def test_get_vector_dim_invalid_negative(monkeypatch):
    """Verify get_vector_dim raises ValueError for negative DOC_HUB_VECTOR_DIM."""
    monkeypatch.setenv("DOC_HUB_VECTOR_DIM", "-1")

    from doc_hub.db import get_vector_dim

    with pytest.raises(ValueError, match="positive integer"):
        get_vector_dim()


def test_get_vector_dim_invalid_zero(monkeypatch):
    """Verify get_vector_dim raises ValueError for zero DOC_HUB_VECTOR_DIM."""
    monkeypatch.setenv("DOC_HUB_VECTOR_DIM", "0")

    from doc_hub.db import get_vector_dim

    with pytest.raises(ValueError, match="positive integer"):
        get_vector_dim()


def test_chunks_ddl_uses_configured_dim(monkeypatch):
    """Verify _chunks_ddl() uses the configured vector dimension."""
    monkeypatch.setenv("DOC_HUB_VECTOR_DIM", "1536")

    from doc_hub.db import _chunks_ddl

    ddl = _chunks_ddl()
    assert "vector(1536)" in ddl, f"Expected vector(1536) in DDL: {ddl}"


def test_chunks_ddl_default_dim(monkeypatch):
    """Verify _chunks_ddl() defaults to vector(768)."""
    monkeypatch.delenv("DOC_HUB_VECTOR_DIM", raising=False)

    from doc_hub.db import _chunks_ddl

    ddl = _chunks_ddl()
    assert "vector(768)" in ddl, f"Expected vector(768) in DDL: {ddl}"


def test_ddl_contains_weighted_tsvector(monkeypatch):
    """Verify the chunks DDL uses setweight for tsvector generation."""
    monkeypatch.delenv("DOC_HUB_VECTOR_DIM", raising=False)

    from doc_hub.db import _chunks_ddl

    ddl = _chunks_ddl()
    assert "setweight(to_tsvector('english', heading), 'A')" in ddl
    assert "setweight(to_tsvector('english', content), 'B')" in ddl


def test_ddl_corpus_id_fk(monkeypatch):
    """Verify the chunks DDL references doc_corpora(slug)."""
    monkeypatch.delenv("DOC_HUB_VECTOR_DIM", raising=False)

    from doc_hub.db import _chunks_ddl

    assert "REFERENCES doc_corpora(slug)" in _chunks_ddl()


def test_ddl_unique_constraint(monkeypatch):
    """Verify the chunks DDL has UNIQUE(corpus_id, content_hash)."""
    monkeypatch.delenv("DOC_HUB_VECTOR_DIM", raising=False)

    from doc_hub.db import _chunks_ddl

    assert "UNIQUE (corpus_id, content_hash)" in _chunks_ddl()


def test_ddl_heading_before_tsv(monkeypatch):
    """Verify heading and content are defined before tsv in CREATE TABLE DDL."""
    monkeypatch.delenv("DOC_HUB_VECTOR_DIM", raising=False)

    from doc_hub.db import _chunks_ddl

    ddl = _chunks_ddl()
    heading_pos = ddl.find("heading")
    content_pos = ddl.find("content")
    tsv_pos = ddl.find("tsv")

    assert heading_pos < tsv_pos, "heading column must be defined before tsv"
    assert content_pos < tsv_pos, "content column must be defined before tsv"


def test_ddl_meta_composite_pk():
    """Verify doc_index_meta DDL has composite PK (corpus_id, key)."""
    from doc_hub.db import _META_DDL

    assert "PRIMARY KEY (corpus_id, key)" in _META_DDL


def test_ddl_meta_corpus_id_fk():
    """Verify doc_index_meta DDL has FK to doc_corpora."""
    from doc_hub.db import _META_DDL

    assert "REFERENCES doc_corpora(slug)" in _META_DDL


def test_ddl_on_delete_cascade(monkeypatch):
    """Verify FK constraints use ON DELETE CASCADE."""
    monkeypatch.delenv("DOC_HUB_VECTOR_DIM", raising=False)

    from doc_hub.db import _META_DDL, _chunks_ddl

    assert "ON DELETE CASCADE" in _chunks_ddl()
    assert "ON DELETE CASCADE" in _META_DDL


@pytest.mark.asyncio
async def test_ensure_schema_raises_on_dim_mismatch(monkeypatch):
    """Verify ensure_schema raises RuntimeError if existing vector dim differs from config."""
    monkeypatch.setenv("DOC_HUB_VECTOR_DIM", "1536")

    from doc_hub.db import ensure_schema

    # Mock pool that returns existing_dim=768 (different from configured 1536)
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=768)  # existing dim is 768

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=False),
    ))

    with pytest.raises(RuntimeError, match="vector\\(768\\)"):
        await ensure_schema(mock_pool)


def test_pyproject_python_version():
    """Verify pyproject.toml specifies requires-python >= 3.11."""
    import tomllib
    from pathlib import Path

    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    requires_python = data["project"]["requires-python"]
    assert "3.11" in requires_python or "3.13" in requires_python, (
        f"Expected >=3.11 or >=3.13, got: {requires_python}"
    )


def test_pyproject_no_pydantic_ai_dependency():
    """Verify pyproject.toml does NOT include pydantic-ai as a dependency."""
    import tomllib
    from pathlib import Path

    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    deps = data["project"]["dependencies"]
    dep_names = [d.split(">=")[0].split("==")[0].strip() for d in deps]
    assert "pydantic-ai" not in dep_names, (
        "doc-hub should NOT depend on pydantic-ai (optional helper, not core)"
    )


def test_pyproject_has_asyncpg():
    """Verify pyproject.toml includes asyncpg."""
    import tomllib
    from pathlib import Path

    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    deps = data["project"]["dependencies"]
    dep_names = [d.split(">=")[0].split("==")[0].strip() for d in deps]
    assert "asyncpg" in dep_names


def test_pyproject_scripts():
    """Verify all expected CLI entry points are defined in pyproject.toml."""
    import tomllib
    from pathlib import Path

    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    scripts = data["project"].get("scripts", {})
    expected = [
        "doc-hub-pipeline",
        "doc-hub-search",
        "doc-hub-mcp",
        "doc-hub-eval",
        "doc-hub-sync-all",
    ]
    for script in expected:
        assert script in scripts, f"Missing script entry point: {script}"


# ---------------------------------------------------------------------------
# Integration tests — require live DB
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_pool_and_ensure_schema():
    """Integration: create pool and run ensure_schema on a live DB."""
    from doc_hub.db import create_pool, ensure_schema

    pool = await create_pool()
    try:
        await ensure_schema(pool)

        async with pool.acquire() as conn:
            # Verify doc_corpora table exists
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_tables WHERE tablename = 'doc_corpora'"
            )
            assert exists == 1, "doc_corpora table should exist"

            # Verify doc_chunks table exists
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_tables WHERE tablename = 'doc_chunks'"
            )
            assert exists == 1, "doc_chunks table should exist"

            # Verify doc_index_meta table exists
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_tables WHERE tablename = 'doc_index_meta'"
            )
            assert exists == 1, "doc_index_meta table should exist"

    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_doc_chunks_has_corpus_id():
    """Integration: verify doc_chunks has corpus_id column."""
    from doc_hub.db import create_pool, ensure_schema

    pool = await create_pool()
    try:
        await ensure_schema(pool)

        async with pool.acquire() as conn:
            col = await conn.fetchval(
                """
                SELECT 1 FROM pg_attribute
                WHERE attrelid = 'doc_chunks'::regclass
                  AND attname = 'corpus_id'
                  AND NOT attisdropped
                """
            )
            assert col == 1, "doc_chunks should have corpus_id column"
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_doc_chunks_has_parser_embedder_columns():
    """Integration: verify doc_corpora has parser and embedder columns."""
    from doc_hub.db import create_pool, ensure_schema

    pool = await create_pool()
    try:
        await ensure_schema(pool)

        async with pool.acquire() as conn:
            for col_name in ("parser", "embedder"):
                col = await conn.fetchval(
                    """
                    SELECT 1 FROM pg_attribute
                    WHERE attrelid = 'doc_corpora'::regclass
                      AND attname = $1
                      AND NOT attisdropped
                    """,
                    col_name,
                )
                assert col == 1, f"doc_corpora should have {col_name} column"
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_doc_corpora_no_check_constraint():
    """Integration: verify fetch_strategy has no CHECK constraint."""
    from doc_hub.db import create_pool, ensure_schema

    pool = await create_pool()
    try:
        await ensure_schema(pool)

        async with pool.acquire() as conn:
            # Count CHECK constraints on doc_corpora — should be 0
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM pg_constraint
                WHERE conrelid = 'doc_corpora'::regclass
                  AND contype = 'c'
                """
            )
            assert count == 0, f"doc_corpora should have no CHECK constraints, found {count}"
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_doc_chunks_unique_constraint():
    """Integration: verify doc_chunks unique constraint is (corpus_id, content_hash)."""
    from doc_hub.db import create_pool, ensure_schema

    pool = await create_pool()
    try:
        await ensure_schema(pool)

        async with pool.acquire() as conn:
            # Check for unique constraint on (corpus_id, content_hash)
            result = await conn.fetchval(
                """
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'doc_chunks'::regclass
                  AND contype = 'u'
                  AND array_length(conkey, 1) = 2
                """
            )
            assert result == 1, "doc_chunks should have a 2-column unique constraint"
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tsv_weighted_generation():
    """Integration: verify tsv column definition uses weighted tsvector."""
    from doc_hub.db import create_pool, ensure_schema

    pool = await create_pool()
    try:
        await ensure_schema(pool)

        async with pool.acquire() as conn:
            expr = await conn.fetchval(
                """
                SELECT pg_get_expr(adbin, adrelid)
                FROM pg_attrdef
                WHERE adrelid = 'doc_chunks'::regclass
                  AND adnum = (
                      SELECT attnum FROM pg_attribute
                      WHERE attrelid = 'doc_chunks'::regclass
                        AND attname = 'tsv'
                  )
                """
            )
            assert expr is not None, "tsv generated column expression should exist"
            assert "setweight" in expr.lower() or "'A'" in expr or "'B'" in expr, (
                f"tsv expression should use setweight: {expr}"
            )
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_doc_index_meta_composite_pk():
    """Integration: verify doc_index_meta has composite PK (corpus_id, key)."""
    from doc_hub.db import create_pool, ensure_schema

    pool = await create_pool()
    try:
        await ensure_schema(pool)

        async with pool.acquire() as conn:
            result = await conn.fetchval(
                """
                SELECT array_length(conkey, 1)
                FROM pg_constraint
                WHERE conrelid = 'doc_index_meta'::regclass
                  AND contype = 'p'
                """
            )
            assert result == 2, (
                f"doc_index_meta PK should be composite (2 cols), got: {result}"
            )
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_jsonb_codec_roundtrip():
    """Integration: verify JSONB codec serializes/deserializes Python dicts."""
    from doc_hub.db import create_pool, ensure_schema

    pool = await create_pool()
    try:
        await ensure_schema(pool)

        test_config = {"url": "https://example.com", "extra": {"key": "value"}}

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO doc_corpora (slug, name, fetch_strategy, fetch_config)
                VALUES ('_test_jsonb_codec', 'Test', 'llms_txt', $1)
                ON CONFLICT (slug) DO UPDATE SET fetch_config = EXCLUDED.fetch_config
                """,
                test_config,
            )

            row = await conn.fetchrow(
                "SELECT fetch_config FROM doc_corpora WHERE slug = '_test_jsonb_codec'"
            )
            assert isinstance(row["fetch_config"], dict), (
                "fetch_config should be deserialized as dict, not str"
            )
            assert row["fetch_config"] == test_config

            await conn.execute(
                "DELETE FROM doc_corpora WHERE slug = '_test_jsonb_codec'"
            )
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ensure_schema_is_idempotent():
    """Integration: calling ensure_schema twice should not raise errors."""
    from doc_hub.db import create_pool, ensure_schema

    pool = await create_pool()
    try:
        await ensure_schema(pool)
        await ensure_schema(pool)  # Second call should be a no-op
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_corpus_id_indexes_exist():
    """Integration: verify corpus-scoped indexes are created."""
    from doc_hub.db import create_pool, ensure_schema

    pool = await create_pool()
    try:
        await ensure_schema(pool)

        expected_indexes = [
            "doc_chunks_corpus_id_idx",
            "doc_chunks_corpus_tsv_idx",
            "doc_chunks_corpus_category_idx",
            "doc_chunks_corpus_hash_idx",
            "doc_chunks_source_url_idx",
            "doc_chunks_section_path_idx",
            "doc_chunks_heading_level_idx",
        ]

        async with pool.acquire() as conn:
            for idx_name in expected_indexes:
                exists = await conn.fetchval(
                    """
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname = $1
                    """,
                    idx_name,
                )
                assert exists == 1, f"Expected index not found: {idx_name}"
    finally:
        await pool.close()
