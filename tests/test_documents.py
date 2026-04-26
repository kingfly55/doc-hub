from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_hub.documents import (
    DocumentNode,
    _build_doc_id_map,
    doc_path_from_source_file,
    _derive_title,
    _humanize_path_segment,
    _slugify,
    _synthetic_tree_fallback,
    build_document_tree,
    delete_stale_documents,
    derive_doc_id,
    get_document_chunks,
    get_document_chunks_by_doc_id,
    get_document_sections,
    get_document_tree,
    link_chunks_to_documents,
    upsert_documents,
)
from doc_hub.parse import Chunk


def _make_chunk(
    source_file: str,
    *,
    source_url: str = "",
    heading: str = "Section",
    heading_level: int = 2,
    content: str = "Body",
    section_path: str | None = None,
    start_line: int = 1,
) -> Chunk:
    return Chunk(
        source_file=source_file,
        source_url=source_url,
        section_path=section_path or heading,
        heading=heading,
        heading_level=heading_level,
        content=content,
        start_line=start_line,
        end_line=start_line,
        char_count=len(content),
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        category="other",
        snapshot_id="legacy",
        source_version="latest",
        fetched_at="2026-04-24T12:00:00Z",
    )


def _make_mock_pool() -> tuple[MagicMock, AsyncMock]:
    pool = MagicMock()
    conn = AsyncMock()
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


def test_doc_path_from_source_file():
    assert doc_path_from_source_file("guides__install.md") == "guides/install"
    assert doc_path_from_source_file("index.md") == "index"


def test_humanize_path_segment():
    assert _humanize_path_segment("getting-started_now") == "Getting Started Now"


def test_slugify():
    assert _slugify("API Reference v2!") == "api-reference-v2"


def test_derive_doc_id_is_deterministic_and_short():
    doc_id = derive_doc_id("pydantic-ai", "guide/install")

    assert doc_id == derive_doc_id("pydantic-ai", "guide/install")
    assert len(doc_id) == 6
    assert doc_id.isalnum()
    assert doc_id == doc_id.lower()


def test_derive_doc_id_extends_on_collision():
    with patch("doc_hub.documents.hashlib.sha256") as mock_sha256:
        mock_sha256.side_effect = [
            MagicMock(hexdigest=MagicMock(return_value="abc123456789")),
            MagicMock(hexdigest=MagicMock(return_value="abc123999999")),
            MagicMock(hexdigest=MagicMock(return_value="abc123456789")),
        ]

        mapping = _build_doc_id_map("pydantic-ai", ["guide/install", "guide/other"])
        result = derive_doc_id("pydantic-ai", "guide/install")

    assert result == "abc123"
    assert mapping["guide/install"] == "abc123"
    assert mapping["guide/other"] == "abc12399"


def test_build_tree_empty_input():
    assert build_document_tree([]) == []


def test_build_tree_from_url_paths():
    chunks = [
        _make_chunk("guides__install.md", source_url="https://example.com/guides/install.md", heading="# Install", heading_level=1),
        _make_chunk("api__models.md", source_url="https://example.com/api/models.md", heading="# Models", heading_level=1),
    ]

    tree = build_document_tree(chunks)

    assert [node.doc_path for node in tree] == [
        "guides",
        "guides/install",
        "api",
        "api/models",
    ]
    assert tree[0] == DocumentNode(
        doc_path="guides",
        title="Guides",
        parent_path=None,
        depth=0,
        sort_order=0,
        is_group=True,
    )
    assert tree[1].title == "Install"
    assert tree[1].source_file == "guides__install.md"
    assert tree[1].source_url == "https://example.com/guides/install.md"


def test_build_tree_with_manifest_sections():
    chunks = [
        _make_chunk("guides__install.md", source_url="https://example.com/guides/install.md", heading="# Install Guide", heading_level=1),
        _make_chunk("api__models.md", source_url="https://example.com/api/models.md", heading="# Models", heading_level=1),
        _make_chunk("misc.md", source_url="https://example.com/misc.md", heading="# Misc", heading_level=1),
    ]
    sections = [
        {"title": "Guides", "heading_level": 2, "urls": ["https://example.com/guides/install.md"]},
        {"title": "API Reference", "heading_level": 2, "urls": ["https://example.com/api/models.md"]},
    ]

    tree = build_document_tree(chunks, manifest_sections=sections)

    assert [node.doc_path for node in tree] == [
        "_section/guides",
        "_section/guides/guides",
        "_section/guides/guides/install",
        "_section/api-reference",
        "_section/api-reference/api",
        "_section/api-reference/api/models",
        "misc",
    ]
    assert tree[0].title == "Guides"
    assert tree[0].parent_path is None
    assert tree[0].depth == 0
    assert tree[2].source_file == "guides__install.md"
    assert tree[6].parent_path is None


