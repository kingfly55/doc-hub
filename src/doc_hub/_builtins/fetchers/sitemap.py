"""Built-in sitemap fetcher plugin for doc-hub."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import logging
import os
import socket
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from doc_hub._builtins.fetchers.llms_txt import (
    DownloadResult,
    compute_manifest_diff,
    load_manifest,
    write_manifest,
)

log = logging.getLogger(__name__)

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
DEFAULT_WORKERS = 5
DEFAULT_RETRIES = 3
TIMEOUT = 30
RETRY_AFTER_DEFAULT = 2
JINA_READER_PREFIX = "https://r.jina.ai/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def html_url_to_filename(url: str, base_url: str) -> str:
    """Convert an HTML page URL to a flat .md filename.

    Strips base_url prefix and trailing slash, replaces ``/`` with ``__``,
    appends ``.md``.  Empty path becomes ``index``.
    """
    base = base_url.rstrip("/") + "/"
    rel = url.removeprefix(base).strip("/")
    if not rel:
        rel = "index"
    return rel.replace("/", "__") + ".md"


def parse_sitemap_xml(xml_content: str) -> list[str]:
    """Extract unique <loc> URLs from a sitemap XML string, preserving order."""
    root = ET.fromstring(xml_content)
    seen: set[str] = set()
    urls: list[str] = []
    for loc in root.iter(f"{{{_SITEMAP_NS}}}loc"):
        url = loc.text
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def build_sections_from_urls(urls: list[str], base_url: str) -> list[dict[str, Any]]:
    """Group URLs into manifest sections by first path segment."""
    base = base_url.rstrip("/") + "/"
    root_urls: list[str] = []
    groups: dict[str, list[str]] = {}
    group_order: list[str] = []

    for url in urls:
        rel = url.removeprefix(base).strip("/")
        if not rel:
            root_urls.append(url)
            continue
        first_segment = rel.split("/", 1)[0]
        if first_segment not in groups:
            groups[first_segment] = []
            group_order.append(first_segment)
        groups[first_segment].append(url)

    sections: list[dict[str, Any]] = []
    if root_urls:
        sections.append({"title": "", "heading_level": 0, "urls": root_urls})
    for segment in group_order:
        sections.append({"title": segment, "heading_level": 2, "urls": groups[segment]})
    return sections


def _derive_base_url(sitemap_url: str) -> str:
    """Derive the base URL from a sitemap URL.

    e.g. https://camoufox.com/sitemap.xml.gz -> https://camoufox.com/
    """
    parsed = urlparse(sitemap_url)
    return f"{parsed.scheme}://{parsed.netloc}/"


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


async def _download_via_jina(
    session: aiohttp.ClientSession,
    url: str,
    filename: str,
    output_dir: Path,
    retries: int = DEFAULT_RETRIES,
) -> DownloadResult:
    """Fetch a page's markdown via Jina Reader API with retry on 429."""
    jina_url = f"{JINA_READER_PREFIX}{url}"
    outpath = output_dir / filename
    last_error: str | None = None

    for attempt in range(1, retries + 1):
        try:
            async with session.get(jina_url) as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", RETRY_AFTER_DEFAULT))
                    last_error = f"429 rate limited (attempt {attempt})"
                    log.debug("Rate limited on %s, waiting %ds", url, retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                resp.raise_for_status()
                content = await resp.text()
            content_bytes = content.encode()
            outpath.write_bytes(content_bytes)
            content_hash = hashlib.sha256(content_bytes).hexdigest()
            return DownloadResult(url=url, filename=filename, success=True, content_hash=content_hash)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            last_error = str(exc)
            if attempt < retries:
                log.debug("Retry %d/%d for %s: %s", attempt, retries, url, last_error)

    return DownloadResult(url=url, filename=filename, success=False, error=last_error)


async def _download_all_via_jina(
    urls: list[str],
    base_url: str,
    output_dir: Path,
    api_key: str,
    workers: int = DEFAULT_WORKERS,
    retries: int = DEFAULT_RETRIES,
) -> list[DownloadResult]:
    """Download all URLs via Jina Reader with bounded concurrency."""
    output_dir.mkdir(parents=True, exist_ok=True)

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    connector = aiohttp.TCPConnector(limit=workers, family=socket.AF_INET)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/markdown",
    }

    sem = asyncio.Semaphore(workers)

    async def bounded(url: str, filename: str) -> DownloadResult:
        async with sem:
            return await _download_via_jina(session, url, filename, output_dir, retries)

    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout, headers=headers,
    ) as session:
        tasks = [
            asyncio.create_task(bounded(url, html_url_to_filename(url, base_url)))
            for url in urls
        ]
        results = await asyncio.gather(*tasks)

    for r in results:
        if r.success:
            log.info("OK: %s", r.filename)
        else:
            log.warning("FAIL: %s — %s", r.url, r.error)

    return list(results)


