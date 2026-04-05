"""Tests for doc_hub.mcp_server — MCP tool implementations.

Unit tests only — no network, no DB, no live Gemini API required.
All external dependencies (asyncpg pool, run_pipeline, search_docs,
db helpers) are mocked throughout.

Tests use the ``_*_impl()`` functions (the extracted core logic) directly
so they do not require the MCP framework to be running. This pattern matches
the existing pydantic-ai-docs mcp_server.py approach.
"""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_hub.models import Corpus
from doc_hub.mcp_server import (
    AppState,
    _add_corpus_impl,
    _browse_corpus_impl,
    _get_document_impl,
    _list_corpora_impl,
    _refresh_corpus_impl,
    _search_tool_impl,
    server,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_corpus(
    slug: str = "pydantic-ai",
    name: str = "Pydantic AI Docs",
    strategy: str = "llms_txt",
    fetch_config: dict | None = None,
    enabled: bool = True,
    total_chunks: int = 42,
    last_indexed_at: str | None = "2024-01-01T00:00:00+00:00",
) -> Corpus:
    if fetch_config is None:
        fetch_config = {"url": "https://ai.pydantic.dev/llms.txt"}
    return Corpus(
        slug=slug,
        name=name,
        fetch_strategy=strategy,
        fetch_config=fetch_config,
        enabled=enabled,
        total_chunks=total_chunks,
        last_indexed_at=last_indexed_at,
    )


def _make_search_result(
    id: int = 1,
    corpus_id: str = "pydantic-ai",
    heading: str = "Test heading",
    section_path: str = "/guide/test",
    content: str = "Some content here that could be long " * 30,
    source_url: str = "https://ai.pydantic.dev/guide/test.md",
    score: float = 0.03456,
    similarity: float = 0.87654,
    category: str = "guide",
    source_file: str = "guide__test.md",
):
    """Build a fake SearchResult-like object."""
    from doc_hub.search import SearchResult
    return SearchResult(
        id=id,
        corpus_id=corpus_id,
        heading=heading,
        section_path=section_path,
        content=content,
        source_url=source_url,
        score=score,
        similarity=similarity,
        category=category,
        start_line=1,
        end_line=10,
        source_file=source_file,
    )


def _make_mock_pool() -> MagicMock:
    """Build a mock asyncpg Pool."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Server registration
# ---------------------------------------------------------------------------


class TestServerRegistration:
    """Verify the six tools are registered on the FastMCP server."""

    def test_server_name(self):
        """Server name should be 'doc-hub'."""
        assert server.name == "doc-hub"

    def test_search_docs_tool_registered(self):
        """search_docs_tool is registered on the server."""
        tool_names = [t.name for t in server._tool_manager.list_tools()]
        assert "search_docs_tool" in tool_names

    def test_list_corpora_tool_registered(self):
        """list_corpora_tool is registered on the server."""
        tool_names = [t.name for t in server._tool_manager.list_tools()]
        assert "list_corpora_tool" in tool_names

    def test_add_corpus_tool_registered(self):
        """add_corpus_tool is registered on the server."""
        tool_names = [t.name for t in server._tool_manager.list_tools()]
        assert "add_corpus_tool" in tool_names

    def test_refresh_corpus_tool_registered(self):
        """refresh_corpus_tool is registered on the server."""
        tool_names = [t.name for t in server._tool_manager.list_tools()]
        assert "refresh_corpus_tool" in tool_names

    def test_browse_corpus_tool_registered(self):
        """browse_corpus_tool is registered on the server."""
        tool_names = [t.name for t in server._tool_manager.list_tools()]
        assert "browse_corpus_tool" in tool_names

    def test_get_document_tool_registered(self):
        """get_document_tool is registered on the server."""
        tool_names = [t.name for t in server._tool_manager.list_tools()]
        assert "get_document_tool" in tool_names

    def test_exactly_six_tools_registered(self):
        """Exactly six tools are registered — no accidental extras."""
        tool_names = [t.name for t in server._tool_manager.list_tools()]
        expected = {
            "search_docs_tool",
            "list_corpora_tool",
            "add_corpus_tool",
            "refresh_corpus_tool",
            "browse_corpus_tool",
            "get_document_tool",
        }
        assert set(tool_names) == expected


# ---------------------------------------------------------------------------
# _search_tool_impl
# ---------------------------------------------------------------------------


class TestSearchToolImpl:
    """Tests for the core search logic (no MCP framework needed)."""

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        """_search_tool_impl returns a list of dicts."""
        pool = _make_mock_pool()
        results = [_make_search_result()]

        with patch("doc_hub.mcp_server.search_docs", new=AsyncMock(return_value=results)):
            output = await _search_tool_impl(
                "test query",
                corpus=None,
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        assert isinstance(output, list)
        assert len(output) == 1
        assert isinstance(output[0], dict)

    @pytest.mark.asyncio
    async def test_result_has_expected_keys(self):
        """Result dicts contain heading, section_path, content, source_url,
        corpus_id, score, similarity, category."""
        pool = _make_mock_pool()
        results = [_make_search_result()]

        with patch("doc_hub.mcp_server.search_docs", new=AsyncMock(return_value=results)):
            output = await _search_tool_impl(
                "test",
                corpus=None,
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        r = output[0]
        assert "heading" in r
        assert "section_path" in r
        assert "content" in r
        assert "source_url" in r
        assert "corpus_id" in r
        assert "doc_id" in r
        assert "score" in r
        assert "similarity" in r
        assert "category" in r

    @pytest.mark.asyncio
    async def test_corpus_id_in_results(self):
        """Results include corpus_id field."""
        pool = _make_mock_pool()
        results = [_make_search_result(corpus_id="fastapi")]

        with patch("doc_hub.mcp_server.search_docs", new=AsyncMock(return_value=results)):
            output = await _search_tool_impl(
                "test",
                corpus="fastapi",
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        assert output[0]["corpus_id"] == "fastapi"

    @pytest.mark.asyncio
    async def test_doc_id_derived_from_source_file(self):
        """doc_id is derived from corpus_id and source_file."""
        from doc_hub.documents import derive_doc_id, doc_path_from_source_file

        pool = _make_mock_pool()
        results = [_make_search_result(corpus_id="pydantic-ai", source_file="guide__install.md")]

        with patch("doc_hub.mcp_server.search_docs", new=AsyncMock(return_value=results)):
            output = await _search_tool_impl(
                "test",
                corpus="pydantic-ai",
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        expected_doc_id = derive_doc_id("pydantic-ai", doc_path_from_source_file("guide__install.md"))
        assert output[0]["doc_id"] == expected_doc_id
        assert len(output[0]["doc_id"]) == 6

    @pytest.mark.asyncio
    async def test_content_truncated_to_max_chars(self):
        """Content is truncated to max_content_chars characters."""
        pool = _make_mock_pool()
        long_content = "A" * 2000
        results = [_make_search_result(content=long_content)]

        with patch("doc_hub.mcp_server.search_docs", new=AsyncMock(return_value=results)):
            output = await _search_tool_impl(
                "test",
                corpus=None,
                categories=None,
                limit=5,
                max_content_chars=100,
                pool=pool,
            )

        assert len(output[0]["content"]) == 100

    @pytest.mark.asyncio
    async def test_content_not_truncated_if_short_enough(self):
        """Short content is not truncated."""
        pool = _make_mock_pool()
        short_content = "Short content"
        results = [_make_search_result(content=short_content)]

        with patch("doc_hub.mcp_server.search_docs", new=AsyncMock(return_value=results)):
            output = await _search_tool_impl(
                "test",
                corpus=None,
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        assert output[0]["content"] == short_content

    @pytest.mark.asyncio
    async def test_score_rounded_to_4dp(self):
        """Score is rounded to 4 decimal places."""
        pool = _make_mock_pool()
        results = [_make_search_result(score=0.0345678)]

        with patch("doc_hub.mcp_server.search_docs", new=AsyncMock(return_value=results)):
            output = await _search_tool_impl(
                "test",
                corpus=None,
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        assert output[0]["score"] == round(0.0345678, 4)

    @pytest.mark.asyncio
    async def test_similarity_rounded_to_3dp(self):
        """Similarity is rounded to 3 decimal places."""
        pool = _make_mock_pool()
        results = [_make_search_result(similarity=0.876543)]

        with patch("doc_hub.mcp_server.search_docs", new=AsyncMock(return_value=results)):
            output = await _search_tool_impl(
                "test",
                corpus=None,
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        assert output[0]["similarity"] == round(0.876543, 3)

    @pytest.mark.asyncio
    async def test_empty_results_when_no_match(self):
        """Returns empty list when search_docs returns no results."""
        pool = _make_mock_pool()

        with patch("doc_hub.mcp_server.search_docs", new=AsyncMock(return_value=[])):
            output = await _search_tool_impl(
                "no match query",
                corpus=None,
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        assert output == []

    @pytest.mark.asyncio
    async def test_passes_corpus_filter_to_search_docs(self):
        """corpus parameter is forwarded to search_docs()."""
        pool = _make_mock_pool()
        mock_search = AsyncMock(return_value=[])

        with patch("doc_hub.mcp_server.search_docs", new=mock_search):
            await _search_tool_impl(
                "test",
                corpus="pydantic-ai",
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["corpus"] == "pydantic-ai"

    @pytest.mark.asyncio
    async def test_passes_categories_filter_to_search_docs(self):
        """categories parameter is forwarded to search_docs()."""
        pool = _make_mock_pool()
        mock_search = AsyncMock(return_value=[])

        with patch("doc_hub.mcp_server.search_docs", new=mock_search):
            await _search_tool_impl(
                "test",
                corpus=None,
                categories=["api", "guide"],
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["categories"] == ["api", "guide"]

    @pytest.mark.asyncio
    async def test_no_corpus_searches_all(self):
        """corpus=None means search all corpora (forwarded as None)."""
        pool = _make_mock_pool()
        mock_search = AsyncMock(return_value=[])

        with patch("doc_hub.mcp_server.search_docs", new=mock_search):
            await _search_tool_impl(
                "test",
                corpus=None,
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["corpus"] is None

    @pytest.mark.asyncio
    async def test_multiple_results_all_included(self):
        """All results from search_docs are returned, not just the first."""
        pool = _make_mock_pool()
        results = [
            _make_search_result(id=1, heading="First"),
            _make_search_result(id=2, heading="Second"),
            _make_search_result(id=3, heading="Third"),
        ]

        with patch("doc_hub.mcp_server.search_docs", new=AsyncMock(return_value=results)):
            output = await _search_tool_impl(
                "test",
                corpus=None,
                categories=None,
                limit=5,
                max_content_chars=800,
                pool=pool,
            )

        assert len(output) == 3
        assert output[0]["heading"] == "First"
        assert output[1]["heading"] == "Second"
        assert output[2]["heading"] == "Third"


# ---------------------------------------------------------------------------
# _list_corpora_impl
# ---------------------------------------------------------------------------


class TestListCorporaImpl:
    """Tests for the core list-corpora logic."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_corpora(self):
        """Returns [] when no corpora are registered."""
        pool = _make_mock_pool()

        with patch("doc_hub.mcp_server.db.list_corpora", new=AsyncMock(return_value=[])):
            output = await _list_corpora_impl(pool=pool)

        assert output == []

    @pytest.mark.asyncio
    async def test_returns_all_corpora(self):
        """Returns all corpora, including disabled ones."""
        pool = _make_mock_pool()
        corpora = [
            _make_corpus(slug="pydantic-ai", enabled=True),
            _make_corpus(slug="fastapi", enabled=False),
        ]

        with patch("doc_hub.mcp_server.db.list_corpora", new=AsyncMock(return_value=corpora)):
            output = await _list_corpora_impl(pool=pool)

        assert len(output) == 2

    @pytest.mark.asyncio
    async def test_calls_list_corpora_with_enabled_only_false(self):
        """list_corpora is called with enabled_only=False to include all corpora."""
        pool = _make_mock_pool()
        mock_list = AsyncMock(return_value=[])

        with patch("doc_hub.mcp_server.db.list_corpora", new=mock_list):
            await _list_corpora_impl(pool=pool)

        mock_list.assert_called_once_with(pool, enabled_only=False)

    @pytest.mark.asyncio
    async def test_result_has_expected_keys(self):
        """Result dicts contain slug, name, strategy, enabled, total_chunks,
        last_indexed_at."""
        pool = _make_mock_pool()
        corpora = [_make_corpus()]

        with patch("doc_hub.mcp_server.db.list_corpora", new=AsyncMock(return_value=corpora)):
            output = await _list_corpora_impl(pool=pool)

        r = output[0]
        assert "slug" in r
        assert "name" in r
        assert "strategy" in r
        assert "enabled" in r
        assert "total_chunks" in r
        assert "last_indexed_at" in r

    @pytest.mark.asyncio
    async def test_strategy_is_string_value(self):
        """strategy field is the plain string value (e.g. 'llms_txt')."""
        pool = _make_mock_pool()
        corpora = [_make_corpus(strategy="llms_txt")]

        with patch("doc_hub.mcp_server.db.list_corpora", new=AsyncMock(return_value=corpora)):
            output = await _list_corpora_impl(pool=pool)

        assert output[0]["strategy"] == "llms_txt"
        assert isinstance(output[0]["strategy"], str)

    @pytest.mark.asyncio
    async def test_disabled_corpus_included(self):
        """Disabled corpora are included in the list (enabled_only=False)."""
        pool = _make_mock_pool()
        disabled_corpus = _make_corpus(slug="disabled-corpus", enabled=False)
        corpora = [disabled_corpus]

        with patch("doc_hub.mcp_server.db.list_corpora", new=AsyncMock(return_value=corpora)):
            output = await _list_corpora_impl(pool=pool)

        assert len(output) == 1
        assert output[0]["slug"] == "disabled-corpus"
        assert output[0]["enabled"] is False

    @pytest.mark.asyncio
    async def test_total_chunks_and_last_indexed_at(self):
        """total_chunks and last_indexed_at are accurately reflected."""
        pool = _make_mock_pool()
        corpus = _make_corpus(total_chunks=999, last_indexed_at="2024-06-15T12:00:00+00:00")
        corpora = [corpus]

        with patch("doc_hub.mcp_server.db.list_corpora", new=AsyncMock(return_value=corpora)):
            output = await _list_corpora_impl(pool=pool)

        assert output[0]["total_chunks"] == 999
        assert output[0]["last_indexed_at"] == "2024-06-15T12:00:00+00:00"


# ---------------------------------------------------------------------------
# _add_corpus_impl
# ---------------------------------------------------------------------------


class TestAddCorpusImpl:
    """Tests for the core add-corpus logic."""

    @pytest.mark.asyncio
    async def test_registers_corpus_with_valid_strategy(self):
        """add_corpus creates a corpus row and returns registered status."""
        pool = _make_mock_pool()
        mock_upsert = AsyncMock()

        with (
            patch("doc_hub.mcp_server.db.upsert_corpus", new=mock_upsert),
            patch("doc_hub.discovery.get_registry") as mock_registry,
        ):
            mock_registry.return_value.list_fetchers.return_value = ["llms_txt"]
            mock_registry.return_value.list_parsers.return_value = ["markdown"]
            mock_registry.return_value.list_embedders.return_value = ["gemini"]
            result = await _add_corpus_impl(
                slug="fastapi",
                name="FastAPI",
                strategy="llms_txt",
                config={"url": "https://fastapi.tiangolo.com/llms.txt"},
                pool=pool,
            )

        assert result == {"status": "registered", "slug": "fastapi"}

    @pytest.mark.asyncio
    async def test_upsert_called_once(self):
        """db.upsert_corpus is called exactly once."""
        pool = _make_mock_pool()
        mock_upsert = AsyncMock()

        with (
            patch("doc_hub.mcp_server.db.upsert_corpus", new=mock_upsert),
            patch("doc_hub.discovery.get_registry") as mock_registry,
        ):
            mock_registry.return_value.list_fetchers.return_value = []
            mock_registry.return_value.list_parsers.return_value = []
            mock_registry.return_value.list_embedders.return_value = []
            await _add_corpus_impl(
                slug="langchain",
                name="LangChain",
                strategy="sitemap",
                config={"url": "https://python.langchain.com/sitemap.xml"},
                pool=pool,
            )

        mock_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_corpus_slug_passed_to_upsert(self):
        """The Corpus object passed to upsert has the correct slug."""
        pool = _make_mock_pool()
        captured = []

        async def capture_upsert(p, c):
            captured.append(c)

        with (
            patch("doc_hub.mcp_server.db.upsert_corpus", side_effect=capture_upsert),
            patch("doc_hub.discovery.get_registry") as mock_registry,
        ):
            mock_registry.return_value.list_fetchers.return_value = []
            mock_registry.return_value.list_parsers.return_value = []
            mock_registry.return_value.list_embedders.return_value = []
            await _add_corpus_impl(
                slug="my-corpus",
                name="My Corpus",
                strategy="local_dir",
                config={"path": "/data/docs"},
                pool=pool,
            )

        assert captured[0].slug == "my-corpus"

    @pytest.mark.asyncio
    async def test_corpus_strategy_passed_to_upsert(self):
        """The Corpus object passed to upsert has the correct fetch_strategy string."""
        pool = _make_mock_pool()
        captured = []

        async def capture_upsert(p, c):
            captured.append(c)

        with (
            patch("doc_hub.mcp_server.db.upsert_corpus", side_effect=capture_upsert),
            patch("doc_hub.discovery.get_registry") as mock_registry,
        ):
            mock_registry.return_value.list_fetchers.return_value = []
            mock_registry.return_value.list_parsers.return_value = []
            mock_registry.return_value.list_embedders.return_value = []
            await _add_corpus_impl(
                slug="test",
                name="Test",
                strategy="git_repo",
                config={"url": "https://github.com/example/repo"},
                pool=pool,
            )

        assert captured[0].fetch_strategy == "git_repo"

    @pytest.mark.asyncio
    async def test_parser_passed_to_upsert(self):
        """The Corpus object passed to upsert has the correct parser."""
        pool = _make_mock_pool()
        captured = []

        async def capture_upsert(p, c):
            captured.append(c)

        with (
            patch("doc_hub.mcp_server.db.upsert_corpus", side_effect=capture_upsert),
            patch("doc_hub.discovery.get_registry") as mock_registry,
        ):
            mock_registry.return_value.list_fetchers.return_value = []
            mock_registry.return_value.list_parsers.return_value = []
            mock_registry.return_value.list_embedders.return_value = []
            await _add_corpus_impl(
                slug="test",
                name="Test",
                strategy="llms_txt",
                config={},
                parser="markdown",
                pool=pool,
            )

        assert captured[0].parser == "markdown"

    @pytest.mark.asyncio
    async def test_embedder_passed_to_upsert(self):
        """The Corpus object passed to upsert has the correct embedder."""
        pool = _make_mock_pool()
        captured = []

        async def capture_upsert(p, c):
            captured.append(c)

        with (
            patch("doc_hub.mcp_server.db.upsert_corpus", side_effect=capture_upsert),
            patch("doc_hub.discovery.get_registry") as mock_registry,
        ):
            mock_registry.return_value.list_fetchers.return_value = []
            mock_registry.return_value.list_parsers.return_value = []
            mock_registry.return_value.list_embedders.return_value = []
            await _add_corpus_impl(
                slug="test",
                name="Test",
                strategy="llms_txt",
                config={},
                embedder="gemini",
                pool=pool,
            )

        assert captured[0].embedder == "gemini"

    @pytest.mark.asyncio
    async def test_default_parser_is_markdown(self):
        """Default parser is 'markdown'."""
        pool = _make_mock_pool()
        captured = []

        async def capture_upsert(p, c):
            captured.append(c)

        with (
            patch("doc_hub.mcp_server.db.upsert_corpus", side_effect=capture_upsert),
            patch("doc_hub.discovery.get_registry") as mock_registry,
        ):
            mock_registry.return_value.list_fetchers.return_value = []
            mock_registry.return_value.list_parsers.return_value = []
            mock_registry.return_value.list_embedders.return_value = []
            await _add_corpus_impl(
                slug="test",
                name="Test",
                strategy="llms_txt",
                config={},
                pool=pool,
            )

        assert captured[0].parser == "markdown"

    @pytest.mark.asyncio
    async def test_default_embedder_is_gemini(self):
        """Default embedder is 'gemini'."""
        pool = _make_mock_pool()
        captured = []

        async def capture_upsert(p, c):
            captured.append(c)

        with (
            patch("doc_hub.mcp_server.db.upsert_corpus", side_effect=capture_upsert),
            patch("doc_hub.discovery.get_registry") as mock_registry,
        ):
            mock_registry.return_value.list_fetchers.return_value = []
            mock_registry.return_value.list_parsers.return_value = []
            mock_registry.return_value.list_embedders.return_value = []
            await _add_corpus_impl(
                slug="test",
                name="Test",
                strategy="llms_txt",
                config={},
                pool=pool,
            )

        assert captured[0].embedder == "gemini"

    @pytest.mark.asyncio
    async def test_any_strategy_string_accepted(self):
        """Any strategy string is accepted — validation deferred to pipeline time."""
        pool = _make_mock_pool()
        mock_upsert = AsyncMock()

        with (
            patch("doc_hub.mcp_server.db.upsert_corpus", new=mock_upsert),
            patch("doc_hub.discovery.get_registry") as mock_registry,
        ):
            mock_registry.return_value.list_fetchers.return_value = []
            mock_registry.return_value.list_parsers.return_value = []
            mock_registry.return_value.list_embedders.return_value = []
            result = await _add_corpus_impl(
                slug="test",
                name="Test",
                strategy="some_custom_plugin",
                config={},
                pool=pool,
            )

        assert result == {"status": "registered", "slug": "test"}
        mock_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_builtin_strategies_accepted(self):
        """All four built-in strategy values are accepted without error."""
        pool = _make_mock_pool()
        valid_strategies = ["llms_txt", "sitemap", "local_dir", "git_repo"]

        for strategy in valid_strategies:
            mock_upsert = AsyncMock()
            with (
                patch("doc_hub.mcp_server.db.upsert_corpus", new=mock_upsert),
                patch("doc_hub.discovery.get_registry") as mock_registry,
            ):
                mock_registry.return_value.list_fetchers.return_value = [strategy]
                mock_registry.return_value.list_parsers.return_value = ["markdown"]
                mock_registry.return_value.list_embedders.return_value = ["gemini"]
                result = await _add_corpus_impl(
                    slug=f"test-{strategy}",
                    name="Test",
                    strategy=strategy,
                    config={},
                    pool=pool,
                )
            assert "error" not in result, f"Strategy {strategy!r} should be valid"
            assert result["status"] == "registered"

    @pytest.mark.asyncio
    async def test_slug_in_response(self):
        """Response includes the slug of the registered corpus."""
        pool = _make_mock_pool()

        with (
            patch("doc_hub.mcp_server.db.upsert_corpus", new=AsyncMock()),
            patch("doc_hub.discovery.get_registry") as mock_registry,
        ):
            mock_registry.return_value.list_fetchers.return_value = []
            mock_registry.return_value.list_parsers.return_value = []
            mock_registry.return_value.list_embedders.return_value = []
            result = await _add_corpus_impl(
                slug="my-special-corpus",
                name="My Special Corpus",
                strategy="llms_txt",
                config={"url": "https://example.com/llms.txt"},
                pool=pool,
            )

        assert result["slug"] == "my-special-corpus"


# ---------------------------------------------------------------------------
# _refresh_corpus_impl
# ---------------------------------------------------------------------------


class TestRefreshCorpusImpl:
    """Tests for the core refresh-corpus logic."""

    def _make_index_result(
        self,
        inserted: int = 10,
        updated: int = 5,
        deleted: int = 0,
        total: int = 100,
    ):
        """Build a fake IndexResult."""
        from doc_hub.index import IndexResult
        return IndexResult(inserted=inserted, updated=updated, deleted=deleted, total=total)

    @pytest.mark.asyncio
    async def test_returns_error_when_corpus_not_found(self):
        """Returns error dict when corpus slug does not exist."""
        pool = _make_mock_pool()

        with patch("doc_hub.mcp_server.db.get_corpus", new=AsyncMock(return_value=None)):
            result = await _refresh_corpus_impl(
                slug="nonexistent",
                full=False,
                pool=pool,
            )

        assert "error" in result
        assert "nonexistent" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_corpus_disabled(self):
        """Returns error dict when corpus is disabled."""
        pool = _make_mock_pool()
        disabled = _make_corpus(slug="disabled", enabled=False)

        with patch("doc_hub.mcp_server.db.get_corpus", new=AsyncMock(return_value=disabled)):
            result = await _refresh_corpus_impl(
                slug="disabled",
                full=False,
                pool=pool,
            )

        assert "error" in result
        assert "disabled" in result["error"]

    @pytest.mark.asyncio
    async def test_runs_pipeline_for_enabled_corpus(self):
        """run_pipeline is called for an enabled corpus."""
        pool = _make_mock_pool()
        corpus = _make_corpus(slug="pydantic-ai", enabled=True)
        index_result = self._make_index_result()
        mock_pipeline = AsyncMock(return_value=index_result)

        with (
            patch("doc_hub.mcp_server.db.get_corpus", new=AsyncMock(return_value=corpus)),
            patch("doc_hub.mcp_server.run_pipeline", new=mock_pipeline),
        ):
            result = await _refresh_corpus_impl(
                slug="pydantic-ai",
                full=False,
                pool=pool,
            )

        mock_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_complete_status_on_success(self):
        """Returns status=complete with pipeline stats on success."""
        pool = _make_mock_pool()
        corpus = _make_corpus(slug="pydantic-ai", enabled=True)
        index_result = self._make_index_result(inserted=10, updated=5, deleted=2, total=100)

        with (
            patch("doc_hub.mcp_server.db.get_corpus", new=AsyncMock(return_value=corpus)),
            patch("doc_hub.mcp_server.run_pipeline", new=AsyncMock(return_value=index_result)),
        ):
            result = await _refresh_corpus_impl(
                slug="pydantic-ai",
                full=False,
                pool=pool,
            )

        assert result["status"] == "complete"
        assert result["slug"] == "pydantic-ai"
        assert result["chunks_indexed"] == 100
        assert result["inserted"] == 10
        assert result["updated"] == 5
        assert result["deleted"] == 2

    @pytest.mark.asyncio
    async def test_passes_full_flag_to_run_pipeline(self):
        """The full= flag is forwarded to run_pipeline."""
        pool = _make_mock_pool()
        corpus = _make_corpus(enabled=True)
        index_result = self._make_index_result()
        captured_kwargs = {}

        async def capture_pipeline(c, **kwargs):
            captured_kwargs.update(kwargs)
            return index_result

        with (
            patch("doc_hub.mcp_server.db.get_corpus", new=AsyncMock(return_value=corpus)),
            patch("doc_hub.mcp_server.run_pipeline", side_effect=capture_pipeline),
        ):
            await _refresh_corpus_impl(
                slug="pydantic-ai",
                full=True,
                pool=pool,
            )

        assert captured_kwargs.get("full") is True

    @pytest.mark.asyncio
    async def test_passes_pool_to_run_pipeline(self):
        """The pool is forwarded to run_pipeline."""
        pool = _make_mock_pool()
        corpus = _make_corpus(enabled=True)
        index_result = self._make_index_result()
        captured_kwargs = {}

        async def capture_pipeline(c, **kwargs):
            captured_kwargs.update(kwargs)
            return index_result

        with (
            patch("doc_hub.mcp_server.db.get_corpus", new=AsyncMock(return_value=corpus)),
            patch("doc_hub.mcp_server.run_pipeline", side_effect=capture_pipeline),
        ):
            await _refresh_corpus_impl(
                slug="pydantic-ai",
                full=False,
                pool=pool,
            )

        assert captured_kwargs.get("pool") is pool

    @pytest.mark.asyncio
    async def test_no_gemini_client_in_refresh(self):
        """_refresh_corpus_impl does not accept or pass gemini_client."""
        import inspect
        sig = inspect.signature(_refresh_corpus_impl)
        assert "gemini_client" not in sig.parameters

    @pytest.mark.asyncio
    async def test_run_pipeline_not_called_for_missing_corpus(self):
        """run_pipeline is NOT called when corpus is not found."""
        pool = _make_mock_pool()
        mock_pipeline = AsyncMock()

        with (
            patch("doc_hub.mcp_server.db.get_corpus", new=AsyncMock(return_value=None)),
            patch("doc_hub.mcp_server.run_pipeline", new=mock_pipeline),
        ):
            await _refresh_corpus_impl(
                slug="missing",
                full=False,
                pool=pool,
            )

        mock_pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_pipeline_not_called_for_disabled_corpus(self):
        """run_pipeline is NOT called when corpus is disabled."""
        pool = _make_mock_pool()
        disabled = _make_corpus(enabled=False)
        mock_pipeline = AsyncMock()

        with (
            patch("doc_hub.mcp_server.db.get_corpus", new=AsyncMock(return_value=disabled)),
            patch("doc_hub.mcp_server.run_pipeline", new=mock_pipeline),
        ):
            await _refresh_corpus_impl(
                slug="pydantic-ai",
                full=False,
                pool=pool,
            )

        mock_pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_none_pipeline_result_gracefully(self):
        """Handles None from run_pipeline (stage-only run) without crashing."""
        pool = _make_mock_pool()
        corpus = _make_corpus(enabled=True)

        with (
            patch("doc_hub.mcp_server.db.get_corpus", new=AsyncMock(return_value=corpus)),
            patch("doc_hub.mcp_server.run_pipeline", new=AsyncMock(return_value=None)),
        ):
            result = await _refresh_corpus_impl(
                slug="pydantic-ai",
                full=False,
                pool=pool,
            )

        assert result["status"] == "complete"
        assert result["chunks_indexed"] == 0
        assert result["inserted"] == 0
        assert result["updated"] == 0
        assert result["deleted"] == 0


# ---------------------------------------------------------------------------
# _browse_corpus_impl
# ---------------------------------------------------------------------------


class TestBrowseCorpusImpl:
    """Tests for the core browse-corpus logic."""

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        """Returns a list of dicts from get_document_tree."""
        pool = _make_mock_pool()
        tree = [{"doc_path": "guide/intro", "title": "Intro"}]

        with patch("doc_hub.documents.get_document_tree", new=AsyncMock(return_value=tree)):
            result = await _browse_corpus_impl(corpus="pydantic-ai", path=None, depth=None, pool=pool)

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)

    @pytest.mark.asyncio
    async def test_passes_path_filter(self):
        """Forwards path to get_document_tree."""
        pool = _make_mock_pool()
        mock_tree = AsyncMock(return_value=[])

        with patch("doc_hub.documents.get_document_tree", new=mock_tree):
            await _browse_corpus_impl(corpus="pydantic-ai", path="guide", depth=None, pool=pool)

        mock_tree.assert_awaited_once_with(pool, "pydantic-ai", path="guide", max_depth=None)

    @pytest.mark.asyncio
    async def test_passes_depth_filter(self):
        """Forwards depth to get_document_tree as max_depth."""
        pool = _make_mock_pool()
        mock_tree = AsyncMock(return_value=[])

        with patch("doc_hub.documents.get_document_tree", new=mock_tree):
            await _browse_corpus_impl(corpus="pydantic-ai", path=None, depth=2, pool=pool)

        mock_tree.assert_awaited_once_with(pool, "pydantic-ai", path=None, max_depth=2)

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_corpus(self):
        """Returns [] unchanged for an unknown corpus."""
        pool = _make_mock_pool()

        with patch("doc_hub.documents.get_document_tree", new=AsyncMock(return_value=[])):
            result = await _browse_corpus_impl(corpus="missing", path=None, depth=None, pool=pool)

        assert result == []