def test_build_tree_preserves_manifest_section_order_over_file_order():
    chunks = [
        _make_chunk("z-last.md", source_url="https://example.com/last.md", heading="# Last", heading_level=1),
        _make_chunk("a-first.md", source_url="https://example.com/first.md", heading="# First", heading_level=1),
    ]
    sections = [
        {"title": "First Section", "heading_level": 2, "urls": ["https://example.com/first.md"]},
        {"title": "Last Section", "heading_level": 2, "urls": ["https://example.com/last.md"]},
    ]

    tree = build_document_tree(chunks, manifest_sections=sections)

    assert [node.doc_path for node in tree] == [
        "_section/first-section",
        "_section/first-section/a-first",
        "_section/last-section",
        "_section/last-section/z-last",
    ]


def test_build_tree_keeps_empty_manifest_sections():
    chunks = [
        _make_chunk("guide.md", source_url="https://example.com/guide.md", heading="# Guide", heading_level=1),
    ]
    sections = [
        {"title": "Empty Section", "heading_level": 2, "urls": []},
        {"title": "Guides", "heading_level": 2, "urls": ["https://example.com/guide.md"]},
    ]

    tree = build_document_tree(chunks, manifest_sections=sections)

    assert [node.doc_path for node in tree] == [
        "_section/empty-section",
        "_section/guides",
        "_section/guides/guide",
    ]
    assert tree[0].is_group is True
    assert tree[0].parent_path is None
    assert tree[0].depth == 0
    assert tree[1].depth == 0
    assert tree[2].parent_path == "_section/guides"
    assert tree[2].depth == 1


def test_build_tree_section_namespace_depth_is_relative_to_section_root():
    chunks = [
        _make_chunk(
            "guides__guide.md",
            source_url="https://example.com/guides/guide.md",
            heading="# Guide",
            heading_level=1,
        ),
    ]
    sections = [
        {"title": "Guides", "heading_level": 2, "urls": ["https://example.com/guides/guide.md"]},
    ]

    tree = build_document_tree(chunks, manifest_sections=sections)

    assert [node.doc_path for node in tree] == [
        "_section/guides",
        "_section/guides/guides",
        "_section/guides/guides/guide",
    ]
    assert [node.depth for node in tree] == [0, 1, 2]
    assert [node.parent_path for node in tree] == [
        None,
        "_section/guides",
        "_section/guides/guides",
    ]


def test_build_tree_preserves_manifest_order_for_synthetic_root_section_files():
    chunks = [
        _make_chunk("z-last.md", source_url="https://example.com/last.md", heading="# Last", heading_level=1),
        _make_chunk("m-middle.md", source_url="https://example.com/middle.md", heading="# Middle", heading_level=1),
        _make_chunk("a-first.md", source_url="https://example.com/first.md", heading="# First", heading_level=1),
    ]
    sections = [
        {"title": "", "heading_level": 2, "urls": ["https://example.com/last.md", "https://example.com/first.md"]},
        {"title": "Guides", "heading_level": 2, "urls": ["https://example.com/middle.md"]},
    ]

    tree = build_document_tree(chunks, manifest_sections=sections)

    assert [node.doc_path for node in tree] == [
        "z-last",
        "a-first",
        "_section/guides",
        "_section/guides/m-middle",
    ]
    assert [node.source_file for node in tree if not node.is_group] == [
        "z-last.md",
        "a-first.md",
        "m-middle.md",
    ]


def test_build_tree_flat_fallback():
    chunks = [
        _make_chunk("getting-started.md", source_url="https://example.com/getting-started.md", heading_level=2),
        _make_chunk("faq.md", source_url="https://example.com/faq.md", heading="# FAQ", heading_level=1),
    ]

    tree = build_document_tree(chunks)

    assert [node.doc_path for node in tree] == ["getting-started", "faq"]
    assert [node.parent_path for node in tree] == [None, None]


