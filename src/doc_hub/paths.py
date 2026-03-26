"""Path resolution for the doc-hub pipeline.

Each corpus gets an isolated sub-directory under the shared data root::

    ~/.local/share/doc-hub/
      {slug}/
        raw/            # Downloaded markdown files + manifest.json
        chunks/         # Parsed chunks, embedded chunks, and cache JSONL
      plugins/          # Local plugin files for discovery

Data root resolution order:

1. ``DOC_HUB_DATA_DIR`` environment variable (explicit override)
2. ``$XDG_DATA_HOME/doc-hub`` if ``XDG_DATA_HOME`` is set
3. ``~/.local/share/doc-hub`` (XDG default)

The data root is NOT created automatically — callers that write files are
responsible for calling ``mkdir(parents=True, exist_ok=True)``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doc_hub.models import Corpus


def data_root() -> Path:
    """Return the root data directory.

    Resolution order:
    1. DOC_HUB_DATA_DIR env var (explicit override)
    2. XDG_DATA_HOME/doc-hub (if XDG_DATA_HOME is set)
    3. ~/.local/share/doc-hub (XDG default)

    The directory is NOT created automatically — callers that write
    files are responsible for calling mkdir(parents=True, exist_ok=True).
    """
    env_override = os.environ.get("DOC_HUB_DATA_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()

    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data).expanduser().resolve() / "doc-hub"

    return Path.home() / ".local" / "share" / "doc-hub"


def plugins_dir() -> Path:
    """Return the local plugins directory: {data_root}/plugins/.

    Used by the plugin discovery system (doc_hub.discovery) to scan
    for local .py plugin files.
    """
    return data_root() / "plugins"


def corpus_dir(corpus_or_slug: Corpus | str) -> Path:
    """Per-corpus root directory: {data_root}/{slug}/.

    Args:
        corpus_or_slug: A Corpus object or a plain slug string.

    Returns:
        Absolute path to ``{data_root}/{slug}/``.

    Raises:
        ValueError: If the slug is invalid (empty, contains path separators,
            or starts with '.').
    """
    slug = corpus_or_slug if isinstance(corpus_or_slug, str) else corpus_or_slug.slug
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        raise ValueError(
            f"Invalid corpus slug: {slug!r}. Slugs must be non-empty, "
            "must not contain path separators, and must not start with '.'"
        )
    return data_root() / slug


def raw_dir(corpus_or_slug: Corpus | str) -> Path:
    """Raw downloaded files directory: {data_root}/{slug}/raw/.

    Args:
        corpus_or_slug: A Corpus object or a plain slug string.

    Returns:
        Absolute path to ``{data_root}/{slug}/raw/``.
    """
    return corpus_dir(corpus_or_slug) / "raw"


def chunks_dir(corpus_or_slug: Corpus | str) -> Path:
    """Parsed and embedded chunks directory: {data_root}/{slug}/chunks/.

    Args:
        corpus_or_slug: A Corpus object or a plain slug string.

    Returns:
        Absolute path to ``{data_root}/{slug}/chunks/``.
    """
    return corpus_dir(corpus_or_slug) / "chunks"


def manifest_path(corpus_or_slug: Corpus | str) -> Path:
    """Manifest file for incremental sync: {data_root}/{slug}/raw/manifest.json.

    Args:
        corpus_or_slug: A Corpus object or a plain slug string.

    Returns:
        Absolute path to ``{data_root}/{slug}/raw/manifest.json``.
    """
    return raw_dir(corpus_or_slug) / "manifest.json"


def embedded_chunks_path(corpus_or_slug: Corpus | str) -> Path:
    """Embedded chunks JSONL file: {data_root}/{slug}/chunks/embedded_chunks.jsonl.

    Args:
        corpus_or_slug: A Corpus object or a plain slug string.

    Returns:
        Absolute path to ``{data_root}/{slug}/chunks/embedded_chunks.jsonl``.
    """
    return chunks_dir(corpus_or_slug) / "embedded_chunks.jsonl"


def embeddings_cache_path(corpus_or_slug: Corpus | str) -> Path:
    """Embedding cache JSONL file: {data_root}/{slug}/chunks/embeddings_cache.jsonl.

    Args:
        corpus_or_slug: A Corpus object or a plain slug string.

    Returns:
        Absolute path to ``{data_root}/{slug}/chunks/embeddings_cache.jsonl``.
    """
    return chunks_dir(corpus_or_slug) / "embeddings_cache.jsonl"
