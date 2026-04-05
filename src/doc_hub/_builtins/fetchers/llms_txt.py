"""Built-in llms_txt fetcher plugin for doc-hub."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import socket
from pathlib import Path
from typing import Any

import aiohttp

from doc_hub._builtins.fetchers.jina import DownloadResult  # noqa: F401
from doc_hub._builtins.fetchers import jina as _jina

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_WORKERS = 20
DEFAULT_RETRIES = 3
TIMEOUT = 30  # seconds per HTTP request
USER_AGENT = "doc-hub-fetcher/1.0"


def url_to_filename(url: str, base_url: str) -> str:
    """Convert a doc URL to a flat filename using double-underscore convention.

    e.g.  https://ai.pydantic.dev/models/openai/index.md → models__openai.md
          https://ai.pydantic.dev/index.md                → index.md

    The ``base_url`` prefix is stripped first (must end with ``/`` or match
    the URL prefix exactly).
    """
    base = base_url.rstrip("/") + "/"
    rel = url.removeprefix(base)

    if rel.endswith("/index.md"):
        stem = rel.removesuffix("/index.md")
    elif rel.endswith(".md"):
        stem = rel.removesuffix(".md")
    else:
        stem = rel

    if not stem:
        stem = "index"

    return stem.replace("/", "__") + ".md"


def load_manifest(output_dir: Path) -> dict[str, dict[str, str | None]]:
    """Load existing manifest.json → ``{filename: {"url": ..., "content_hash": ...}}``.

    Returns only successful entries. Old manifests without ``content_hash``
    degrade gracefully (hash is ``None``, treated as "needs re-download").

    Returns empty dict if the file is missing or malformed.
    """
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text())
        return {
            f["filename"]: {
                "url": f["url"],
                "content_hash": f.get("content_hash"),
            }
            for f in data.get("files", [])
            if f.get("success", False)
        }
    except (json.JSONDecodeError, KeyError):
        return {}


def compute_manifest_diff(
    upstream_urls: list[str],
    existing_manifest: dict[str, dict[str, str | None]],
) -> tuple[list[str], list[str]]:
    """Return ``(new_urls, removed_filenames)`` for an incremental sync."""
    existing_urls = {entry["url"] for entry in existing_manifest.values()}
    new_urls = [u for u in upstream_urls if u not in existing_urls]
    upstream_url_set = set(upstream_urls)
    removed = [fn for fn, entry in existing_manifest.items() if entry["url"] not in upstream_url_set]
    return new_urls, removed


def write_manifest(
    results: list[DownloadResult],
    output_dir: Path,
    sections: list[dict[str, Any]] | None = None,
) -> None:
    """Write a JSON manifest of download results to ``output_dir/manifest.json``."""
    manifest = {
        "total": len(results),
        "success": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "files": [
            {
                "url": r.url,
                "filename": r.filename,
                "success": r.success,
                "error": r.error,
                "content_hash": r.content_hash,
            }
            for r in sorted(results, key=lambda r: r.filename)
        ],
    }
    if sections is not None:
        manifest["sections"] = sections
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    log.info("Manifest written to %s", manifest_path)


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


async def _download_one(
    session: aiohttp.ClientSession,
    url: str,
    filename: str,
    output_dir: Path,
    retries: int = DEFAULT_RETRIES,
) -> DownloadResult:
    """Download a single URL with retries (no backoff — matches original behaviour)."""
    outpath = output_dir / filename
    last_error: str | None = None

    for attempt in range(1, retries + 1):
        try:
            async with session.get(url) as resp:
                resp.raise_for_status()
                content = await resp.read()
            outpath.write_bytes(content)
            content_hash = hashlib.sha256(content).hexdigest()
            return DownloadResult(url=url, filename=filename, success=True, content_hash=content_hash)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            last_error = str(exc)
            if attempt < retries:
                log.debug("Retry %d/%d for %s: %s", attempt, retries, url, last_error)

    return DownloadResult(url=url, filename=filename, success=False, error=last_error)


async def _download_all(
    urls: list[str],
    base_url: str,
    output_dir: Path,
    workers: int = DEFAULT_WORKERS,
    retries: int = DEFAULT_RETRIES,
) -> list[DownloadResult]:
    """Download all URLs concurrently with a bounded semaphore."""
    output_dir.mkdir(parents=True, exist_ok=True)

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    connector = aiohttp.TCPConnector(
        limit=workers,
        family=socket.AF_INET,
    )
    headers = {"User-Agent": USER_AGENT}

    sem = asyncio.Semaphore(workers)

    async def bounded(url: str, filename: str) -> DownloadResult:
        async with sem:
            return await _download_one(session, url, filename, output_dir, retries)

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        headers=headers,
    ) as session:
        tasks = [
            asyncio.create_task(bounded(url, url_to_filename(url, base_url)))
            for url in urls
        ]
        results = await asyncio.gather(*tasks)

    for r in results:
        if r.success:
            log.info("OK: %s", r.filename)
        else:
            log.warning("FAIL: %s — %s", r.url, r.error)

    return list(results)


async def _derive_base_url(llms_txt_url: str) -> str:
    """Derive the base URL from an llms.txt URL."""
    return llms_txt_url.rsplit("/", 1)[0] + "/"


def _derive_url_pattern(base_url: str, require_md_suffix: bool = True) -> str:
    r"""Derive a default URL extraction regex from the base URL.

    When *require_md_suffix* is False (used together with ``url_suffix`` in
    fetch_config) the pattern matches any path under *base_url* so that a
    suffix can be appended after extraction.
    """
    escaped = re.escape(base_url.rstrip("/"))
    if require_md_suffix:
        return escaped + r"/[^\s\)]+\.md"
    return escaped + r"/[^\s\)\"\]<>]+"


def _parse_sections(llms_txt_content: str, url_pattern: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    root_urls: list[str] = []
    root_seen: set[str] = set()

    for line in llms_txt_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            hashes, _, title = stripped.partition(" ")
            current_section = {
                "title": title.strip(),
                "heading_level": len(hashes),
                "urls": [],
            }
            sections.append(current_section)
            continue
        if stripped.startswith("#"):
            continue

        matches = re.findall(url_pattern, line)
        if not matches:
            continue

        if current_section is None:
            for url in matches:
                if url not in root_seen:
                    root_seen.add(url)
                    root_urls.append(url)
            continue

        seen_urls = set(current_section["urls"])
        for url in matches:
            if url not in seen_urls:
                current_section["urls"].append(url)
                seen_urls.add(url)

    if root_urls:
        sections.insert(0, {"title": "", "heading_level": 0, "urls": root_urls})
    return sections


async def _fetch_bytes_if_exists(
    session: aiohttp.ClientSession,
    url: str,
) -> bytes | None:
    async with session.get(url) as resp:
        if resp.status >= 400:
            return None
        return await resp.read()


async def _resolve_one(
    url: str,
    filename: str,
    output_dir: Path,
    strategy: str,
    direct_session: aiohttp.ClientSession,
    jina_session: aiohttp.ClientSession | None,
    retries: int,
) -> DownloadResult:
    if url.endswith(".md") or strategy == "direct":
        return await _download_one(direct_session, url, filename, output_dir, retries)

    if strategy == "jina":
        return await _jina.fetch_one(jina_session, url, filename, output_dir, retries)

    # strategy == "try_md"
    if url.endswith(".md"):
        return await _download_one(direct_session, url, filename, output_dir, retries)

    md_url = url.rstrip("/") + ".md"
    content = await _fetch_bytes_if_exists(direct_session, md_url)
    if content is not None:
        outpath = output_dir / filename
        outpath.write_bytes(content)
        content_hash = hashlib.sha256(content).hexdigest()
        return DownloadResult(url=url, filename=filename, success=True, content_hash=content_hash)

    log.debug("try_md: %s → falling back to Jina", url)
    return await _jina.fetch_one(jina_session, url, filename, output_dir, retries)


async def _resolve_all(
    urls: list[str],
    base_url: str,
    output_dir: Path,
    strategy: str,
    workers: int,
    retries: int,
    jina_api_key: str | None,
) -> list[DownloadResult]:
    output_dir.mkdir(parents=True, exist_ok=True)

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    sem = asyncio.Semaphore(workers)

    async with contextlib.AsyncExitStack() as stack:
        direct_session = await stack.enter_async_context(
            aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=workers, family=socket.AF_INET),
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
        )

        jina_session: aiohttp.ClientSession | None = None
        if strategy in ("jina", "try_md"):
            jina_session = await stack.enter_async_context(
                aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(limit=workers, family=socket.AF_INET),
                    timeout=timeout,
                    headers=_jina.make_headers(jina_api_key),
                )
            )

        async def bounded(url: str) -> DownloadResult:
            filename = url_to_filename(url, base_url)
            async with sem:
                return await _resolve_one(
                    url, filename, output_dir, strategy,
                    direct_session, jina_session, retries,
                )

        tasks = [asyncio.create_task(bounded(url)) for url in urls]
        results = await asyncio.gather(*tasks)

    for r in results:
        if r.success:
            log.info("OK: %s", r.filename)
        else:
            log.warning("FAIL: %s — %s", r.url, r.error)

    return list(results)


# ---------------------------------------------------------------------------
# LlmsTxtFetcher class
# ---------------------------------------------------------------------------


class LlmsTxtFetcher:
    """Fetcher plugin that downloads pages listed in an llms.txt manifest.

    Entry point name: "llms_txt"

    Required fetch_config keys:
        url (str): URL to the llms.txt file.

    Optional fetch_config keys:
        url_pattern (str): Regex to extract doc URLs. Auto-derived if omitted.
        url_suffix (str): Suffix appended to each extracted URL (after stripping
            any trailing slash) before downloading.  Use ``".md"`` for sites
            like docs.deno.com where the index lists bare URLs but each page is
            served with an ``.md`` extension.  When set, the auto-derived
            ``url_pattern`` no longer requires a ``.md`` suffix.
        base_url (str): Base URL for filename generation. Auto-derived if omitted.
        workers (int): Download concurrency (default 20).
        retries (int): Per-URL retry count (default 3).
    """

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        """Download pages listed in an llms.txt manifest.

        Args:
            corpus_slug: Corpus identifier (for logging).
            fetch_config: Strategy-specific configuration dict.
            output_dir: Directory where fetched files will be written.

        Returns:
            ``output_dir`` — the directory containing the downloaded ``.md`` files.
        """
        llms_txt_url: str = fetch_config["url"]
        base_url: str = fetch_config.get("base_url") or await _derive_base_url(llms_txt_url)
        url_suffix: str = fetch_config.get("url_suffix", "")
        non_md_strategy: str = fetch_config.get("non_md_strategy", "direct")
        url_pattern: str = fetch_config.get("url_pattern") or _derive_url_pattern(
            base_url, require_md_suffix=not url_suffix and non_md_strategy == "direct"
        )
        workers: int = int(fetch_config.get("workers", DEFAULT_WORKERS))
        retries: int = int(fetch_config.get("retries", DEFAULT_RETRIES))

        jina_api_key: str | None = None
        if non_md_strategy in ("jina", "try_md"):
            jina_api_key = _jina.get_api_key()

        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        headers = {"User-Agent": USER_AGENT}

        # 1. Fetch the llms.txt manifest
        log.info("[%s] Fetching llms.txt from %s", corpus_slug, llms_txt_url)
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout, headers=headers
        ) as session:
            async with session.get(llms_txt_url) as resp:
                resp.raise_for_status()
                llms_txt_content = await resp.text()

        # Save the raw llms.txt for reference
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "_llms.txt").write_text(llms_txt_content)

        # 2. Extract doc URLs using the configured regex pattern
        sections = _parse_sections(llms_txt_content, url_pattern)
        upstream_urls = re.findall(url_pattern, llms_txt_content)

        # Apply url_suffix transform (e.g. ".md") if configured
        if url_suffix:
            upstream_urls = [u.rstrip("/") + url_suffix for u in upstream_urls]
            for section in sections:
                section["urls"] = [u.rstrip("/") + url_suffix for u in section["urls"]]

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_urls: list[str] = []
        for u in upstream_urls:
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)
        log.info("[%s] Found %d unique URLs", corpus_slug, len(unique_urls))

        # 3. Compute diff to identify removed files
        existing_manifest = load_manifest(output_dir)
        _new_urls, removed_filenames = compute_manifest_diff(unique_urls, existing_manifest)

        # 4. Delete removed files from disk
        for fn in removed_filenames:
            removed_path = output_dir / fn
            if removed_path.exists():
                removed_path.unlink()
                log.info("[%s] Deleted removed file: %s", corpus_slug, fn)

        if removed_filenames:
            log.info("[%s] Removed %d files no longer upstream", corpus_slug, len(removed_filenames))

        # 5. Re-download ALL upstream URLs to detect content changes.
        download_results: list[DownloadResult] = []
        if unique_urls:
            download_results = await _resolve_all(
                unique_urls, base_url, output_dir, non_md_strategy, workers, retries, jina_api_key
            )

        # 6. Compare content hashes against manifest to classify changes
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

        ok = sum(1 for r in download_results if r.success)
        fail = sum(1 for r in download_results if not r.success)
        log.info(
            "[%s] Sync complete: %d new, %d changed, %d unchanged, %d failed, %d removed",
            corpus_slug,
            new_count,
            changed_count,
            unchanged_count,
            fail,
            len(removed_filenames),
        )

        # 7. Write updated manifest with content hashes
        write_manifest(download_results, output_dir, sections=sections)

        if fetch_config.get("clean") and download_results:
            from doc_hub.clean import DEFAULT_CLEAN_WORKERS, clean_corpus  # noqa: PLC0415
            clean_workers = int(fetch_config.get("clean_workers", DEFAULT_CLEAN_WORKERS))
            log.info("[%s] Running LLM cleaning on fetched pages", corpus_slug)
            await clean_corpus(output_dir, workers=clean_workers)

        return output_dir