def test_title_derivation_from_h1():
    chunks_by_file = {
        "guides__install.md": [
            _make_chunk("guides__install.md", heading="Overview", heading_level=2),
            _make_chunk("guides__install.md", heading="Install Guide", heading_level=1),
        ]
    }

    assert _derive_title("guides__install.md", chunks_by_file) == "Install Guide"


def test_title_derivation_fallback():
    chunks_by_file = {
        "api__reference.md": [
            _make_chunk("api__reference.md", heading="Overview", heading_level=2),
        ]
    }

    assert _derive_title("api__reference.md", chunks_by_file) == "Reference"


def test_virtual_group_nodes():
    chunks = [
        _make_chunk("guides__advanced__caching.md", heading="# Caching", heading_level=1),
    ]

    tree = build_document_tree(chunks)

    assert [node.doc_path for node in tree] == [
        "guides",
        "guides/advanced",
        "guides/advanced/caching",
    ]
    assert tree[0].is_group is True
    assert tree[1].is_group is True
    assert tree[2].is_group is False


def test_concrete_document_wins_over_same_doc_path_group():
    chunks = [
        _make_chunk("api.md", heading="# API", heading_level=1),
        _make_chunk("api__models.md", heading="# Models", heading_level=1),
    ]

    tree = build_document_tree(chunks)

    assert [node.doc_path for node in tree] == [
        "api",
        "api/models",
    ]
    assert tree[0].is_group is False
    assert len({node.doc_path for node in tree}) == len(tree)


def test_concrete_parent_is_emitted_before_child_when_input_is_child_first():
    chunks = [
        _make_chunk("api__models.md", heading="# Models", heading_level=1),
        _make_chunk("api.md", heading="# API", heading_level=1),
    ]

    tree = build_document_tree(chunks)

    assert [node.doc_path for node in tree] == [
        "api",
        "api/models",
    ]
    assert [node.is_group for node in tree] == [False, False]
    assert [node.parent_path for node in tree] == [None, "api"]
    assert [node.sort_order for node in tree] == [0, 1]


def test_sort_order_monotonically_increasing():
    chunks = [
        _make_chunk("b__page.md"),
        _make_chunk("a__page.md"),
    ]

    tree = build_document_tree(chunks)

    assert [node.sort_order for node in tree] == list(range(len(tree)))


def test_total_chars_computed():
    chunks = [
        _make_chunk("guide.md", content="abc"),
        _make_chunk("guide.md", content="defgh"),
    ]

    tree = build_document_tree(chunks)

    assert tree == [
        DocumentNode(
            doc_path="guide",
            title="Guide",
            source_file="guide.md",
            total_chars=8,
            section_count=2,
        )
    ]


def test_section_count_computed():
    chunks = [
        _make_chunk("guide.md", heading="A"),
        _make_chunk("guide.md", heading="B"),
        _make_chunk("other.md", heading="C"),
    ]

    tree = build_document_tree(chunks)

    counts = {node.doc_path: node.section_count for node in tree if not node.is_group}
    assert counts == {"guide": 2, "other": 1}


def test_unassigned_files_go_to_root():
    chunks = [
        _make_chunk("guides__install.md", source_url="https://example.com/guides/install.md", heading="# Install", heading_level=1),
        _make_chunk("loose.md", source_url="https://example.com/loose.md", heading="# Loose", heading_level=1),
    ]
    sections = [
        {"title": "Guides", "heading_level": 2, "urls": ["https://example.com/guides/install.md"]},
    ]

    tree = build_document_tree(chunks, manifest_sections=sections)

    assert [node.doc_path for node in tree] == [
        "_section/guides",
        "_section/guides/guides",
        "_section/guides/guides/install",
        "loose",
    ]
    assert tree[-1].parent_path is None


@pytest.mark.asyncio
async def test_upsert_documents_calls_insert():
    pool, conn = _make_mock_pool()
    nodes = [DocumentNode(doc_path="guide", title="Guide", source_file="guide.md")]
    conn.fetchrow = AsyncMock(return_value={"id": 10})
    conn.execute = AsyncMock(return_value="UPDATE 1")

    await upsert_documents(pool, "test-corpus", nodes)

    insert_sql, *insert_args = conn.fetchrow.call_args.args
    assert "INSERT INTO doc_documents" in insert_sql
    assert insert_args[0] == "test-corpus"
    assert insert_args[1] == "legacy"
    assert insert_args[2] == "latest"
    assert insert_args[3] == "guide"


