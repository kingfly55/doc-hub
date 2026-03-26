"""Tests for doc_hub.parse — core pipeline: category, embedding input, merge/split/dedup.

Unit tests only — no network, no DB, no Gemini API calls required.

parse_docs() integration tests mock the plugin registry to inject
MarkdownParser directly, so they do not require the package to be
installed with entry points.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from doc_hub.parse import (
    Chunk,
    _merge_tiny_chunks,
    _split_mega_chunks,
    derive_category,
    embedding_input,
    parse_docs,
    write_chunks_jsonl,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(
    source_file: str = "guide.md",
    source_url: str = "https://example.com/guide/",
    section_path: str = "Guide",
    heading: str = "Guide",
    heading_level: int = 1,
    content: str = "This is test content.",
    start_line: int = 1,
    end_line: int | None = None,
) -> Chunk:
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
    )


def _make_registry_with_markdown_parser():
    """Return a PluginRegistry with a real MarkdownParser instance."""
    from doc_hub.discovery import PluginRegistry
    from doc_hub._builtins.parsers.markdown import MarkdownParser

    registry = PluginRegistry()
    registry.parsers["markdown"] = MarkdownParser()
    return registry


# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------


def test_derive_category_api():
    """Files containing 'api' -> 'api'."""
    assert derive_category("api__models.md") == "api"


def test_derive_category_reference():
    """Files containing 'reference' -> 'api'."""
    assert derive_category("reference__types.md") == "api"


def test_derive_category_example():
    """Files containing 'example' -> 'example'."""
    assert derive_category("examples__weather.md") == "example"


def test_derive_category_tutorial():
    """Files containing 'tutorial' -> 'example'."""
    assert derive_category("tutorial__quickstart.md") == "example"


def test_derive_category_eval():
    """Files containing 'eval' -> 'eval'."""
    assert derive_category("eval__benchmarks.md") == "eval"


def test_derive_category_guide_keywords():
    """Various guide keywords -> 'guide'."""
    guide_files = [
        "install.md",
        "config.md",
        "migration.md",
        "quickstart.md",
        "getting-started.md",
        "getting_started.md",
        "setup.md",
        "guide.md",
        "how-to.md",
        "howto.md",
        "changelog.md",
        "contributing.md",
        "readme.md",
    ]
    for fname in guide_files:
        assert derive_category(fname) == "guide", f"Expected 'guide' for {fname}"


def test_derive_category_other():
    """Files with no matching keywords -> 'other'."""
    assert derive_category("models__openai.md") == "other"


def test_derive_category_priority_api_over_guide():
    """'api' check comes before guide — file with both gets 'api'."""
    assert derive_category("api-guide.md") == "api"


def test_derive_category_priority_example_over_guide():
    """'example' check comes before guide — file with both gets 'example'."""
    assert derive_category("example-guide.md") == "example"


def test_derive_category_case_insensitive():
    """Category derivation is case-insensitive."""
    assert derive_category("API__Models.md") == "api"
    assert derive_category("GUIDE.md") == "guide"


# ---------------------------------------------------------------------------
# embedding_input()
# ---------------------------------------------------------------------------


def test_embedding_input_prefix_format():
    """embedding_input() prepends the correct context prefix."""
    chunk = _make_chunk(
        source_file="models__openai.md",
        section_path="Configuration > API Keys",
        content="Configure your OpenAI API key here.",
    )
    result = embedding_input(chunk)
    assert result.startswith("Document: models/openai | Section: Configuration > API Keys\n\n")


def test_embedding_input_includes_content():
    """embedding_input() includes the original chunk content after the prefix."""
    content = "This is the actual chunk content."
    chunk = _make_chunk(content=content)
    result = embedding_input(chunk)
    assert result.endswith(content)


def test_embedding_input_double_underscore_to_slash():
    """embedding_input() replaces '__' with '/' in doc_name."""
    chunk = _make_chunk(source_file="a__b__c.md", section_path="Section")
    result = embedding_input(chunk)
    assert "Document: a/b/c" in result


def test_embedding_input_strips_md_extension():
    """embedding_input() removes the .md extension from doc_name."""
    chunk = _make_chunk(source_file="models__openai.md", section_path="S")
    result = embedding_input(chunk)
    assert "models/openai" in result
    assert ".md" not in result.split("|")[0]


def test_embedding_input_not_raw_content():
    """embedding_input() returns more than just chunk.content (has prefix)."""
    chunk = _make_chunk(content="Hello world.")
    raw_input = embedding_input(chunk)
    assert raw_input != chunk.content
    assert len(raw_input) > len(chunk.content)


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------


def test_chunk_has_start_line_field():
    """Chunk dataclass has a start_line field."""
    chunk = _make_chunk(start_line=42)
    assert chunk.start_line == 42


def test_chunk_has_content_hash_field():
    """Chunk has a SHA-256 content_hash field."""
    chunk = _make_chunk(content="Hello world.")
    expected_hash = hashlib.sha256("Hello world.".encode()).hexdigest()
    assert chunk.content_hash == expected_hash


def test_chunk_fields_are_correct_types():
    """Chunk fields have the expected Python types."""
    chunk = _make_chunk()
    assert isinstance(chunk.source_file, str)
    assert isinstance(chunk.source_url, str)
    assert isinstance(chunk.section_path, str)
    assert isinstance(chunk.heading, str)
    assert isinstance(chunk.heading_level, int)
    assert isinstance(chunk.content, str)
    assert isinstance(chunk.start_line, int)
    assert isinstance(chunk.char_count, int)
    assert isinstance(chunk.content_hash, str)
    assert isinstance(chunk.category, str)


# ---------------------------------------------------------------------------
# _merge_tiny_chunks() — call-site values
# ---------------------------------------------------------------------------


def test_merge_tiny_chunks_merges_short_chunks():
    """_merge_tiny_chunks merges chunks shorter than min_chars into predecessor."""
    short = _make_chunk(content="Short.", source_file="doc.md")
    long = _make_chunk(content="x" * 600, source_file="doc.md")
    result = _merge_tiny_chunks([long, short], min_chars=500)
    assert len(result) == 1
    assert "Short." in result[0].content


def test_merge_tiny_chunks_does_not_merge_across_files():
    """_merge_tiny_chunks does NOT merge chunks from different source files."""
    short = _make_chunk(content="Short.", source_file="file_a.md")
    prev_in_b = _make_chunk(content="x" * 600, source_file="file_b.md")
    result = _merge_tiny_chunks([prev_in_b, short], min_chars=500)
    assert len(result) == 2


def test_merge_tiny_chunks_keeps_large_chunks():
    """_merge_tiny_chunks preserves chunks that are already >= min_chars."""
    large = _make_chunk(content="x" * 600)
    result = _merge_tiny_chunks([large], min_chars=500)
    assert len(result) == 1
    assert result[0] is large


def test_merge_tiny_chunks_callsite_value_500():
    """Confirm the call-site uses min_chars=500 (not the 200 default)."""
    short = _make_chunk(content="x" * 499, source_file="doc.md")
    long = _make_chunk(content="y" * 600, source_file="doc.md")
    result = _merge_tiny_chunks([long, short], min_chars=500)
    assert len(result) == 1


def test_merge_tiny_chunks_exactly_500_not_merged():
    """A chunk with exactly 500 chars is NOT merged (>= 500 is kept)."""
    chunk500 = _make_chunk(content="x" * 500, source_file="doc.md")
    result = _merge_tiny_chunks([chunk500], min_chars=500)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _split_mega_chunks() — call-site values
# ---------------------------------------------------------------------------


def test_split_mega_chunks_splits_large_chunks():
    """_split_mega_chunks splits chunks larger than max_chars."""
    para = "word " * 100  # 500 chars
    long_content = (para + "\n\n") * 7  # 7 * 502 = 3514 chars
    large = _make_chunk(content=long_content)
    assert large.char_count > 2500
    result = _split_mega_chunks([large], max_chars=2500, target=1000)
    assert len(result) > 1
    for chunk in result:
        assert chunk.char_count <= 2600  # some tolerance for boundary finding


def test_split_mega_chunks_preserves_small_chunks():
    """_split_mega_chunks leaves chunks <= max_chars unchanged."""
    small = _make_chunk(content="x" * 100)
    result = _split_mega_chunks([small], max_chars=2500, target=1000)
    assert len(result) == 1
    assert result[0] is small


def test_split_mega_chunks_callsite_value_2500():
    """Confirm the call-site uses max_chars=2500 (not the 6000 default)."""
    content = "a" * 100 + "\n\n" + "b" * 2400  # 2503 chars with \n\n
    chunk = _make_chunk(content=content)
    result = _split_mega_chunks([chunk], max_chars=2500, target=1000)
    assert len(result) > 1


def test_split_mega_chunks_does_not_split_inside_code_fence():
    """_split_mega_chunks respects code fence boundaries when splitting."""
    code_fence = "```python\n" + "# code\n" * 50 + "```"
    prose = "\n\nProse content to fill up space.\n\n" * 30
    content = "Initial prose.\n\n" + code_fence + prose
    chunk = _make_chunk(content=content)
    if chunk.char_count > 2500:
        result = _split_mega_chunks([chunk], max_chars=2500, target=1000)
        for sub in result:
            backtick_fences = sub.content.count("```")
            assert backtick_fences % 2 == 0


# ---------------------------------------------------------------------------
# Content deduplication
# ---------------------------------------------------------------------------


def test_parse_docs_deduplicates_by_content_hash(tmp_path):
    """parse_docs removes duplicate chunks (same content_hash)."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()

    identical_content = "# Same Section\n\nIdentical content here. " * 10
    (raw_path / "file_a.md").write_text(identical_content)
    (raw_path / "file_b.md").write_text(identical_content)

    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=tmp_path / "chunks"):
            chunks = parse_docs("test-corpus", raw_path)

    content_hashes = [c.content_hash for c in chunks]
    assert len(content_hashes) == len(set(content_hashes)), "Duplicate hashes found"


