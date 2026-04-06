"""Built-in git_repo fetcher plugin for doc-hub.

Fetches markdown files from a GitHub repository (or any GitHub-hosted subdir)
using the GitHub Trees API + raw.githubusercontent.com — no git binary required.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger(__name__)

TIMEOUT = 30
USER_AGENT = "doc-hub-fetcher/1.0"


def _parse_github_url(url: str) -> tuple[str, str, str]:
    """Parse a GitHub repo URL and return (owner, repo, branch).

    Accepts:
      https://github.com/owner/repo
      https://github.com/owner/repo/tree/branch/...
    """
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"Cannot parse GitHub URL: {url!r}")
    owner, repo = parts[0], parts[1]
    # /tree/<branch>/... form
    if len(parts) >= 4 and parts[2] == "tree":
        branch = parts[3]
    else:
        branch = "main"
    return owner, repo, branch


def _subdir_from_url(url: str) -> str:
    """Extract the subdir path from a GitHub tree URL.

    e.g. https://github.com/owner/repo/tree/main/docs  →  "docs"
    """
    parts = urlparse(url).path.strip("/").split("/")
    # parts: owner repo tree branch [subdir...]
    if len(parts) >= 5 and parts[2] == "tree":
        return "/".join(parts[4:])
    return ""


def _rel_to_filename(rel_path: str) -> str:
    """Convert a relative path like api/backends.md to api__backends.md."""
    if rel_path.endswith("/index.md"):
        stem = rel_path.removesuffix("/index.md")
    elif rel_path.endswith(".md"):
        stem = rel_path.removesuffix(".md")
    else:
        stem = rel_path
    return stem.replace("/", "__") + ".md"


class GitRepoFetcher:
    """Fetcher plugin for GitHub repositories.

    Uses the GitHub Trees API to enumerate files and raw.githubusercontent.com
    to download them. No git binary is required.

    Entry point name: "git_repo"

    Required fetch_config keys:
        url (str): GitHub URL — either a repo root or a tree URL pointing at a
                   specific branch + subdirectory.
                   e.g. "https://github.com/owner/repo/tree/main/docs"

    Optional fetch_config keys:
        subdir (str):  Subdirectory within the repo to restrict fetching to.
                       Derived automatically from the URL if it contains a tree path.
        branch (str):  Branch/tag/SHA to fetch (default: derived from URL or "main").
        extensions (list[str]): File extensions to include (default: [".md"]).
        github_token (str): Personal access token for private repos or higher rate
                            limits. Overrides the GITHUB_TOKEN / GH_TOKEN env vars
                            for this specific corpus.
    """

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        url: str = fetch_config["url"]
        owner, repo, branch = _parse_github_url(url)

        # subdir: explicit config wins, then derived from URL tree path
        subdir: str = fetch_config.get("subdir") or _subdir_from_url(url)
        subdir = subdir.strip("/")

        extensions: list[str] = fetch_config.get("extensions", [".md"])
        # fetch_config token wins (per-repo override), then fall back to env vars
        token: str | None = (
            fetch_config.get("github_token")
            or os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
        )

        output_dir.mkdir(parents=True, exist_ok=True)

        headers: dict[str, str] = {"User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        api_url = (
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        )

        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        connector = aiohttp.TCPConnector(family=socket.AF_INET)

        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout, headers=headers
        ) as session:
            # Step 1: enumerate tree
            async with session.get(api_url) as resp:
                resp.raise_for_status()
                tree_data = await resp.json()

            all_blobs: list[dict] = [
                item for item in tree_data.get("tree", []) if item["type"] == "blob"
            ]

            # Filter to subdir + desired extensions
            prefix = (subdir + "/") if subdir else ""
            blobs = [
                b for b in all_blobs
                if b["path"].startswith(prefix)
                and any(b["path"].endswith(ext) for ext in extensions)
            ]

            log.info(
                "[%s] GitHub tree: %d total blobs, %d matching %s under %r",
                corpus_slug,
                len(all_blobs),
                len(blobs),
                extensions,
                subdir or "/",
            )

            # Step 2: download each file
            results: list[dict[str, Any]] = []
            raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"

            for blob in blobs:
                path_in_repo: str = blob["path"]
                rel = path_in_repo.removeprefix(prefix)
                filename = _rel_to_filename(rel)
                raw_url = f"{raw_base}/{path_in_repo}"
                outpath = output_dir / filename

                try:
                    async with session.get(raw_url) as resp:
                        resp.raise_for_status()
                        content = await resp.read()
                    outpath.write_bytes(content)
                    content_hash = hashlib.sha256(content).hexdigest()
                    log.info("[%s] OK: %s → %s", corpus_slug, path_in_repo, filename)
                    results.append(
                        {
                            "url": raw_url,
                            "filename": filename,
                            "success": True,
                            "error": None,
                            "content_hash": content_hash,
                        }
                    )
                except (aiohttp.ClientError, OSError) as exc:
                    log.warning("[%s] FAIL: %s — %s", corpus_slug, path_in_repo, exc)
                    results.append(
                        {
                            "url": raw_url,
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