@pytest.mark.asyncio
async def test_upsert_documents_sets_parent_id():
    pool, conn = _make_mock_pool()
    nodes = [
        DocumentNode(doc_path="guides", title="Guides", is_group=True),
        DocumentNode(doc_path="guides/install", title="Install", parent_path="guides", source_file="guides__install.md"),
    ]
    conn.fetchrow = AsyncMock(side_effect=[{"id": 1}, {"id": 2}])
    conn.execute = AsyncMock(return_value="UPDATE 1")

    await upsert_documents(pool, "test-corpus", nodes)

    parent_update_args = conn.execute.call_args_list[1].args
    assert parent_update_args[1] == 1
    assert parent_update_args[2] == "test-corpus"
    assert parent_update_args[3] == "legacy"
    assert parent_update_args[4] == "guides/install"


@pytest.mark.asyncio
async def test_upsert_documents_clears_root_parent_id():
    pool, conn = _make_mock_pool()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    conn.execute = AsyncMock(return_value="UPDATE 1")

    await upsert_documents(pool, "test-corpus", [DocumentNode(doc_path="root", title="Root")])

    update_args = conn.execute.call_args.args
    assert update_args[1] is None
    assert update_args[3] == "legacy"
    assert update_args[4] == "root"


@pytest.mark.asyncio
async def test_upsert_documents_returns_path_to_id():
    pool, conn = _make_mock_pool()
    conn.fetchrow = AsyncMock(side_effect=[{"id": 11}, {"id": 22}])
    conn.execute = AsyncMock(return_value="UPDATE 1")
    nodes = [
        DocumentNode(doc_path="a", title="A"),
        DocumentNode(doc_path="a/b", title="B", parent_path="a"),
    ]

    result = await upsert_documents(pool, "test-corpus", nodes)

    assert result == {"a": 11, "a/b": 22}


@pytest.mark.asyncio
async def test_link_chunks_to_documents_updates_rows():
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=[
        [{"source_file": "guide.md", "id": 1}],
        [{"source_file": "guide.md"}, {"source_file": "missing.md"}],
    ])
    pool.execute = AsyncMock(side_effect=["UPDATE 2"])

    result = await link_chunks_to_documents(pool, "test-corpus", {"guide": 1})

    assert result == 2
    assert pool.execute.call_count == 1


@pytest.mark.asyncio
async def test_link_chunks_to_documents_uses_source_file_exact_match():
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=[
        [{"source_file": "guide.md", "id": 1}],
        [{"source_file": "guide.md"}, {"source_file": "guide.md.bak"}],
    ])
    pool.execute = AsyncMock(return_value="UPDATE 1")

    await link_chunks_to_documents(pool, "test-corpus", {})

    _, doc_id, corpus_slug, snapshot_id, source_file = pool.execute.call_args.args
    assert doc_id == 1
    assert corpus_slug == "test-corpus"
    assert snapshot_id == "legacy"
    assert source_file == "guide.md"


@pytest.mark.asyncio
async def test_delete_stale_documents_deletes():
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="DELETE 3")

    result = await delete_stale_documents(pool, "test-corpus", ["keep/me"])

    assert result == 3
    sql, corpus_slug, snapshot_id, paths = pool.execute.call_args.args
    assert "NOT (doc_path = ANY($3::text[]))" in sql
    assert corpus_slug == "test-corpus"
    assert snapshot_id == "legacy"
    assert paths == ["keep/me"]


@pytest.mark.asyncio
async def test_delete_stale_documents_deletes_all_when_empty():
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="DELETE 5")

    result = await delete_stale_documents(pool, "test-corpus", [])

    assert result == 5
    assert pool.execute.call_args.args[2] == "legacy"
    assert pool.execute.call_args.args[3] == []