# ---------------------------------------------------------------------------
# parse_docs() — integration tests
# ---------------------------------------------------------------------------


def test_parse_docs_reads_md_files(tmp_path):
    """parse_docs reads .md files from raw_path."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()
    (raw_path / "doc.md").write_text("# Title\n\nContent here.")

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            chunks = parse_docs("test-corpus", raw_path)

    assert len(chunks) >= 1


def test_parse_docs_writes_chunks_jsonl(tmp_path):
    """parse_docs writes chunks.jsonl to chunks_dir(corpus_slug)."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()
    (raw_path / "doc.md").write_text("# Title\n\nContent here.")

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            parse_docs("test-corpus", raw_path)

    assert (chunks_output / "chunks.jsonl").exists()


def test_parse_docs_skips_underscore_files(tmp_path):
    """parse_docs skips files starting with '_'."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()
    (raw_path / "_llms.txt.md").write_text("# Hidden\n\nShould not be parsed.")
    (raw_path / "visible.md").write_text("# Visible\n\nShould be parsed.")

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            chunks = parse_docs("test-corpus", raw_path)

    source_files = [c.source_file for c in chunks]
    assert "_llms.txt.md" not in source_files
    assert any("visible" in sf for sf in source_files)


def test_parse_docs_chunk_size_constraints(tmp_path):
    """parse_docs applies the correct call-site size constraints (500/2500)."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()

    md_content = ""
    for i in range(10):
        md_content += f"## Section {i}\n\nShort content {i}.\n\n"
    para = "Paragraph content. " * 30  # ~570 chars
    md_content += "## Long Section\n\n" + (para + "\n\n") * 5
    (raw_path / "doc.md").write_text(md_content)

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            chunks = parse_docs("test-corpus", raw_path)

    for chunk in chunks:
        assert chunk.char_count <= 2600  # slight tolerance for boundary-finding


