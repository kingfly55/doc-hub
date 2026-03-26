"""Plugin discovery and registry for doc-hub.

Two discovery mechanisms:
1. Python entry points (importlib.metadata) — primary
2. Local plugin files ({data_root}/plugins/) — secondary

Entry points take precedence on name collision.

Usage:
    from doc_hub.discovery import get_registry

    registry = get_registry()
    fetcher = registry.get_fetcher("llms_txt")
    parser = registry.get_parser("markdown")
    embedder = registry.get_embedder("gemini")
"""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doc_hub.protocols import Embedder, Fetcher, Parser

log = logging.getLogger(__name__)

# Entry point group names
EP_FETCHERS = "doc_hub.fetchers"
EP_PARSERS = "doc_hub.parsers"
EP_EMBEDDERS = "doc_hub.embedders"

# Decorator attribute name for local plugin files
_PLUGIN_ATTR = "_doc_hub_plugin"


# ---------------------------------------------------------------------------
# Decorator for local plugin files
# ---------------------------------------------------------------------------


def fetcher_plugin(name: str):
    """Decorator to mark a class as a fetcher plugin in a local .py file.

    Usage (in {data_root}/plugins/fetchers/my_fetcher.py):

        from doc_hub.discovery import fetcher_plugin

        @fetcher_plugin("my_source")
        class MyFetcher:
            async def fetch(self, corpus_slug, fetch_config, output_dir):
                ...
    """
    def decorator(cls):
        setattr(cls, _PLUGIN_ATTR, ("fetcher", name))
        return cls
    return decorator


def parser_plugin(name: str):
    """Decorator to mark a class as a parser plugin in a local .py file."""
    def decorator(cls):
        setattr(cls, _PLUGIN_ATTR, ("parser", name))
        return cls
    return decorator


def embedder_plugin(name: str):
    """Decorator to mark a class as an embedder plugin in a local .py file."""
    def decorator(cls):
        setattr(cls, _PLUGIN_ATTR, ("embedder", name))
        return cls
    return decorator


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class PluginRegistry:
    """Central registry of discovered plugins.

    Plugins are stored by name. Entry points are loaded first, then
    local plugin files. On name collision, the entry point wins (logged
    as a warning).
    """

    fetchers: dict[str, Fetcher] = field(default_factory=dict)
    parsers: dict[str, Parser] = field(default_factory=dict)
    embedders: dict[str, Embedder] = field(default_factory=dict)

    def get_fetcher(self, name: str) -> Fetcher:
        """Look up a fetcher by name.

        Args:
            name: Plugin name (e.g. "llms_txt", "git_repo").

        Returns:
            Fetcher instance.

        Raises:
            KeyError: If no fetcher with this name is registered.
                Message includes available fetcher names and install hint.
        """
        if name not in self.fetchers:
            available = sorted(self.fetchers.keys())
            raise KeyError(
                f"Unknown fetcher: {name!r}. "
                f"Available fetchers: {available}. "
                f"If you just installed a plugin, restart the process "
                f"or call reset_registry()."
            )
        return self.fetchers[name]

    def get_parser(self, name: str) -> Parser:
        """Look up a parser by name. Raises KeyError if not found."""
        if name not in self.parsers:
            available = sorted(self.parsers.keys())
            raise KeyError(
                f"Unknown parser: {name!r}. "
                f"Available parsers: {available}. "
                f"If you just installed a plugin, restart the process "
                f"or call reset_registry()."
            )
        return self.parsers[name]

    def get_embedder(self, name: str) -> Embedder:
        """Look up an embedder by name. Raises KeyError if not found."""
        if name not in self.embedders:
            available = sorted(self.embedders.keys())
            raise KeyError(
                f"Unknown embedder: {name!r}. "
                f"Available embedders: {available}. "
                f"If you just installed a plugin, restart the process "
                f"or call reset_registry()."
            )
        return self.embedders[name]

    def list_fetchers(self) -> list[str]:
        """Return sorted list of registered fetcher names."""
        return sorted(self.fetchers.keys())

    def list_parsers(self) -> list[str]:
        """Return sorted list of registered parser names."""
        return sorted(self.parsers.keys())

    def list_embedders(self) -> list[str]:
        """Return sorted list of registered embedder names."""
        return sorted(self.embedders.keys())


# ---------------------------------------------------------------------------
# Discovery: entry points
# ---------------------------------------------------------------------------


