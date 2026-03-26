"""Tests for doc_hub.discovery — plugin discovery and registry."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from doc_hub.discovery import (
    PluginRegistry,
    fetcher_plugin,
    parser_plugin,
    embedder_plugin,
    get_registry,
    reset_registry,
    _load_entry_points,
    _load_local_plugins,
    _load_plugin_file,
)


# ---------------------------------------------------------------------------
# Helpers: minimal conforming classes
# ---------------------------------------------------------------------------


class GoodFetcher:
    async def fetch(self, corpus_slug, fetch_config, output_dir):
        return output_dir


class GoodParser:
    def parse(self, input_dir, *, corpus_slug, base_url):
        return []


class GoodEmbedder:
    @property
    def model_name(self):
        return "test-model"

    @property
    def dimensions(self):
        return 128

    @property
    def task_type_document(self):
        return "RETRIEVAL_DOCUMENT"

    @property
    def task_type_query(self):
        return "RETRIEVAL_QUERY"

    async def embed_batch(self, texts):
        return [[0.0] * 128 for _ in texts]

    async def embed_query(self, query):
        return [0.0] * 128


class BadPlugin:
    """No protocol methods."""
    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_registry():
    """Reset the global registry before and after each test."""
    reset_registry()
    yield
    reset_registry()


# ---------------------------------------------------------------------------
# PluginRegistry: get_* / list_* methods
# ---------------------------------------------------------------------------


def test_get_fetcher_returns_registered_instance():
    registry = PluginRegistry()
    instance = GoodFetcher()
    registry.fetchers["my_fetcher"] = instance
    assert registry.get_fetcher("my_fetcher") is instance


def test_get_fetcher_unknown_raises_keyerror_with_available_names():
    registry = PluginRegistry()
    registry.fetchers["alpha"] = GoodFetcher()
    registry.fetchers["beta"] = GoodFetcher()
    with pytest.raises(KeyError) as exc_info:
        registry.get_fetcher("nonexistent")
    msg = str(exc_info.value)
    assert "nonexistent" in msg
    assert "alpha" in msg
    assert "beta" in msg
    assert "reset_registry" in msg


def test_get_parser_unknown_raises_keyerror_with_available_names():
    registry = PluginRegistry()
    registry.parsers["markdown"] = GoodParser()
    with pytest.raises(KeyError) as exc_info:
        registry.get_parser("nonexistent")
    msg = str(exc_info.value)
    assert "nonexistent" in msg
    assert "markdown" in msg


def test_get_embedder_unknown_raises_keyerror_with_available_names():
    registry = PluginRegistry()
    registry.embedders["gemini"] = GoodEmbedder()
    with pytest.raises(KeyError) as exc_info:
        registry.get_embedder("nonexistent")
    msg = str(exc_info.value)
    assert "nonexistent" in msg
    assert "gemini" in msg


def test_list_fetchers_returns_sorted():
    registry = PluginRegistry()
    registry.fetchers["zebra"] = GoodFetcher()
    registry.fetchers["alpha"] = GoodFetcher()
    registry.fetchers["mango"] = GoodFetcher()
    assert registry.list_fetchers() == ["alpha", "mango", "zebra"]


def test_list_parsers_returns_sorted():
    registry = PluginRegistry()
    registry.parsers["z_parser"] = GoodParser()
    registry.parsers["a_parser"] = GoodParser()
    assert registry.list_parsers() == ["a_parser", "z_parser"]


def test_list_embedders_returns_sorted():
    registry = PluginRegistry()
    registry.embedders["openai"] = GoodEmbedder()
    registry.embedders["gemini"] = GoodEmbedder()
    assert registry.list_embedders() == ["gemini", "openai"]


def test_get_parser_returns_registered_instance():
    registry = PluginRegistry()
    instance = GoodParser()
    registry.parsers["markdown"] = instance
    assert registry.get_parser("markdown") is instance


def test_get_embedder_returns_registered_instance():
    registry = PluginRegistry()
    instance = GoodEmbedder()
    registry.embedders["gemini"] = instance
    assert registry.get_embedder("gemini") is instance


# ---------------------------------------------------------------------------
# Entry point loading
# ---------------------------------------------------------------------------


def _make_entry_point(name: str, loaded_value):
    """Create a mock entry point that returns loaded_value."""
    ep = MagicMock()
    ep.name = name
    ep.value = f"fake.module:{name}"
    ep.load.return_value = loaded_value
    return ep


def test_entry_points_load_class_and_instantiate():
    registry = PluginRegistry()
    ep = _make_entry_point("my_fetcher", GoodFetcher)

    with patch("importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = lambda group: [ep] if group == "doc_hub.fetchers" else []
        _load_entry_points(registry)

    assert "my_fetcher" in registry.fetchers
    assert isinstance(registry.fetchers["my_fetcher"], GoodFetcher)


def test_entry_points_use_pre_instantiated_object():
    """If the entry point loads an instance (not a class), use it as-is."""
    registry = PluginRegistry()
    instance = GoodFetcher()
    ep = _make_entry_point("pre_inst", instance)

    with patch("importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = lambda group: [ep] if group == "doc_hub.fetchers" else []
        _load_entry_points(registry)

    assert registry.fetchers["pre_inst"] is instance


def test_entry_points_skip_non_conforming_plugin(caplog):
    """Non-conforming plugins are skipped with a warning."""
    registry = PluginRegistry()
    ep = _make_entry_point("bad_fetcher", BadPlugin)

    with patch("importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = lambda group: [ep] if group == "doc_hub.fetchers" else []
        with caplog.at_level(logging.WARNING):
            _load_entry_points(registry)

    assert "bad_fetcher" not in registry.fetchers
    assert any("bad_fetcher" in r.message for r in caplog.records)


def test_entry_points_duplicate_name_first_wins(caplog):
    """When two entry points share a name, the first-loaded wins."""
    registry = PluginRegistry()
    ep1 = _make_entry_point("dup", GoodFetcher)
    ep2 = _make_entry_point("dup", GoodFetcher)
    ep2.value = "another.module:GoodFetcher"

    with patch("importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = lambda group: [ep1, ep2] if group == "doc_hub.fetchers" else []
        with caplog.at_level(logging.WARNING):
            _load_entry_points(registry)

    # First ep loads (ep1.load called), second is skipped
    assert ep1.load.call_count == 1
    assert ep2.load.call_count == 0
    assert any("already registered" in r.message for r in caplog.records)


def test_entry_points_init_raises_is_skipped_and_logged(caplog):
    """If plugin __init__ raises, plugin is skipped and logged, others load."""

    class FailingFetcher:
        def __init__(self):
            raise RuntimeError("API key missing")

    registry = PluginRegistry()
    ep_bad = _make_entry_point("failing", FailingFetcher)
    ep_good = _make_entry_point("good", GoodFetcher)

    with patch("importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = lambda group: [ep_bad, ep_good] if group == "doc_hub.fetchers" else []
        with caplog.at_level(logging.ERROR):
            _load_entry_points(registry)

    assert "failing" not in registry.fetchers
    assert "good" in registry.fetchers
    assert any("failing" in r.message for r in caplog.records)


def test_entry_points_parser_and_embedder_load():
    registry = PluginRegistry()
    ep_parser = _make_entry_point("markdown", GoodParser)
    ep_embedder = _make_entry_point("gemini", GoodEmbedder)

    def side_effect(group):
        if group == "doc_hub.parsers":
            return [ep_parser]
        if group == "doc_hub.embedders":
            return [ep_embedder]
        return []

    with patch("importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = side_effect
        _load_entry_points(registry)

    assert "markdown" in registry.parsers
    assert "gemini" in registry.embedders


# ---------------------------------------------------------------------------
# Local plugin file loading
# ---------------------------------------------------------------------------


def test_local_plugin_file_fetcher(tmp_path):
    """A local .py file with @fetcher_plugin is discovered and loaded."""
    fetchers_dir = tmp_path / "fetchers"
    fetchers_dir.mkdir(parents=True)

    plugin_code = """
