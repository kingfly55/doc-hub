from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from doc_hub.models import Corpus
from doc_hub.pipeline import _build_arg_parser, run_build_tree, run_pipeline


def _make_corpus(slug: str = "test") -> Corpus:
    return Corpus(
        slug=slug,
        name="Test Corpus",
        fetch_strategy="llms_txt",
        fetch_config={
            "url": "https://example.com/llms.txt",
            "url_pattern": r".*\.md",
            "base_url": "https://example.com",
        },
    )


def _write_chunks_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _chunk_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "source_file": "guide.md",
        "source_url": "https://example.com/guide.md",
        "section_path": "Guide",
        "heading": "# Guide",
        "heading_level": 1,
        "content": "Body",
        "start_line": 1,
        "end_line": 2,
        "char_count": 4,
        "content_hash": "abc123",
        "category": "other",
    }
    row.update(overrides)
    return row


def test_run_build_tree_importable():
    from doc_hub.pipeline import run_build_tree as imported

    assert imported is run_build_tree


@pytest.mark.asyncio
async def test_run_build_tree_missing_chunks_file(tmp_path):
    corpus = _make_corpus()
    chunks_path = tmp_path / "chunks" / "chunks.jsonl"
    raw_path = tmp_path / "raw"

    with (
        patch("doc_hub.pipeline.chunks_dir", return_value=chunks_path.parent),
        patch("doc_hub.pipeline.raw_dir", return_value=raw_path),
        patch("doc_hub.documents.build_document_tree") as mock_build_tree,
        patch("doc_hub.db.create_pool", new=AsyncMock()) as mock_create_pool,
        patch("doc_hub.db.ensure_schema", new=AsyncMock()) as mock_ensure_schema,
        patch("doc_hub.documents.upsert_documents", new=AsyncMock()) as mock_upsert,
        patch("doc_hub.documents.link_chunks_to_documents", new=AsyncMock()) as mock_link,
        patch("doc_hub.documents.delete_stale_documents", new=AsyncMock()) as mock_delete,
    ):
        result = await run_build_tree(corpus)

    assert result == {"documents": 0, "linked_chunks": 0, "deleted": 0}
    mock_build_tree.assert_not_called()
    mock_create_pool.assert_not_awaited()
    mock_ensure_schema.assert_not_awaited()
    mock_upsert.assert_not_awaited()
    mock_link.assert_not_awaited()
    mock_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_build_tree_reads_manifest_sections(tmp_path):
    corpus = _make_corpus()
    chunks_path = tmp_path / "chunks" / "chunks.jsonl"
    raw_path = tmp_path / "raw"
    manifest_path = raw_path / "manifest.json"
    _write_chunks_jsonl(chunks_path, [_chunk_row()])
    raw_path.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"sections": [{"title": "Guides", "urls": ["https://example.com/guide.md"]}]}),
        encoding="utf-8",
    )

    pool = MagicMock()

    with (
        patch("doc_hub.pipeline.chunks_dir", return_value=chunks_path.parent),
        patch("doc_hub.pipeline.raw_dir", return_value=raw_path),
        patch("doc_hub.documents.build_document_tree", return_value=[]) as mock_build_tree,
    ):
        result = await run_build_tree(corpus, pool=pool)

    assert result == {"documents": 0, "linked_chunks": 0, "deleted": 0}
    assert mock_build_tree.call_args.kwargs["manifest_sections"] == [
        {"title": "Guides", "urls": ["https://example.com/guide.md"]}
    ]


@pytest.mark.asyncio
async def test_run_build_tree_no_manifest_sections(tmp_path):
    corpus = _make_corpus()
    chunks_path = tmp_path / "chunks" / "chunks.jsonl"
    raw_path = tmp_path / "raw"
    manifest_path = raw_path / "manifest.json"
    _write_chunks_jsonl(chunks_path, [_chunk_row()])
    raw_path.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")

    pool = MagicMock()

    with (
        patch("doc_hub.pipeline.chunks_dir", return_value=chunks_path.parent),
        patch("doc_hub.pipeline.raw_dir", return_value=raw_path),
        patch("doc_hub.documents.build_document_tree", return_value=[]) as mock_build_tree,
    ):
        result = await run_build_tree(corpus, pool=pool)

    assert result == {"documents": 0, "linked_chunks": 0, "deleted": 0}
    assert mock_build_tree.call_args.kwargs["manifest_sections"] is None


@pytest.mark.asyncio
async def test_run_build_tree_calls_build_document_tree(tmp_path):
    corpus = _make_corpus()
    chunks_path = tmp_path / "chunks" / "chunks.jsonl"
    raw_path = tmp_path / "raw"
    _write_chunks_jsonl(chunks_path, [_chunk_row()])
    raw_path.mkdir(parents=True, exist_ok=True)

    pool = MagicMock()
    nodes = [MagicMock(doc_path="guide")]
    path_to_id = {"guide": 1}

    with (
        patch("doc_hub.pipeline.chunks_dir", return_value=chunks_path.parent),
        patch("doc_hub.pipeline.raw_dir", return_value=raw_path),
        patch("doc_hub.documents.build_document_tree", return_value=nodes) as mock_build_tree,
        patch("doc_hub.db.ensure_schema", new=AsyncMock()) as mock_ensure_schema,
        patch("doc_hub.documents.upsert_documents", new=AsyncMock(return_value=path_to_id)) as mock_upsert,
        patch("doc_hub.documents.link_chunks_to_documents", new=AsyncMock(return_value=7)) as mock_link,
        patch("doc_hub.documents.delete_stale_documents", new=AsyncMock(return_value=2)) as mock_delete,
    ):
        result = await run_build_tree(corpus, pool=pool)

    assert result == {"documents": 1, "linked_chunks": 7, "deleted": 2}
    loaded_chunks = mock_build_tree.call_args.args[0]
    assert len(loaded_chunks) == 1
    assert loaded_chunks[0].source_file == "guide.md"
    assert mock_build_tree.call_args.kwargs["manifest_sections"] is None
    assert mock_ensure_schema.await_args_list == [call(pool)]
    assert mock_upsert.await_args_list == [call(pool, corpus.slug, nodes)]
    assert mock_link.await_args_list == [call(pool, corpus.slug, path_to_id)]
    assert mock_delete.await_args_list == [call(pool, corpus.slug, ["guide"])]


