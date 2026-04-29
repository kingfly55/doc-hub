"""CLI entry points for browsing and reading indexed documentation.

``doc-hub-browse`` lists a corpus document tree. ``doc-hub-read`` reads a
single document, returning its full content.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

from doc_hub.corpora import validate_corpus_available
from doc_hub.db import create_pool, ensure_schema, get_default_snapshot_id, resolve_version_selector
from doc_hub.documents import get_document_chunks_by_doc_id, get_document_tree

log = logging.getLogger(__name__)

DEFAULT_BROWSE_OUTPUT_TOKENS = 1500


def get_browse_output_token_limit() -> int:
    raw = os.getenv("DOC_HUB_BROWSE_MAX_TOKENS", str(DEFAULT_BROWSE_OUTPUT_TOKENS))
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"DOC_HUB_BROWSE_MAX_TOKENS must be an integer, got {raw!r}") from None


def _estimate_text_tokens(text: str) -> int:
    return len(text) // 4

def _split_corpus_selector(selector: str) -> tuple[str, str | None]:
    corpus, sep, version = selector.partition("@")
    return corpus, version if sep else None


async def _resolve_snapshot_id(pool, corpus: str, version: str | None) -> str:
    if version is None:
        return await get_default_snapshot_id(pool, corpus)
    snapshot_id = await resolve_version_selector(pool, corpus, version)
    if snapshot_id is None:
        raise ValueError(f"Version {version!r} not found for corpus {corpus!r}")
    return snapshot_id


def _parent_doc_path(doc_path: str) -> str | None:
    if doc_path.startswith("_section/"):
        parts = doc_path.split("/")
        if len(parts) == 2:
            return None
        return doc_path.rsplit("/", 1)[0]
    return doc_path.rsplit("/", 1)[0] if "/" in doc_path else None


def _render_tree(
    nodes: list[dict],
    *,
    base_depth: int | None = None,
    include_group_paths: bool = False,
) -> str:
    if not nodes:
        return "(no documents)"

    if base_depth is None:
        base_depth = min(int(node.get("depth", 0)) for node in nodes)

    lines = []
    for node in nodes:
        if node.get("overview_mode"):
            title = str(node.get("title", ""))
            path = str(node.get("browse_path") or node.get("doc_path") or "")
            doc_count = int(node.get("descendant_document_count", 0))
            child_count = int(node.get("children_count", 0))
            doc_label = "doc" if doc_count == 1 else "docs"
            child_label = "child" if child_count == 1 else "children"
            lines.append(f"{title} <{path}> — {doc_count} {doc_label}, {child_count} {child_label}")
            samples = [str(sample) for sample in node.get("representative_titles", []) if str(sample)]
            if samples:
                lines.append(f"  areas: {', '.join(samples)}")
            continue

        depth = max(0, int(node.get("depth", 0)) - base_depth)
        indent = " " * (depth * 4)
        title = str(node.get("title", ""))
        if node.get("is_group"):
            path_suffix = ""
            if include_group_paths and node.get("browse_path"):
                path_suffix = f" <{node['browse_path']}>"
            summary_suffix = ""
            if node.get("summary_mode"):
                child_count = int(node.get("children_count", 0))
                doc_count = int(node.get("descendant_document_count", 0))
                child_label = "child" if child_count == 1 else "children"
                doc_label = "doc" if doc_count == 1 else "docs"
                summary_suffix = f" — {child_count} {child_label}, {doc_count} {doc_label}"
            lines.append(f"{indent}{title} [group]{path_suffix}{summary_suffix}")
            continue

        section_count = int(node.get("section_count", 0))
        section_label = "section" if section_count == 1 else "sections"
        total_chars = int(node.get("total_chars", 0))
        doc_id = node.get("doc_id")
        id_suffix = f" [{doc_id}]" if doc_id else ""
        lines.append(f"{indent}{title}{id_suffix} {total_chars:,} chars  {section_count} {section_label}")
    return "\n".join(lines)


def _build_browse_view(
    corpus: str,
    snapshot_id: str,
    nodes: list[dict],
    *,
    path: str | None,
    max_output_tokens: int = DEFAULT_BROWSE_OUTPUT_TOKENS,
    full: bool = False,
) -> dict:
    base_depth = min((int(node.get("depth", 0)) for node in nodes), default=0)
    full_body = _render_tree(nodes, base_depth=base_depth)
    full_text = f"{corpus}@{snapshot_id}\n{full_body}"
    if full or max_output_tokens < 0 or _estimate_text_tokens(full_text) <= max_output_tokens:
        return {
            "mode": "full",
            "truncated": False,
            "documents": nodes,
            "total_nodes": len(nodes),
            "displayed_nodes": len(nodes),
            "omitted_immediate_entries": 0,
            "auto_expanded_path": None,
            "hint": None,
            "base_depth": base_depth,
            "include_group_paths": False,
        }

    by_parent: dict[str | None, list[dict]] = {}
    for node in nodes:
        doc_path = str(node.get("doc_path", ""))
        parent_path = _parent_doc_path(doc_path) if doc_path else None
        by_parent.setdefault(parent_path, []).append(node)

    def subtree_stats(node_path: str) -> tuple[int, int, int]:
        descendants = 0
        group_count = 0
        doc_count = 0
        stack = list(by_parent.get(node_path, []))
        while stack:
            current = stack.pop()
            descendants += 1
            if bool(current.get("is_group")):
                group_count += 1
            else:
                doc_count += 1
            child_path = str(current.get("doc_path", ""))
            if child_path:
                stack.extend(by_parent.get(child_path, []))
        return descendants, group_count, doc_count

    def representative_titles(node_path: str, limit: int = 5) -> list[str]:
        direct_children = by_parent.get(node_path, [])
        titles = [str(child.get("title", "")) for child in direct_children if child.get("is_group")]
        if len(titles) < limit:
            titles.extend(str(child.get("title", "")) for child in direct_children if not child.get("is_group"))
        if len(titles) < limit:
            stack = list(direct_children)
            while stack and len(titles) < limit:
                current = stack.pop(0)
                current_path = str(current.get("doc_path", ""))
                if current_path:
                    grandchildren = by_parent.get(current_path, [])
                    titles.extend(str(child.get("title", "")) for child in grandchildren if child.get("is_group"))
                    stack.extend(grandchildren)
        return list(dict.fromkeys(title for title in titles if title))[:limit]

    def is_wrapper(node: dict) -> bool:
        node_path = str(node.get("doc_path", ""))
        if not node_path or not node.get("is_group"):
            return False
        children = by_parent.get(node_path, [])
        if len(children) == 1 and children[0].get("is_group"):
            return True
        if str(node.get("title", "")).strip().lower() in {"docs", "documentation", "documents"}:
            return True
        return False

    def informative_children(parent_path: str | None) -> tuple[list[dict], list[str]]:
        expanded: list[str] = []
        current = by_parent.get(parent_path, [])
        while len(current) == 1 and is_wrapper(current[0]):
            expanded_path = str(current[0]["doc_path"])
            expanded.append(expanded_path)
            current = by_parent.get(expanded_path, [])

        changed = True
        while changed and current and all(bool(node.get("is_group")) for node in current):
            changed = False
            flattened: list[dict] = []
            for node in current:
                node_path = str(node.get("doc_path", ""))
                children = by_parent.get(node_path, [])
                if is_wrapper(node) and children:
                    expanded.append(node_path)
                    flattened.extend(children)
                    changed = True
                else:
                    flattened.append(node)
            current = flattened

        if len(current) <= 2 and current and all(bool(node.get("is_group")) for node in current):
            next_level: list[dict] = []
            for node in current:
                children = by_parent.get(str(node.get("doc_path", "")), [])
                if children:
                    expanded.append(str(node.get("doc_path", "")))
                    next_level.extend(children)
                else:
                    next_level.append(node)
            if len(next_level) > len(current):
                current = next_level
        return current, list(dict.fromkeys(expanded))

    frontier_nodes, expanded_paths = informative_children(path)
    if not frontier_nodes:
        frontier_nodes = nodes

    overview_nodes: list[dict] = []
    for node in frontier_nodes:
        entry = dict(node)
        entry["browse_path"] = entry.get("doc_path")
        entry["overview_mode"] = True
        if entry.get("is_group") and entry.get("doc_path"):
            descendants, group_count, doc_count = subtree_stats(str(entry["doc_path"]))
            entry["children_count"] = len(by_parent.get(str(entry["doc_path"]), []))
            entry["descendant_count"] = descendants
            entry["descendant_group_count"] = group_count
            entry["descendant_document_count"] = doc_count
            entry["representative_titles"] = representative_titles(str(entry["doc_path"]))
        else:
            entry["children_count"] = 0
            entry["descendant_count"] = 0
            entry["descendant_group_count"] = 0
            entry["descendant_document_count"] = 1
            entry["representative_titles"] = []
        if int(entry.get("descendant_document_count", 0)) > 0:
            overview_nodes.append(entry)

    overview_nodes.sort(
        key=lambda node: (
            -int(node.get("descendant_document_count", 0)),
            str(node.get("title", "")).lower(),
        )
    )
    summary_base_depth = 0

    displayed_nodes: list[dict] = []
    for node in overview_nodes:
        candidate = displayed_nodes + [node]
        candidate_body = _render_tree(candidate, base_depth=summary_base_depth, include_group_paths=True)
        omitted_immediate = len(overview_nodes) - len(candidate)
        footer_bits = [
            f"overview mode: showing {len(candidate)} areas out of {len(overview_nodes)} candidate areas ({len(nodes)} total nodes)",
            "drill in with: doc-hub docs browse --corpus <slug> --path <group-path>",
        ]
        if expanded_paths:
            footer_bits.insert(0, f"expanded wrappers: {', '.join(expanded_paths)}")
        if omitted_immediate > 0:
            footer_bits.append(f"{omitted_immediate} more areas omitted at this level")
        footer = f"({'; '.join(footer_bits)})"
        candidate_text = f"{corpus}@{snapshot_id}\n{candidate_body}\n{footer}"
        if _estimate_text_tokens(candidate_text) <= max_output_tokens or not displayed_nodes:
            displayed_nodes = candidate
        else:
            break

    omitted_immediate_entries = max(0, len(overview_nodes) - len(displayed_nodes))
    hint_parts = []
    if expanded_paths:
        hint_parts.append(f"expanded wrappers: {', '.join(expanded_paths)}")
    hint_parts.append(
        f"overview mode: showing {len(displayed_nodes)} areas out of {len(overview_nodes)} candidate areas ({len(nodes)} total nodes)"
    )
    hint_parts.append("drill in with: doc-hub docs browse --corpus <slug> --path <group-path>")
    hint_parts.append("or search directly with: doc-hub docs search --corpus <slug> \"query\"")
    if omitted_immediate_entries > 0:
        hint_parts.append(f"{omitted_immediate_entries} more areas omitted at this level")

    return {
        "mode": "overview",
        "truncated": True,
        "documents": displayed_nodes,
        "total_nodes": len(nodes),
        "displayed_nodes": len(displayed_nodes),
        "omitted_immediate_entries": omitted_immediate_entries,
        "auto_expanded_path": expanded_paths[-1] if expanded_paths else None,
        "expanded_paths": expanded_paths,
        "hint": "; ".join(hint_parts),
        "base_depth": summary_base_depth,
        "include_group_paths": True,
    }


async def browse(args: argparse.Namespace) -> None:
    pool = await create_pool()
    try:
        await ensure_schema(pool)
        corpus, inline_version = _split_corpus_selector(args.corpus)
        if args.version is not None and inline_version is not None:
            raise ValueError("Specify versions either with corpus@version or --version, not both")
        version = args.version or inline_version
        await validate_corpus_available(pool, corpus)
        snapshot_id = await _resolve_snapshot_id(pool, corpus, version)
        nodes = await get_document_tree(pool, corpus, path=args.path, max_depth=args.depth, snapshot_id=snapshot_id)
        view = _build_browse_view(
            corpus,
            snapshot_id,
            nodes,
            path=args.path,
            max_output_tokens=getattr(args, "max_output_tokens", get_browse_output_token_limit()),
            full=getattr(args, "full", False),
        )
        if args.json:
            print(json.dumps({
                "corpus": corpus,
                "snapshot_id": snapshot_id,
                "path": args.path,
                "mode": view["mode"],
                "truncated": view["truncated"],
                "total_nodes": view["total_nodes"],
                "displayed_nodes": view["displayed_nodes"],
                "omitted_immediate_entries": view["omitted_immediate_entries"],
                "auto_expanded_path": view["auto_expanded_path"],
                "expanded_paths": view.get("expanded_paths", []),
                "hint": view["hint"],
                "documents": view["documents"],
            }, indent=2))
            return

        print(f"{corpus}@{snapshot_id}")
        print(_render_tree(
            view["documents"],
            base_depth=view["base_depth"],
            include_group_paths=view["include_group_paths"],
        ))
        if view["hint"]:
            print(f"({view['hint']})")
    finally:
        await pool.close()


async def read(args: argparse.Namespace) -> None:
    pool = await create_pool()
    try:
        await ensure_schema(pool)
        corpus, inline_version = _split_corpus_selector(args.corpus)
        if args.version is not None and inline_version is not None:
            raise ValueError("Specify versions either with corpus@version or --version, not both")
        version = args.version or inline_version
        await validate_corpus_available(pool, corpus)
        snapshot_id = await _resolve_snapshot_id(pool, corpus, version)
        doc_path, chunks = await get_document_chunks_by_doc_id(pool, corpus, args.doc_id, snapshot_id=snapshot_id)
        if doc_path is None or not chunks:
            message = f"Document '{args.doc_id}' not found in corpus '{corpus}' at snapshot '{snapshot_id}'"
            if args.json:
                print(json.dumps({"error": message}, indent=2), file=sys.stderr)
                raise SystemExit(1)
            print(message)
            return

        title = next(
            (str(chunk.get("heading", "")) for chunk in chunks if chunk.get("heading_level") == 1),
            doc_path,
        )
        source_url = str(chunks[0].get("source_url", ""))
        total_chars = sum(int(chunk.get("char_count", 0)) for chunk in chunks)
        section_count = len(chunks)
        content = "\n\n".join(str(chunk.get("content", "")) for chunk in chunks)
        original_content_chars = len(content)
        max_content_chars = args.max_content_chars
        content_truncated = max_content_chars is not None and max_content_chars >= 0 and len(content) > max_content_chars
        if content_truncated:
            content = content[:max_content_chars]
        line_starts = [int(chunk.get("start_line", 0)) for chunk in chunks if chunk.get("start_line") is not None]
        line_ends = [int(chunk.get("end_line", 0)) for chunk in chunks if chunk.get("end_line") is not None]

        payload = {
            "mode": "full",
            "corpus": corpus,
            "doc_id": args.doc_id,
            "doc_path": doc_path,
            "title": title,
            "content": content,
            "source_url": source_url,
            "snapshot_id": snapshot_id,
            "source_version": version,
            "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "line_range": {"start": min(line_starts), "end": max(line_ends)} if line_starts and line_ends else None,
            "sections": [
                {
                    "heading": chunk.get("heading"),
                    "section_path": chunk.get("section_path"),
                    "line_range": {"start": chunk.get("start_line"), "end": chunk.get("end_line")},
                    "category": chunk.get("category"),
                }
                for chunk in chunks
            ],
            "invocation": {
                "tool": "doc-hub docs read",
                "argv": sys.argv[1:] if sys.argv else None,
                "cwd": None,
            },
            "total_chars": total_chars,
            "section_count": section_count,
            "content_truncated": content_truncated,
            "original_content_chars": original_content_chars,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
            return

        print(title)
        print(payload["content"])
    finally:
        await pool.close()


def build_browse_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        prog="doc-hub-browse",
        description="Browse the indexed document tree for a corpus.",
    )
    parser.add_argument("--corpus", required=True, help="Corpus slug to browse")
    parser.add_argument("--path", help="Optional subtree path to browse")
    parser.add_argument("--depth", type=int, help="Maximum depth below the selected path")
    parser.add_argument("--version", help="Version selector to browse")
    parser.add_argument("--max-output-tokens", type=int, default=get_browse_output_token_limit(), help="Approximate token budget for browse results before summary mode kicks in")
    parser.add_argument("--full", action="store_true", help="Disable summary mode and render the full browse tree")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    return parser


def build_read_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        prog="doc-hub-read",
        description="Read a document from a corpus.",
    )
    parser.add_argument("--corpus", required=True, help="Corpus slug containing the document")
    parser.add_argument("doc_id", help="Document ID shown in doc-hub docs search or browse output")
    parser.add_argument("--version", help="Version selector to read")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument(
        "--max-content-chars",
        type=int,
        default=-1,
        help="Maximum characters of content in JSON output; use -1 for full content (default: -1).",
    )
    return parser


def _build_browse_parser() -> argparse.ArgumentParser:
    return build_browse_parser()


def _build_read_parser() -> argparse.ArgumentParser:
    return build_read_parser()


def browse_main(argv: list[str] | None = None) -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_browse_parser().parse_args(argv)
    asyncio.run(browse(args))


def read_main(argv: list[str] | None = None) -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_read_parser().parse_args(argv)
    asyncio.run(read(args))