# ---------------------------------------------------------------------------
# _get_document_impl
# ---------------------------------------------------------------------------


class TestGetDocumentImpl:
    """Tests for the core get-document logic."""

    def _make_chunk(
        self,
        *,
        heading: str = "Overview",
        heading_level: int = 2,
        section_path: str = "Overview",
        content: str = "Section content",
        source_url: str = "https://example.com/docs/intro",
        char_count: int | None = None,
    ) -> dict:
        return {
            "id": 1,
            "heading": heading,
            "heading_level": heading_level,
            "section_path": section_path,
            "char_count": len(content) if char_count is None else char_count,
            "source_file": "guide__intro.md",
            "source_url": source_url,
            "content": content,
            "start_line": 1,
            "end_line": 10,
            "category": "guide",
        }

    @pytest.mark.asyncio
    async def test_returns_full_mode(self):
        """Returns full mode with document content."""
        pool = _make_mock_pool()
        chunks = [self._make_chunk(content="Short doc")]

        with patch("doc_hub.documents.get_document_chunks", new=AsyncMock(return_value=chunks)):
            result = await _get_document_impl(
                corpus="pydantic-ai",
                doc_path="guide/intro",
                pool=pool,
            )

        assert result["mode"] == "full"
        assert result["content"] == "Short doc"

    @pytest.mark.asyncio
    async def test_returns_error_for_missing_doc(self):
        """Returns an error dict when no chunks are found."""
        pool = _make_mock_pool()

        with patch("doc_hub.documents.get_document_chunks", new=AsyncMock(return_value=[])):
            result = await _get_document_impl(
                corpus="pydantic-ai",
                doc_path="missing/doc",
                pool=pool,
            )

        assert result == {"error": "Document 'missing/doc' not found in corpus 'pydantic-ai'"}

    @pytest.mark.asyncio
    async def test_content_is_concatenated_chunks(self):
        """Concatenates chunk content with blank lines."""
        pool = _make_mock_pool()
        chunks = [
            self._make_chunk(content="First chunk"),
            self._make_chunk(content="Second chunk"),
        ]

        with patch("doc_hub.documents.get_document_chunks", new=AsyncMock(return_value=chunks)):
            result = await _get_document_impl(
                corpus="pydantic-ai",
                doc_path="guide/intro",
                pool=pool,
            )

        assert result["content"] == "First chunk\n\nSecond chunk"

    @pytest.mark.asyncio
    async def test_title_from_h1_chunk(self):
        """Uses the first H1 heading as the document title."""
        pool = _make_mock_pool()
        chunks = [
            self._make_chunk(heading="Subsection", heading_level=2),
            self._make_chunk(heading="Real Title", heading_level=1),
        ]

        with patch("doc_hub.documents.get_document_chunks", new=AsyncMock(return_value=chunks)):
            result = await _get_document_impl(
                corpus="pydantic-ai",
                doc_path="guide/intro",
                pool=pool,
            )

        assert result["title"] == "Real Title"

    @pytest.mark.asyncio
    async def test_title_fallback_to_doc_path(self):
        """Falls back to doc_path when no H1 chunk exists."""
        pool = _make_mock_pool()
        chunks = [self._make_chunk(heading="Overview", heading_level=2)]

        with patch("doc_hub.documents.get_document_chunks", new=AsyncMock(return_value=chunks)):
            result = await _get_document_impl(
                corpus="pydantic-ai",
                doc_path="guide/intro",
                pool=pool,
            )

        assert result["title"] == "guide/intro"

    @pytest.mark.asyncio
    async def test_source_url_from_first_chunk(self):
        """Uses source_url from the first chunk."""
        pool = _make_mock_pool()
        chunks = [
            self._make_chunk(source_url="https://example.com/first"),
            self._make_chunk(source_url="https://example.com/second"),
        ]

        with patch("doc_hub.documents.get_document_chunks", new=AsyncMock(return_value=chunks)):
            result = await _get_document_impl(
                corpus="pydantic-ai",
                doc_path="guide/intro",
                pool=pool,
            )

        assert result["source_url"] == "https://example.com/first"

    @pytest.mark.asyncio
    async def test_total_chars_computed_from_chunk_char_count(self):
        """Computes total_chars from chunk char_count values, not content length."""
        pool = _make_mock_pool()
        chunks = [
            self._make_chunk(content="abc", char_count=30),
            self._make_chunk(content="defgh", char_count=50),
        ]

        with patch("doc_hub.documents.get_document_chunks", new=AsyncMock(return_value=chunks)):
            result = await _get_document_impl(
                corpus="pydantic-ai",
                doc_path="guide/intro",
                pool=pool,
            )

        assert result["total_chars"] == 80
        assert result["section_count"] == 2