from doc_hub.discovery import fetcher_plugin

@fetcher_plugin("local_test")
class LocalTestFetcher:
    async def fetch(self, corpus_slug, fetch_config, output_dir):
        return output_dir
"""
    (fetchers_dir / "my_fetcher.py").write_text(plugin_code)

    registry = PluginRegistry()
    _load_local_plugins(registry, tmp_path)

    assert "local_test" in registry.fetchers


def test_local_plugin_file_parser(tmp_path):
    parsers_dir = tmp_path / "parsers"
    parsers_dir.mkdir(parents=True)

    plugin_code = """
from doc_hub.discovery import parser_plugin

@parser_plugin("local_md")
class LocalParser:
    def parse(self, input_dir, *, corpus_slug, base_url):
        return []
"""
    (parsers_dir / "my_parser.py").write_text(plugin_code)

    registry = PluginRegistry()
    _load_local_plugins(registry, tmp_path)

    assert "local_md" in registry.parsers


def test_local_plugin_file_embedder(tmp_path):
    embedders_dir = tmp_path / "embedders"
    embedders_dir.mkdir(parents=True)

    plugin_code = """
from doc_hub.discovery import embedder_plugin

@embedder_plugin("local_embed")
class LocalEmbedder:
    @property
    def model_name(self): return "local"
    @property
    def dimensions(self): return 64
    @property
    def task_type_document(self): return ""
    @property
    def task_type_query(self): return ""
    async def embed_batch(self, texts): return [[0.0]*64 for _ in texts]
    async def embed_query(self, query): return [0.0]*64
