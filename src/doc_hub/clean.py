"""LLM-based markdown cleaning for doc-hub.

Sends raw markdown (typically fetched via Jina Reader) through an
OpenAI-compatible LLM to strip navigation, footers, and other artifacts
while preserving documentation content.

Configuration is via environment variables:
    DOC_HUB_CLEAN_MODEL     Model slug (required when cleaning)
    DOC_HUB_CLEAN_API_KEY   API key (required when cleaning)
    DOC_HUB_CLEAN_BASE_URL  Base URL for the API (required when cleaning)
    DOC_HUB_CLEAN_PROMPT    System prompt (optional, has a built-in default)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from openai import AsyncOpenAI

log = logging.getLogger(__name__)

DEFAULT_CLEAN_WORKERS = 5

DEFAULT_CLEAN_PROMPT = """\
You are a web content extractor. You will be given raw markdown scraped from a webpage. Your job is to return ONLY the main page content as clean markdown.

## What to strip

- Navigation menus, sidebars, and breadcrumbs (lists of internal links, "Docs" nav trees)
- Search bars, theme toggles (Light/Dark/System), and UI chrome
- Footer content (copyright notices, "Powered by" attributions, donation links)
- "Links/Buttons" reference sections listing URLs by index
- Scraping artifacts like 【1†label†url】 markers — remove these inline without leaving gaps
- Table of contents or "Contents" sections that merely re-list page headings
- Previous/Next page navigation links
- Any repeated or duplicated menu content

## What to preserve

- The page title as a single top-level heading
- All body text, paragraphs, and explanations — verbatim
- Tables that are part of the content (not navigation)
- Code blocks and inline code in full — never truncate
- Section headings within the main content
- Tips, notes, warnings, and callouts
- Images or image references that are part of the content
- Long lists and enumerations — reproduce them completely

## Critical output rules

1. Return ONLY the cleaned markdown. Nothing else.
2. Do NOT add any preamble ("Here is the content", "Sure!", "I've extracted…").
3. Do NOT add any postamble ("Let me know if…", "Hope this helps").
4. Do NOT add meta-commentary about what you removed or how you processed the content.
5. Do NOT wrap the output in ```markdown``` code fences.
6. Do NOT summarize, paraphrase, or rewrite — extract the original text as-is.
7. Do NOT add, invent, or infer content that isn't on the page.
8. Do NOT reorder sections — preserve the original sequence.
9. Do NOT change heading levels, convert tables to lists, or alter markdown formatting.
10. Do NOT truncate long code blocks or lists with "..." or "[remaining items]". Reproduce them in full.
11. Do NOT add wrapper headings like "## Extracted Content" around the output.
12. If the content is ambiguous, keep it. Err on the side of inclusion over removal.

Your entire response must be the cleaned markdown content and absolutely nothing else."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CleanConfig:
    """Configuration for the LLM cleaning step."""

    model: str
    api_key: str
    base_url: str
    prompt: str


def get_clean_config() -> CleanConfig:
    """Read cleaning configuration from environment variables.

    Raises:
        ValueError: If any required variable is missing.
    """
    model = os.environ.get("DOC_HUB_CLEAN_MODEL")
    api_key = os.environ.get("DOC_HUB_CLEAN_API_KEY")
    base_url = os.environ.get("DOC_HUB_CLEAN_BASE_URL")
    prompt = os.environ.get("DOC_HUB_CLEAN_PROMPT", DEFAULT_CLEAN_PROMPT)

    missing = []
    if not model:
        missing.append("DOC_HUB_CLEAN_MODEL")
    if not api_key:
        missing.append("DOC_HUB_CLEAN_API_KEY")
    if not base_url:
        missing.append("DOC_HUB_CLEAN_BASE_URL")

    if missing:
        raise ValueError(
            f"Missing required environment variable(s) for LLM cleaning: "
            f"{', '.join(missing)}. "
            f"Set these in your .env file or environment."
        )

    return CleanConfig(
        model=model,  # type: ignore[arg-type]
        api_key=api_key,  # type: ignore[arg-type]
        base_url=base_url,  # type: ignore[arg-type]
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# Core cleaning
# ---------------------------------------------------------------------------


class CleanResult(NamedTuple):
    """Result of cleaning a single file."""

    filename: str
    success: bool
    error: str | None = None


async def clean_markdown(content: str, config: CleanConfig) -> str:
    """Send markdown through an LLM to clean it.

    Args:
        content: Raw markdown content.
        config:  LLM cleaning configuration.

    Returns:
        Cleaned markdown string.
    """
    client = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)
    response = await client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": config.prompt},
            {"role": "user", "content": content},
        ],
    )
    return response.choices[0].message.content or ""


async def clean_corpus(
    output_dir: Path,
    *,
    workers: int = DEFAULT_CLEAN_WORKERS,
) -> list[CleanResult]:
    """Clean markdown files in a corpus directory.

    Reads the manifest to determine which files need cleaning (where
    ``content_hash != clean_hash``), sends them through the LLM, writes
    the cleaned content back, and updates the manifest with ``clean_hash``.

    Args:
        output_dir: Path to the corpus raw/ directory containing .md files
                    and manifest.json.
        workers:    Concurrency limit for LLM API calls.

    Returns:
        List of CleanResult for each file that was processed.
    """
    config = get_clean_config()

    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        log.warning("No manifest.json found in %s — nothing to clean", output_dir)
        return []

    manifest_data = json.loads(manifest_path.read_text())
    files = manifest_data.get("files", [])

    # Find files that need cleaning
    to_clean: list[dict] = []
    for entry in files:
        if not entry.get("success"):
            continue
        content_hash = entry.get("content_hash")
        clean_hash = entry.get("clean_hash")
        if content_hash and content_hash != clean_hash:
            to_clean.append(entry)

    if not to_clean:
        log.info("All files already clean — nothing to do")
        return []

    log.info("Cleaning %d of %d files", len(to_clean), len(files))

    sem = asyncio.Semaphore(workers)

    async def _clean_one(entry: dict) -> CleanResult:
        filename = entry["filename"]
        filepath = output_dir / filename
        async with sem:
            try:
                if not filepath.exists():
                    return CleanResult(filename=filename, success=False, error="file not found")

                raw_content = filepath.read_text(encoding="utf-8")
                cleaned = await clean_markdown(raw_content, config)
                filepath.write_text(cleaned, encoding="utf-8")
                log.info("Cleaned: %s", filename)
                return CleanResult(filename=filename, success=True)
            except Exception as exc:
                log.warning("Failed to clean %s: %s", filename, exc)
                return CleanResult(filename=filename, success=False, error=str(exc))

    tasks = [asyncio.create_task(_clean_one(entry)) for entry in to_clean]
    results = await asyncio.gather(*tasks)

    # Update manifest with clean_hash for successfully cleaned files
    cleaned_set = {r.filename for r in results if r.success}
    for entry in files:
        if entry["filename"] in cleaned_set:
            entry["clean_hash"] = entry["content_hash"]

    manifest_path.write_text(json.dumps(manifest_data, indent=2))
    log.info("Manifest updated with clean hashes")

    ok = sum(1 for r in results if r.success)
    fail = sum(1 for r in results if not r.success)
    log.info("Clean complete: %d succeeded, %d failed", ok, fail)

    return list(results)
