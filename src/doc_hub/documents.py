"""Document hierarchy helpers for doc-hub."""
from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass

from doc_hub.index import _parse_command_count
from doc_hub.parse import Chunk


@dataclass
class DocumentNode:
    doc_path: str
    title: str
    source_url: str = ""
    source_file: str = ""
    snapshot_id: str = "legacy"
    source_version: str = "latest"
    parent_path: str | None = None
    depth: int = 0
    sort_order: int = 0
    is_group: bool = False
    total_chars: int = 0
    section_count: int = 0


def doc_path_from_source_file(source_file: str) -> str:
    return source_file.removesuffix(".md").replace("__", "/")


def _humanize_path_segment(segment: str) -> str:
    cleaned = segment.replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", cleaned).strip().title()


def _slugify(title: str) -> str:
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", title.lower()))


def _clean_heading_text(heading: str) -> str:
    return re.sub(r"^#+\s*", "", heading).strip()


def _doc_id_exists(corpus_slug: str, candidate: str, *, exclude_doc_path: str | None = None) -> bool:
    return False


def _build_doc_id_map(
    corpus_slug: str,
    doc_paths: list[str],
    *,
    snapshot_id: str = "legacy",
    length: int = 6,
) -> dict[str, str]:
    ids_by_path: dict[str, str] = {}
    used_ids: set[str] = set()

    for doc_path in sorted(doc_paths):
        digest = hashlib.sha256(f"{corpus_slug}:{snapshot_id}:{doc_path}".encode()).hexdigest()
        candidate_length = length
        while True:
            candidate = digest[:candidate_length]
            if candidate not in used_ids:
                ids_by_path[doc_path] = candidate
                used_ids.add(candidate)
                break
            candidate_length += 2
            if candidate_length > len(digest):
                ids_by_path[doc_path] = digest
                used_ids.add(digest)
                break

    return ids_by_path


def derive_doc_id(
    corpus_slug: str,
    doc_path: str,
    length: int = 6,
    *,
    snapshot_id: str = "legacy",
) -> str:
    return _build_doc_id_map(corpus_slug, [doc_path], snapshot_id=snapshot_id, length=length)[doc_path]


def _derive_title(source_file: str, chunks_by_file: dict[str, list[Chunk]]) -> str:
    for chunk in chunks_by_file.get(source_file, []):
        if chunk.heading_level == 1:
            title = _clean_heading_text(chunk.heading)
            if title:
                return title
    doc_path = doc_path_from_source_file(source_file)
    return _humanize_path_segment(doc_path.rsplit("/", 1)[-1])


def _iter_path_prefixes(doc_path: str):
    parts = doc_path.split("/")
    for i in range(1, len(parts)):
        yield "/".join(parts[:i])


def _node_parent_path_and_depth(doc_path: str) -> tuple[str | None, int]:
    if doc_path.startswith("_section/"):
        parts = doc_path.split("/")
        if len(parts) == 2:
            return None, 0
        parent_path = doc_path.rsplit("/", 1)[0]
        depth = len(parts) - 2
        return parent_path, depth
    parent_path = doc_path.rsplit("/", 1)[0] if "/" in doc_path else None
    depth = doc_path.count("/")
    return parent_path, depth