"""
    (embedders_dir / "my_embedder.py").write_text(plugin_code)

    registry = PluginRegistry()
    _load_local_plugins(registry, tmp_path)

    assert "local_embed" in registry.embedders


def test_local_plugin_skips_underscore_files(tmp_path):
    """Files starting with _ are not loaded."""
    fetchers_dir = tmp_path / "fetchers"
    fetchers_dir.mkdir()
    (fetchers_dir / "_private.py").write_text("""
from doc_hub.discovery import fetcher_plugin
@fetcher_plugin("private")
class PrivateFetcher:
    async def fetch(self, corpus_slug, fetch_config, output_dir): return output_dir
""")
    registry = PluginRegistry()
    _load_local_plugins(registry, tmp_path)
    assert "private" not in registry.fetchers


def test_local_plugin_skips_missing_subdirs(tmp_path):
    """Non-existent subdirectories are skipped gracefully."""
    registry = PluginRegistry()
    _load_local_plugins(registry, tmp_path)  # no fetchers/parsers/embedders dirs
    assert registry.fetchers == {}
    assert registry.parsers == {}
    assert registry.embedders == {}


def test_local_plugin_non_conforming_skipped(tmp_path, caplog):
    """A decorated class that doesn't match the protocol is skipped."""
    fetchers_dir = tmp_path / "fetchers"
    fetchers_dir.mkdir()
    plugin_code = """
from doc_hub.discovery import fetcher_plugin

@fetcher_plugin("bad_local")
class BadLocalFetcher:
    pass  # No fetch() method
"""
    (fetchers_dir / "bad.py").write_text(plugin_code)

    registry = PluginRegistry()
    with caplog.at_level(logging.WARNING):
        _load_local_plugins(registry, tmp_path)

    assert "bad_local" not in registry.fetchers
    assert any("bad_local" in r.message for r in caplog.records)


def test_local_plugin_syntax_error_skipped(tmp_path, caplog):
    """A local plugin file with a syntax error is skipped; others continue."""
    fetchers_dir = tmp_path / "fetchers"
    fetchers_dir.mkdir()

    # Bad file (syntax error)
    (fetchers_dir / "a_bad.py").write_text("this is not valid python !!!")

    # Good file (loads after the bad one, alphabetically)
    (fetchers_dir / "b_good.py").write_text("""
from doc_hub.discovery import fetcher_plugin

@fetcher_plugin("good_after_bad")
class GoodFetcher:
    async def fetch(self, corpus_slug, fetch_config, output_dir): return output_dir
""")

    registry = PluginRegistry()
    with caplog.at_level(logging.ERROR):
        _load_local_plugins(registry, tmp_path)

    # Bad file's plugin not loaded; good file's plugin loaded
    assert "good_after_bad" in registry.fetchers


def test_local_plugin_sys_modules_cleanup_on_error(tmp_path):
    """Partially-loaded module is cleaned from sys.modules on exec error."""
    import sys
    fetchers_dir = tmp_path / "fetchers"
    fetchers_dir.mkdir()

    # File that fails partway through execution (after import starts)
    (fetchers_dir / "fails.py").write_text("""
x = 1  # this line runs fine
raise RuntimeError("intentional error during module load")
""")

    module_name = "doc_hub._local_plugins.fetchers.fails"
    registry = PluginRegistry()
    _load_local_plugins(registry, tmp_path)

    # Module should not be left in sys.modules
    assert module_name not in sys.modules


