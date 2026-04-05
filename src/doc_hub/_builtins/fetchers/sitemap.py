"""Built-in sitemap fetcher plugin for doc-hub."""
from __future__ import annotations

import gzip
import logging
import socket
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from doc_hub._builtins.fetchers.jina import DownloadResult
from doc_hub._builtins.fetchers.llms_txt import (
    compute_manifest_diff,
    load_manifest,
    write_manifest,
)
from doc_hub._builtins.fetchers import jina as _jina

log = logging.getLogger(__name__)

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
DEFAULT_WORKERS = 5
DEFAULT_RETRIES = 3
TIMEOUT = 30


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
# SitemapFetcher class
# ---------------------------------------------------------------------------


class SitemapFetcher:
    """Fetcher plugin that downloads pages listed in a sitemap XML.

    Entry point name: "sitemap"

    Required fetch_config keys:
        url (str): URL to the sitemap.xml or sitemap.xml.gz file.

    Optional fetch_config keys:
        url_prefix (str): Only fetch URLs whose full URL starts with this prefix.
        base_url (str):   Override the base URL used for filename derivation.
                          Defaults to the scheme+host of the sitemap URL.
        workers (int):    Download concurrency (default 5).
        retries (int):    Per-URL retry count (default 3).
        clean (bool):     Run LLM cleaning after download (default false).

    Required environment variables:
        JINA_API_KEY: API key for Jina Reader (r.jina.ai).
    """

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        api_key = _jina.get_api_key()

        sitemap_url: str = fetch_config["url"]
        workers: int = int(fetch_config.get("workers", DEFAULT_WORKERS))
        retries: int = int(fetch_config.get("retries", DEFAULT_RETRIES))
        url_prefix: str | None = fetch_config.get("url_prefix")
        # base_url drives filename generation: explicit config > url_prefix > sitemap host
        base_url: str = fetch_config.get("base_url") or url_prefix or _derive_base_url(sitemap_url)

        # 1. Download the sitemap (supports both plain XML and gzip)
        log.info("[%s] Fetching sitemap from %s", corpus_slug, sitemap_url)
        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(sitemap_url) as resp:
                resp.raise_for_status()
                raw_bytes = await resp.read()

        # Decompress if gzip, otherwise decode directly
        try:
            xml_content = gzip.decompress(raw_bytes).decode()
        except (gzip.BadGzipFile, OSError):
            xml_content = raw_bytes.decode()

        # 2. Parse URLs from sitemap XML
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "_sitemap.xml").write_text(xml_content)

        upstream_urls = parse_sitemap_xml(xml_content)
        log.info("[%s] Found %d unique URLs in sitemap", corpus_slug, len(upstream_urls))

        # Filter to url_prefix if specified
        if url_prefix:
            upstream_urls = [u for u in upstream_urls if u.startswith(url_prefix)]
            log.info(
                "[%s] Filtered to %d URLs matching prefix %r",
                corpus_slug, len(upstream_urls), url_prefix,
            )

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
            download_results = await _jina.fetch_all(
                upstream_urls,
                output_dir,
                api_key,
                filename_fn=lambda u: html_url_to_filename(u, base_url),
                workers=workers,
                retries=retries,
            )

        # 5b. Write manifest before cleaning so clean_corpus can read it
        write_manifest(download_results, output_dir, sections=sections)

        # 5c. LLM cleaning (opt-in via fetch_config["clean"])
        if fetch_config.get("clean") and download_results:
            from doc_hub.clean import DEFAULT_CLEAN_WORKERS, clean_corpus  # noqa: PLC0415

            clean_workers: int = int(fetch_config.get("clean_workers", DEFAULT_CLEAN_WORKERS))
            log.info("[%s] Running LLM cleaning on fetched pages", corpus_slug)
            await clean_corpus(output_dir, workers=clean_workers)

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

        return output_dir
