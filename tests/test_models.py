"""Tests for doc_hub.models — Corpus dataclass.

All tests here are pure unit tests (no DB or network required).
FetchStrategy enum was removed in M5; fetch_strategy is now a plain str.
embedding_model and embedding_dimensions were removed in M7; the Corpus
no longer tracks embedder model details (owned by the embedder plugin).
"""

from __future__ import annotations

import pytest

from doc_hub.models import Corpus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**overrides):
    """Return a minimal valid corpus row dict."""
    row = {
        "slug": "pydantic-ai",
        "name": "Pydantic AI Docs",
        "fetch_strategy": "llms_txt",
        "fetch_config": {"url": "https://ai.pydantic.dev/llms.txt"},
        "enabled": True,
        "last_indexed_at": None,
        "total_chunks": 0,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Corpus.from_row tests — plain dict (all keys present)
# ---------------------------------------------------------------------------


def test_corpus_from_row_basic():
    """Corpus.from_row constructs a Corpus from a full dict."""
    corpus = Corpus.from_row(_make_row())
    assert corpus.slug == "pydantic-ai"
    assert corpus.name == "Pydantic AI Docs"
    assert corpus.fetch_strategy == "llms_txt"
    assert isinstance(corpus.fetch_strategy, str)
    assert corpus.fetch_config == {"url": "https://ai.pydantic.dev/llms.txt"}
    assert corpus.enabled is True
    assert corpus.last_indexed_at is None
    assert corpus.total_chunks == 0


def test_corpus_from_row_all_strategy_strings():
    """Corpus.from_row handles all expected strategy string values."""
    for strategy in ["llms_txt", "sitemap", "local_dir", "git_repo"]:
        corpus = Corpus.from_row(_make_row(fetch_strategy=strategy))
        assert corpus.fetch_strategy == strategy
        assert isinstance(corpus.fetch_strategy, str)


def test_corpus_from_row_with_last_indexed_at():
    """Corpus.from_row preserves non-None last_indexed_at."""
    ts = "2024-01-15T12:00:00+00:00"
    corpus = Corpus.from_row(_make_row(last_indexed_at=ts))
    assert corpus.last_indexed_at == ts


def test_corpus_from_row_with_total_chunks():
    """Corpus.from_row preserves total_chunks."""
    corpus = Corpus.from_row(_make_row(total_chunks=1234))
    assert corpus.total_chunks == 1234


def test_corpus_from_row_disabled():
    """Corpus.from_row preserves enabled=False."""
    corpus = Corpus.from_row(_make_row(enabled=False))
    assert corpus.enabled is False


def test_corpus_from_row_silently_ignores_embedding_model():
    """Corpus.from_row ignores embedding_model column if present (legacy deployments)."""
    # Some deployments may still have this column from before M7
    corpus = Corpus.from_row(_make_row(embedding_model="gemini-embedding-001"))
    # The field should NOT exist on the Corpus object
    assert not hasattr(corpus, "embedding_model")


def test_corpus_from_row_null_total_chunks_falls_back():
    """Corpus.from_row treats None total_chunks as 0."""
    corpus = Corpus.from_row(_make_row(total_chunks=None))
    assert corpus.total_chunks == 0


def test_corpus_from_row_parser_and_embedder_defaults():
    """Corpus.from_row uses default parser/embedder when not in row."""
    corpus = Corpus.from_row(_make_row())
    assert corpus.parser == "markdown"
    assert corpus.embedder == "gemini"


def test_corpus_from_row_parser_and_embedder_explicit():
    """Corpus.from_row uses explicit parser/embedder from row."""
    corpus = Corpus.from_row(_make_row(parser="custom_parser", embedder="openai"))
    assert corpus.parser == "custom_parser"
    assert corpus.embedder == "openai"


# ---------------------------------------------------------------------------
# Corpus.from_row tests — asyncpg-like Record (no .get())
# ---------------------------------------------------------------------------


class _FakeRecord:
    """Minimal asyncpg Record stand-in: supports key access but not .get()."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        return self._data.keys()

    # Intentionally NO .get() method to simulate asyncpg Record.


def test_corpus_from_row_asyncpg_record():
    """Corpus.from_row works with an asyncpg-like Record object (no .get())."""
    row = _FakeRecord(
        {
            "slug": "test-corpus",
            "name": "Test Corpus",
            "fetch_strategy": "sitemap",
            "fetch_config": {"sitemap_url": "https://example.com/sitemap.xml"},
            "parser": "markdown",
            "embedder": "gemini",
            "enabled": True,
            "last_indexed_at": None,
            "total_chunks": 42,
        }
    )
    corpus = Corpus.from_row(row)
    assert corpus.slug == "test-corpus"
    assert corpus.fetch_strategy == "sitemap"
    assert isinstance(corpus.fetch_strategy, str)
    assert corpus.total_chunks == 42
    assert corpus.parser == "markdown"
    assert corpus.embedder == "gemini"


def test_corpus_from_row_asyncpg_record_no_embedding_model():
    """Corpus.from_row works correctly without embedding_model column (Record path)."""
    row = _FakeRecord(
        {
            "slug": "x",
            "name": "X",
            "fetch_strategy": "llms_txt",
            "fetch_config": {},
            "parser": "markdown",
            "embedder": "gemini",
            "enabled": True,
            "last_indexed_at": None,
            "total_chunks": None,
        }
    )
    corpus = Corpus.from_row(row)
    assert corpus.total_chunks == 0
    assert not hasattr(corpus, "embedding_model")
    assert not hasattr(corpus, "embedding_dimensions")


# ---------------------------------------------------------------------------
# embedding_model / embedding_dimensions removal verification (M7)
# ---------------------------------------------------------------------------


def test_embedding_model_field_removed():
    """Corpus no longer has an embedding_model field (removed in M7)."""
    corpus = Corpus(
        slug="test",
        name="Test",
        fetch_strategy="llms_txt",
        fetch_config={},
    )
    assert not hasattr(corpus, "embedding_model")


def test_embedding_dimensions_property_removed():
    """Corpus no longer has an embedding_dimensions property (removed in M7)."""
    corpus = Corpus(
        slug="test",
        name="Test",
        fetch_strategy="llms_txt",
        fetch_config={},
    )
    assert not hasattr(corpus, "embedding_dimensions")


# ---------------------------------------------------------------------------
# FetchStrategy removal verification
# ---------------------------------------------------------------------------


def test_fetch_strategy_enum_removed():
    """FetchStrategy no longer exists in doc_hub.models."""
    import doc_hub.models
    assert not hasattr(doc_hub.models, "FetchStrategy"), "FetchStrategy should be removed in M5"


def test_fetch_strategy_is_plain_str():
    """Corpus.fetch_strategy is a plain str, not an enum."""
    corpus = Corpus(
        slug="test",
        name="Test",
        fetch_strategy="llms_txt",
        fetch_config={},
    )
    assert corpus.fetch_strategy == "llms_txt"
    assert type(corpus.fetch_strategy) is str