def build_document_tree(
    chunks: list[Chunk],
    manifest_sections: list[dict] | None = None,
) -> list[DocumentNode]:
    if not chunks:
        return []

    chunks_by_file: OrderedDict[str, list[Chunk]] = OrderedDict()
    for chunk in chunks:
        chunks_by_file.setdefault(chunk.source_file, []).append(chunk)

    file_nodes: list[dict[str, object]] = []
    for source_file, file_chunks in chunks_by_file.items():
        source_url = next((chunk.source_url for chunk in file_chunks if chunk.source_url), "")
        file_nodes.append(
            {
                "source_file": source_file,
                "source_url": source_url,
                "snapshot_id": file_chunks[0].snapshot_id,
                "source_version": file_chunks[0].source_version,
                "doc_path": doc_path_from_source_file(source_file),
                "title": _derive_title(source_file, chunks_by_file),
                "total_chars": sum(chunk.char_count for chunk in file_chunks),
                "section_count": len(file_chunks),
            }
        )

    section_by_url: OrderedDict[str, dict[str, object]] = OrderedDict()
    ordered_sections: list[dict[str, object]] = []
    root_urls: set[str] = set()
    if manifest_sections is not None:
        for section in manifest_sections:
            title = str(section.get("title", ""))
            urls = list(dict.fromkeys(section.get("urls", [])))
            if title == "":
                ordered_sections.append({"title": title, "urls": urls})
                root_urls.update(urls)
                continue
            slug = _slugify(title)
            section_info = {
                "title": title,
                "slug": slug,
                "doc_path": f"_section/{slug}",
                "urls": urls,
            }
            ordered_sections.append(section_info)
            for url in urls:
                section_by_url[url] = section_info

    nodes: list[DocumentNode] = []
    emitted_doc_paths: set[str] = set()
    sort_order = 0

    assigned_section_by_source_file: dict[str, dict[str, object]] = {}
    if manifest_sections is not None:
        for file_info in file_nodes:
            source_url = str(file_info["source_url"])
            if source_url in root_urls:
                continue
            section_info = section_by_url.get(source_url)
            if section_info is not None:
                assigned_section_by_source_file[str(file_info["source_file"])] = section_info

    concrete_doc_paths = {
        (
            f"{assigned_section_by_source_file[str(file_info['source_file'])]['doc_path']}/{file_info['doc_path']}"
            if str(file_info["source_file"]) in assigned_section_by_source_file
            else str(file_info["doc_path"])
        )
        for file_info in file_nodes
    }

    def emit_group(doc_path: str, title: str) -> None:
        nonlocal sort_order
        if doc_path in emitted_doc_paths or doc_path in concrete_doc_paths:
            return
        parent_path, depth = _node_parent_path_and_depth(doc_path)
        nodes.append(
            DocumentNode(
                doc_path=doc_path,
                title=title,
                parent_path=parent_path,
                depth=depth,
                sort_order=sort_order,
                is_group=True,
            )
        )
        emitted_doc_paths.add(doc_path)
        sort_order += 1

    def emit_document(doc_path: str, file_info: dict[str, object]) -> None:
        nonlocal sort_order
        if doc_path in emitted_doc_paths:
            return

        parent_path, depth = _node_parent_path_and_depth(doc_path)
        if parent_path in concrete_doc_paths:
            parent_file_info = concrete_file_info_by_doc_path.get(parent_path)
            if parent_file_info is not None:
                emit_document(parent_path, parent_file_info)

        for prefix in _iter_path_prefixes(doc_path):
            if prefix == "_section":
                continue
            emit_group(prefix, _humanize_path_segment(prefix.rsplit("/", 1)[-1]))

        nodes.append(
            DocumentNode(
                doc_path=doc_path,
                title=str(file_info["title"]),
                source_url=str(file_info["source_url"]),
                source_file=str(file_info["source_file"]),
                snapshot_id=str(file_info["snapshot_id"]),
                source_version=str(file_info["source_version"]),
                parent_path=parent_path,
                depth=depth,
                sort_order=sort_order,
                is_group=False,
                total_chars=int(file_info["total_chars"]),
                section_count=int(file_info["section_count"]),
            )
        )
        emitted_doc_paths.add(doc_path)
        sort_order += 1

    file_info_by_source_url = {
        str(file_info["source_url"]): file_info
        for file_info in file_nodes
        if str(file_info["source_url"])
    }
    concrete_file_info_by_doc_path: dict[str, dict[str, object]] = {}
    assigned_source_files: set[str] = set()

    if manifest_sections is not None:
        for section_info in ordered_sections:
            section_title = str(section_info["title"])
            section_path = str(section_info.get("doc_path", ""))
            if section_title:
                emit_group(section_path, section_title)

            section_file_infos: list[tuple[str, dict[str, object]]] = []
            for url in section_info["urls"]:
                file_info = file_info_by_source_url.get(str(url))
                if file_info is None:
                    continue
                assigned_source_files.add(str(file_info["source_file"]))
                doc_path = (
                    f"{section_path}/{file_info['doc_path']}"
                    if section_title
                    else str(file_info["doc_path"])
                )
                section_file_infos.append((doc_path, file_info))
                concrete_file_info_by_doc_path[doc_path] = file_info

            for doc_path, file_info in section_file_infos:
                emit_document(doc_path, file_info)

    root_file_infos: list[tuple[str, dict[str, object]]] = []
    for file_info in file_nodes:
        if str(file_info["source_file"]) in assigned_source_files:
            continue
        doc_path = str(file_info["doc_path"])
        root_file_infos.append((doc_path, file_info))
        concrete_file_info_by_doc_path[doc_path] = file_info

    for doc_path, file_info in root_file_infos:
        emit_document(doc_path, file_info)

    return nodes