# ---------------------------------------------------------------------------
# Name collision: entry point takes precedence over local
# ---------------------------------------------------------------------------


def test_entry_point_takes_precedence_over_local_plugin(tmp_path, caplog):
    """When an entry point and local plugin share a name, entry point wins."""
    fetchers_dir = tmp_path / "fetchers"
    fetchers_dir.mkdir()

    # Local plugin with name "shared"
    plugin_code = """
from doc_hub.discovery import fetcher_plugin

@fetcher_plugin("shared")
class LocalSharedFetcher:
    async def fetch(self, corpus_slug, fetch_config, output_dir): return output_dir
"""
    (fetchers_dir / "shared.py").write_text(plugin_code)

    # Entry point also registers "shared"
    ep = _make_entry_point("shared", GoodFetcher)

    registry = PluginRegistry()
    with patch("importlib.metadata.entry_points") as mock_eps:
        mock_eps.side_effect = lambda group: [ep] if group == "doc_hub.fetchers" else []
        with caplog.at_level(logging.WARNING):
            _load_entry_points(registry)
            _load_local_plugins(registry, tmp_path)

    # Entry point wins — instance is GoodFetcher, not LocalSharedFetcher
    assert isinstance(registry.fetchers["shared"], GoodFetcher)
    assert any("already registered" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# get_registry / reset_registry
# ---------------------------------------------------------------------------


def test_get_registry_returns_plugin_registry():
    with patch("importlib.metadata.entry_points", return_value=[]):
        registry = get_registry(plugins_dir=Path("/nonexistent/path"))
    assert isinstance(registry, PluginRegistry)


def test_get_registry_cached_on_subsequent_calls():
    with patch("importlib.metadata.entry_points", return_value=[]):
        r1 = get_registry(plugins_dir=Path("/nonexistent/path"))
        r2 = get_registry(plugins_dir=Path("/nonexistent/path"))
    assert r1 is r2


def test_reset_registry_clears_cache(tmp_path):
    """After reset_registry(), get_registry() returns a fresh instance."""
    fetchers_dir = tmp_path / "plugins" / "fetchers"
    fetchers_dir.mkdir(parents=True)

    plugin_code = """
from doc_hub.discovery import fetcher_plugin

@fetcher_plugin("first_plugin")
class FirstFetcher:
    async def fetch(self, corpus_slug, fetch_config, output_dir): return output_dir
"""
    (fetchers_dir / "first.py").write_text(plugin_code)

    with patch("importlib.metadata.entry_points", return_value=[]):
        r1 = get_registry(plugins_dir=tmp_path / "plugins")

    assert "first_plugin" in r1.fetchers

    reset_registry()

    # Now load with a different plugins dir (no plugins)
    with patch("importlib.metadata.entry_points", return_value=[]):
        r2 = get_registry(plugins_dir=Path("/nonexistent/path"))

    assert r1 is not r2
    assert "first_plugin" not in r2.fetchers


def test_get_registry_with_no_plugins():
    """Registry with empty entry points and no plugins_dir has zero plugins."""
    with patch("importlib.metadata.entry_points", return_value=[]):
        registry = get_registry(plugins_dir=Path("/nonexistent/does_not_exist"))

    assert registry.list_fetchers() == []
    assert registry.list_parsers() == []
    assert registry.list_embedders() == []


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def test_fetcher_plugin_decorator_sets_attribute():
    @fetcher_plugin("decorated_fetcher")
    class MyFetcher:
        pass

    assert hasattr(MyFetcher, "_doc_hub_plugin")
    assert MyFetcher._doc_hub_plugin == ("fetcher", "decorated_fetcher")


def test_parser_plugin_decorator_sets_attribute():
    @parser_plugin("decorated_parser")
    class MyParser:
        pass

    assert MyParser._doc_hub_plugin == ("parser", "decorated_parser")


def test_embedder_plugin_decorator_sets_attribute():
    @embedder_plugin("decorated_embedder")
    class MyEmbedder:
        pass

    assert MyEmbedder._doc_hub_plugin == ("embedder", "decorated_embedder")