@pytest.mark.asyncio
async def test_run_pipeline_tree_stage():
    corpus = _make_corpus()

    with (
        patch("doc_hub.pipeline.run_fetch", new=AsyncMock()) as mock_fetch,
        patch("doc_hub.pipeline.run_parse", new=AsyncMock()) as mock_parse,
        patch("doc_hub.pipeline.run_embed", new=AsyncMock()) as mock_embed,
        patch("doc_hub.pipeline.run_index", new=AsyncMock()) as mock_index,
        patch("doc_hub.pipeline.run_build_tree", new=AsyncMock(return_value={"documents": 1, "linked_chunks": 1, "deleted": 0})) as mock_tree,
    ):
        result = await run_pipeline(corpus, stage="tree")

    assert result is None
    mock_tree.assert_awaited_once_with(corpus, pool=None)
    mock_fetch.assert_not_awaited()
    mock_parse.assert_not_awaited()
    mock_embed.assert_not_awaited()
    mock_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_build_tree_returns_zero_without_db_work_for_empty_tree(tmp_path):
    corpus = _make_corpus()
    chunks_path = tmp_path / "chunks" / "chunks.jsonl"
    raw_path = tmp_path / "raw"
    _write_chunks_jsonl(chunks_path, [_chunk_row()])
    raw_path.mkdir(parents=True, exist_ok=True)

    with (
        patch("doc_hub.pipeline.chunks_dir", return_value=chunks_path.parent),
        patch("doc_hub.pipeline.raw_dir", return_value=raw_path),
        patch("doc_hub.documents.build_document_tree", return_value=[]) as mock_build_tree,
        patch("doc_hub.db.create_pool", new=AsyncMock()) as mock_create_pool,
        patch("doc_hub.db.ensure_schema", new=AsyncMock()) as mock_ensure_schema,
        patch("doc_hub.documents.upsert_documents", new=AsyncMock()) as mock_upsert,
        patch("doc_hub.documents.link_chunks_to_documents", new=AsyncMock()) as mock_link,
        patch("doc_hub.documents.delete_stale_documents", new=AsyncMock()) as mock_delete,
    ):
        result = await run_build_tree(corpus)

    assert result == {"documents": 0, "linked_chunks": 0, "deleted": 0}
    assert mock_build_tree.call_count == 1
    mock_create_pool.assert_not_awaited()
    mock_ensure_schema.assert_not_awaited()
    mock_upsert.assert_not_awaited()
    mock_link.assert_not_awaited()
    mock_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_pipeline_full_passes_same_pool_to_tree():
    corpus = _make_corpus()
    call_order: list[str] = []
    shared_pool = MagicMock(name="shared_pool")
    index_result = MagicMock(name="index_result")

    async def fake_fetch(*args, **kwargs):
        call_order.append("fetch")

    async def fake_parse(*args, **kwargs):
        call_order.append("parse")
        return []

    async def fake_embed(*args, **kwargs):
        call_order.append("embed")
        return []

    async def fake_index(*args, **kwargs):
        call_order.append("index")
        return index_result

    async def fake_tree(*args, **kwargs):
        call_order.append("tree")
        return {"documents": 1, "linked_chunks": 1, "deleted": 0}

    with (
        patch("doc_hub.pipeline.run_fetch", side_effect=fake_fetch),
        patch("doc_hub.pipeline.run_parse", side_effect=fake_parse),
        patch("doc_hub.pipeline.run_embed", side_effect=fake_embed),
        patch("doc_hub.pipeline.run_index", side_effect=fake_index),
        patch("doc_hub.pipeline.run_build_tree", side_effect=fake_tree) as mock_tree,
        patch("doc_hub.discovery.get_registry") as mock_registry,
    ):
        mock_registry.return_value.get_embedder.return_value = MagicMock()
        result = await run_pipeline(corpus, stage=None, pool=shared_pool)

    assert result is index_result
    assert call_order == ["fetch", "parse", "embed", "index", "tree"]
    mock_tree.assert_awaited_once_with(corpus, pool=shared_pool)


@pytest.mark.asyncio
async def test_run_pipeline_unknown_stage_lists_tree_in_error_message():
    corpus = _make_corpus()

    with pytest.raises(ValueError, match=r"Unknown stage: 'frobnicate'.*fetch, parse, embed, index, tree"):
        await run_pipeline(corpus, stage="frobnicate")


def test_stage_tree_in_choices():
    parser = _build_arg_parser()
    args = parser.parse_args(["--corpus", "x", "--stage", "tree"])
    assert args.stage == "tree"
