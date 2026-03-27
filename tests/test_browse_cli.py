from __future__ import annotations

import argparse
import io
import json
import tomllib
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from doc_hub.mcp_server import LARGE_DOC_THRESHOLD as MCP_LARGE_DOC_THRESHOLD


def test_browse_main_importable():
    from doc_hub.browse import browse_main

    assert callable(browse_main)


def test_read_main_importable():
    from doc_hub.browse import read_main

    assert callable(read_main)


def test_render_tree_empty():
    from doc_hub.browse import _render_tree

    assert _render_tree([]) == "(no documents)"


def test_render_tree_group_nodes():
    from doc_hub.browse import _render_tree

    nodes = [
        {
            "title": "Guides",
            "depth": 0,
            "is_group": True,
            "total_chars": 0,
            "section_count": 0,
        }
    ]

    assert _render_tree(nodes) == "Guides [group]"


def test_render_tree_content_nodes():
    from doc_hub.browse import _render_tree

    nodes = [
        {
            "title": "Install",
            "depth": 0,
            "is_group": False,
            "doc_id": "abc123",
            "total_chars": 12345,
            "section_count": 3,
        }
    ]

    assert _render_tree(nodes) == "Install [abc123] 12,345 chars  3 sections"


def test_render_tree_indentation():
    from doc_hub.browse import _render_tree

    nodes = [
        {
            "title": "Root",
            "depth": 0,
            "is_group": True,
            "total_chars": 0,
            "section_count": 0,
        },
        {
            "title": "Child",
            "depth": 1,
            "is_group": False,
            "doc_id": "child1",
            "total_chars": 100,
            "section_count": 2,
        },
        {
            "title": "Grandchild",
            "depth": 2,
            "is_group": False,
            "doc_id": "grand2",
            "total_chars": 50,
            "section_count": 1,
        },
    ]

    assert _render_tree(nodes).splitlines() == [
        "Root [group]",
        "    Child [child1] 100 chars  2 sections",
        "        Grandchild [grand2] 50 chars  1 section",
    ]


def test_render_tree_single_section_no_plural():
    from doc_hub.browse import _render_tree

    nodes = [
        {
            "title": "Overview",
            "depth": 0,
            "is_group": False,
            "doc_id": "ovr123",
            "total_chars": 1,
            "section_count": 1,
        }
    ]

    assert _render_tree(nodes) == "Overview [ovr123] 1 chars  1 section"


def test_render_tree_preserves_input_order():
    from doc_hub.browse import _render_tree

    nodes = [
        {"title": "Second", "depth": 0, "is_group": True, "total_chars": 0, "section_count": 0},
        {"title": "First", "depth": 0, "is_group": True, "total_chars": 0, "section_count": 0},
    ]

    assert _render_tree(nodes).splitlines() == ["Second [group]", "First [group]"]


def test_render_outline_basic():
    from doc_hub.browse import _render_outline

    sections = [
        {"heading": "Overview", "heading_level": 1, "char_count": 1200},
        {"heading": "Install", "heading_level": 2, "char_count": 345},
    ]

    assert _render_outline(sections).splitlines() == [
        "Overview 1,200 chars",
        "  Install 345 chars",
    ]


def test_render_outline_nested_headings():
    from doc_hub.browse import _render_outline

    sections = [
        {"heading": "Top", "heading_level": 1, "char_count": 10},
        {"heading": "Mid", "heading_level": 2, "char_count": 20},
        {"heading": "Deep", "heading_level": 3, "char_count": 30},
    ]

    assert _render_outline(sections).splitlines() == [
        "Top 10 chars",
        "  Mid 20 chars",
        "    Deep 30 chars",
    ]


def test_large_doc_threshold_matches_mcp():
    from doc_hub.browse import LARGE_DOC_THRESHOLD

    assert LARGE_DOC_THRESHOLD == MCP_LARGE_DOC_THRESHOLD == 20_000