# ---------------------------------------------------------------------------
# SitemapFetcher class
# ---------------------------------------------------------------------------


class SitemapFetcher:
    """Fetcher plugin that downloads pages listed in a sitemap XML.

    Entry point name: "sitemap"

    Required fetch_config keys:
        url (str): URL to the sitemap.xml.gz file.

    Optional fetch_config keys:
        workers (int): Download concurrency (default 5).
        retries (int): Per-URL retry count (default 3).

    Required environment variables:
        JINA_API_KEY: API key for Jina Reader (r.jina.ai).
    """

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        api_key = os.environ.get("JINA_API_KEY")
        if not api_key:
            raise ValueError(
                "JINA_API_KEY environment variable is required for the sitemap fetcher. "
                "Get your key at https://jina.ai/api-dashboard/key-manager"
            )

        sitemap_url: str = fetch_config["url"]
        workers: int = int(fetch_config.get("workers", DEFAULT_WORKERS))
        retries: int = int(fetch_config.get("retries", DEFAULT_RETRIES))
        base_url = _derive_base_url(sitemap_url)

        # 1. Download and decompress the sitemap
        log.info("[%s] Fetching sitemap from %s", corpus_slug, sitemap_url)
        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(sitemap_url) as resp:
                resp.raise_for_status()
                gz_bytes = await resp.read()

        xml_content = gzip.decompress(gz_bytes).decode()

        # 2. Parse URLs from sitemap XML
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "_sitemap.xml").write_text(xml_content)

        upstream_urls = parse_sitemap_xml(xml_content)
        log.info("[%s] Found %d unique URLs in sitemap", corpus_slug, len(upstream_urls))

        # 3. Build sections from URL structure
        sections = build_sections_from_urls(upstream_urls, base_url)

        # 4. Compute diff to identify removed files
        existing_manifest = load_manifest(output_dir)
        _new_urls, removed_filenames = compute_manifest_diff(upstream_urls, existing_manifest)

        for fn in removed_filenames:
            removed_path = output_dir / fn
            if removed_path.exists():
                removed_path.unlink()
                log.info("[%s] Deleted removed file: %s", corpus_slug, fn)

        if removed_filenames:
            log.info("[%s] Removed %d files no longer in sitemap", corpus_slug, len(removed_filenames))

        # 5. Download all pages via Jina Reader
        download_results: list[DownloadResult] = []
        if upstream_urls:
            download_results = await _download_all_via_jina(
                upstream_urls, base_url, output_dir, api_key, workers, retries,
            )

        # 6. Log sync summary
        new_count = 0
        changed_count = 0
        unchanged_count = 0
        for r in download_results:
            if not r.success:
                continue
            old_entry = existing_manifest.get(r.filename)
            if old_entry is None:
                new_count += 1
            elif old_entry.get("content_hash") is None or old_entry["content_hash"] != r.content_hash:
                changed_count += 1
            else:
                unchanged_count += 1

        fail = sum(1 for r in download_results if not r.success)
        log.info(
            "[%s] Sync complete: %d new, %d changed, %d unchanged, %d failed, %d removed",
            corpus_slug, new_count, changed_count, unchanged_count, fail, len(removed_filenames),
        )

        # 7. Write manifest
        write_manifest(download_results, output_dir, sections=sections)

        return output_dir
