"""Tests for doc_hub fetchers — LlmsTxtFetcher, LocalDirFetcher, etc.

Unit tests only — no network, no DB required. HTTP calls are mocked.
Tests target the builtin plugin classes directly.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_hub._builtins.fetchers.llms_txt import (
    DEFAULT_RETRIES,
    DEFAULT_WORKERS,
    DownloadResult,
    LlmsTxtFetcher,
    _derive_base_url,
    _derive_url_pattern,
    _parse_sections,
    compute_manifest_diff,
    load_manifest,
    url_to_filename,
    write_manifest,
)
from doc_hub._builtins.fetchers.local_dir import LocalDirFetcher
from doc_hub._builtins.fetchers.sitemap import (
    SitemapFetcher,
    build_sections_from_urls,
    html_url_to_filename,
    parse_sitemap_xml,
)
from doc_hub._builtins.fetchers.git_repo import GitRepoFetcher
from doc_hub.fetchers import DEFAULT_RETRIES as FETCHERS_DEFAULT_RETRIES
from doc_hub.fetchers import DEFAULT_WORKERS as FETCHERS_DEFAULT_WORKERS


# ---------------------------------------------------------------------------
# Constants exported from fetchers.py
# ---------------------------------------------------------------------------


def test_fetchers_exports_default_workers():
    """fetchers.py re-exports DEFAULT_WORKERS for pipeline.py compatibility."""
    assert FETCHERS_DEFAULT_WORKERS == 20


def test_fetchers_exports_default_retries():
    """fetchers.py re-exports DEFAULT_RETRIES for pipeline.py compatibility."""
    assert FETCHERS_DEFAULT_RETRIES == 3


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_llms_txt_fetcher_conforms_to_protocol():
    """LlmsTxtFetcher conforms to the Fetcher protocol."""
    from doc_hub.protocols import Fetcher
    assert isinstance(LlmsTxtFetcher(), Fetcher)


def test_local_dir_fetcher_conforms_to_protocol():
    """LocalDirFetcher conforms to the Fetcher protocol."""
    from doc_hub.protocols import Fetcher
    assert isinstance(LocalDirFetcher(), Fetcher)


def test_sitemap_fetcher_conforms_to_protocol():
    """SitemapFetcher conforms to the Fetcher protocol."""
    from doc_hub.protocols import Fetcher
    assert isinstance(SitemapFetcher(), Fetcher)


def test_git_repo_fetcher_conforms_to_protocol():
    """GitRepoFetcher conforms to the Fetcher protocol."""
    from doc_hub.protocols import Fetcher
    assert isinstance(GitRepoFetcher(), Fetcher)


# ---------------------------------------------------------------------------
# Stub fetchers raise NotImplementedError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sitemap_fetcher_raises_without_api_key(tmp_path, monkeypatch):
    """SitemapFetcher raises ValueError when JINA_API_KEY is not set."""
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    fetcher = SitemapFetcher()
    with pytest.raises(ValueError, match="JINA_API_KEY"):
        await fetcher.fetch("test-corpus", {"url": "https://example.com/sitemap.xml.gz"}, tmp_path)


@pytest.mark.asyncio
async def test_git_repo_fetcher_missing_url_raises():
    fetcher = GitRepoFetcher()
    with pytest.raises(KeyError):
        await fetcher.fetch("test-corpus", {}, Path("/tmp"))


# ---------------------------------------------------------------------------
# LocalDirFetcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_dir_fetcher_missing_path_key_raises():
    """LocalDirFetcher raises KeyError if 'path' config key is missing."""
    fetcher = LocalDirFetcher()
    with pytest.raises(KeyError):
        await fetcher.fetch("test", {}, Path("/tmp"))


@pytest.mark.asyncio
async def test_local_dir_fetcher_nonexistent_raises():
    """LocalDirFetcher raises FileNotFoundError for a missing directory."""
    fetcher = LocalDirFetcher()
    with pytest.raises(FileNotFoundError, match="Local dir not found"):
        await fetcher.fetch("test", {"path": "/nonexistent/dir/that/does/not/exist"}, Path("/tmp"))


@pytest.mark.asyncio
async def test_local_dir_fetcher_returns_path(tmp_path):
    """LocalDirFetcher returns the configured path when the directory exists."""
    fetcher = LocalDirFetcher()
    result = await fetcher.fetch("test", {"path": str(tmp_path)}, Path("/tmp"))
    assert result == tmp_path


# ---------------------------------------------------------------------------
# url_to_filename
# ---------------------------------------------------------------------------


def test_url_to_filename_index():
    """Root index URL → index.md."""
    base = "https://ai.pydantic.dev"
    url = "https://ai.pydantic.dev/index.md"
    assert url_to_filename(url, base) == "index.md"


def test_url_to_filename_nested():
    """Nested path uses double-underscore separator."""
    base = "https://ai.pydantic.dev"
    url = "https://ai.pydantic.dev/models/openai/index.md"
    assert url_to_filename(url, base) == "models__openai.md"


def test_url_to_filename_simple():
    """Simple top-level page."""
    base = "https://ai.pydantic.dev"
    url = "https://ai.pydantic.dev/agents.md"
    assert url_to_filename(url, base) == "agents.md"


def test_url_to_filename_two_levels():
    """Two-level path without index."""
    base = "https://ai.pydantic.dev"
    url = "https://ai.pydantic.dev/api/models.md"
    assert url_to_filename(url, base) == "api__models.md"


def test_url_to_filename_base_with_trailing_slash():
    """base_url with trailing slash is handled correctly."""
    base = "https://ai.pydantic.dev/"
    url = "https://ai.pydantic.dev/agents.md"
    assert url_to_filename(url, base) == "agents.md"


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def test_load_manifest_missing(tmp_path):
    """load_manifest returns {} when manifest.json does not exist."""
    assert load_manifest(tmp_path) == {}


def test_load_manifest_empty_files(tmp_path):
    """load_manifest handles manifest with empty files list."""
    (tmp_path / "manifest.json").write_text(json.dumps({"files": []}))
    assert load_manifest(tmp_path) == {}


def test_load_manifest_success_only(tmp_path):
    """load_manifest returns only entries where success=True."""
    data = {
        "files": [
            {"filename": "a.md", "url": "https://example.com/a.md", "success": True, "content_hash": "abc123"},
            {"filename": "b.md", "url": "https://example.com/b.md", "success": False},
        ]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(data))
    result = load_manifest(tmp_path)
    assert result == {"a.md": {"url": "https://example.com/a.md", "content_hash": "abc123"}}


def test_load_manifest_malformed(tmp_path):
    """load_manifest returns {} for malformed JSON."""
    (tmp_path / "manifest.json").write_text("not json")
    assert load_manifest(tmp_path) == {}


def test_load_manifest_backward_compat_no_content_hash(tmp_path):
    """load_manifest handles old manifests without content_hash (returns None)."""
    data = {
        "files": [
            {"filename": "a.md", "url": "https://example.com/a.md", "success": True},
        ]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(data))
    result = load_manifest(tmp_path)
    assert result == {"a.md": {"url": "https://example.com/a.md", "content_hash": None}}


def test_write_manifest_creates_file(tmp_path):
    """write_manifest creates manifest.json with correct structure."""
    results = [
        DownloadResult(url="https://example.com/a.md", filename="a.md", success=True, content_hash="abc123"),
        DownloadResult(url="https://example.com/b.md", filename="b.md", success=False, error="404"),
    ]
    write_manifest(results, tmp_path)
    data = json.loads((tmp_path / "manifest.json").read_text())
    assert data["total"] == 2
    assert data["success"] == 1
    assert data["failed"] == 1
    filenames = [f["filename"] for f in data["files"]]
    assert "a.md" in filenames
    assert "b.md" in filenames


def test_write_manifest_includes_content_hash(tmp_path):
    """write_manifest includes content_hash in each file entry."""
    results = [
        DownloadResult(url="https://example.com/a.md", filename="a.md", success=True, content_hash="abc123"),
    ]
    write_manifest(results, tmp_path)
    data = json.loads((tmp_path / "manifest.json").read_text())
    assert data["files"][0]["content_hash"] == "abc123"


def test_write_manifest_sorted_by_filename(tmp_path):
    """write_manifest sorts files by filename."""
    results = [
        DownloadResult(url="https://example.com/z.md", filename="z.md", success=True),
        DownloadResult(url="https://example.com/a.md", filename="a.md", success=True),
    ]
    write_manifest(results, tmp_path)
    data = json.loads((tmp_path / "manifest.json").read_text())
    filenames = [f["filename"] for f in data["files"]]
    assert filenames == sorted(filenames)


def test_parse_sections_from_llms_txt():
    content = """# Site Title