def test_browse_main_uses_load_dotenv_and_asyncio_run():
    from doc_hub import browse as browse_module

    argv = ["demo-corpus"]
    parsed_args = argparse.Namespace(corpus="demo-corpus", path=None, depth=None, json=False)

    with (
        patch.object(browse_module, "load_dotenv") as mock_load_dotenv,
        patch("doc_hub.browse.asyncio.run") as mock_asyncio_run,
        patch.object(browse_module.logging, "basicConfig") as mock_basic_config,
        patch.object(browse_module, "build_browse_parser") as mock_parser_builder,
        patch.object(browse_module, "browse", new=MagicMock(return_value="browse-coro")) as mock_browse,
    ):
        mock_parser = MagicMock()
        mock_parser.parse_args.return_value = parsed_args
        mock_parser_builder.return_value = mock_parser

        browse_module.browse_main(argv)

    mock_browse.assert_called_once_with(parsed_args)

    mock_load_dotenv.assert_called_once_with()
    mock_basic_config.assert_called_once()
    mock_parser.parse_args.assert_called_once_with(argv)
    mock_asyncio_run.assert_called_once()


def test_read_main_uses_load_dotenv_and_asyncio_run():
    from doc_hub import browse as browse_module

    argv = ["demo-corpus", "guide/intro"]
    parsed_args = argparse.Namespace(corpus="demo-corpus", doc_path="guide/intro", section=None, force=False, json=False)

    with (
        patch.object(browse_module, "load_dotenv") as mock_load_dotenv,
        patch("doc_hub.browse.asyncio.run") as mock_asyncio_run,
        patch.object(browse_module.logging, "basicConfig") as mock_basic_config,
        patch.object(browse_module, "build_read_parser") as mock_parser_builder,
        patch.object(browse_module, "read", new=MagicMock(return_value="read-coro")) as mock_read,
    ):
        mock_parser = MagicMock()
        mock_parser.parse_args.return_value = parsed_args
        mock_parser_builder.return_value = mock_parser

        browse_module.read_main(argv)

    mock_read.assert_called_once_with(parsed_args)

    mock_load_dotenv.assert_called_once_with()
    mock_basic_config.assert_called_once()
    mock_parser.parse_args.assert_called_once_with(argv)
    mock_asyncio_run.assert_called_once()


def test_browse_async_json_output():
    from doc_hub import browse as browse_module

    args = argparse.Namespace(corpus="demo", path="guides", depth=1, json=True)
    pool = MagicMock()
    nodes = [
        {"title": "Guides", "depth": 0, "is_group": True, "total_chars": 0, "section_count": 0},
        {"title": "Install", "depth": 1, "is_group": False, "doc_id": "abc123", "total_chars": 120, "section_count": 2},
    ]

    stdout = io.StringIO()
    with (
        patch.object(browse_module, "create_pool", new=AsyncMock(return_value=pool)),
        patch.object(browse_module, "ensure_schema", new=AsyncMock()),
        patch.object(browse_module, "get_document_tree", new=AsyncMock(return_value=nodes)) as mock_get_tree,
        redirect_stdout(stdout),
    ):
        pool.close = AsyncMock()
        import asyncio
        asyncio.run(browse_module.browse(args))

    mock_get_tree.assert_awaited_once_with(pool, "demo", path="guides", max_depth=1)
    assert json.loads(stdout.getvalue()) == nodes


def test_read_not_found_prints_message_and_returns_successfully():
    from doc_hub import browse as browse_module

    args = argparse.Namespace(corpus="demo", doc_path="missing/doc", section=None, force=False, json=False)
    pool = MagicMock()

    stdout = io.StringIO()
    with (
        patch.object(browse_module, "create_pool", new=AsyncMock(return_value=pool)),
        patch.object(browse_module, "ensure_schema", new=AsyncMock()),
        patch.object(browse_module, "resolve_doc_path", new=AsyncMock(return_value=None)),
        patch.object(browse_module, "get_document_chunks", new=AsyncMock(return_value=[])),
        redirect_stdout(stdout),
    ):
        pool.close = AsyncMock()
        import asyncio
        asyncio.run(browse_module.read(args))

    assert "Document 'missing/doc' not found in corpus 'demo'" in stdout.getvalue()


