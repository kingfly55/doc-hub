from __future__ import annotations

import argparse
import io
import json
import tomllib
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


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



def test_browse_main_uses_load_dotenv_and_asyncio_run():
    from doc_hub import browse as browse_module

    argv = ["demo-corpus"]
    parsed_args = argparse.Namespace(corpus="demo-corpus", path=None, depth=None, version=None, json=False)

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

    argv = ["demo-corpus", "abc123"]
    parsed_args = argparse.Namespace(corpus="demo-corpus", doc_id="abc123", version=None, json=False)

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


def test_browse_async_missing_corpus_raises_clear_error():
    from doc_hub import browse as browse_module

    args = argparse.Namespace(corpus="gastown", path=None, depth=None, version=None, json=True)
    pool = MagicMock()

    with (
        patch.object(browse_module, "create_pool", new=AsyncMock(return_value=pool)),
        patch.object(browse_module, "ensure_schema", new=AsyncMock()),
        patch.object(browse_module, "validate_corpus_available", new=AsyncMock(side_effect=ValueError("Corpus 'gastown' not found. Did you mean: Gas City [gascity-v1]?"))),
    ):
        pool.close = AsyncMock()
        import asyncio
        try:
            asyncio.run(browse_module.browse(args))
            assert False, "Expected ValueError"
        except ValueError as exc:
            assert "Corpus 'gastown' not found" in str(exc)
            assert "Did you mean" in str(exc)


def test_browse_async_json_output():
    from doc_hub import browse as browse_module

    args = argparse.Namespace(corpus="demo", path="guides", depth=1, version=None, json=True)
    pool = MagicMock()
    nodes = [
        {"title": "Guides", "depth": 0, "is_group": True, "total_chars": 0, "section_count": 0},
        {"title": "Install", "depth": 1, "is_group": False, "doc_id": "abc123", "total_chars": 120, "section_count": 2},
    ]

    stdout = io.StringIO()
    with (
        patch.object(browse_module, "create_pool", new=AsyncMock(return_value=pool)),
        patch.object(browse_module, "ensure_schema", new=AsyncMock()),
        patch.object(browse_module, "validate_corpus_available", new=AsyncMock()),
        patch.object(browse_module, "get_default_snapshot_id", new=AsyncMock(return_value="legacy")),
        patch.object(browse_module, "get_document_tree", new=AsyncMock(return_value=nodes)) as mock_get_tree,
        redirect_stdout(stdout),
    ):
        pool.close = AsyncMock()
        import asyncio
        asyncio.run(browse_module.browse(args))

    mock_get_tree.assert_awaited_once_with(pool, "demo", path="guides", max_depth=1, snapshot_id="legacy")
    assert json.loads(stdout.getvalue()) == {"corpus": "demo", "snapshot_id": "legacy", "documents": nodes}


def test_read_not_found_prints_message_and_returns_successfully():
    from doc_hub import browse as browse_module

    args = argparse.Namespace(corpus="demo", doc_id="missing1", version=None, json=False)
    pool = MagicMock()

    stdout = io.StringIO()
    with (
        patch.object(browse_module, "create_pool", new=AsyncMock(return_value=pool)),
        patch.object(browse_module, "ensure_schema", new=AsyncMock()),
        patch.object(browse_module, "validate_corpus_available", new=AsyncMock()),
        patch.object(browse_module, "get_default_snapshot_id", new=AsyncMock(return_value="legacy")),
        patch.object(browse_module, "get_document_chunks_by_doc_id", new=AsyncMock(return_value=(None, []))),
        redirect_stdout(stdout),
    ):
        pool.close = AsyncMock()
        import asyncio
        asyncio.run(browse_module.read(args))

    assert "Document 'missing1' not found in corpus 'demo'" in stdout.getvalue()


def test_read_json_full_output():
    from doc_hub import browse as browse_module

    args = argparse.Namespace(corpus="demo", doc_id="abc123", version=None, json=True)
    pool = MagicMock()
    chunks = [
        {"heading": "Title", "heading_level": 1, "section_path": "Title", "char_count": 5, "source_url": "https://example.com/doc", "content": "First"},
        {"heading": "Deep Dive", "heading_level": 2, "section_path": "Title > Deep Dive", "char_count": 10, "source_url": "https://example.com/doc", "content": "Second"},
    ]

    stdout = io.StringIO()
    with (
        patch.object(browse_module, "create_pool", new=AsyncMock(return_value=pool)),
        patch.object(browse_module, "ensure_schema", new=AsyncMock()),
        patch.object(browse_module, "validate_corpus_available", new=AsyncMock()),
        patch.object(browse_module, "get_default_snapshot_id", new=AsyncMock(return_value="legacy")),
        patch.object(browse_module, "get_document_chunks_by_doc_id", new=AsyncMock(return_value=("guide/large", chunks))) as mock_get_chunks,
        redirect_stdout(stdout),
    ):
        pool.close = AsyncMock()
        import asyncio
        asyncio.run(browse_module.read(args))

    payload = json.loads(stdout.getvalue())
    assert payload["mode"] == "full"
    assert payload["content"] == "First\n\nSecond"
    assert payload["doc_path"] == "guide/large"
    assert payload["snapshot_id"] == "legacy"
    mock_get_chunks.assert_awaited_once_with(pool, "demo", "abc123", snapshot_id="legacy")


def test_pyproject_entry_points():
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    scripts = data["project"]["scripts"]
    assert scripts == {"doc-hub": "doc_hub.cli.main:main"}
