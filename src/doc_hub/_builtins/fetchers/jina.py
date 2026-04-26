"""Jina Reader API client for doc-hub fetchers."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
from pathlib import Path
from typing import Callable, NamedTuple

import aiohttp

from doc_hub.versions import utc_now_iso

log = logging.getLogger(__name__)

JINA_READER_PREFIX = "https://r.jina.ai/"
USER_AGENT = "doc-hub-fetcher/1.0"
DEFAULT_WORKERS = 5
DEFAULT_RETRIES = 3
RETRY_AFTER_DEFAULT = 2
TIMEOUT = 30


class DownloadResult(NamedTuple):
    url: str
    filename: str
    success: bool
    error: str | None = None
    content_hash: str | None = None
    skipped: bool = False
    fetched_at: str | None = None
    source_version: str | None = None
    resolved_version: str | None = None


def get_api_key() -> str:
    key = os.environ.get("JINA_API_KEY")
    if not key:
        raise ValueError(
            "JINA_API_KEY environment variable is required for Jina Reader. "
            "Get your key at https://jina.ai/api-dashboard/key-manager"
        )
    return key


def make_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/markdown",
        "User-Agent": USER_AGENT,
    }


async def fetch_one(
    session: aiohttp.ClientSession,
    url: str,
    filename: str,
    output_dir: Path,
    retries: int = DEFAULT_RETRIES,
    *,
    skip_existing: bool = True,
    fetched_at: str | None = None,
    source_version: str | None = None,
    resolved_version: str | None = None,
) -> DownloadResult:
    outpath = output_dir / filename
    fetched_at = fetched_at or utc_now_iso()

    if skip_existing and outpath.exists():
        content_hash = hashlib.sha256(outpath.read_bytes()).hexdigest()
        return DownloadResult(
            url=url,
            filename=filename,
            success=True,
            content_hash=content_hash,
            skipped=True,
            fetched_at=fetched_at,
            source_version=source_version,
            resolved_version=resolved_version,
        )

    jina_url = f"{JINA_READER_PREFIX}{url}"
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
            return DownloadResult(
                url=url,
                filename=filename,
                success=True,
                content_hash=content_hash,
                fetched_at=fetched_at,
                source_version=source_version,
                resolved_version=resolved_version,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            last_error = str(exc)
            if attempt < retries:
                log.debug("Retry %d/%d for %s: %s", attempt, retries, url, last_error)

    return DownloadResult(
        url=url,
        filename=filename,
        success=False,
        error=last_error,
        fetched_at=fetched_at,
        source_version=source_version,
        resolved_version=resolved_version,
    )


async def fetch_all(
    urls: list[str],
    output_dir: Path,
    api_key: str,
    *,
    filename_fn: Callable[[str], str],
    workers: int = DEFAULT_WORKERS,
    retries: int = DEFAULT_RETRIES,
    skip_existing: bool = True,
    fetched_at: str | None = None,
    source_version: str | None = None,
    resolved_version: str | None = None,
) -> list[DownloadResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = fetched_at or utc_now_iso()

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    connector = aiohttp.TCPConnector(limit=workers, family=socket.AF_INET)
    headers = make_headers(api_key)

    sem = asyncio.Semaphore(workers)
    total = len(urls)
    completed = 0

    async def bounded(url: str, filename: str) -> DownloadResult:
        nonlocal completed
        async with sem:
            result = await fetch_one(
                session,
                url,
                filename,
                output_dir,
                retries,
                skip_existing=skip_existing,
                fetched_at=fetched_at,
                source_version=source_version,
                resolved_version=resolved_version,
            )
        completed += 1
        if not result.success:
            log.warning("[%d/%d] FAIL: %s — %s", completed, total, result.url, result.error)
        elif result.skipped:
            log.debug("[%d/%d] skip: %s", completed, total, result.filename)
        else:
            log.info("[%d/%d] OK: %s", completed, total, result.filename)
        return result

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        headers=headers,
    ) as session:
        tasks = [
            asyncio.create_task(bounded(url, filename_fn(url)))
            for url in urls
        ]
        results = await asyncio.gather(*tasks)

    fetched = sum(1 for r in results if r.success and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    failed  = sum(1 for r in results if not r.success)
    if skipped:
        log.info("Download complete: %d fetched, %d already on disk, %d failed", fetched, skipped, failed)
    else:
        log.info("Download complete: %d/%d succeeded, %d failed", fetched, total, failed)

    return list(results)
