"""Tests for doc_hub.search — hybrid search engine.

Unit tests only — no network, no DB required.
The embedder plugin and asyncpg pool are mocked throughout.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_hub.search import (
    SearchConfig,
    SearchResult,
    VALID_PG_LANGUAGES,
    _build_hybrid_sql,
    _escape_like,
    _embed_query_async,
    resolve_search_scope,
    search_docs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    id: int = 1,
    corpus_id: str = "pydantic-ai",
    heading: str = "Test heading",
    section_path: str = "/guide/test",
    content: str = "Some content here",
    source_url: str = "https://ai.pydantic.dev/guide/test.md",
    category: str = "guide",
    vec_similarity: float = 0.80,
    rrf_score: float = 0.033,
    source_file: str = "guide__test.md",
    snapshot_id: str = "legacy",
    source_version: str = "latest",
) -> dict:
    """Build a fake asyncpg-style row dict."""
    return {
        "id": id,
        "corpus_id": corpus_id,
        "heading": heading,
        "section_path": section_path,
        "content": content,
        "source_url": source_url,
        "category": category,
        "vec_similarity": vec_similarity,
        "rrf_score": rrf_score,
        "start_line": 1,
        "end_line": 10,
        "source_file": source_file,
        "doc_path": source_file.removesuffix(".md").replace("__", "/"),
        "snapshot_id": snapshot_id,
        "source_version": source_version,
    }


def _make_pool(rows: list[dict] | None = None) -> MagicMock:
    """Build a mock asyncpg Pool that returns the given rows from conn.fetch()."""
    if rows is None:
        rows = [_make_row()]

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=rows)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_mock_embedder(embedding: list[float] | None = None) -> MagicMock:
    """Build a mock Embedder that returns a fixed embedding from embed_query."""
    if embedding is None:
        embedding = [0.1] * 768

    embedder = MagicMock()
    embedder.model_name = "test-model"
    embedder.dimensions = 768
    embedder.embed_query = AsyncMock(return_value=embedding)
    embedder.embed_batch = AsyncMock(return_value=[embedding])
    return embedder


# ---------------------------------------------------------------------------
# SearchConfig validation
# ---------------------------------------------------------------------------


class TestSearchConfig:
    def test_defaults(self):
        cfg = SearchConfig()
        assert cfg.vector_limit == 20
        assert cfg.text_limit == 10
        assert cfg.rrfk == 60
        assert cfg.language == "english"

    def test_invalid_language_raises(self):
        with pytest.raises(ValueError, match="Invalid language"):
            SearchConfig(language="nonexistent_language")

    def test_valid_languages(self):
        for lang in ["english", "french", "german", "spanish", "simple"]:
            cfg = SearchConfig(language=lang)
            assert cfg.language == lang

    def test_zero_vector_limit_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            SearchConfig(vector_limit=0)

    def test_zero_text_limit_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            SearchConfig(text_limit=0)

    def test_zero_rrfk_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            SearchConfig(rrfk=0)

    def test_negative_values_raise(self):
        with pytest.raises(ValueError):
            SearchConfig(vector_limit=-1)

    def test_valid_pg_languages_whitelist(self):
        # Spot check the whitelist contains expected values
        assert "english" in VALID_PG_LANGUAGES
        assert "french" in VALID_PG_LANGUAGES
        assert "arabic" in VALID_PG_LANGUAGES
        assert "simple" in VALID_PG_LANGUAGES
        # Should NOT contain invalid languages
        assert "elvish" not in VALID_PG_LANGUAGES
        assert "klingon" not in VALID_PG_LANGUAGES

    def test_sql_injection_attempt_rejected(self):
        # An injected language string should be rejected
        with pytest.raises(ValueError, match="Invalid language"):
            SearchConfig(language="english'; DROP TABLE doc_chunks; --")


# ---------------------------------------------------------------------------
# SearchResult dataclass
# ---------------------------------------------------------------------------


class TestSearchResult:
    def test_fields_present(self):
        r = SearchResult(
            id=42,
            corpus_id="test-corpus",
            heading="Hello",
            section_path="/api/hello",
            content="body text",
            source_url="https://example.com/api/hello",
            score=0.033,
            similarity=0.87,
            category="api",
            start_line=1,
            end_line=5,
            source_file="api__hello.md",
            doc_path="api/hello",
            snapshot_id="snap-1",
            source_version="1.0",
        )
        assert r.id == 42
        assert r.corpus_id == "test-corpus"
        assert r.heading == "Hello"
        assert r.section_path == "/api/hello"
        assert r.content == "body text"
        assert r.source_url == "https://example.com/api/hello"
        assert r.score == 0.033
        assert r.similarity == 0.87
        assert r.category == "api"
        assert r.start_line == 1
        assert r.end_line == 5
        assert r.source_file == "api__hello.md"
        assert r.snapshot_id == "snap-1"
        assert r.source_version == "1.0"

    def test_source_file_field_present(self):
        """SearchResult has a source_file field for doc_id derivation."""
        r = SearchResult(
            id=1,
            corpus_id="c",
            heading="h",
            section_path="p",
            content="c",
            source_url="u",
            score=0.0,
            similarity=0.0,
            category="guide",
            start_line=1,
            end_line=1,
            source_file="test.md",
            doc_path="test",
        )
        assert r.source_file == "test.md"


# ---------------------------------------------------------------------------
# _escape_like()
# ---------------------------------------------------------------------------


class TestEscapeLike:
    def test_plain_string_unchanged(self):
        assert _escape_like("hello/world") == "hello/world"

    def test_percent_escaped(self):
        assert _escape_like("50%") == "50\\%"

    def test_underscore_escaped(self):
        assert _escape_like("column_name") == "column\\_name"

    def test_backslash_escaped(self):
        assert _escape_like("path\\to\\file") == "path\\\\to\\\\file"

    def test_combined_metacharacters(self):
        result = _escape_like("50%_off\\sale")
        assert result == "50\\%\\_off\\\\sale"

    def test_empty_string(self):
        assert _escape_like("") == ""


# ---------------------------------------------------------------------------
# _build_hybrid_sql()
# ---------------------------------------------------------------------------


class TestBuildHybridSQL:
    def test_returns_string(self):
        sql = _build_hybrid_sql()
        assert isinstance(sql, str)
        assert len(sql) > 100

    def test_uses_websearch_to_tsquery(self):
        sql = _build_hybrid_sql()
        assert "websearch_to_tsquery" in sql
        assert "plainto_tsquery" not in sql

    def test_uses_row_number(self):
        sql = _build_hybrid_sql()
        assert "ROW_NUMBER()" in sql

    def test_uses_full_outer_join(self):
        sql = _build_hybrid_sql()
        assert "FULL OUTER JOIN" in sql

    def test_has_corpus_id_filter(self):
        sql = _build_hybrid_sql()
        # $3 is the corpus_id array parameter
        assert "$3::text[] IS NULL OR corpus_id = ANY($3)" in sql

    def test_has_category_include_filter(self):
        sql = _build_hybrid_sql()
        assert "$4::text[] IS NULL OR category = ANY($4)" in sql

    def test_has_category_exclude_filter(self):
        sql = _build_hybrid_sql()
        assert "$5::text[] IS NULL OR category != ALL($5)" in sql

    def test_has_source_url_filter(self):
        sql = _build_hybrid_sql()
        assert "source_url LIKE $6" in sql

    def test_has_section_path_filter(self):
        sql = _build_hybrid_sql()
        assert "section_path LIKE $7" in sql

    def test_limit_offset_params(self):
        sql = _build_hybrid_sql()
        assert "LIMIT $9" in sql
        assert "OFFSET $10" in sql

    def test_rrf_scoring_formula(self):
        sql = _build_hybrid_sql()
        # RRF formula uses vec_rank and text_rank
        assert "vec_rank" in sql
        assert "text_rank" in sql
        assert "rrf_score" in sql

    def test_vector_limit_interpolated(self):
        cfg = SearchConfig(vector_limit=42)
        sql = _build_hybrid_sql(config=cfg)
        assert "LIMIT 42" in sql

    def test_text_limit_interpolated(self):
        cfg = SearchConfig(text_limit=7)
        sql = _build_hybrid_sql(config=cfg)
        # text_limit appears as a LIMIT in text_results CTE
        assert "7" in sql

    def test_language_interpolated(self):
        cfg = SearchConfig(language="french")
        sql = _build_hybrid_sql(config=cfg)
        assert "french" in sql

    def test_rrfk_interpolated(self):
        cfg = SearchConfig(rrfk=80)
        sql = _build_hybrid_sql(config=cfg)
        assert "80" in sql

    def test_selects_corpus_id(self):
        sql = _build_hybrid_sql()
        assert "corpus_id" in sql

    def test_selects_id(self):
        sql = _build_hybrid_sql()
        assert "AS id" in sql

    def test_vector_limit_default(self):
        sql = _build_hybrid_sql()
        assert "LIMIT 20" in sql

    def test_vec_similarity_coalesced(self):
        sql = _build_hybrid_sql()
        assert "vec_similarity" in sql


# ---------------------------------------------------------------------------
# _embed_query_async()
# ---------------------------------------------------------------------------


class TestEmbedQueryAsync:
    @pytest.mark.asyncio
    async def test_returns_normalized_vector(self):
        """Should call embedder.embed_query() and return an L2-normalized vector."""
        embedder = _make_mock_embedder()
        result = await _embed_query_async("test query", embedder=embedder)
        assert isinstance(result, list)
        assert len(result) == 768

    @pytest.mark.asyncio
    async def test_calls_embed_query_on_embedder(self):
        """Must call embedder.embed_query() with the query string."""
        embedder = _make_mock_embedder()
        await _embed_query_async("test query", embedder=embedder)
        embedder.embed_query.assert_called_once_with("test query")

    @pytest.mark.asyncio
    async def test_l2_normalized(self):
        """Returned vector should have unit norm (L2-normalized)."""
        import numpy as np

        # Provide a non-unit vector from embed_query
        raw = [3.0] * 768
        embedder = _make_mock_embedder(embedding=raw)
        result = await _embed_query_async("query", embedder=embedder)
        norm = np.linalg.norm(result)
        assert abs(norm - 1.0) < 1e-5

    @pytest.mark.asyncio
    async def test_resolves_embedder_from_registry_when_none(self):
        """When embedder=None, resolve from plugin registry."""
        mock_embedder = _make_mock_embedder()
        mock_registry = MagicMock()
        mock_registry.list_embedders.return_value = ["gemini"]
        mock_registry.get_embedder.return_value = mock_embedder

        with patch("doc_hub.discovery.get_registry", return_value=mock_registry):
            result = await _embed_query_async("test", embedder=None)

        mock_registry.get_embedder.assert_called_once_with("gemini")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_raises_when_no_embedders_registered(self):
        """RuntimeError when no embedder plugins are registered."""
        mock_registry = MagicMock()
        mock_registry.list_embedders.return_value = []

        with patch("doc_hub.discovery.get_registry", return_value=mock_registry):
            with pytest.raises(RuntimeError, match="No embedder plugins"):
                await _embed_query_async("test", embedder=None)

    @pytest.mark.asyncio
    async def test_fallback_to_first_embedder_when_no_gemini(self):
        """Falls back to first available embedder when 'gemini' not in registry."""
        mock_embedder = _make_mock_embedder()
        mock_registry = MagicMock()
        mock_registry.list_embedders.return_value = ["openai"]
        mock_registry.get_embedder.return_value = mock_embedder

        with patch("doc_hub.discovery.get_registry", return_value=mock_registry):
            await _embed_query_async("test", embedder=None)

        mock_registry.get_embedder.assert_called_once_with("openai")


# ---------------------------------------------------------------------------
# resolve_search_scope()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_search_scope_versions_are_explicit_snapshot_keys():
    pool = MagicMock()
    rows = [
        {"source_version": "18", "snapshot_id": "snap-18", "aliases": None},
        {"source_version": "19", "snapshot_id": "snap-19", "aliases": ["latest"]},
    ]

    async def fetch(query, *args):
        return rows

    async def fetchval(query, corpus, selector):
        if selector == "18":
            return "snap-18"
        if selector == "19":
            return "snap-19"
        return None

    pool.fetch = AsyncMock(side_effect=fetch)
    pool.fetchval = AsyncMock(side_effect=fetchval)

    scope = await resolve_search_scope(pool, ["react"], versions=["18", "19"])

    assert scope["snapshot_scope_keys"] == ["react:snap-18", "react:snap-19"]
    assert scope["searched_versions"] == [
        {"corpus": "react", "requested": "18", "snapshot_id": "snap-18", "selected_by": "explicit"},
        {"corpus": "react", "requested": "19", "snapshot_id": "snap-19", "selected_by": "explicit"},
    ]
    assert scope["available_versions"] == {"react": ["18", "19"]}
    assert scope["aliases"] == {"react": {"latest": "19"}}


@pytest.mark.asyncio
async def test_resolve_search_scope_rejects_conflicting_modes():
    pool = MagicMock()
    with pytest.raises(ValueError, match="only one"):
        await resolve_search_scope(pool, ["react"], version="18", all_versions=True)


# ---------------------------------------------------------------------------
# search_docs() — core search function
# ---------------------------------------------------------------------------


class TestSearchDocs:
    @pytest.mark.asyncio
    async def test_returns_search_results(self):
        """Should return a list of SearchResult objects."""
        pool = _make_pool([_make_row(vec_similarity=0.80)])
        embedder = _make_mock_embedder()

        results = await search_docs("test query", pool=pool, embedder=embedder)

        assert isinstance(results, list)
        assert len(results) == 1
        assert isinstance(results[0], SearchResult)

    @pytest.mark.asyncio
    async def test_result_has_corpus_id(self):
        """SearchResult.corpus_id must be populated from the SQL row."""
        pool = _make_pool([_make_row(corpus_id="my-corpus", vec_similarity=0.90)])
        embedder = _make_mock_embedder()

        results = await search_docs("query", pool=pool, embedder=embedder)

        assert results[0].corpus_id == "my-corpus"

    @pytest.mark.asyncio
    async def test_result_has_id(self):
        """SearchResult.id must be populated from the SQL row."""
        pool = _make_pool([_make_row(id=99, vec_similarity=0.90)])
        embedder = _make_mock_embedder()

        results = await search_docs("query", pool=pool, embedder=embedder)

        assert results[0].id == 99

    @pytest.mark.asyncio
    async def test_min_similarity_post_filter_default(self):
        """Results below min_similarity=0.55 (default) must be filtered out in Python."""
        rows = [
            _make_row(id=1, vec_similarity=0.80),  # passes
            _make_row(id=2, vec_similarity=0.30),  # filtered
            _make_row(id=3, vec_similarity=0.60),  # passes
        ]
        pool = _make_pool(rows)
        embedder = _make_mock_embedder()

        results = await search_docs("query", pool=pool, embedder=embedder)

        ids = [r.id for r in results]
        assert 1 in ids
        assert 3 in ids
        assert 2 not in ids

    @pytest.mark.asyncio
    async def test_min_similarity_is_python_filter_not_sql(self):
        """min_similarity filtering happens in Python AFTER SQL execution."""
        rows = [
            _make_row(id=1, vec_similarity=0.80),
            _make_row(id=2, vec_similarity=0.10),  # low — should be filtered in Python
            _make_row(id=3, vec_similarity=0.0, rrf_score=0.028),  # text-only result should survive
        ]
        pool = _make_pool(rows)
        embedder = _make_mock_embedder()

        results = await search_docs("query", pool=pool, embedder=embedder, min_similarity=0.55)

        # conn.fetch was called once (SQL ran once without filtering)
        mock_conn = pool.acquire.return_value.__aenter__.return_value
        assert mock_conn.fetch.call_count == 1

        ids = [result.id for result in results]
        assert 1 in ids
        assert 2 not in ids
        assert 3 in ids

    @pytest.mark.asyncio
    async def test_empty_results_when_all_below_threshold(self):
        """Returns [] when all results are below min_similarity."""
        rows = [_make_row(vec_similarity=0.10)]
        pool = _make_pool(rows)
        embedder = _make_mock_embedder()

        results = await search_docs("query", pool=pool, embedder=embedder)

        assert results == []

    @pytest.mark.asyncio
    async def test_corpora_filter_passed_to_sql(self):
        """The corpora parameter must be passed as $3 to the SQL query."""
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs("query", pool=pool, embedder=embedder, corpora=["pydantic-ai", "fastapi"])

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        positional_args = call_args[0]
        assert positional_args[3] == ["pydantic-ai", "fastapi"]  # $3

    @pytest.mark.asyncio
    async def test_corpora_none_passes_null_to_sql(self):
        """corpora=None should pass None as $3."""
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs("query", pool=pool, embedder=embedder, corpora=None)

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        positional_args = call_args[0]
        assert positional_args[3] is None  # $3

    @pytest.mark.asyncio
    async def test_categories_include_filter(self):
        """categories list is passed as $4 to the SQL query."""
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs("query", pool=pool, embedder=embedder, categories=["api"])

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        positional_args = call_args[0]
        assert positional_args[4] == ["api"]  # $4

    @pytest.mark.asyncio
    async def test_exclude_categories_filter(self):
        """exclude_categories list is passed as $5 to the SQL query."""
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs(
            "query", pool=pool, embedder=embedder, exclude_categories=["eval"]
        )

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        positional_args = call_args[0]
        assert positional_args[5] == ["eval"]  # $5

    @pytest.mark.asyncio
    async def test_source_url_prefix_escaped_and_passed(self):
        """source_url_prefix is LIKE-escaped and passed as $6."""
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs(
            "query",
            pool=pool,
            embedder=embedder,
            source_url_prefix="https://example.com/50%",
        )

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        positional_args = call_args[0]
        # Should be LIKE-escaped: % → \%
        assert positional_args[6] == "https://example.com/50\\%"  # $6

    @pytest.mark.asyncio
    async def test_section_path_prefix_escaped_and_passed(self):
        """section_path_prefix is LIKE-escaped and passed as $7."""
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs(
            "query",
            pool=pool,
            embedder=embedder,
            section_path_prefix="/api/some_module",
        )

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        positional_args = call_args[0]
        # _ → \_ via LIKE escaping
        assert positional_args[7] == "/api/some\\_module"  # $7

    @pytest.mark.asyncio
    async def test_limit_passed_to_sql(self):
        """limit is passed as $8."""
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs("query", pool=pool, embedder=embedder, limit=10)

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        positional_args = call_args[0]
        assert positional_args[9] == 10  # $9

    @pytest.mark.asyncio
    async def test_offset_passed_to_sql(self):
        """offset is passed as $9."""
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs("query", pool=pool, embedder=embedder, offset=20)

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        positional_args = call_args[0]
        assert positional_args[10] == 20  # $10

    @pytest.mark.asyncio
    async def test_custom_min_similarity(self):
        """min_similarity can be customised."""
        rows = [
            _make_row(id=1, vec_similarity=0.70),
            _make_row(id=2, vec_similarity=0.50),
        ]
        pool = _make_pool(rows)
        embedder = _make_mock_embedder()

        results = await search_docs(
            "query", pool=pool, embedder=embedder, min_similarity=0.60
        )

        assert len(results) == 1
        assert results[0].id == 1

    @pytest.mark.asyncio
    async def test_result_similarity_field(self):
        """SearchResult.similarity comes from vec_similarity column."""
        pool = _make_pool([_make_row(vec_similarity=0.75)])
        embedder = _make_mock_embedder()

        results = await search_docs("query", pool=pool, embedder=embedder)

        assert abs(results[0].similarity - 0.75) < 1e-9

    @pytest.mark.asyncio
    async def test_result_score_field(self):
        """SearchResult.score comes from rrf_score column."""
        pool = _make_pool([_make_row(rrf_score=0.0423, vec_similarity=0.90)])
        embedder = _make_mock_embedder()

        results = await search_docs("query", pool=pool, embedder=embedder)

        assert abs(results[0].score - 0.0423) < 1e-9

    @pytest.mark.asyncio
    async def test_multi_corpus_search_preserves_cross_corpus_results(self):
        """When multiple corpora are requested, results may come from each requested corpus."""
        rows = [
            _make_row(id=1, corpus_id="corpus-a", vec_similarity=0.90),
            _make_row(id=2, corpus_id="corpus-b", vec_similarity=0.85),
        ]
        pool = _make_pool(rows)
        embedder = _make_mock_embedder()

        results = await search_docs(
            "query",
            pool=pool,
            embedder=embedder,
            corpora=["corpus-a", "corpus-b"],
        )

        corpus_ids = {r.corpus_id for r in results}
        assert "corpus-a" in corpus_ids
        assert "corpus-b" in corpus_ids

    @pytest.mark.asyncio
    async def test_source_file_in_result(self):
        """SearchResult should have a source_file attribute for doc_id derivation."""
        pool = _make_pool([_make_row(vec_similarity=0.90, source_file="guide__install.md")])
        embedder = _make_mock_embedder()

        results = await search_docs("query", pool=pool, embedder=embedder)

        assert len(results) == 1
        assert results[0].source_file == "guide__install.md"

    @pytest.mark.asyncio
    async def test_result_has_version_metadata(self):
        pool = _make_pool([_make_row(vec_similarity=0.90, snapshot_id="snap-1", source_version="1.0")])
        embedder = _make_mock_embedder()

        results = await search_docs("query", pool=pool, embedder=embedder)

        assert results[0].snapshot_id == "snap-1"
        assert results[0].source_version == "1.0"

    @pytest.mark.asyncio
    async def test_snapshot_scope_passed_to_sql(self):
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs(
            "query",
            pool=pool,
            embedder=embedder,
            snapshot_ids={"corpus-a": "snap-1", "corpus-b": "snap-2"},
        )

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        positional_args = mock_conn.fetch.call_args[0]
        assert positional_args[8] == ["corpus-a:snap-1", "corpus-b:snap-2"]

    @pytest.mark.asyncio
    async def test_snapshot_scope_keys_passed_to_sql(self):
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs(
            "query",
            pool=pool,
            embedder=embedder,
            snapshot_scope_keys=["corpus-a:snap-1", "corpus-a:snap-2"],
        )

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        positional_args = mock_conn.fetch.call_args[0]
        assert positional_args[8] == ["corpus-a:snap-1", "corpus-a:snap-2"]

    @pytest.mark.asyncio
    async def test_custom_search_config(self):
        """SearchConfig is forwarded to _build_hybrid_sql."""
        pool = _make_pool([])
        embedder = _make_mock_embedder()
        cfg = SearchConfig(vector_limit=50, text_limit=25, rrfk=80, language="french")

        await search_docs("query", pool=pool, embedder=embedder, config=cfg)

        # SQL passed to conn.fetch should contain our custom config values
        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        sql = call_args[0][0]
        assert "LIMIT 50" in sql
        assert "french" in sql
        assert "80" in sql

    @pytest.mark.asyncio
    async def test_pagination_offset(self):
        """Verify offset is threaded through to SQL bind parameters."""
        pool = _make_pool([])
        embedder = _make_mock_embedder()

        await search_docs("query", pool=pool, embedder=embedder, offset=15, limit=5)

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        positional_args = call_args[0]
        # $9=limit, $10=offset
        assert positional_args[9] == 5   # $9
        assert positional_args[10] == 15  # $10


# ---------------------------------------------------------------------------
# SQL structure: verify bind parameter ordering matches docstring
# ---------------------------------------------------------------------------


class TestSQLBindParameterOrder:
    """Verify the SQL bind parameter contract ($1..$10) is correct."""

    def test_sql_has_ten_parameters(self):
        sql = _build_hybrid_sql()
        assert "$10" in sql

    def test_corpus_is_third_param(self):
        sql = _build_hybrid_sql()
        assert "$3::text[] IS NULL OR corpus_id = ANY($3)" in sql

    def test_categories_is_fourth_param(self):
        sql = _build_hybrid_sql()
        assert "$4::text[]" in sql

    def test_exclude_categories_is_fifth_param(self):
        sql = _build_hybrid_sql()
        assert "$5::text[]" in sql

    def test_source_url_is_sixth_param(self):
        sql = _build_hybrid_sql()
        assert "$6" in sql
        assert "source_url LIKE $6" in sql

    def test_section_path_is_seventh_param(self):
        sql = _build_hybrid_sql()
        assert "$7" in sql
        assert "section_path LIKE $7" in sql

    def test_snapshot_scope_is_eighth_param(self):
        sql = _build_hybrid_sql()
        assert "$8::text[] IS NULL OR corpus_id || ':' || snapshot_id = ANY($8)" in sql

    def test_limit_is_ninth_param(self):
        sql = _build_hybrid_sql()
        assert "LIMIT $9" in sql

    def test_offset_is_tenth_param(self):
        sql = _build_hybrid_sql()
        assert "OFFSET $10" in sql


# ---------------------------------------------------------------------------
# CLI entry point (basic smoke test)
# ---------------------------------------------------------------------------


class TestCLIParsing:
    def test_search_requires_at_least_one_corpus(self):
        from doc_hub.search import build_search_parser

        parser = build_search_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["retry logic"])

    def test_search_accepts_multiple_corpora(self):
        from doc_hub.search import build_search_parser

        parser = build_search_parser()
        args = parser.parse_args(["--corpus", "pydantic-ai", "--corpus", "fastapi", "retry logic"])

        assert args.corpora == ["pydantic-ai", "fastapi"]

    def test_handle_search_args_passes_corpora_to_search(self):
        from doc_hub.search import handle_search_args

        args = argparse.Namespace(
            query="retry logic",
            corpora=["pydantic-ai", "fastapi"],
            categories=None,
            exclude_categories=None,
            limit=5,
            offset=0,
            min_similarity=0.55,
            source_url_prefix=None,
            section_path_prefix=None,
            version=None,
            versions=None,
            all_versions=False,
            vector_limit=None,
            text_limit=None,
            rrfk=None,
            language=None,
            json=False,
        )

        with patch("doc_hub.search.search_docs_sync", return_value=[] ) as mock_search:
            handle_search_args(args)

        assert mock_search.call_args.kwargs["corpora"] == ["pydantic-ai", "fastapi"]

    def test_search_accepts_version_flag(self):
        from doc_hub.search import build_search_parser

        parser = build_search_parser()
        args = parser.parse_args(["--corpus", "pydantic-ai", "--version", "1.0", "retry logic"])

        assert args.version == "1.0"

    def test_handle_search_args_passes_version_to_search(self):
        from doc_hub.search import handle_search_args

        args = argparse.Namespace(
            query="retry logic",
            corpora=["pydantic-ai"],
            categories=None,
            exclude_categories=None,
            limit=5,
            offset=0,
            min_similarity=0.55,
            source_url_prefix=None,
            section_path_prefix=None,
            version="1.0",
            versions=None,
            all_versions=False,
            vector_limit=None,
            text_limit=None,
            rrfk=None,
            language=None,
            json=False,
        )

        with patch("doc_hub.search.search_docs_sync", return_value=[] ) as mock_search:
            handle_search_args(args)

        assert mock_search.call_args.kwargs["version"] == "1.0"

    def test_search_accepts_versions_flag(self):
        from doc_hub.search import build_search_parser

        parser = build_search_parser()
        args = parser.parse_args(["--corpus", "pydantic-ai", "--versions", "1.0,2.0", "retry logic"])

        assert args.versions == "1.0,2.0"

    def test_search_accepts_all_versions_flag(self):
        from doc_hub.search import build_search_parser

        parser = build_search_parser()
        args = parser.parse_args(["--corpus", "pydantic-ai", "--all-versions", "retry logic"])

        assert args.all_versions is True


class TestCLIMain:
    def test_main_importable(self):
        """main() should be importable without side effects."""
        from doc_hub.search import main
        assert callable(main)

    def test_search_docs_sync_importable(self):
        from doc_hub.search import search_docs_sync
        assert callable(search_docs_sync)
