"""Built-in direct_url fetcher plugin for doc-hub."""
from __future__ import annotations

import hashlib
import json
import logging
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger(__name__)

TIMEOUT = 30
USER_AGENT = "doc-hub-fetcher/1.0"


def _url_to_filename(url: str) -> str:
    """Derive a .md filename from a URL path."""
    path = urlparse(url).path
    name = path.rstrip("/").rsplit("/", 1)[-1] or "index"
    if "." in name:
        stem = name.rsplit(".", 1)[0]
    else:
        stem = name
    return stem + ".md"


class DirectUrlFetcher:
    """Fetcher plugin that downloads one or more URLs directly as markdown files.

    Useful for monolithic documentation files (e.g. llms-summary.txt, llms-full.txt)
    that contain all docs in a single download rather than listing URLs to fetch.

    Entry point name: "direct_url"

    Required fetch_config keys (one of):
        url (str):        A single URL to download.
        urls (list[str]): A list of URLs to download.

    Optional fetch_config keys:
        filenames (dict[str, str]): Map of URL → output filename override.
    """

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        urls: list[str] = fetch_config.get("urls") or [fetch_config["url"]]
        filenames_override: dict[str, str] = fetch_config.get("filenames", {})

        output_dir.mkdir(parents=True, exist_ok=True)

        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        headers = {"User-Agent": USER_AGENT}

        results: list[dict[str, Any]] = []
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout, headers=headers
        ) as session:
            for url in urls:
                filename = filenames_override.get(url) or _url_to_filename(url)
                outpath = output_dir / filename
                try:
                    async with session.get(url) as resp:
                        resp.raise_for_status()
                        content = await resp.read()
                    outpath.write_bytes(content)
                    content_hash = hashlib.sha256(content).hexdigest()
                    log.info("[%s] OK: %s → %s", corpus_slug, url, filename)
                    results.append(
                        {
                            "url": url,
                            "filename": filename,
                            "success": True,
                            "error": None,
                            "content_hash": content_hash,
                        }
                    )
                except (aiohttp.ClientError, OSError) as exc:
                    log.warning("[%s] FAIL: %s — %s", corpus_slug, url, exc)
                    results.append(
                        {
                            "url": url,
                            "filename": filename,
                            "success": False,
                            "error": str(exc),
                            "content_hash": None,
                        }
                    )

        manifest = {
            "total": len(results),
            "success": sum(1 for r in results if r["success"]),
            "failed": sum(1 for r in results if not r["success"]),
            "files": sorted(results, key=lambda r: r["filename"]),
        }
        (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        log.info(
            "[%s] Done: %d/%d downloaded",
            corpus_slug,
            manifest["success"],
            manifest["total"],
        )

        return output_dir