def test_read_json_outline_for_large_document():
    from doc_hub import browse as browse_module

    args = argparse.Namespace(corpus="demo", doc_path="guide/large", section=None, force=False, json=True)
    pool = MagicMock()
    chunks = [
        {"heading": "Title", "heading_level": 1, "section_path": "Title", "char_count": 5, "source_url": "https://example.com/doc", "content": "Title"},
        {"heading": "Deep Dive", "heading_level": 2, "section_path": "Title > Deep Dive", "char_count": browse_module.LARGE_DOC_THRESHOLD + 1, "source_url": "https://example.com/doc", "content": "X"},
    ]

    stdout = io.StringIO()
    with (
        patch.object(browse_module, "create_pool", new=AsyncMock(return_value=pool)),
        patch.object(browse_module, "ensure_schema", new=AsyncMock()),
        patch.object(browse_module, "resolve_doc_path", new=AsyncMock(return_value="guide/large")),
        patch.object(browse_module, "get_document_chunks", new=AsyncMock(return_value=chunks)),
        redirect_stdout(stdout),
    ):
        pool.close = AsyncMock()
        import asyncio
        asyncio.run(browse_module.read(args))

    payload = json.loads(stdout.getvalue())
    assert payload["mode"] == "outline"
    assert payload["doc_path"] == "guide/large"
    assert "sections" in payload
    assert "hint" in payload


def test_read_json_full_for_force_or_section():
    from doc_hub import browse as browse_module

    force_args = argparse.Namespace(corpus="demo", doc_path="guide/large", section=None, force=True, json=True)
    pool = MagicMock()
    chunks = [
        {"heading": "Title", "heading_level": 1, "section_path": "Title", "char_count": browse_module.LARGE_DOC_THRESHOLD + 5, "source_url": "https://example.com/doc", "content": "First"},
        {"heading": "Deep Dive", "heading_level": 2, "section_path": "Title > Deep Dive", "char_count": 10, "source_url": "https://example.com/doc", "content": "Second"},
    ]

    stdout = io.StringIO()
    with (
        patch.object(browse_module, "create_pool", new=AsyncMock(return_value=pool)),
        patch.object(browse_module, "ensure_schema", new=AsyncMock()),
        patch.object(browse_module, "resolve_doc_path", new=AsyncMock(return_value="guide/large")) as mock_resolve_doc_path,
        patch.object(browse_module, "get_document_chunks", new=AsyncMock(return_value=chunks)) as mock_get_chunks,
        redirect_stdout(stdout),
    ):
        pool.close = AsyncMock()
        import asyncio
        asyncio.run(browse_module.read(force_args))

    payload = json.loads(stdout.getvalue())
    assert payload["mode"] == "full"
    assert payload["content"] == "First\n\nSecond"
    mock_resolve_doc_path.assert_awaited_once_with(pool, "demo", "guide/large")
    mock_get_chunks.assert_awaited_once_with(pool, "demo", "guide/large", section=None)


def test_read_resolves_short_doc_id_before_loading_chunks():
    from doc_hub import browse as browse_module

    args = argparse.Namespace(corpus="demo", doc_path="abc123", section=None, force=True, json=True)
    pool = MagicMock()
    chunks = [
        {"heading": "Title", "heading_level": 1, "section_path": "Title", "char_count": 10, "source_url": "https://example.com/doc", "content": "First"},
    ]

    stdout = io.StringIO()
    with (
        patch.object(browse_module, "create_pool", new=AsyncMock(return_value=pool)),
        patch.object(browse_module, "ensure_schema", new=AsyncMock()),
        patch.object(browse_module, "resolve_doc_path", new=AsyncMock(return_value="guide/large")) as mock_resolve_doc_path,
        patch.object(browse_module, "get_document_chunks", new=AsyncMock(return_value=chunks)) as mock_get_chunks,
        redirect_stdout(stdout),
    ):
        pool.close = AsyncMock()
        import asyncio
        asyncio.run(browse_module.read(args))

    payload = json.loads(stdout.getvalue())
    assert payload["doc_path"] == "guide/large"
    mock_resolve_doc_path.assert_awaited_once_with(pool, "demo", "abc123")
    mock_get_chunks.assert_awaited_once_with(pool, "demo", "guide/large", section=None)


def test_pyproject_entry_points():
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    scripts = data["project"]["scripts"]
    assert scripts == {"doc-hub": "doc_hub.cli.main:main"}
