"""Built-in git_repo fetcher plugin stub for doc-hub."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class GitRepoFetcher:
    """Fetcher plugin stub for git repository fetching.

    Entry point name: "git_repo"
    """

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        raise NotImplementedError(
            f"git_repo fetcher is not yet implemented (corpus={corpus_slug!r}). "
            "Implement when a corpus requires the 'git_repo' strategy."
        )