def test_parse_docs_returns_list_of_chunks(tmp_path):
    """parse_docs returns a list of Chunk objects."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()
    (raw_path / "doc.md").write_text("# Title\n\nContent.")

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            result = parse_docs("test-corpus", raw_path)

    assert isinstance(result, list)
    assert all(isinstance(c, Chunk) for c in result)


def test_parse_docs_category_derived_from_source_file(tmp_path):
    """parse_docs derives category from source filename (core pipeline responsibility)."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()
    (raw_path / "api__models.md").write_text("# API Models\n\nThe API docs.")

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            chunks = parse_docs("test-corpus", raw_path)

    assert all(c.category == "api" for c in chunks)


def test_parse_docs_uses_manifest_for_source_url(tmp_path):
    """parse_docs uses manifest.json for source URLs when available."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()
    (raw_path / "guide.md").write_text("# Guide\n\nContent.")

    manifest = {
        "total": 1,
        "success": 1,
        "failed": 0,
        "files": [
            {"filename": "guide.md", "url": "https://example.com/guide/", "success": True},
        ],
    }
    (raw_path / "manifest.json").write_text(json.dumps(manifest))

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            chunks = parse_docs("test-corpus", raw_path)

    guide_chunks = [c for c in chunks if c.source_file == "guide.md"]
    assert len(guide_chunks) >= 1
    assert guide_chunks[0].source_url == "https://example.com/guide/"


def test_parse_docs_empty_url_without_manifest(tmp_path):
    """parse_docs uses empty string for source_url when no manifest exists."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()
    (raw_path / "doc.md").write_text("# Title\n\nContent.")

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            chunks = parse_docs("test-corpus", raw_path)

    assert chunks[0].source_url == ""


