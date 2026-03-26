"""Built-in local_dir fetcher plugin for doc-hub."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class LocalDirFetcher:
    """Fetcher plugin for docs already on disk.

    Entry point name: "local_dir"

    Required fetch_config keys:
        path (str): Absolute path to the docs directory.
    """

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        path = Path(fetch_config["path"])
        if not path.is_dir():
            raise FileNotFoundError(f"Local dir not found: {path}")
        return path
