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


def _validate_snapshot_id(snapshot_id: str) -> str:
    if not snapshot_id or "/" in snapshot_id or "\\" in snapshot_id or snapshot_id.startswith("."):
        raise ValueError(
            f"Invalid snapshot id: {snapshot_id!r}. Snapshot ids must be non-empty, "
            "must not contain path separators, and must not start with '.'."
        )
    return snapshot_id


def versions_dir(corpus_or_slug: Corpus | str) -> Path:
    """Version snapshots directory: {data_root}/{slug}/versions/."""
    return corpus_dir(corpus_or_slug) / "versions"


def snapshot_dir(corpus_or_slug: Corpus | str, snapshot_id: str) -> Path:
    """One immutable snapshot directory: {data_root}/{slug}/versions/{snapshot_id}/."""
    return versions_dir(corpus_or_slug) / _validate_snapshot_id(snapshot_id)


def raw_dir(corpus_or_slug: Corpus | str, snapshot_id: str | None = None) -> Path:
    """Raw downloaded files directory.

    Without ``snapshot_id``, returns the legacy corpus-level raw directory.
    With ``snapshot_id``, returns the versioned snapshot raw directory.
    """
    if snapshot_id is None:
        return corpus_dir(corpus_or_slug) / "raw"
    return snapshot_dir(corpus_or_slug, snapshot_id) / "raw"


def chunks_dir(corpus_or_slug: Corpus | str, snapshot_id: str | None = None) -> Path:
    """Parsed and embedded chunks directory.

    Without ``snapshot_id``, returns the legacy corpus-level chunks directory.
    With ``snapshot_id``, returns the versioned snapshot chunks directory.
    """
    if snapshot_id is None:
        return corpus_dir(corpus_or_slug) / "chunks"
    return snapshot_dir(corpus_or_slug, snapshot_id) / "chunks"


def manifest_path(corpus_or_slug: Corpus | str, snapshot_id: str | None = None) -> Path:
    """Manifest file path for legacy or versioned raw directories."""
    return raw_dir(corpus_or_slug, snapshot_id=snapshot_id) / "manifest.json"


def embedded_chunks_path(corpus_or_slug: Corpus | str, snapshot_id: str | None = None) -> Path:
    """Embedded chunks JSONL path for legacy or versioned chunks directories."""
    return chunks_dir(corpus_or_slug, snapshot_id=snapshot_id) / "embedded_chunks.jsonl"


def embeddings_cache_path(corpus_or_slug: Corpus | str, snapshot_id: str | None = None) -> Path:
    """Embedding cache JSONL path for legacy or versioned chunks directories."""
    return chunks_dir(corpus_or_slug, snapshot_id=snapshot_id) / "embeddings_cache.jsonl"