def _load_entry_points(registry: PluginRegistry) -> None:
    """Load plugins from Python entry points.

    Entry point groups:
    - doc_hub.fetchers: name → Fetcher class or factory
    - doc_hub.parsers: name → Parser class or factory
    - doc_hub.embedders: name → Embedder class or factory

    Each entry point value should be a class that can be instantiated
    with no args: entry_point.load()()

    After instantiation, the instance is validated against the protocol
    using isinstance(). Non-conforming plugins are skipped with a warning.
    """
    for group, target_dict, protocol_cls, kind in [
        (EP_FETCHERS, registry.fetchers, Fetcher, "fetcher"),
        (EP_PARSERS, registry.parsers, Parser, "parser"),
        (EP_EMBEDDERS, registry.embedders, Embedder, "embedder"),
    ]:
        eps = importlib.metadata.entry_points(group=group)
        for ep in eps:
            # Handle duplicate entry point names from different packages.
            # importlib.metadata may return multiple entries with the same
            # .name from different installed packages. First-loaded wins.
            if ep.name in target_dict:
                log.warning(
                    "%s entry point %r already registered — skipping "
                    "duplicate (from entry point %r)",
                    kind.capitalize(), ep.name, ep.value,
                )
                continue

            try:
                loaded = ep.load()
                # Entry points must be classes instantiable with no args.
                # Plugins that need configuration (API keys etc.) should
                # read from environment variables in __init__ or defer
                # until first use (lazy init).
                if isinstance(loaded, type):
                    instance = loaded()
                else:
                    instance = loaded

                # Validate protocol conformance at registration time.
                # @runtime_checkable protocols allow isinstance() checks.
                # NOTE: @runtime_checkable only checks method/attribute
                # NAMES exist, not signatures. A class with fetch(self)
                # (wrong arity) passes isinstance() but fails at call
                # time. Static type checkers catch this at dev time.
                if not isinstance(instance, protocol_cls):
                    log.warning(
                        "Skipping %s entry point %r: instance %r does not "
                        "conform to %s protocol (missing required methods)",
                        kind, ep.name, type(instance).__name__,
                        protocol_cls.__name__,
                    )
                    continue

                target_dict[ep.name] = instance
                log.debug("Loaded %s entry point: %s", kind, ep.name)
            except Exception:
                log.exception(
                    "Failed to load %s entry point %r (skipping — this "
                    "plugin will not be available)", kind, ep.name
                )


# ---------------------------------------------------------------------------
# Discovery: local plugin files
# ---------------------------------------------------------------------------


def _load_local_plugins(registry: PluginRegistry, plugins_dir: Path) -> None:
    """Scan local plugin directories for decorated classes.

    Directory structure:
        {plugins_dir}/
            fetchers/*.py
            parsers/*.py
            embedders/*.py

    Each .py file is loaded as a module. Classes decorated with
    @fetcher_plugin, @parser_plugin, or @embedder_plugin are
    instantiated and added to the registry.

    Entry points take precedence: if a name is already registered
    (from entry points), the local plugin is skipped with a warning.
    """
    subdirs = {
        "fetchers": (registry.fetchers, "fetcher"),
        "parsers": (registry.parsers, "parser"),
        "embedders": (registry.embedders, "embedder"),
    }

    for subdir_name, (target_dict, kind) in subdirs.items():
        subdir = plugins_dir / subdir_name
        if not subdir.is_dir():
            continue

        for py_file in sorted(subdir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                _load_plugin_file(py_file, target_dict, kind)
            except Exception:
                log.exception(
                    "Failed to load local %s plugin: %s", kind, py_file
                )


def _load_plugin_file(
    py_file: Path,
    target_dict: dict[str, Any],
    kind: str,
) -> None:
    """Load a single .py plugin file and register decorated classes."""
    module_name = f"doc_hub._local_plugins.{kind}s.{py_file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_file)
    if spec is None or spec.loader is None:
        log.warning("Could not load spec for %s", py_file)
        return

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Clean up partially-loaded module to avoid stale entries
        sys.modules.pop(module_name, None)
        raise

    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if not isinstance(obj, type):
            continue
        plugin_info = getattr(obj, _PLUGIN_ATTR, None)
        if plugin_info is None:
            continue

        plugin_kind, plugin_name = plugin_info
        if plugin_kind != kind:
            continue

        if plugin_name in target_dict:
            log.warning(
                "Local %s plugin %r skipped — name already registered "
                "(entry point takes precedence)",
                kind,
                plugin_name,
            )
            continue

        instance = obj()

        # Validate against the appropriate protocol
        from doc_hub.protocols import Fetcher, Parser, Embedder
        protocol_map = {"fetcher": Fetcher, "parser": Parser, "embedder": Embedder}
        protocol_cls = protocol_map[kind]
        if not isinstance(instance, protocol_cls):
            log.warning(
                "Skipping local %s plugin %r from %s: does not conform "
                "to %s protocol",
                kind, plugin_name, py_file, protocol_cls.__name__,
            )
            continue

        target_dict[plugin_name] = instance
        log.info(
            "Loaded local %s plugin: %s (from %s)", kind, plugin_name, py_file
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_registry: PluginRegistry | None = None


def get_registry(*, plugins_dir: Path | None = None) -> PluginRegistry:
    """Return the global plugin registry, creating it on first call.

    Loads plugins from entry points first, then from local plugin
    files. The registry is cached — subsequent calls return the same
    instance.

    Args:
        plugins_dir: Override the local plugins directory. If None,
            uses {data_root}/plugins/ (from doc_hub.paths.data_root()).
            Only used on first call.

    Returns:
        The global PluginRegistry instance.
    """
    global _registry
    if _registry is not None:
        return _registry

    registry = PluginRegistry()

    # 1. Load entry points (primary mechanism)
    _load_entry_points(registry)

    # 2. Load local plugin files (secondary mechanism)
    if plugins_dir is None:
        from doc_hub.paths import data_root
        plugins_dir = data_root() / "plugins"

    if plugins_dir.is_dir():
        _load_local_plugins(registry, plugins_dir)
    else:
        log.debug("No local plugins directory at %s", plugins_dir)

    _registry = registry
    log.info(
        "Plugin registry loaded: %d fetchers, %d parsers, %d embedders",
        len(registry.fetchers),
        len(registry.parsers),
        len(registry.embedders),
    )
    return registry


def reset_registry() -> None:
    """Clear the cached registry. Used in tests."""
    global _registry
    _registry = None
