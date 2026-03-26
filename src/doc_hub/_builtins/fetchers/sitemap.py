"""Built-in sitemap fetcher plugin stub for doc-hub."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class SitemapFetcher:
    """Fetcher plugin stub for sitemap-based crawling.

    Entry point name: "sitemap"
    """

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        raise NotImplementedError(
            f"sitemap fetcher is not yet implemented (corpus={corpus_slug!r}). "
            "Implement when a corpus requires the 'sitemap' strategy."
        )
