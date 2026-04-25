"""Tests for doc_hub._builtins.parsers.markdown — MarkdownParser class.

Unit tests only — no network, no DB required.
Tests target MarkdownParser directly (heading split, manifest, URL mapping).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from doc_hub._builtins.parsers.markdown import MarkdownParser
from doc_hub.parse import Chunk
from doc_hub.protocols import Parser


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_markdown_parser_conforms_to_parser_protocol():
    """MarkdownParser conforms to the Parser protocol."""
    parser = MarkdownParser()
    assert isinstance(parser, Parser), "MarkdownParser does not match Parser protocol"


# ---------------------------------------------------------------------------
# _load_manifest()
# ---------------------------------------------------------------------------


def test_load_manifest_returns_empty_if_missing(tmp_path):
    """_load_manifest returns {} if manifest.json doesn't exist."""
    result = MarkdownParser._load_manifest(tmp_path)
    assert result == {}


def test_load_manifest_returns_file_metadata(tmp_path):
    """_load_manifest maps filename to metadata for successful entries."""
    manifest = {
        "total": 2,
        "success": 1,
        "failed": 1,
        "files": [
            {"filename": "a.md", "url": "https://example.com/a/", "success": True},
            {"filename": "b.md", "url": "https://example.com/b/", "success": False},
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    result = MarkdownParser._load_manifest(tmp_path)
    assert list(result) == ["a.md"]
    assert result["a.md"].url == "https://example.com/a/"


def test_load_manifest_ignores_failed_entries(tmp_path):
    """_load_manifest only includes entries where success=True."""
    manifest = {
        "files": [
            {"filename": "ok.md", "url": "https://example.com/ok/", "success": True},
            {"filename": "fail.md", "url": "https://example.com/fail/", "success": False},
        ]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    result = MarkdownParser._load_manifest(tmp_path)
    assert "ok.md" in result
    assert "fail.md" not in result


def test_load_manifest_returns_empty_on_invalid_json(tmp_path):
    """_load_manifest returns {} for malformed JSON."""
    (tmp_path / "manifest.json").write_text("not valid json{")
    result = MarkdownParser._load_manifest(tmp_path)
    assert result == {}


# ---------------------------------------------------------------------------
# _is_fence_marker()
# ---------------------------------------------------------------------------


def test_is_fence_marker_backtick():
    """Lines starting with ``` are fence markers."""
    assert MarkdownParser._is_fence_marker("```python")
    assert MarkdownParser._is_fence_marker("```")


def test_is_fence_marker_tilde():
    """Lines starting with ~~~ are fence markers."""
    assert MarkdownParser._is_fence_marker("~~~")
    assert MarkdownParser._is_fence_marker("~~~python")


def test_is_fence_marker_false_for_normal_lines():
    """Normal text lines are not fence markers."""
    assert not MarkdownParser._is_fence_marker("# Heading")
    assert not MarkdownParser._is_fence_marker("Some text")
    assert not MarkdownParser._is_fence_marker("")


def test_is_fence_marker_strips_leading_whitespace():
    """Leading whitespace is stripped before checking."""
    assert MarkdownParser._is_fence_marker("    ```python")


# ---------------------------------------------------------------------------
# _parse_headings()
# ---------------------------------------------------------------------------


def test_parse_headings_basic():
    """_parse_headings finds headings in simple markdown."""
    text = "# Title\n\nSome content.\n\n## Subtitle\n\nMore content."
    headings = MarkdownParser._parse_headings(text)
    assert len(headings) == 2
    assert headings[0][0] == 1  # level
    assert headings[0][1] == "Title"
    assert headings[1][0] == 2
    assert headings[1][1] == "Subtitle"


def test_parse_headings_ignores_headings_in_code_fence():
    """_parse_headings ignores # inside fenced code blocks."""
    text = "# Real Heading\n\n```python\n# Not a heading\n```\n\n## Another Real"
    headings = MarkdownParser._parse_headings(text)
    titles = [h[1] for h in headings]
    assert "Not a heading" not in titles
    assert "Real Heading" in titles
    assert "Another Real" in titles


def test_parse_headings_ignores_tilde_fence():
    """_parse_headings ignores # inside ~~~ fenced code blocks."""
    text = "# Heading\n\n~~~\n# Not a heading\n~~~\n\n## After"
    headings = MarkdownParser._parse_headings(text)
    assert len(headings) == 2


def test_parse_headings_returns_line_numbers():
    """_parse_headings returns 1-based line numbers."""
    text = "# First\n\nContent.\n\n## Second"
    headings = MarkdownParser._parse_headings(text)
    assert headings[0][3] == 1  # line 1
    assert headings[1][3] == 5  # line 5


def test_parse_headings_all_levels():
    """_parse_headings recognizes all heading levels 1-6."""
    text = "\n".join(f"{'#' * i} Level {i}" for i in range(1, 7))
    headings = MarkdownParser._parse_headings(text)
    levels = [h[0] for h in headings]
    assert levels == [1, 2, 3, 4, 5, 6]


def test_parse_headings_empty_returns_empty():
    """_parse_headings returns [] for text with no headings."""
    text = "Just some plain text without any headings."
    headings = MarkdownParser._parse_headings(text)
    assert headings == []


# ---------------------------------------------------------------------------
# _build_section_path()
# ---------------------------------------------------------------------------


def test_build_section_path_single():
    """Single heading produces a simple path."""
    stack: list[tuple[int, str]] = []
    path = MarkdownParser._build_section_path(stack, 1, "Title")
    assert path == "Title"


def test_build_section_path_nested():
    """Nested headings produce hierarchical paths."""
    stack: list[tuple[int, str]] = []
    MarkdownParser._build_section_path(stack, 1, "Parent")
    path = MarkdownParser._build_section_path(stack, 2, "Child")
    assert path == "Parent > Child"


def test_build_section_path_resets_on_same_level():
    """A heading at the same level replaces the previous sibling."""
    stack: list[tuple[int, str]] = []
    MarkdownParser._build_section_path(stack, 1, "First")
    path = MarkdownParser._build_section_path(stack, 1, "Second")
    assert path == "Second"


def test_build_section_path_resets_deeper_levels():
    """Heading at level 1 after level 2 clears the level 2 entry."""
    stack: list[tuple[int, str]] = []
    MarkdownParser._build_section_path(stack, 1, "Parent")
    MarkdownParser._build_section_path(stack, 2, "Child")
    path = MarkdownParser._build_section_path(stack, 1, "New Parent")
    assert path == "New Parent"


# ---------------------------------------------------------------------------
# _split_into_chunks()
# ---------------------------------------------------------------------------


def test_split_no_headings():
    """Files with no headings are treated as a single chunk."""
    parser = MarkdownParser()
    text = "This is a file with no headings at all."
    chunks = parser._split_into_chunks(text, "readme.md", "https://example.com/readme/")
    assert len(chunks) == 1
    assert chunks[0].heading == "readme"
    assert chunks[0].heading_level == 0


def test_split_basic_headings():
    """Split produces one chunk per heading section."""
    parser = MarkdownParser()
    text = "# Section 1\n\nContent 1.\n\n# Section 2\n\nContent 2."
    chunks = parser._split_into_chunks(text, "doc.md", "https://example.com/doc/")
    assert len(chunks) == 2
    assert chunks[0].heading == "Section 1"
    assert chunks[1].heading == "Section 2"


def test_split_captures_preamble():
    """Content before the first heading is captured as a preamble chunk."""
    parser = MarkdownParser()
    text = "Preamble content here.\n\n# First Heading\n\nSection content."
    chunks = parser._split_into_chunks(text, "doc.md", "https://example.com/doc/")
    assert len(chunks) == 2
    assert chunks[0].heading == "(preamble)"
    assert chunks[0].section_path == "(preamble)"


def test_split_source_file_and_url_propagated():
    """source_file and source_url are set on every chunk."""
    parser = MarkdownParser()
    text = "# Heading\n\nContent."
    chunks = parser._split_into_chunks(text, "my__doc.md", "https://example.com/doc/")
    for chunk in chunks:
        assert chunk.source_file == "my__doc.md"
        assert chunk.source_url == "https://example.com/doc/"


def test_split_section_path_hierarchy():
    """Nested headings produce hierarchical section paths."""
    parser = MarkdownParser()
    text = "# Parent\n\nIntro.\n\n## Child\n\nChild content."
    chunks = parser._split_into_chunks(text, "doc.md", "https://example.com/")
    assert chunks[0].section_path == "Parent"
    assert chunks[1].section_path == "Parent > Child"


def test_split_content_hash_is_sha256():
    """Each chunk's content_hash is the SHA-256 of its content."""
    parser = MarkdownParser()
    text = "# Section\n\nContent here."
    chunks = parser._split_into_chunks(text, "doc.md", "https://example.com/")
    for chunk in chunks:
        expected = hashlib.sha256(chunk.content.encode()).hexdigest()
        assert chunk.content_hash == expected


def test_split_start_line_populated():
    """start_line field is populated with the correct line number."""
    parser = MarkdownParser()
    text = "# First\n\nContent.\n\n## Second\n\nMore."
    chunks = parser._split_into_chunks(text, "doc.md", "https://example.com/")
    assert chunks[0].start_line == 1
    assert chunks[1].start_line == 5


def test_split_category_is_empty_string():
    """MarkdownParser._split_into_chunks sets category='' (core pipeline fills it)."""
    parser = MarkdownParser()
    text = "# API Reference\n\nSome content."
    chunks = parser._split_into_chunks(text, "api__models.md", "")
    for chunk in chunks:
        assert chunk.category == "", (
            f"Parser should not set category; got {chunk.category!r}"
        )


# ---------------------------------------------------------------------------
# parse() method — end-to-end
# ---------------------------------------------------------------------------


def test_parse_reads_md_files(tmp_path):
    """MarkdownParser.parse() reads .md files from input_dir."""
    parser = MarkdownParser()
    (tmp_path / "doc.md").write_text("# Title\n\nContent here.")
    chunks = parser.parse(tmp_path, corpus_slug="test", base_url="")
    assert len(chunks) >= 1


def test_parse_returns_list_of_chunks(tmp_path):
    """MarkdownParser.parse() returns a list of Chunk objects."""
    parser = MarkdownParser()
    (tmp_path / "doc.md").write_text("# Title\n\nContent.")
    result = parser.parse(tmp_path, corpus_slug="test", base_url="")
    assert isinstance(result, list)
    assert all(isinstance(c, Chunk) for c in result)


def test_parse_skips_underscore_files(tmp_path):
    """MarkdownParser.parse() skips files starting with '_'."""
    parser = MarkdownParser()
    (tmp_path / "_hidden.md").write_text("# Hidden\n\nShould not appear.")
    (tmp_path / "visible.md").write_text("# Visible\n\nShould appear.")
    chunks = parser.parse(tmp_path, corpus_slug="test", base_url="")
    source_files = [c.source_file for c in chunks]
    assert "_hidden.md" not in source_files
    assert any("visible" in sf for sf in source_files)


def test_parse_uses_manifest_for_url(tmp_path):
    """MarkdownParser.parse() reads source_url from manifest.json."""
    parser = MarkdownParser()
    (tmp_path / "guide.md").write_text("# Guide\n\nContent.")
    manifest = {
        "files": [
            {"filename": "guide.md", "url": "https://example.com/guide/", "success": True},
        ]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    chunks = parser.parse(tmp_path, corpus_slug="test", base_url="")
    guide_chunks = [c for c in chunks if c.source_file == "guide.md"]
    assert guide_chunks[0].source_url == "https://example.com/guide/"


def test_parse_filters_by_manifest(tmp_path):
    """MarkdownParser.parse() skips files not listed in manifest.json."""
    parser = MarkdownParser()
    (tmp_path / "active.md").write_text("# Active\n\nContent.")
    (tmp_path / "orphaned.md").write_text("# Orphaned\n\nContent.")
    manifest = {
        "files": [
            {"filename": "active.md", "url": "https://example.com/active/", "success": True},
        ]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    chunks = parser.parse(tmp_path, corpus_slug="test", base_url="")
    source_files = {c.source_file for c in chunks}
    assert "active.md" in source_files
    assert "orphaned.md" not in source_files


def test_parse_category_always_empty_string(tmp_path):
    """MarkdownParser.parse() always sets category='' on all chunks."""
    parser = MarkdownParser()
    (tmp_path / "api__models.md").write_text("# API\n\nContent.")
    chunks = parser.parse(tmp_path, corpus_slug="test", base_url="")
    for chunk in chunks:
        assert chunk.category == "", (
            f"Parser should not set category; got {chunk.category!r}"
        )