@pytest.mark.asyncio
async def test_get_document_tree_returns_list():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {
            "doc_path": "guide",
            "title": "Guide",
            "source_url": "https://example.com/guide",
            "depth": 0,
            "is_group": False,
            "total_chars": 100,
            "section_count": 2,
        }
    ])

    result = await get_document_tree(pool, "test-corpus")

    assert result == [
        {
            "doc_path": "guide",
            "doc_id": derive_doc_id("test-corpus", "guide"),
            "title": "Guide",
            "source_url": "https://example.com/guide",
            "depth": 0,
            "is_group": False,
            "total_chars": 100,
            "section_count": 2,
            "children_count": 0,
        }
    ]


@pytest.mark.asyncio
async def test_get_document_tree_falls_back_only_when_no_documents_exist():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)

    with patch("doc_hub.documents._synthetic_tree_fallback", new=AsyncMock(return_value=[{"doc_path": "guide"}])) as fallback:
        result = await get_document_tree(pool, "test-corpus")

    assert result == [{"doc_path": "guide"}]
    fallback.assert_awaited_once_with(pool, "test-corpus", snapshot_id="legacy")


@pytest.mark.asyncio
async def test_get_document_tree_returns_empty_for_missing_subtree_when_documents_exist():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value={"present": 1})

    with patch("doc_hub.documents._synthetic_tree_fallback", new=AsyncMock(return_value=[{"doc_path": "fallback"}])) as fallback:
        result = await get_document_tree(pool, "test-corpus", path="guides")

    assert result == []
    fallback.assert_not_awaited()
    sql, *args = pool.fetch.call_args_list[0].args
    assert "doc_path = $3 OR doc_path LIKE $3 || '/%'" in sql
    assert args[2] == "guides"


@pytest.mark.asyncio
async def test_get_document_tree_returns_empty_for_missing_subtree_when_path_matches_nothing():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value={"present": 1})

    with patch("doc_hub.documents._synthetic_tree_fallback", new=AsyncMock(return_value=[{"doc_path": "fallback"}])) as fallback:
        result = await get_document_tree(pool, "test-corpus", path="missing/subtree")

    assert result == []
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_document_tree_with_path_filter():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value={"present": 1})

    with patch("doc_hub.documents._synthetic_tree_fallback", new=AsyncMock(return_value=[])):
        await get_document_tree(pool, "test-corpus", path="guides")

    sql, *args = pool.fetch.call_args_list[0].args
    assert "doc_path = $3 OR doc_path LIKE $3 || '/%'" in sql
    assert args[2] == "guides"


@pytest.mark.asyncio
async def test_get_document_tree_with_depth_filter_relative():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value={"present": 1})

    with patch("doc_hub.documents._synthetic_tree_fallback", new=AsyncMock(return_value=[])):
        await get_document_tree(pool, "test-corpus", path="guides", max_depth=2)

    sql, *args = pool.fetch.call_args.args
    # root_depth for "guides" (0 slashes) is 0, so absolute cutoff = 0 + 2 = 2
    assert "depth <= $4" in sql
    assert args[3] == 2


@pytest.mark.asyncio
async def test_get_document_tree_orders_by_sort_order():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value={"present": 1})

    with patch("doc_hub.documents._synthetic_tree_fallback", new=AsyncMock(return_value=[])):
        await get_document_tree(pool, "test-corpus")

    sql = pool.fetch.call_args.args[0]
    assert "ORDER BY sort_order" in sql


@pytest.mark.asyncio
async def test_get_document_chunks_returns_ordered_with_id():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"id": 9, "source_file": "guide.md"})
    pool.fetch = AsyncMock(return_value=[
        {
            "id": 101,
            "heading": "Install",
            "heading_level": 2,
            "section_path": "Guide > Install",
            "char_count": 5,
            "source_file": "guide.md",
            "source_url": "https://example.com/guide",
            "content": "body",
            "start_line": 3,
            "end_line": 4,
            "category": "guide",
        }
    ])

    result = await get_document_chunks(pool, "test-corpus", "guide")

    assert result[0]["id"] == 101
    assert result[0]["start_line"] == 3
    sql = pool.fetch.call_args.args[0]
    assert "ORDER BY c.start_line" in sql



@pytest.mark.asyncio
async def test_get_document_chunks_handles_namespaced_doc_path():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])

    await get_document_chunks(pool, "test-corpus", "_section/guides/guides/install")

    assert pool.fetch.call_args.args[-1] == "guides__install.md"