async def upsert_documents(
    pool,
    corpus_slug: str,
    nodes: list[DocumentNode],
    *,
    snapshot_id: str = "legacy",
    source_version: str = "latest",
) -> dict[str, int]:
    path_to_id: dict[str, int] = {}
    if not nodes:
        return path_to_id

    insert_sql = """
    INSERT INTO doc_documents (
        corpus_id, snapshot_id, source_version, doc_path, title, source_url,
        source_file, parent_id, depth, sort_order, is_group, total_chars, section_count
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, NULL, $8, $9, $10, $11, $12)
    ON CONFLICT (corpus_id, snapshot_id, doc_path) DO UPDATE SET
        title = EXCLUDED.title,
        source_url = EXCLUDED.source_url,
        source_file = EXCLUDED.source_file,
        depth = EXCLUDED.depth,
        sort_order = EXCLUDED.sort_order,
        is_group = EXCLUDED.is_group,
        total_chars = EXCLUDED.total_chars,
        section_count = EXCLUDED.section_count,
        parent_id = NULL
    RETURNING id
    """
    update_sql = """
    UPDATE doc_documents
    SET parent_id = $1
    WHERE corpus_id = $2 AND snapshot_id = $3 AND doc_path = $4
    """

    async with pool.acquire() as conn:
        for node in nodes:
            row = await conn.fetchrow(
                insert_sql,
                corpus_slug,
                snapshot_id if snapshot_id != "legacy" else node.snapshot_id,
                source_version if source_version != "latest" else node.source_version,
                node.doc_path,
                node.title,
                node.source_url,
                node.source_file,
                node.depth,
                node.sort_order,
                node.is_group,
                node.total_chars,
                node.section_count,
            )
            path_to_id[node.doc_path] = int(row["id"])

        for node in nodes:
            parent_id = path_to_id.get(node.parent_path) if node.parent_path else None
            node_snapshot_id = snapshot_id if snapshot_id != "legacy" else node.snapshot_id
            await conn.execute(update_sql, parent_id, corpus_slug, node_snapshot_id, node.doc_path)

    return path_to_id


async def link_chunks_to_documents(
    pool,
    corpus_slug: str,
    path_to_id: dict[str, int],
    *,
    snapshot_id: str = "legacy",
) -> int:
    del path_to_id
    docs_sql = """
    SELECT source_file, id
    FROM doc_documents
    WHERE corpus_id = $1 AND snapshot_id = $2 AND source_file <> ''
    """
    chunks_sql = """
    SELECT DISTINCT source_file
    FROM doc_chunks
    WHERE corpus_id = $1 AND snapshot_id = $2 AND source_file <> ''
    """
    update_sql = """
    UPDATE doc_chunks
    SET document_id = $1
    WHERE corpus_id = $2 AND snapshot_id = $3 AND source_file = $4
    """

    doc_rows = await pool.fetch(docs_sql, corpus_slug, snapshot_id)
    source_file_to_id = {str(row["source_file"]): int(row["id"]) for row in doc_rows}
    chunk_rows = await pool.fetch(chunks_sql, corpus_slug, snapshot_id)

    updated = 0
    for row in chunk_rows:
        source_file = str(row["source_file"])
        doc_id = source_file_to_id.get(source_file)
        if doc_id is None:
            continue
        status = await pool.execute(update_sql, doc_id, corpus_slug, snapshot_id, source_file)
        updated += _parse_command_count(status)
    return updated


