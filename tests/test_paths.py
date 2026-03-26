"""Tests for doc_hub.paths — XDG-compliant data directory resolution.

All tests here are pure unit tests (no DB, network, or filesystem writes
required beyond tmpdir fixtures).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from doc_hub.models import Corpus
from doc_hub import paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_corpus(slug: str = "pydantic-ai") -> Corpus:
    """Return a minimal Corpus with the given slug."""
    return Corpus(
        slug=slug,
        name="Test Corpus",
        fetch_strategy="llms_txt",
        fetch_config={"url": "https://example.com/llms.txt"},
    )


# ---------------------------------------------------------------------------
# _find_repo_root is gone
# ---------------------------------------------------------------------------


def test_find_repo_root_removed():
    """_find_repo_root must not exist in paths.py (monorepo coupling removed)."""
    assert not hasattr(paths, "_find_repo_root"), (
        "_find_repo_root still exists in paths.py — monorepo coupling not removed"
    )


# ---------------------------------------------------------------------------
# data_root() tests
# ---------------------------------------------------------------------------


def test_data_root_uses_doc_hub_data_dir(tmp_path, monkeypatch):
    """data_root() returns DOC_HUB_DATA_DIR when set."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = paths.data_root()
    assert result == tmp_path.resolve()


def test_data_root_env_var_overrides_xdg(tmp_path, monkeypatch):
    """DOC_HUB_DATA_DIR takes priority over XDG_DATA_HOME."""
    custom = tmp_path / "custom_data"
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(custom))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    result = paths.data_root()
    assert result == custom.resolve()