@pytest.mark.asyncio
async def test_get_document_chunks_by_doc_id_resolves_and_returns_chunks():
    pool = MagicMock()
    doc_id = derive_doc_id("test-corpus", "guide/install")
    db_rows = [MagicMock(**{"__getitem__": lambda self, k: {"id": 7, "doc_path": "guide/install"}[k]})]
    db_rows[0].__iter__ = lambda self: iter({"id": 7, "doc_path": "guide/install"}.items())
    # Use a simpler approach: plain dicts via asyncpg Record-like objects
    pool.fetch = AsyncMock(side_effect=[
        [{"id": 7, "doc_path": "guide/install"}],   # doc_documents query
        [{"heading": "Install", "heading_level": 1, "section_path": "Install",
          "char_count": 10, "source_file": "guide__install.md",
          "source_url": "https://example.com", "content": "body",
          "start_line": 1, "end_line": 5, "category": "guide", "id": 99}],  # chunks query
    ])

    doc_path, chunks = await get_document_chunks_by_doc_id(pool, "test-corpus", doc_id)

    assert doc_path == "guide/install"
    assert len(chunks) == 1
    assert chunks[0]["heading"] == "Install"


@pytest.mark.asyncio
async def test_get_document_chunks_by_doc_id_returns_none_for_unknown_id():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"id": 7, "doc_path": "guide/install"}])

    doc_path, chunks = await get_document_chunks_by_doc_id(pool, "test-corpus", "unknown")

    assert doc_path is None
    assert chunks == []


@pytest.mark.asyncio
async def test_get_document_sections_returns_outline():
    chunks = [
        {
            "heading": "Install",
            "heading_level": 2,
            "section_path": "Guide > Install",
            "char_count": 10,
            "content": "body",
            "source_file": "guide.md",
            "source_url": "https://example.com/guide",
            "start_line": 1,
            "end_line": 2,
            "category": "guide",
        }
    ]

    with patch("doc_hub.documents.get_document_chunks", new=AsyncMock(return_value=chunks)):
        result = await get_document_sections(MagicMock(), "test-corpus", "guide")

    assert result == [
        {
            "heading": "Install",
            "heading_level": 2,
            "section_path": "Guide > Install",
            "char_count": 10,
        }
    ]


@pytest.mark.asyncio
async def test_synthetic_tree_fallback_flat_list_uses_first_available_source_url():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {
            "source_file": "guides__install.md",
            "source_url": "https://example.com/install",
            "total_chars": 12,
            "section_count": 2,
        },
        {
            "source_file": "reference.md",
            "source_url": "",
            "total_chars": 4,
            "section_count": 1,
        },
    ])

    result = await _synthetic_tree_fallback(pool, "test-corpus")

    assert result == [
        {
            "doc_path": "guides/install",
            "doc_id": derive_doc_id("test-corpus", "guides/install"),
            "title": "Install",
            "source_url": "https://example.com/install",
            "depth": 0,
            "is_group": False,
            "total_chars": 12,
            "section_count": 2,
            "children_count": 0,
        },
        {
            "doc_path": "reference",
            "doc_id": derive_doc_id("test-corpus", "reference"),
            "title": "Reference",
            "source_url": "",
            "depth": 0,
            "is_group": False,
            "total_chars": 4,
            "section_count": 1,
            "children_count": 0,
        },
    ]
    sql = pool.fetch.call_args.args[0]
    assert "GROUP BY source_file" in sql
    assert "ARRAY_AGG(source_url ORDER BY start_line) FILTER (WHERE source_url <> '')" in sql
    assert "MAX(source_url)" not in sql


@pytest.mark.asyncio
async def test_synthetic_tree_fallback_prefers_first_available_source_url_over_lexicographic_max():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[
        {
            "source_file": "guide.md",
            "source_url": "https://example.com/a-first",
            "total_chars": 9,
            "section_count": 3,
        }
    ])

    result = await _synthetic_tree_fallback(pool, "test-corpus")

    assert result == [
        {
            "doc_path": "guide",
            "doc_id": derive_doc_id("test-corpus", "guide"),
            "title": "Guide",
            "source_url": "https://example.com/a-first",
            "depth": 0,
            "is_group": False,
            "total_chars": 9,
            "section_count": 3,
            "children_count": 0,
        }
    ]