async def delete_stale_documents(
    pool,
    corpus_slug: str,
    current_paths: list[str],
    *,
    snapshot_id: str = "legacy",
) -> int:
    sql = """
    DELETE FROM doc_documents
    WHERE corpus_id = $1
      AND snapshot_id = $2
      AND (cardinality($3::text[]) = 0 OR NOT (doc_path = ANY($3::text[])))
    """
    status = await pool.execute(sql, corpus_slug, snapshot_id, current_paths)
    return _parse_command_count(status)


async def get_document_tree(
    pool,
    corpus_slug: str,
    path: str | None = None,
    max_depth: int | None = None,
    *,
    snapshot_id: str = "legacy",
) -> list[dict]:
    conditions = ["corpus_id = $1", "snapshot_id = $2"]
    args: list[object] = [corpus_slug, snapshot_id]
    next_index = 3
    root_depth = 0

    if path is not None:
        conditions.append(f"(doc_path = ${next_index} OR doc_path LIKE ${next_index} || '/%')")
        args.append(path)
        next_index += 1
        root_depth = path.count("/") if not path.startswith("_section/") else max(len(path.split("/")) - 2, 0)
    if max_depth is not None:
        conditions.append(f"depth <= ${next_index}")
        args.append(root_depth + max_depth)
        next_index += 1

    sql = f"""
    SELECT doc_path, title, source_url, depth, is_group, total_chars, section_count
    FROM doc_documents
    WHERE {' AND '.join(conditions)}
    ORDER BY sort_order
    """
    rows = await pool.fetch(sql, *args)
    if not rows:
        has_documents = await pool.fetchrow(
            "SELECT 1 AS present FROM doc_documents WHERE corpus_id = $1 AND snapshot_id = $2 LIMIT 1",
            corpus_slug,
            snapshot_id,
        )
        if has_documents:
            return []
        return await _synthetic_tree_fallback(pool, corpus_slug, snapshot_id=snapshot_id)

    row_dicts = [dict(row) for row in rows]
    path_set = {str(row["doc_path"]) for row in row_dicts}
    concrete_doc_paths = [str(row["doc_path"]) for row in row_dicts if not bool(row["is_group"])]
    doc_ids_by_path = _build_doc_id_map(corpus_slug, concrete_doc_paths, snapshot_id=snapshot_id)
    child_counts = {doc_path: 0 for doc_path in path_set}
    for doc_path in path_set:
        parent_path, _ = _node_parent_path_and_depth(doc_path)
        if parent_path in child_counts:
            child_counts[parent_path] += 1

    return [
        {
            "doc_path": str(row["doc_path"]),
            "doc_id": None if bool(row["is_group"]) else doc_ids_by_path[str(row["doc_path"])],
            "title": str(row["title"]),
            "source_url": str(row["source_url"]),
            "depth": int(row["depth"]),
            "is_group": bool(row["is_group"]),
            "total_chars": int(row["total_chars"]),
            "section_count": int(row["section_count"]),
            "children_count": child_counts.get(str(row["doc_path"]), 0),
        }
        for row in row_dicts
    ]


async def _synthetic_tree_fallback(pool, corpus_slug: str, *, snapshot_id: str = "legacy") -> list[dict]:
    sql = """
    SELECT
        source_file,
        COALESCE((ARRAY_AGG(source_url ORDER BY start_line) FILTER (WHERE source_url <> ''))[1], '') AS source_url,
        COALESCE(SUM(char_count), 0) AS total_chars,
        COUNT(*) AS section_count
    FROM doc_chunks
    WHERE corpus_id = $1 AND snapshot_id = $2 AND source_file <> ''
    GROUP BY source_file
    ORDER BY source_file
    """
    rows = await pool.fetch(sql, corpus_slug, snapshot_id)
    result = []
    doc_paths = [doc_path_from_source_file(str(row["source_file"])) for row in rows]
    doc_ids_by_path = _build_doc_id_map(corpus_slug, doc_paths, snapshot_id=snapshot_id)
    for row in rows:
        source_file = str(row["source_file"])
        doc_path = doc_path_from_source_file(source_file)
        result.append(
            {
                "doc_path": doc_path,
                "doc_id": doc_ids_by_path[doc_path],
                "title": _humanize_path_segment(doc_path.rsplit("/", 1)[-1]),
                "source_url": str(row["source_url"]),
                "depth": 0,
                "is_group": False,
                "total_chars": int(row["total_chars"]),
                "section_count": int(row["section_count"]),
                "children_count": 0,
            }
        )
    return result


