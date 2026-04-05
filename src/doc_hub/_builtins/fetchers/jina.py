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
) -> DownloadResult:
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


async def fetch_all(
    urls: list[str],
    output_dir: Path,
    api_key: str,
    *,
    filename_fn: Callable[[str], str],
    workers: int = DEFAULT_WORKERS,
    retries: int = DEFAULT_RETRIES,
) -> list[DownloadResult]:
    output_dir.mkdir(parents=True, exist_ok=True)

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    connector = aiohttp.TCPConnector(limit=workers, family=socket.AF_INET)
    headers = make_headers(api_key)

    sem = asyncio.Semaphore(workers)

    async def bounded(url: str, filename: str) -> DownloadResult:
        async with sem:
            return await fetch_one(session, url, filename, output_dir, retries)

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

    for r in results:
        if r.success:
            log.info("OK: %s", r.filename)
        else:
            log.warning("FAIL: %s — %s", r.url, r.error)

    return list(results)
