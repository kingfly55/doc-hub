from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest


def test_documents_ddl_has_all_columns():
    """Verify doc_documents DDL includes all milestone 1 columns."""
    from doc_hub.db import _DOCUMENTS_DDL

    expected_columns = [
        "id serial PRIMARY KEY",
        "corpus_id text NOT NULL",
        "doc_path text NOT NULL",
        "title text NOT NULL",
        "source_url text NOT NULL DEFAULT ''",
        "source_file text NOT NULL DEFAULT ''",
        "parent_id int",
        "depth smallint NOT NULL DEFAULT 0",
        "sort_order int NOT NULL DEFAULT 0",
        "is_group boolean NOT NULL DEFAULT false",
        "total_chars int NOT NULL DEFAULT 0",
        "section_count int NOT NULL DEFAULT 0",
    ]

    for column in expected_columns:
        assert column in _DOCUMENTS_DDL, f"Missing column in _DOCUMENTS_DDL: {column}"


def test_documents_ddl_is_idempotent():
    """Verify doc_documents DDL uses CREATE TABLE IF NOT EXISTS."""
    from doc_hub.db import _DOCUMENTS_DDL

    assert "CREATE TABLE IF NOT EXISTS doc_documents" in _DOCUMENTS_DDL


def test_documents_ddl_has_unique_constraint():
    """Verify doc_documents DDL has UNIQUE(corpus_id, doc_path)."""
    from doc_hub.db import _DOCUMENTS_DDL

    assert "UNIQUE (corpus_id, doc_path)" in _DOCUMENTS_DDL


def test_documents_ddl_parent_fk_on_delete_set_null():
    """Verify parent_id self-reference uses ON DELETE SET NULL."""
    from doc_hub.db import _DOCUMENTS_DDL

    assert "parent_id int REFERENCES doc_documents(id) ON DELETE SET NULL" in _DOCUMENTS_DDL


def test_documents_ddl_has_corpus_fk():
    """Verify doc_documents DDL references doc_corpora(slug)."""
    from doc_hub.db import _DOCUMENTS_DDL

    assert "corpus_id text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE" in _DOCUMENTS_DDL


def test_chunks_document_id_ddl_is_idempotent():
    """Verify doc_chunks document_id alter is idempotent."""
    from doc_hub.db import _CHUNKS_DOCUMENT_ID_DDL

    assert "ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS document_id int" in _CHUNKS_DOCUMENT_ID_DDL


def test_chunks_document_id_ddl_on_delete_set_null():
    """Verify doc_chunks document_id FK uses ON DELETE SET NULL."""
    from doc_hub.db import _CHUNKS_DOCUMENT_ID_DDL

    assert "REFERENCES doc_documents(id) ON DELETE SET NULL" in _CHUNKS_DOCUMENT_ID_DDL


def test_documents_indexes_present():
    """Verify all milestone 1 doc_documents indexes are declared."""
    from doc_hub.db import _DOCUMENTS_INDEXES_DDL

    expected_indexes = [
        "CREATE INDEX IF NOT EXISTS doc_documents_corpus_id_idx",
        "ON doc_documents (corpus_id)",
        "CREATE INDEX IF NOT EXISTS doc_documents_parent_id_idx",
        "ON doc_documents (parent_id)",
        "CREATE INDEX IF NOT EXISTS doc_documents_corpus_sort_order_idx",
        "ON doc_documents (corpus_id, sort_order)",
        "CREATE INDEX IF NOT EXISTS doc_documents_corpus_path_idx",
        "ON doc_documents (corpus_id, doc_path text_pattern_ops)",
    ]

    for index_stmt in expected_indexes:
        assert index_stmt in _DOCUMENTS_INDEXES_DDL, (
            f"Missing index declaration in _DOCUMENTS_INDEXES_DDL: {index_stmt}"
        )


def test_chunks_document_id_index_present():
    """Verify doc_chunks document_id index is declared."""
    from doc_hub.db import _CHUNKS_DOCUMENT_ID_INDEX

    assert (
        "CREATE INDEX IF NOT EXISTS doc_chunks_document_id_idx ON doc_chunks (document_id)"
        in _CHUNKS_DOCUMENT_ID_INDEX
    )