def _source_file_from_doc_path(doc_path: str) -> str:
    if doc_path.startswith("_section/"):
        parts = doc_path.split("/")
        if len(parts) > 2:
            doc_path = "/".join(parts[2:])
        else:
            doc_path = parts[-1]
    return f"{doc_path.replace('/', '__')}.md"


async def get_document_chunks_by_doc_id(
    pool,
    corpus_slug: str,
    doc_id: str,
    *,
    snapshot_id: str = "legacy",
) -> tuple[str | None, list[dict]]:
    """Return (doc_path, chunks) for the document identified by *doc_id*.

    Resolves the short doc_id to a doc_path using a lightweight query against
    doc_documents (no full tree fetch), then loads chunks via the document_id
    integer FK.  Returns (None, []) if the doc_id is not found.
    """
    rows = await pool.fetch(
        """
        SELECT id, doc_path
        FROM doc_documents
        WHERE corpus_id = $1 AND snapshot_id = $2 AND is_group = false
        """,
        corpus_slug,
        snapshot_id,
    )
    if not rows:
        return None, []

    doc_paths = [str(r["doc_path"]) for r in rows]
    id_map = _build_doc_id_map(corpus_slug, doc_paths, snapshot_id=snapshot_id)

    matched_path = next((path for path, did in id_map.items() if did == doc_id), None)
    if matched_path is None:
        return None, []

    matched_row = next(r for r in rows if str(r["doc_path"]) == matched_path)
    document_id = int(matched_row["id"])

    sql = """
    SELECT
        c.id,
        c.heading,
        c.heading_level,
        c.section_path,
        c.char_count,
        c.source_file,
        c.source_url,
        c.content,
        c.start_line,
        c.end_line,
        c.category
    FROM doc_chunks c
    WHERE c.corpus_id = $1 AND c.snapshot_id = $3 AND c.document_id = $2
    ORDER BY c.start_line
    """
    chunk_rows = await pool.fetch(sql, corpus_slug, document_id, snapshot_id)
    return matched_path, [dict(r) for r in chunk_rows]


async def get_document_chunks(
    pool,
    corpus_slug: str,
    doc_path: str,
    *,
    snapshot_id: str = "legacy",
) -> list[dict]:
    doc_sql = """
    SELECT id, source_file
    FROM doc_documents
    WHERE corpus_id = $1 AND snapshot_id = $2 AND doc_path = $3
    """
    doc_row = await pool.fetchrow(doc_sql, corpus_slug, snapshot_id, doc_path)

    if doc_row is not None:
        filter_clause = "c.document_id = $3"
        args: list[object] = [corpus_slug, snapshot_id, int(doc_row["id"])]
    else:
        filter_clause = "c.source_file = $3"
        args = [corpus_slug, snapshot_id, _source_file_from_doc_path(doc_path)]

    sql = f"""
    SELECT
        c.id,
        c.heading,
        c.heading_level,
        c.section_path,
        c.char_count,
        c.source_file,
        c.source_url,
        c.content,
        c.start_line,
        c.end_line,
        c.category
    FROM doc_chunks c
    WHERE c.corpus_id = $1 AND c.snapshot_id = $2 AND {filter_clause}
    ORDER BY c.start_line
    """
    rows = await pool.fetch(sql, *args)
    return [dict(row) for row in rows]


async def get_document_sections(pool, corpus_slug: str, doc_path: str) -> list[dict]:
    chunks = await get_document_chunks(pool, corpus_slug, doc_path)
    return [
        {
            "heading": chunk["heading"],
            "heading_level": chunk["heading_level"],
            "section_path": chunk["section_path"],
            "char_count": chunk["char_count"],
        }
        for chunk in chunks
    ]
