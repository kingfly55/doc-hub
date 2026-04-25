"""Built-in local_dir fetcher plugin for doc-hub."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from doc_hub.versions import snapshot_manifest_from_downloads, utc_now_iso, write_snapshot_manifest


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

        fetched_at = utc_now_iso()
        source_version: str = fetch_config.get("source_version", "latest")
        resolved_version: str | None = fetch_config.get("resolved_version")
        results = []
        for file_path in sorted(path.rglob("*.md")):
            rel = file_path.relative_to(path).as_posix()
            results.append({
                "url": file_path.as_uri(),
                "filename": rel,
                "success": True,
                "error": None,
                "content_hash": hashlib.sha256(file_path.read_bytes()).hexdigest(),
                "fetched_at": fetched_at,
                "source_version": source_version,
                "resolved_version": resolved_version,
            })

        manifest = snapshot_manifest_from_downloads(
            corpus_slug=corpus_slug,
            fetch_strategy="local_dir",
            source_type="local_dir",
            source_url=path.as_posix(),
            source_version=source_version,
            resolved_version=resolved_version,
            fetched_at=fetched_at,
            fetch_config=fetch_config,
            files=results,
            raw={
                "total": len(results),
                "success": len(results),
                "failed": 0,
            },
        )
        write_snapshot_manifest(manifest, path)
        return path