## Guides
- [Install](https://example.com/guides/install.md)
- [Upgrade](https://example.com/guides/upgrade.md)

### API
- [Models](https://example.com/api/models.md)
"""

    assert _parse_sections(content, r"https://example\.com/[^\s\)]+\.md") == [
        {
            "title": "Guides",
            "heading_level": 2,
            "urls": [
                "https://example.com/guides/install.md",
                "https://example.com/guides/upgrade.md",
            ],
        },
        {
            "title": "API",
            "heading_level": 3,
            "urls": ["https://example.com/api/models.md"],
        },
    ]


def test_parse_sections_urls_before_heading():
    content = """https://example.com/root.md

## Guides
- [Install](https://example.com/guides/install.md)
"""

    assert _parse_sections(content, r"https://example\.com/[^\s\)]+\.md") == [
        {
            "title": "",
            "heading_level": 0,
            "urls": ["https://example.com/root.md"],
        },
        {
            "title": "Guides",
            "heading_level": 2,
            "urls": ["https://example.com/guides/install.md"],
        },
    ]


def test_parse_sections_deduplicates_urls():
    content = """## Guides
- [Install](https://example.com/guides/install.md)
- [Install Again](https://example.com/guides/install.md)
"""

    assert _parse_sections(content, r"https://example\.com/[^\s\)]+\.md") == [
        {
            "title": "Guides",
            "heading_level": 2,
            "urls": ["https://example.com/guides/install.md"],
        }
    ]


def test_parse_sections_empty_content():
    assert _parse_sections("", r"https://example\.com/[^\s\)]+\.md") == []


def test_parse_sections_no_urls():
    content = """# Site Title

## Guides
Some text.

### API
More text.
"""

    assert _parse_sections(content, r"https://example\.com/[^\s\)]+\.md") == [
        {"title": "Guides", "heading_level": 2, "urls": []},
        {"title": "API", "heading_level": 3, "urls": []},
    ]


def test_manifest_includes_sections(tmp_path):
    results = [
        DownloadResult(url="https://example.com/a.md", filename="a.md", success=True, content_hash="abc123"),
    ]
    sections = [{"title": "Guides", "heading_level": 2, "urls": ["https://example.com/a.md"]}]

    write_manifest(results, tmp_path, sections=sections)
    data = json.loads((tmp_path / "manifest.json").read_text())

    assert data["sections"] == sections


def test_manifest_omits_sections_when_none(tmp_path):
    results = [
        DownloadResult(url="https://example.com/a.md", filename="a.md", success=True),
    ]

    write_manifest(results, tmp_path, sections=None)
    data = json.loads((tmp_path / "manifest.json").read_text())

    assert "sections" not in data


# ---------------------------------------------------------------------------
# compute_manifest_diff
# ---------------------------------------------------------------------------


def test_compute_manifest_diff_all_new():
    """All upstream URLs are new when manifest is empty."""
    upstream = ["https://example.com/a.md", "https://example.com/b.md"]
    new_urls, removed = compute_manifest_diff(upstream, {})
    assert set(new_urls) == set(upstream)
    assert removed == []


def test_compute_manifest_diff_nothing_new():
    """No new URLs when manifest matches upstream exactly."""
    upstream = ["https://example.com/a.md"]
    manifest = {"a.md": {"url": "https://example.com/a.md", "content_hash": "abc"}}
    new_urls, removed = compute_manifest_diff(upstream, manifest)
    assert new_urls == []
    assert removed == []


def test_compute_manifest_diff_some_new():
    """Only URLs absent from manifest are returned as new."""
    upstream = ["https://example.com/a.md", "https://example.com/b.md"]
    manifest = {"a.md": {"url": "https://example.com/a.md", "content_hash": "abc"}}
    new_urls, removed = compute_manifest_diff(upstream, manifest)
    assert new_urls == ["https://example.com/b.md"]
    assert removed == []


def test_compute_manifest_diff_removed():
    """URLs in manifest not present upstream are returned as removed."""
    upstream = ["https://example.com/a.md"]
    manifest = {
        "a.md": {"url": "https://example.com/a.md", "content_hash": "abc"},
        "b.md": {"url": "https://example.com/b.md", "content_hash": "def"},
    }
    new_urls, removed = compute_manifest_diff(upstream, manifest)
    assert new_urls == []
    assert "b.md" in removed


def test_compute_manifest_diff_preserves_order():
    """new_urls preserves the order from upstream_urls."""
    upstream = ["https://example.com/c.md", "https://example.com/a.md", "https://example.com/b.md"]
    new_urls, _ = compute_manifest_diff(upstream, {})
    assert new_urls == upstream


# ---------------------------------------------------------------------------
# LlmsTxtFetcher — mocked HTTP
# ---------------------------------------------------------------------------


SAMPLE_LLMS_TXT = """
# Pydantic AI

## Docs

- [Agents](https://ai.pydantic.dev/agents.md)
- [Models](https://ai.pydantic.dev/models/openai/index.md)
- [API Reference](https://ai.pydantic.dev/api/base.md)
"""

FETCH_CONFIG = {
    "url": "https://ai.pydantic.dev/llms.txt",
    "url_pattern": r"https://ai\.pydantic\.dev/[^\s\)]+\.md",
    "base_url": "https://ai.pydantic.dev",
}


def _make_mock_session(
    llms_txt_content: str = SAMPLE_LLMS_TXT,
    page_content: bytes = b"# Page content",
):
    """Build a mock aiohttp.ClientSession."""
    llms_resp = AsyncMock()
    llms_resp.raise_for_status = MagicMock()
    llms_resp.text = AsyncMock(return_value=llms_txt_content)
    llms_resp.__aenter__ = AsyncMock(return_value=llms_resp)
    llms_resp.__aexit__ = AsyncMock(return_value=False)

    page_resp = AsyncMock()
    page_resp.raise_for_status = MagicMock()
    page_resp.read = AsyncMock(return_value=page_content)
    page_resp.__aenter__ = AsyncMock(return_value=page_resp)
    page_resp.__aexit__ = AsyncMock(return_value=False)

    session = AsyncMock()
    session.get = MagicMock(side_effect=[llms_resp] + [page_resp] * 20)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    return session


@pytest.mark.asyncio
async def test_llms_txt_fetcher_creates_output_dir(tmp_path):
    """LlmsTxtFetcher.fetch() creates output_dir if it does not exist."""
    fetcher = LlmsTxtFetcher()
    output_dir = tmp_path / "raw"
    assert not output_dir.exists()

    with patch("doc_hub._builtins.fetchers.llms_txt.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_mock_session()
        result = await fetcher.fetch("test-corpus", FETCH_CONFIG, output_dir)

    assert output_dir.exists()
    assert result == output_dir


@pytest.mark.asyncio
async def test_llms_txt_fetcher_saves_llms_txt(tmp_path):
    """LlmsTxtFetcher.fetch() saves the raw llms.txt content to _llms.txt."""
    fetcher = LlmsTxtFetcher()

    with patch("doc_hub._builtins.fetchers.llms_txt.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_mock_session(llms_txt_content=SAMPLE_LLMS_TXT)
        await fetcher.fetch("test-corpus", FETCH_CONFIG, tmp_path)

    assert (tmp_path / "_llms.txt").exists()
    assert "Pydantic AI" in (tmp_path / "_llms.txt").read_text()


@pytest.mark.asyncio
async def test_llms_txt_fetcher_writes_manifest(tmp_path):
    """LlmsTxtFetcher.fetch() writes a manifest.json after downloading."""
    fetcher = LlmsTxtFetcher()

    with patch("doc_hub._builtins.fetchers.llms_txt.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_mock_session()
        await fetcher.fetch("test-corpus", FETCH_CONFIG, tmp_path)

    assert (tmp_path / "manifest.json").exists()
    data = json.loads((tmp_path / "manifest.json").read_text())
    assert "total" in data
    assert "files" in data


@pytest.mark.asyncio
async def test_llms_txt_fetcher_manifest_has_content_hash(tmp_path):
    """LlmsTxtFetcher.fetch() writes content_hash for each file in the manifest."""
    fetcher = LlmsTxtFetcher()

    with patch("doc_hub._builtins.fetchers.llms_txt.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_mock_session()
        await fetcher.fetch("test-corpus", FETCH_CONFIG, tmp_path)

    data = json.loads((tmp_path / "manifest.json").read_text())
    for f in data["files"]:
        if f["success"]:
            assert f["content_hash"] is not None, f"Missing content_hash for {f['filename']}"


@pytest.mark.asyncio
async def test_llms_txt_fetcher_manifest_has_sections(tmp_path):
    fetcher = LlmsTxtFetcher()

    with patch("doc_hub._builtins.fetchers.llms_txt.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_mock_session()
        await fetcher.fetch("test-corpus", FETCH_CONFIG, tmp_path)

    data = json.loads((tmp_path / "manifest.json").read_text())
    assert data["sections"] == [
        {
            "title": "Docs",
            "heading_level": 2,
            "urls": [
                "https://ai.pydantic.dev/agents.md",
                "https://ai.pydantic.dev/models/openai/index.md",
                "https://ai.pydantic.dev/api/base.md",
            ],
        }
    ]


@pytest.mark.asyncio
async def test_llms_txt_fetcher_deduplicates_urls(tmp_path):
    """LlmsTxtFetcher.fetch() deduplicates duplicate URLs in llms.txt."""
    llms_txt = (
        "https://ai.pydantic.dev/agents.md\n"
        "https://ai.pydantic.dev/agents.md\n"  # duplicate
        "https://ai.pydantic.dev/models/openai/index.md\n"
    )
    fetcher = LlmsTxtFetcher()

    with patch("doc_hub._builtins.fetchers.llms_txt.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_mock_session(llms_txt_content=llms_txt)
        await fetcher.fetch("test-corpus", FETCH_CONFIG, tmp_path)

    manifest = load_manifest(tmp_path)
    assert len(manifest) == 2


@pytest.mark.asyncio
async def test_llms_txt_fetcher_deletes_removed_files(tmp_path):
    """LlmsTxtFetcher.fetch() deletes local .md files for URLs removed upstream."""
    (tmp_path / "agents.md").write_text("# Agents")
    (tmp_path / "removed.md").write_text("# Removed doc")
    existing_manifest_data = {
        "total": 2,
        "success": 2,
        "failed": 0,
        "files": [
            {"filename": "agents.md", "url": "https://ai.pydantic.dev/agents.md", "success": True, "content_hash": "old_hash"},
            {"filename": "removed.md", "url": "https://ai.pydantic.dev/removed.md", "success": True, "content_hash": "old_hash2"},
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(existing_manifest_data))

    llms_txt = "https://ai.pydantic.dev/agents.md\n"
    fetcher = LlmsTxtFetcher()

    with patch("doc_hub._builtins.fetchers.llms_txt.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_mock_session(llms_txt_content=llms_txt)
        await fetcher.fetch("test-corpus", FETCH_CONFIG, tmp_path)

    assert not (tmp_path / "removed.md").exists()
    assert (tmp_path / "agents.md").exists()
    manifest = load_manifest(tmp_path)
    assert "removed.md" not in manifest


@pytest.mark.asyncio
async def test_llms_txt_fetcher_detects_content_changes(tmp_path):
    """LlmsTxtFetcher.fetch() re-downloads all URLs and detects content changes via hash."""
    import hashlib

    old_content = b"# Old content"
    new_content = b"# New content"
    old_hash = hashlib.sha256(old_content).hexdigest()

    existing_manifest_data = {
        "total": 1,
        "success": 1,
        "failed": 0,
        "files": [
            {"filename": "agents.md", "url": "https://ai.pydantic.dev/agents.md", "success": True, "content_hash": old_hash},
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(existing_manifest_data))
    (tmp_path / "agents.md").write_text("# Old content")

    llms_txt = "https://ai.pydantic.dev/agents.md\n"
    fetcher = LlmsTxtFetcher()

    with patch("doc_hub._builtins.fetchers.llms_txt.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_mock_session(llms_txt_content=llms_txt, page_content=new_content)
        await fetcher.fetch("test-corpus", FETCH_CONFIG, tmp_path)

    assert (tmp_path / "agents.md").read_bytes() == new_content
    manifest = load_manifest(tmp_path)
    new_hash = hashlib.sha256(new_content).hexdigest()
    assert manifest["agents.md"]["content_hash"] == new_hash
    assert manifest["agents.md"]["content_hash"] != old_hash


@pytest.mark.asyncio
async def test_llms_txt_fetcher_missing_url_key_raises(tmp_path):
    """LlmsTxtFetcher.fetch() raises KeyError if 'url' config key is missing."""
    fetcher = LlmsTxtFetcher()

    with patch("doc_hub._builtins.fetchers.llms_txt.aiohttp.ClientSession"):
        with pytest.raises(KeyError):
            await fetcher.fetch("test-corpus", {}, tmp_path)


@pytest.mark.asyncio
async def test_llms_txt_fetcher_derives_base_url_and_pattern(tmp_path):
    """LlmsTxtFetcher.fetch() works with only 'url' in fetch_config (derives the rest)."""
    fetcher = LlmsTxtFetcher()
    config = {"url": "https://ai.pydantic.dev/llms.txt"}

    with patch("doc_hub._builtins.fetchers.llms_txt.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_mock_session()
        result = await fetcher.fetch("test-corpus", config, tmp_path)

    assert result == tmp_path
    manifest = load_manifest(tmp_path)
    assert len(manifest) > 0


# ---------------------------------------------------------------------------
# _derive_base_url / _derive_url_pattern
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_derive_base_url():
    assert await _derive_base_url("https://ai.pydantic.dev/llms.txt") == "https://ai.pydantic.dev/"
    assert await _derive_base_url("https://docs.example.com/v2/llms-full.txt") == "https://docs.example.com/v2/"


def test_derive_url_pattern_matches_md_urls():
    import re
    pattern = _derive_url_pattern("https://ai.pydantic.dev/")
    assert re.search(pattern, "https://ai.pydantic.dev/agents.md")
    assert re.search(pattern, "https://ai.pydantic.dev/models/openai/index.md")
    assert not re.search(pattern, "https://other.dev/agents.md")


def test_derive_url_pattern_trailing_slash():
    """Handles base_url with and without trailing slash."""
    p1 = _derive_url_pattern("https://example.com/")
    p2 = _derive_url_pattern("https://example.com")
    assert p1 == p2


# ---------------------------------------------------------------------------
# SitemapFetcher — html_url_to_filename
# ---------------------------------------------------------------------------


def test_html_url_to_filename_root():
    """Root URL -> index.md."""
    assert html_url_to_filename("https://camoufox.com/", "https://camoufox.com/") == "index.md"


def test_html_url_to_filename_top_level():
    """Top-level page."""
    assert html_url_to_filename("https://camoufox.com/python/", "https://camoufox.com/") == "python.md"


def test_html_url_to_filename_nested():
    """Nested path uses double-underscore separator."""
    assert html_url_to_filename("https://camoufox.com/python/usage/", "https://camoufox.com/") == "python__usage.md"


def test_html_url_to_filename_no_trailing_slash():
    """URL without trailing slash."""
    assert html_url_to_filename("https://camoufox.com/fingerprint", "https://camoufox.com/") == "fingerprint.md"


def test_html_url_to_filename_deep_nesting():
    """Three levels deep."""
    assert html_url_to_filename("https://camoufox.com/a/b/c/", "https://camoufox.com/") == "a__b__c.md"


# ---------------------------------------------------------------------------
# SitemapFetcher — parse_sitemap_xml
# ---------------------------------------------------------------------------

SAMPLE_SITEMAP_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://camoufox.com/</loc>
    <lastmod>2026-03-09T03:19:28Z</lastmod>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://camoufox.com/fingerprint/</loc>
    <lastmod>2026-03-09T03:19:28Z</lastmod>
    <priority>0.82</priority>
  </url>
  <url>
    <loc>https://camoufox.com/python/</loc>
    <lastmod>2026-03-09T03:19:28Z</lastmod>
    <priority>0.77</priority>
  </url>
</urlset>"""


def test_parse_sitemap_xml_extracts_urls():
    urls = parse_sitemap_xml(SAMPLE_SITEMAP_XML)
    assert urls == [
        "https://camoufox.com/",
        "https://camoufox.com/fingerprint/",
        "https://camoufox.com/python/",
    ]


def test_parse_sitemap_xml_empty():
    xml = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
    assert parse_sitemap_xml(xml) == []


def test_parse_sitemap_xml_deduplicates():
    xml = """\
<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a/</loc></url>
  <url><loc>https://example.com/a/</loc></url>
  <url><loc>https://example.com/b/</loc></url>
</urlset>"""
    assert parse_sitemap_xml(xml) == [
        "https://example.com/a/",
        "https://example.com/b/",
    ]


# ---------------------------------------------------------------------------
# SitemapFetcher — build_sections_from_urls
# ---------------------------------------------------------------------------


def test_build_sections_groups_by_first_segment():
    urls = [
        "https://camoufox.com/",
        "https://camoufox.com/python/",
        "https://camoufox.com/python/usage/",
        "https://camoufox.com/fingerprint/",
    ]
    sections = build_sections_from_urls(urls, "https://camoufox.com/")
    assert sections == [
        {"title": "", "heading_level": 0, "urls": ["https://camoufox.com/"]},
        {"title": "python", "heading_level": 2, "urls": [
            "https://camoufox.com/python/",
            "https://camoufox.com/python/usage/",
        ]},
        {"title": "fingerprint", "heading_level": 2, "urls": [
            "https://camoufox.com/fingerprint/",
        ]},
    ]


def test_build_sections_no_root():
    urls = [
        "https://example.com/docs/intro/",
        "https://example.com/docs/advanced/",
    ]
    sections = build_sections_from_urls(urls, "https://example.com/")
    assert sections == [
        {"title": "docs", "heading_level": 2, "urls": [
            "https://example.com/docs/intro/",
            "https://example.com/docs/advanced/",
        ]},
    ]


def test_build_sections_empty():
    assert build_sections_from_urls([], "https://example.com/") == []


# ---------------------------------------------------------------------------
# SitemapFetcher — full fetch (mocked HTTP)
# ---------------------------------------------------------------------------

SITEMAP_FETCH_CONFIG = {
    "url": "https://camoufox.com/sitemap.xml.gz",
}


def _make_sitemap_mock_session(
    sitemap_xml: str = SAMPLE_SITEMAP_XML,
    page_content: str = "# Page content",
):
    """Build a mock aiohttp.ClientSession for SitemapFetcher tests."""
    gz_bytes = gzip.compress(sitemap_xml.encode())

    sitemap_resp = AsyncMock()
    sitemap_resp.raise_for_status = MagicMock()
    sitemap_resp.read = AsyncMock(return_value=gz_bytes)
    sitemap_resp.__aenter__ = AsyncMock(return_value=sitemap_resp)
    sitemap_resp.__aexit__ = AsyncMock(return_value=False)

    page_resp = AsyncMock()
    page_resp.raise_for_status = MagicMock()
    page_resp.status = 200
    page_resp.text = AsyncMock(return_value=page_content)
    page_resp.headers = {}
    page_resp.__aenter__ = AsyncMock(return_value=page_resp)
    page_resp.__aexit__ = AsyncMock(return_value=False)

    session = AsyncMock()
    session.get = MagicMock(side_effect=[sitemap_resp] + [page_resp] * 50)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    return session


@pytest.mark.asyncio
async def test_sitemap_fetcher_creates_output_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina_test_key")
    fetcher = SitemapFetcher()
    output_dir = tmp_path / "raw"

    with patch("doc_hub._builtins.fetchers.sitemap.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_sitemap_mock_session()
        result = await fetcher.fetch("test-corpus", SITEMAP_FETCH_CONFIG, output_dir)

    assert output_dir.exists()
    assert result == output_dir


@pytest.mark.asyncio
async def test_sitemap_fetcher_writes_md_files(tmp_path, monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina_test_key")
    fetcher = SitemapFetcher()

    with patch("doc_hub._builtins.fetchers.sitemap.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_sitemap_mock_session()
        await fetcher.fetch("test-corpus", SITEMAP_FETCH_CONFIG, tmp_path)

    md_files = list(tmp_path.glob("*.md"))
    assert len(md_files) == 3
    filenames = {f.name for f in md_files}
    assert filenames == {"index.md", "fingerprint.md", "python.md"}


@pytest.mark.asyncio
async def test_sitemap_fetcher_writes_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina_test_key")
    fetcher = SitemapFetcher()

    with patch("doc_hub._builtins.fetchers.sitemap.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_sitemap_mock_session()
        await fetcher.fetch("test-corpus", SITEMAP_FETCH_CONFIG, tmp_path)

    manifest = load_manifest(tmp_path)
    assert len(manifest) == 3
    assert "index.md" in manifest


@pytest.mark.asyncio
async def test_sitemap_fetcher_manifest_has_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina_test_key")
    fetcher = SitemapFetcher()

    with patch("doc_hub._builtins.fetchers.sitemap.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_sitemap_mock_session()
        await fetcher.fetch("test-corpus", SITEMAP_FETCH_CONFIG, tmp_path)

    data = json.loads((tmp_path / "manifest.json").read_text())
    assert "sections" in data
    titles = [s["title"] for s in data["sections"]]
    assert "" in titles  # root section
    assert "fingerprint" in titles
    assert "python" in titles


@pytest.mark.asyncio
async def test_sitemap_fetcher_saves_raw_xml(tmp_path, monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina_test_key")
    fetcher = SitemapFetcher()

    with patch("doc_hub._builtins.fetchers.sitemap.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_sitemap_mock_session()
        await fetcher.fetch("test-corpus", SITEMAP_FETCH_CONFIG, tmp_path)

    assert (tmp_path / "_sitemap.xml").exists()
    assert "camoufox.com" in (tmp_path / "_sitemap.xml").read_text()


@pytest.mark.asyncio
async def test_sitemap_fetcher_missing_url_key(tmp_path, monkeypatch):
    monkeypatch.setenv("JINA_API_KEY", "jina_test_key")
    fetcher = SitemapFetcher()

    with pytest.raises(KeyError):
        await fetcher.fetch("test-corpus", {}, tmp_path)