# ---------------------------------------------------------------------------
# AppState
# ---------------------------------------------------------------------------


class TestAppState:
    """Tests for the AppState dataclass."""

    def test_appstate_has_pool_only(self):
        """AppState has only pool — no gemini_client."""
        import dataclasses
        pool = _make_mock_pool()
        state = AppState(pool=pool)
        assert state.pool is pool
        fields = {f.name for f in dataclasses.fields(AppState)}
        assert "gemini_client" not in fields
        assert "pool" in fields

    def test_appstate_no_gemini_client_field(self):
        """AppState does not have a gemini_client attribute."""
        pool = _make_mock_pool()
        state = AppState(pool=pool)
        assert not hasattr(state, "gemini_client")


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


class TestMainEntryPoint:
    """Tests for the main() CLI entry point."""

    def test_build_mcp_parser_accepts_existing_parser(self):
        from doc_hub.mcp_server import build_mcp_parser

        parser = argparse.ArgumentParser()
        built = build_mcp_parser(parser)
        assert built is parser
        args = parser.parse_args([])
        assert args.transport == "stdio"

    def test_handle_mcp_args_calls_server_run_with_stdio(self):
        from doc_hub.mcp_server import handle_mcp_args

        args = argparse.Namespace(transport="stdio", host="127.0.0.1", port=8340)
        with patch.object(server, "run") as mock_run:
            handle_mcp_args(args)
            mock_run.assert_called_once_with(transport="stdio")

    def test_main_calls_server_run_with_stdio(self):
        """main() calls server.run(transport='stdio')."""
        from doc_hub.mcp_server import main

        with patch.object(server, "run") as mock_run:
            main([])  # pass empty argv to avoid reading pytest's sys.argv
            mock_run.assert_called_once_with(transport="stdio")
