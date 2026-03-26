"""Built-in markdown parser plugin for doc-hub.

Splits markdown files by headings into Chunk objects. This is the
heading-based parser extracted from the original parse.py.

Entry point name: "markdown"
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from doc_hub.parse import Chunk

log = logging.getLogger(__name__)


class MarkdownParser:
    """Parser plugin for markdown files with heading-based splitting.

    Reads .md files from an input directory, splits each by ATX headings
    (# through ######), and returns a list of raw Chunk objects.

    Files starting with '_' are skipped (e.g. _llms.txt manifest).
    When a manifest.json is present, only files listed in it are parsed
    (prevents indexing orphaned files after upstream removal).
    """

    def parse(
        self,
        input_dir: Path,
        *,
        corpus_slug: str,
        base_url: str,
    ) -> list[Chunk]:
        """Parse markdown files into raw chunks.

        Args:
            input_dir: Directory containing .md files.
            corpus_slug: Corpus identifier (for logging).
            base_url: Base URL for source_url reconstruction.

        Returns:
            List of raw Chunk objects (before size optimization).
        """
        url_map = self._load_manifest(input_dir)

        if url_map:
            md_files = sorted(
                input_dir / fn for fn in url_map
                if (input_dir / fn).exists()
            )
        else:
            md_files = sorted(input_dir.glob("*.md"))

        md_files = [f for f in md_files if not f.name.startswith("_")]
        log.info("[%s] Parsing %d markdown files", corpus_slug, len(md_files))

        all_chunks: list[Chunk] = []
        for md_file in md_files:
            text = md_file.read_text(errors="replace")
            source_url = url_map.get(md_file.name, "")
            chunks = self._split_into_chunks(text, md_file.name, source_url)
            all_chunks.extend(chunks)

        log.info(
            "[%s] Raw chunks from heading split: %d",
            corpus_slug,
            len(all_chunks),
        )
        return all_chunks

    # --- Private helpers (moved from parse.py) ---

    @staticmethod
    def _load_manifest(input_dir: Path) -> dict[str, str]:
        """Load manifest.json → {filename: url} for successful entries."""
        manifest_path = input_dir / "manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            data = json.loads(manifest_path.read_text())
            return {
                f["filename"]: f["url"]
                for f in data.get("files", [])
                if f.get("success", False)
            }
        except (json.JSONDecodeError, KeyError):
            return {}

    @staticmethod
    def _is_fence_marker(line: str) -> bool:
        """Return True if line starts/ends a fenced code block."""
        stripped = line.strip()
        return stripped.startswith("```") or stripped.startswith("~~~")

    @staticmethod
    def _parse_headings(text: str) -> list[tuple[int, str, int, int]]:
        """Find all markdown headings and their line positions.

        Tracks fenced code blocks so that ``# comments`` inside code blocks
        are never treated as heading boundaries.

        Args:
            text: Full markdown document text.

        Returns:
            List of (level, heading_text, start_pos, line_number) tuples.
        """
        headings: list[tuple[int, str, int, int]] = []
        in_fence = False

        for i, line in enumerate(text.split("\n")):
            if MarkdownParser._is_fence_marker(line):
                in_fence = not in_fence
                continue

            if in_fence:
                continue

            m = re.match(r"^(#{1,6})\s+(.+)$", line)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()
                pos = sum(len(l) + 1 for l in text.split("\n")[:i])
                headings.append((level, title, pos, i + 1))

        return headings

    @staticmethod
    def _build_section_path(
        headings_stack: list[tuple[int, str]],
        level: int,
        title: str,
    ) -> str:
        """Maintain heading hierarchy and return current section path.

        Args:
            headings_stack: Mutable list of (level, title) pairs (modified in place).
            level: Heading level of the current heading.
            title: Text of the current heading.

        Returns:
            Section path as " > " joined heading titles.
        """
        while headings_stack and headings_stack[-1][0] >= level:
            headings_stack.pop()
        headings_stack.append((level, title))
        return " > ".join(h[1] for h in headings_stack)

    @staticmethod
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
    ) -> Chunk:
        """Construct a Chunk with computed hash. Category is always "".

        The parser MUST NOT derive category — that's the core pipeline's
        job (parse.py:derive_category). Setting category="" here signals
        to the core pipeline that category needs to be filled in.
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
            category="",  # INTENTIONALLY EMPTY — core pipeline fills this
        )

    def _split_into_chunks(
        self,
        text: str,
        source_file: str,
        source_url: str,
    ) -> list[Chunk]:
        """Split a markdown document into chunks at heading boundaries."""
        headings = self._parse_headings(text)
        lines = text.split("\n")
        chunks: list[Chunk] = []

        if not headings:
            # No headings — treat entire file as one chunk
            chunks.append(self._make_chunk(
                source_file=source_file,
                source_url=source_url,
                section_path=source_file.removesuffix(".md"),
                heading=source_file.removesuffix(".md"),
                heading_level=0,
                content=text,
                start_line=1,
                end_line=len(lines),
            ))
            return chunks

        # If there's content before the first heading, capture it
        first_heading_line = headings[0][3]
        if first_heading_line > 1:
            preamble = "\n".join(lines[:first_heading_line - 1]).strip()
            if preamble:
                chunks.append(self._make_chunk(
                    source_file=source_file,
                    source_url=source_url,
                    section_path="(preamble)",
                    heading="(preamble)",
                    heading_level=0,
                    content=preamble,
                    start_line=1,
                    end_line=first_heading_line - 1,
                ))

        headings_stack: list[tuple[int, str]] = []

        for idx, (level, title, _pos, line_num) in enumerate(headings):
            start = line_num - 1  # 0-indexed
            if idx + 1 < len(headings):
                end = headings[idx + 1][3] - 1  # 0-indexed, exclusive
            else:
                end = len(lines)

            content = "\n".join(lines[start:end]).strip()
            section_path = self._build_section_path(headings_stack, level, title)

            # end_line is the last line of this section (1-indexed, inclusive).
            # 'end' is 0-indexed exclusive, so end == 1-indexed inclusive.
            end_line_num = end

            chunks.append(self._make_chunk(
                source_file=source_file,
                source_url=source_url,
                section_path=section_path,
                heading=title,
                heading_level=level,
                content=content,
                start_line=line_num,
                end_line=end_line_num,
            ))

        return chunks