def test_parse_docs_filters_by_manifest(tmp_path):
    """parse_docs only parses files listed in manifest, ignoring orphaned files."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()

    (raw_path / "active.md").write_text("# Active\n\nThis doc is in the manifest.")
    (raw_path / "orphaned.md").write_text("# Orphaned\n\nThis doc was deleted upstream.")

    manifest = {
        "total": 1,
        "success": 1,
        "failed": 0,
        "files": [
            {"filename": "active.md", "url": "https://example.com/active/", "success": True},
        ],
    }
    (raw_path / "manifest.json").write_text(json.dumps(manifest))

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            chunks = parse_docs("test-corpus", raw_path)

    source_files = {c.source_file for c in chunks}
    assert "active.md" in source_files
    assert "orphaned.md" not in source_files


def test_parse_docs_falls_back_to_glob_without_manifest(tmp_path):
    """parse_docs globs all .md files when no manifest exists (e.g. local_dir)."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()

    (raw_path / "file_a.md").write_text("# File A\n\nContent A.")
    (raw_path / "file_b.md").write_text("# File B\n\nContent B.")

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            chunks = parse_docs("test-corpus", raw_path)

    source_files = {c.source_file for c in chunks}
    assert "file_a.md" in source_files
    assert "file_b.md" in source_files


def test_parse_docs_manifest_with_missing_file_on_disk(tmp_path):
    """parse_docs gracefully handles manifest entries where the file doesn't exist."""
    raw_path = tmp_path / "raw"
    raw_path.mkdir()

    (raw_path / "exists.md").write_text("# Exists\n\nContent.")
    manifest = {
        "total": 2,
        "success": 2,
        "failed": 0,
        "files": [
            {"filename": "exists.md", "url": "https://example.com/exists/", "success": True},
            {"filename": "missing.md", "url": "https://example.com/missing/", "success": True},
        ],
    }
    (raw_path / "manifest.json").write_text(json.dumps(manifest))

    chunks_output = tmp_path / "chunks"
    registry = _make_registry_with_markdown_parser()
    with patch("doc_hub.discovery.get_registry", return_value=registry):
        with patch("doc_hub.parse.chunks_dir", return_value=chunks_output):
            chunks = parse_docs("test-corpus", raw_path)

    source_files = {c.source_file for c in chunks}
    assert "exists.md" in source_files
    assert "missing.md" not in source_files


# ---------------------------------------------------------------------------
# write_chunks_jsonl()
# ---------------------------------------------------------------------------


def test_write_chunks_jsonl_creates_file(tmp_path):
    """write_chunks_jsonl creates a JSONL file."""
    chunk = _make_chunk()
    output = tmp_path / "chunks.jsonl"
    write_chunks_jsonl([chunk], output)
    assert output.exists()


def test_write_chunks_jsonl_valid_json(tmp_path):
    """write_chunks_jsonl writes valid JSON on each line."""
    chunk = _make_chunk(content="Test content.")
    output = tmp_path / "chunks.jsonl"
    write_chunks_jsonl([chunk], output)
    lines = output.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["content"] == "Test content."
    assert "content_hash" in data
    assert "start_line" in data


def test_write_chunks_jsonl_all_fields_present(tmp_path):
    """write_chunks_jsonl writes all Chunk fields."""
    chunk = _make_chunk()
    output = tmp_path / "chunks.jsonl"
    write_chunks_jsonl([chunk], output)
    data = json.loads(output.read_text().strip())
    expected_keys = {
        "source_file", "source_url", "section_path", "heading", "heading_level",
        "content", "start_line", "end_line", "char_count", "content_hash", "category",
    }
    assert expected_keys.issubset(data.keys())


# ---------------------------------------------------------------------------
# Full category keyword list validation
# ---------------------------------------------------------------------------


def test_derive_category_full_guide_keywords():
    """All 13 guide keywords in the spec are covered."""
    keywords_expected_as_guide = [
        "install", "config", "migration", "quickstart",
        "getting-started", "getting_started", "setup", "guide",
        "how-to", "howto", "changelog", "contributing", "readme",
    ]
    for kw in keywords_expected_as_guide:
        filename = f"{kw}.md"
        cat = derive_category(filename)
        assert cat == "guide", f"Expected 'guide' for '{filename}', got '{cat}'"
