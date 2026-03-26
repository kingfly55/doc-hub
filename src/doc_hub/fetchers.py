"""Fetcher dispatch for doc-hub.

Routes to the appropriate fetcher plugin via the plugin discovery system.
The hardcoded dispatch table and FetchStrategy enum are gone — any string
name that resolves to a registered Fetcher plugin works.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Pipeline defaults — generic enough to live here since pipeline.py imports them.
# LlmsTxtFetcher also uses these values as fallback defaults internally.
DEFAULT_WORKERS = 20
DEFAULT_RETRIES = 3

from doc_hub.discovery import get_registry


async def fetch(
    corpus_slug: str,
    fetch_strategy: str,
    fetch_config: dict[str, Any],
    output_dir: Path,
) -> Path:
    """Dispatch to the correct fetcher plugin.

    Args:
        corpus_slug: Corpus identifier (for logging).
        fetch_strategy: Name of the fetcher plugin (e.g. "llms_txt").
        fetch_config: Strategy-specific configuration dict.
        output_dir: Directory where fetched files will be written.

    Returns:
        Path to the directory of fetched files.

    Raises:
        KeyError: If no fetcher plugin with this name is registered.
    """
    registry = get_registry()
    fetcher = registry.get_fetcher(fetch_strategy)
    return await fetcher.fetch(corpus_slug, fetch_config, output_dir)