def test_data_root_respects_xdg_data_home(tmp_path, monkeypatch):
    """data_root() uses XDG_DATA_HOME/doc-hub when XDG_DATA_HOME is set."""
    monkeypatch.delenv("DOC_HUB_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = paths.data_root()
    assert result == (tmp_path / "doc-hub").resolve()


def test_data_root_xdg_default(monkeypatch):
    """Default data_root() returns ~/.local/share/doc-hub when no env vars are set."""
    monkeypatch.delenv("DOC_HUB_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = paths.data_root()
    expected = Path.home() / ".local" / "share" / "doc-hub"
    assert result == expected


def test_data_root_default_ends_with_doc_hub(monkeypatch):
    """Default data_root() ends with 'doc-hub'."""
    monkeypatch.delenv("DOC_HUB_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = paths.data_root()
    assert result.name == "doc-hub", (
        f"Expected data_root() to end with 'doc-hub', got {result!r}"
    )


def test_data_root_default_is_not_in_venv(monkeypatch):
    """Default data_root() must NOT resolve to a path inside a .venv directory."""
    monkeypatch.delenv("DOC_HUB_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = paths.data_root()
    assert ".venv" not in result.parts, (
        f"data_root() resolved to {result!r}, which is inside a .venv"
    )


def test_data_root_returns_path_object(monkeypatch):
    """data_root() always returns a Path object."""
    monkeypatch.delenv("DOC_HUB_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = paths.data_root()
    assert isinstance(result, Path)


def test_data_root_env_var_expanduser(tmp_path, monkeypatch):
    """data_root() expands ~ in DOC_HUB_DATA_DIR."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", "~/some/path")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    result = paths.data_root()
    assert not str(result).startswith("~"), "~ was not expanded"


# ---------------------------------------------------------------------------
# plugins_dir() tests
# ---------------------------------------------------------------------------


def test_plugins_dir_returns_data_root_plugins(tmp_path, monkeypatch):
    """plugins_dir() returns {data_root}/plugins/."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    result = paths.plugins_dir()
    assert result == paths.data_root() / "plugins"


def test_plugins_dir_name(tmp_path, monkeypatch):
    """plugins_dir() ends with 'plugins'."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    assert paths.plugins_dir().name == "plugins"


# ---------------------------------------------------------------------------
# corpus_dir() tests — Corpus objects
# ---------------------------------------------------------------------------


def test_corpus_dir_uses_slug(tmp_path, monkeypatch):
    """corpus_dir() appends the corpus slug to the data root."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("pydantic-ai")
    result = paths.corpus_dir(corpus)
    assert result == tmp_path.resolve() / "pydantic-ai"


def test_corpus_dir_different_slugs(tmp_path, monkeypatch):
    """corpus_dir() returns different paths for different slugs."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    c1 = _make_corpus("fastapi")
    c2 = _make_corpus("langchain")
    assert paths.corpus_dir(c1) != paths.corpus_dir(c2)
    assert paths.corpus_dir(c1) == tmp_path.resolve() / "fastapi"
    assert paths.corpus_dir(c2) == tmp_path.resolve() / "langchain"


# ---------------------------------------------------------------------------
# corpus_dir() tests — plain string slugs
# ---------------------------------------------------------------------------


def test_corpus_dir_accepts_string_slug(tmp_path, monkeypatch):
    """corpus_dir() accepts a plain string slug."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    result = paths.corpus_dir("test-corpus")
    assert result.name == "test-corpus"


def test_corpus_dir_string_matches_corpus_object(tmp_path, monkeypatch):
    """corpus_dir('slug') == corpus_dir(Corpus(slug=...))."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("pydantic-ai")
    assert paths.corpus_dir("pydantic-ai") == paths.corpus_dir(corpus)


# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------


def test_corpus_dir_rejects_empty_slug(tmp_path, monkeypatch):
    """corpus_dir() raises ValueError for an empty slug."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="Invalid corpus slug"):
        paths.corpus_dir("")


def test_corpus_dir_rejects_path_traversal(tmp_path, monkeypatch):
    """corpus_dir() raises ValueError for path-traversal slugs."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="Invalid corpus slug"):
        paths.corpus_dir("../etc")


def test_corpus_dir_rejects_slash_in_slug(tmp_path, monkeypatch):
    """corpus_dir() raises ValueError for slugs containing '/'."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="Invalid corpus slug"):
        paths.corpus_dir("foo/bar")


def test_corpus_dir_rejects_dotfile_slug(tmp_path, monkeypatch):
    """corpus_dir() raises ValueError for slugs starting with '.'."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="Invalid corpus slug"):
        paths.corpus_dir(".hidden")


# ---------------------------------------------------------------------------
# raw_dir() tests
# ---------------------------------------------------------------------------


def test_raw_dir_structure(tmp_path, monkeypatch):
    """raw_dir() returns {data_root}/{slug}/raw/."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("pydantic-ai")
    result = paths.raw_dir(corpus)
    assert result == tmp_path.resolve() / "pydantic-ai" / "raw"


def test_raw_dir_is_child_of_corpus_dir(tmp_path, monkeypatch):
    """raw_dir() is a direct child of corpus_dir()."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("mylib")
    assert paths.raw_dir(corpus).parent == paths.corpus_dir(corpus)


def test_raw_dir_accepts_string(tmp_path, monkeypatch):
    """raw_dir() accepts a plain string slug."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    assert paths.raw_dir("mylib").name == "raw"


# ---------------------------------------------------------------------------
# chunks_dir() tests
# ---------------------------------------------------------------------------


def test_chunks_dir_structure(tmp_path, monkeypatch):
    """chunks_dir() returns {data_root}/{slug}/chunks/."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("pydantic-ai")
    result = paths.chunks_dir(corpus)
    assert result == tmp_path.resolve() / "pydantic-ai" / "chunks"


def test_chunks_dir_is_child_of_corpus_dir(tmp_path, monkeypatch):
    """chunks_dir() is a direct child of corpus_dir()."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("mylib")
    assert paths.chunks_dir(corpus).parent == paths.corpus_dir(corpus)


def test_chunks_dir_accepts_string(tmp_path, monkeypatch):
    """chunks_dir() accepts a plain string slug."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    assert paths.chunks_dir("mylib").name == "chunks"


# ---------------------------------------------------------------------------
# manifest_path() tests
# ---------------------------------------------------------------------------


def test_manifest_path_structure(tmp_path, monkeypatch):
    """manifest_path() returns {data_root}/{slug}/raw/manifest.json."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("pydantic-ai")
    result = paths.manifest_path(corpus)
    assert result == tmp_path.resolve() / "pydantic-ai" / "raw" / "manifest.json"


def test_manifest_path_is_json(tmp_path, monkeypatch):
    """manifest_path() has a .json suffix."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("mylib")
    assert paths.manifest_path(corpus).suffix == ".json"


def test_manifest_path_accepts_string(tmp_path, monkeypatch):
    """manifest_path() accepts a plain string slug."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    assert paths.manifest_path("mylib").name == "manifest.json"


# ---------------------------------------------------------------------------
# embedded_chunks_path() tests
# ---------------------------------------------------------------------------


def test_embedded_chunks_path_structure(tmp_path, monkeypatch):
    """embedded_chunks_path() returns {data_root}/{slug}/chunks/embedded_chunks.jsonl."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("pydantic-ai")
    result = paths.embedded_chunks_path(corpus)
    assert result == tmp_path.resolve() / "pydantic-ai" / "chunks" / "embedded_chunks.jsonl"


def test_embedded_chunks_path_is_child_of_chunks_dir(tmp_path, monkeypatch):
    """embedded_chunks_path() lives inside chunks_dir()."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("mylib")
    assert paths.embedded_chunks_path(corpus).parent == paths.chunks_dir(corpus)


def test_embedded_chunks_path_accepts_string(tmp_path, monkeypatch):
    """embedded_chunks_path() accepts a plain string slug."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    assert paths.embedded_chunks_path("mylib").name == "embedded_chunks.jsonl"


# ---------------------------------------------------------------------------
# embeddings_cache_path() tests
# ---------------------------------------------------------------------------


def test_embeddings_cache_path_structure(tmp_path, monkeypatch):
    """embeddings_cache_path() returns {data_root}/{slug}/chunks/embeddings_cache.jsonl."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("pydantic-ai")
    result = paths.embeddings_cache_path(corpus)
    assert result == tmp_path.resolve() / "pydantic-ai" / "chunks" / "embeddings_cache.jsonl"


def test_embeddings_cache_path_is_child_of_chunks_dir(tmp_path, monkeypatch):
    """embeddings_cache_path() lives inside chunks_dir()."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("mylib")
    assert paths.embeddings_cache_path(corpus).parent == paths.chunks_dir(corpus)


def test_embeddings_cache_path_accepts_string(tmp_path, monkeypatch):
    """embeddings_cache_path() accepts a plain string slug."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    assert paths.embeddings_cache_path("mylib").name == "embeddings_cache.jsonl"


# ---------------------------------------------------------------------------
# Cross-corpus isolation
# ---------------------------------------------------------------------------


def test_corpora_have_independent_raw_dirs(tmp_path, monkeypatch):
    """Two different corpora must have completely independent raw_dir() paths."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    c1 = _make_corpus("pydantic-ai")
    c2 = _make_corpus("fastapi")
    assert paths.raw_dir(c1) != paths.raw_dir(c2)
    assert not str(paths.raw_dir(c1)).startswith(str(paths.raw_dir(c2)))
    assert not str(paths.raw_dir(c2)).startswith(str(paths.raw_dir(c1)))


def test_corpora_have_independent_chunks_dirs(tmp_path, monkeypatch):
    """Two different corpora must have completely independent chunks_dir() paths."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    c1 = _make_corpus("pydantic-ai")
    c2 = _make_corpus("langchain")
    assert paths.chunks_dir(c1) != paths.chunks_dir(c2)


# ---------------------------------------------------------------------------
# Directory creation (mkdir)
# ---------------------------------------------------------------------------


def test_directories_can_be_created(tmp_path, monkeypatch):
    """Directories returned by path helpers can be created with mkdir."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("test-corpus")

    for directory in [paths.raw_dir(corpus), paths.chunks_dir(corpus)]:
        directory.mkdir(parents=True, exist_ok=True)
        assert directory.is_dir()


def test_manifest_parent_can_be_created(tmp_path, monkeypatch):
    """manifest_path().parent (raw_dir) can be created, enabling manifest writes."""
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(tmp_path))
    corpus = _make_corpus("test-corpus")
    manifest = paths.manifest_path(corpus)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"test": true}')
    assert manifest.exists()
    assert manifest.read_text() == '{"test": true}'