@pytest.mark.asyncio
async def test_ensure_schema_executes_documents_before_chunk_document_indexes(monkeypatch):
    """Verify ensure_schema executes document DDL and indexes in milestone 1 order."""
    monkeypatch.delenv("DOC_HUB_VECTOR_DIM", raising=False)

    from doc_hub.db import (
        _CHUNKS_DOCUMENT_ID_DDL,
        _CHUNKS_DOCUMENT_ID_INDEX,
        _DOCUMENTS_DDL,
        _DOCUMENTS_INDEXES_DDL,
        _INDEXES_DDL,
        _META_DDL,
        ensure_schema,
    )

    executed: list[str] = []

    async def execute(stmt: str):
        executed.append(stmt)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=execute)
    mock_conn.fetchval = AsyncMock(return_value=768)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=False),
    ))

    await ensure_schema(mock_pool)

    meta_idx = executed.index(_META_DDL)
    documents_idx = executed.index(_DOCUMENTS_DDL)
    chunks_document_id_idx = executed.index(_CHUNKS_DOCUMENT_ID_DDL)
    existing_indexes = [stmt.strip() for stmt in _INDEXES_DDL.strip().split("\n\n") if stmt.strip()]
    document_indexes = [stmt.strip() for stmt in _DOCUMENTS_INDEXES_DDL.strip().split("\n\n") if stmt.strip()]
    first_existing_index_idx = executed.index(existing_indexes[0])
    last_existing_index_idx = executed.index(existing_indexes[-1])
    first_document_index_idx = executed.index(document_indexes[0])
    last_document_index_idx = executed.index(document_indexes[-1])
    chunks_document_id_index_idx = executed.index(_CHUNKS_DOCUMENT_ID_INDEX)

    assert meta_idx < documents_idx
    assert documents_idx < chunks_document_id_idx
    assert chunks_document_id_idx < first_existing_index_idx
    assert last_existing_index_idx < first_document_index_idx
    assert last_document_index_idx < chunks_document_id_index_idx


@pytest.mark.asyncio
async def test_ensure_schema_migrates_legacy_doc_corpora_schema(monkeypatch):
    """Verify ensure_schema repairs old doc_corpora schemas in place."""
    monkeypatch.delenv("DOC_HUB_VECTOR_DIM", raising=False)

    from doc_hub.db import ensure_schema

    executed: list[str] = []

    async def execute(stmt: str):
        executed.append(stmt)

    fetchvals = {
        "SELECT atttypmod\n            FROM pg_attribute\n            WHERE attrelid = 'doc_chunks'::regclass\n              AND attname = 'embedding'\n              AND NOT attisdropped": 768,
        "SELECT 1\n            FROM pg_attribute\n            WHERE attrelid = 'doc_corpora'::regclass\n              AND attname = 'parser'\n              AND NOT attisdropped": None,
        "SELECT 1\n            FROM pg_attribute\n            WHERE attrelid = 'doc_corpora'::regclass\n              AND attname = 'embedder'\n              AND NOT attisdropped": None,
    }

    async def fetchval(stmt: str, *args):
        normalized = stmt.strip()
        if "FROM pg_constraint" in normalized:
            return "doc_corpora_fetch_strategy_check"
        return fetchvals.get(normalized)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(side_effect=execute)
    mock_conn.fetchval = AsyncMock(side_effect=fetchval)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_conn),
        __aexit__=AsyncMock(return_value=False),
    ))

    await ensure_schema(mock_pool)

    normalized_executed = {stmt.strip() for stmt in executed}

    assert "ALTER TABLE doc_corpora ADD COLUMN IF NOT EXISTS parser text NOT NULL DEFAULT 'markdown'" in normalized_executed
    assert "ALTER TABLE doc_corpora ADD COLUMN IF NOT EXISTS embedder text NOT NULL DEFAULT 'gemini'" in normalized_executed
    assert "ALTER TABLE doc_corpora DROP CONSTRAINT IF EXISTS doc_corpora_fetch_strategy_check" in normalized_executed
