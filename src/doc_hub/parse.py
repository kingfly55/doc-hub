"""Core parse pipeline for doc-hub.

Orchestrates: parser plugin → size optimization → dedup → category → JSONL.

The parser plugin (resolved from the corpus's 'parser' field) handles
file-to-chunk conversion. This module handles everything after that:
- Category derivation from source filenames
- Merge tiny chunks (< threshold)
- Split mega chunks (> threshold)
- Deduplication by content hash
- Warning for chunks near embedding token limits
- JSONL output

The Chunk dataclass lives here as it's a core data type used by all
stages downstream of parsing.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

from doc_hub.paths import chunks_dir

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    """A single searchable section of documentation."""

    source_file: str       # Original filename (e.g. "models__openai.md")
    source_url: str        # Original URL from manifest
    section_path: str      # Heading hierarchy (e.g. "Configuration > API Keys")
    heading: str           # The section heading text
    heading_level: int     # 1-6
    content: str           # Full text including the heading
    start_line: int        # Line number in source file (1-indexed)
    end_line: int          # Last line number in source file (1-indexed, inclusive)
    char_count: int        # Length of content
    content_hash: str      # SHA-256 hex digest of content (for embed cache)
    category: str          # Content category (api, example, eval, guide, other)
    snapshot_id: str = "legacy"
    source_version: str = "latest"
    fetched_at: str | None = None


# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------


def derive_category(source_file: str) -> str:
    """Derive a category string from the source filename.

    Rules (applied in priority order):
      - filename contains 'api' or 'reference'  -> 'api'
      - filename contains 'example' or 'tutorial' -> 'example'
      - filename contains 'eval'                -> 'eval'
      - guide-like names (install, config, migration, etc.) -> 'guide'
      - anything else                           -> 'other'
    """
    name = source_file.lower().removesuffix(".md")

    if "api" in name or "reference" in name:
        return "api"
    if "example" in name or "tutorial" in name:
        return "example"
    if "eval" in name:
        return "eval"
    guide_keywords = (
        "install", "config", "migration", "quickstart", "getting-started",
        "getting_started", "setup", "guide", "how-to", "howto",
        "changelog", "contributing", "readme",
    )
    if any(kw in name for kw in guide_keywords):
        return "guide"
    return "other"


# ---------------------------------------------------------------------------
# Embedding input construction (CRITICAL — must match original exactly)
# ---------------------------------------------------------------------------


def embedding_input(chunk: Chunk) -> str:
    """Return the text to pass to the embedding API for this chunk.

    Prepends a short contextual prefix (document name + section path) to
    improve retrieval quality by giving the model explicit document context.

    This prefix is critical for embedding quality — omitting or changing it
    will produce different embeddings than the original, invalidating caches.

    Format: "Document: {doc_name} | Section: {section_path}\\n\\n{content}"
    where doc_name replaces '__' with '/' and strips the '.md' extension.
    """
    doc_name = chunk.source_file.removesuffix(".md").replace("__", "/")
    prefix = f"Document: {doc_name} | Section: {chunk.section_path}\n\n"
    return prefix + chunk.content


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_fence_marker(line: str) -> bool:
    """Return True if the stripped line starts/ends a fenced code block."""
    stripped = line.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _make_chunk(
    *,
    source_file: str,
    source_url: str,
    section_path: str,
    heading: str,
    heading_level: int,
    content: str,
    start_line: int,
    end_line: int | None = None,
    snapshot_id: str = "legacy",
    source_version: str = "latest",
    fetched_at: str | None = None,
) -> Chunk:
    """Construct a Chunk, computing content_hash, category, and end_line automatically.

    If *end_line* is not provided it is derived from *start_line* + the number
    of newlines in *content*.

    Used by merge/split helpers — sets category via derive_category().
    """
    if end_line is None:
        end_line = start_line + content.count("\n")
    return Chunk(
        source_file=source_file,
        source_url=source_url,
        section_path=section_path,
        heading=heading,
        heading_level=heading_level,
        content=content,
        start_line=start_line,
        end_line=end_line,
        char_count=len(content),
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        category=derive_category(source_file),
        snapshot_id=snapshot_id,
        source_version=source_version,
        fetched_at=fetched_at,
    )


# ---------------------------------------------------------------------------
# Chunk size optimization
# ---------------------------------------------------------------------------


def _find_safe_split(content: str, target: int = 1500, max_search: int = 6000) -> int:
    """Find a paragraph-boundary split point near *target* chars.

    Strategy (in order of preference):
    1. A ``\\n\\n`` boundary outside a fenced code block, within ``max_search``
       chars of ``target``.
    2. Any ``\\n\\n`` within ``max_search`` chars of ``target`` (even inside a
       fence) — for chunks whose entire content is one big code block.
    3. The first ``\\n`` within the window (for dense content with no blank lines).
    4. The first ``\\n\\n`` outside a fence anywhere in the content (searching
       from position 0, for chunks where all boundaries precede ``target``).
    5. Any ``\\n\\n`` anywhere in the content.
    6. Any ``\\n`` anywhere in the content.
    7. ``len(content)`` — truly unsplittable; caller keeps the remainder as-is.

    Returns:
        The position just after the split point (start of the next paragraph).
    """
    # Build a set of character ranges that are inside a fence
    fence_ranges: list[tuple[int, int]] = []
    in_fence = False
    fence_start = 0
    char_pos = 0
    for line in content.split("\n"):
        line_end = char_pos + len(line)
        if _is_fence_marker(line):
            if not in_fence:
                in_fence = True
                fence_start = char_pos
            else:
                in_fence = False
                fence_ranges.append((fence_start, line_end))
        char_pos = line_end + 1  # +1 for the \n

    # If file ended while still inside a fence, the remainder is fenced
    if in_fence:
        fence_ranges.append((fence_start, len(content)))

    def _inside_fence(pos: int) -> bool:
        return any(start <= pos < end for start, end in fence_ranges)

    search_start = min(target, len(content))
    search_end = search_start + max_search

    # Pass 1: safe split within window [search_start, search_end]
    idx = content.find("\n\n", search_start)
    while idx != -1 and idx <= search_end:
        if not _inside_fence(idx):
            return idx + 2
        idx = content.find("\n\n", idx + 1)

    # Pass 2: any \n\n within window (even inside a fence)
    idx = content.find("\n\n", search_start)
    if idx != -1 and idx <= search_end:
        return idx + 2

    # Pass 3: single \n within the window (for dense content with no paragraph
    # breaks, e.g. a long type-alias literal or API reference without blank lines)
    idx = content.find("\n", search_start)
    if idx != -1 and idx <= search_end:
        return idx + 1

    # Pass 4: safe \n\n anywhere from start (for chunks where all double-newlines
    # are before the target window)
    idx = content.find("\n\n", 0)
    while idx != -1:
        if not _inside_fence(idx):
            return idx + 2
        idx = content.find("\n\n", idx + 1)

    # Pass 5: any \n\n anywhere in the content
    idx = content.find("\n\n", 0)
    if idx != -1:
        return idx + 2

    # Pass 6: any \n anywhere in the content
    idx = content.find("\n", 0)
    if idx != -1:
        return idx + 1

    return len(content)  # truly unsplittable


def _split_mega_chunk(chunk: Chunk, max_chars: int = 6000, target: int = 1500) -> list[Chunk]:
    """Split a chunk that exceeds *max_chars* at safe paragraph boundaries.

    Returns a list of sub-chunks. Each inherits the parent's metadata;
    the section_path remains unchanged (no "(continued)" suffix needed since
    the embedding_input already encodes the section context).

    Sub-chunks get accurate start_line/end_line values computed from the
    number of newlines consumed so far within the parent chunk's content.
    """
    content = chunk.content
    sub_chunks: list[Chunk] = []
    offset = 0
    # Track which source line we're at relative to the parent chunk
    current_line = chunk.start_line

    while len(content) - offset > max_chars:
        split_at = _find_safe_split(content[offset:], target, max_search=max_chars)
        if split_at >= len(content) - offset or split_at == 0:
            # Can't split further — keep remainder as-is
            break
        piece = content[offset: offset + split_at]
        piece_stripped = piece.strip()
        # Count leading newlines that get stripped off
        leading_newlines = piece[:len(piece) - len(piece.lstrip())].count("\n")
        sub_start = current_line + leading_newlines
        sub_chunks.append(_make_chunk(
            source_file=chunk.source_file,
            source_url=chunk.source_url,
            section_path=chunk.section_path,
            heading=chunk.heading,
            heading_level=chunk.heading_level,
            content=piece_stripped,
            start_line=sub_start,
            snapshot_id=chunk.snapshot_id,
            source_version=chunk.source_version,
            fetched_at=chunk.fetched_at,
        ))
        # Advance current_line by the newlines in the raw (unstripped) piece
        current_line += piece.count("\n")
        offset += split_at

    # Remaining content (could still be > max_chars if no split point found)
    remainder = content[offset:]
    remainder_stripped = remainder.strip()
    if remainder_stripped:
        leading_newlines = remainder[:len(remainder) - len(remainder.lstrip())].count("\n")
        sub_start = current_line + leading_newlines
        sub_chunks.append(_make_chunk(
            source_file=chunk.source_file,
            source_url=chunk.source_url,
            section_path=chunk.section_path,
            heading=chunk.heading,
            heading_level=chunk.heading_level,
            content=remainder_stripped,
            start_line=sub_start,
            snapshot_id=chunk.snapshot_id,
            source_version=chunk.source_version,
            fetched_at=chunk.fetched_at,
        ))

    return sub_chunks if sub_chunks else [chunk]


def _merge_tiny_chunks(chunks: list[Chunk], min_chars: int = 200) -> list[Chunk]:
    """Pass 1 — merge chunks shorter than *min_chars* into their predecessor.

    Rules:
    - Only merge within the same source_file.
    - The absorbed chunk's content is appended to the preceding chunk.
    - The merged chunk keeps the preceding chunk's section_path and heading.
    - Repeat until stable (cascading micro-sections).

    Args:
        chunks: List of chunks to process.
        min_chars: Minimum character count threshold.

    Returns:
        New list of chunks with tiny chunks merged.
    """
    changed = True
    while changed:
        changed = False
        new_chunks: list[Chunk] = []
        i = 0
        while i < len(chunks):
            chunk = chunks[i]
            if (
                chunk.char_count < min_chars
                and new_chunks
                and new_chunks[-1].source_file == chunk.source_file
            ):
                # Merge into previous chunk
                prev = new_chunks[-1]
                merged_content = prev.content + "\n\n" + chunk.content
                new_chunks[-1] = _make_chunk(
                    source_file=prev.source_file,
                    source_url=prev.source_url,
                    section_path=prev.section_path,
                    heading=prev.heading,
                    heading_level=prev.heading_level,
                    content=merged_content,
                    start_line=prev.start_line,
                    end_line=chunk.end_line,
                    snapshot_id=prev.snapshot_id,
                    source_version=prev.source_version,
                    fetched_at=prev.fetched_at,
                )
                changed = True
            else:
                new_chunks.append(chunk)
            i += 1
        chunks = new_chunks

    return chunks


def _split_mega_chunks(chunks: list[Chunk], max_chars: int = 6000, target: int = 1500) -> list[Chunk]:
    """Pass 2 — split any chunk longer than *max_chars*.

    Splitting is done at safe paragraph boundaries (never inside a code fence
    where possible; single-newline fallback for dense code with no blank lines).

    Args:
        chunks: List of chunks to process.
        max_chars: Maximum character count threshold.
        target: Target character count for split sub-chunks.

    Returns:
        New list of chunks with oversized chunks split.
    """
    result: list[Chunk] = []
    for chunk in chunks:
        if chunk.char_count > max_chars:
            result.extend(_split_mega_chunk(chunk, max_chars, target))
        else:
            result.append(chunk)
    return result


def _warn_large_chunks(chunks: list[Chunk], warn_chars: int = 7000) -> None:
    """Emit a warning for any chunk approaching the embedding token limit."""
    for chunk in chunks:
        if chunk.char_count > warn_chars:
            log.warning(
                "Chunk near token limit (%d chars): %s / %s",
                chunk.char_count,
                chunk.source_file,
                chunk.section_path,
            )


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def write_chunks_jsonl(chunks: list[Chunk], output_path: Path) -> None:
    """Write all chunks as JSONL for programmatic search.

    Args:
        chunks: List of Chunk objects to write.
        output_path: Path to the output JSONL file.
    """
    with output_path.open("w") as f:
        for chunk in chunks:
            f.write(json.dumps(asdict(chunk)) + "\n")
    log.info("Wrote %d chunks to %s", len(chunks), output_path)


# ---------------------------------------------------------------------------
# Core parse pipeline entry point
# ---------------------------------------------------------------------------


def parse_docs(
    corpus_slug: str,
    raw_path: Path,
    parser_name: str = "markdown",
    base_url: str = "",
    *,
    snapshot_id: str | None = None,
) -> list[Chunk]:
    """Parse raw files into optimized, deduplicated chunks.

    1. Resolve the parser plugin by name
    2. Call parser.parse() to get raw chunks
    3. Derive categories
    4. Merge tiny chunks (< 500 chars)
    5. Split mega chunks (> 2500 chars)
    6. Deduplicate by content hash
    7. Write chunks.jsonl

    Args:
        corpus_slug: Corpus slug (for paths and logging).
        raw_path: Directory containing raw files from the fetch stage.
        parser_name: Name of the parser plugin to use (default "markdown").
        base_url: Base URL for reconstructing source URLs from filenames.
            Passed through to the parser plugin. The caller (pipeline.py)
            reads this from corpus.fetch_config.get("base_url", "").

    Returns:
        List of optimized, deduplicated Chunk objects.
    """
    from doc_hub.discovery import get_registry

    registry = get_registry()
    parser = registry.get_parser(parser_name)

    # Step 1: Parser produces raw chunks
    all_chunks = parser.parse(raw_path, corpus_slug=corpus_slug, base_url=base_url)

    if snapshot_id is not None:
        for chunk in all_chunks:
            chunk.snapshot_id = snapshot_id

    # Step 2: Derive categories (parser leaves category empty)
    for chunk in all_chunks:
        if not chunk.category:
            chunk.category = derive_category(chunk.source_file)

    log.info("[%s] Initial chunks: %d", corpus_slug, len(all_chunks))

    # Step 3: Merge tiny chunks (< 500 chars)
    all_chunks = _merge_tiny_chunks(all_chunks, min_chars=500)
    log.info("[%s] After merge (< 500): %d chunks", corpus_slug, len(all_chunks))

    # Step 4: Split mega chunks (> 2500 chars)
    all_chunks = _split_mega_chunks(all_chunks, max_chars=2500, target=1000)
    log.info("[%s] After split (> 2500): %d chunks", corpus_slug, len(all_chunks))

    # Step 5: Deduplicate by content hash
    seen_hashes: set[str] = set()
    deduped: list[Chunk] = []
    for chunk in all_chunks:
        if chunk.content_hash not in seen_hashes:
            seen_hashes.add(chunk.content_hash)
            deduped.append(chunk)
    dup_count = len(all_chunks) - len(deduped)
    if dup_count:
        log.info(
            "[%s] Removed %d exact-content duplicates (%.1f%%)",
            corpus_slug,
            dup_count,
            100 * dup_count / len(all_chunks),
        )
    all_chunks = deduped

    _warn_large_chunks(all_chunks)

    # Step 6: Write output
    output_dir = chunks_dir(corpus_slug, snapshot_id=snapshot_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_chunks_jsonl(all_chunks, output_dir / "chunks.jsonl")

    log.info("[%s] Total: %d chunks", corpus_slug, len(all_chunks))
    return all_chunks
