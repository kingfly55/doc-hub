"""CLI entry points for browsing and reading indexed documentation.

``doc-hub-browse`` lists a corpus document tree. ``doc-hub-read`` reads a
single document, returning either an outline for large documents or full
content when requested.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from dotenv import load_dotenv

from doc_hub.db import create_pool, ensure_schema
from doc_hub.documents import get_document_chunks, get_document_tree, resolve_doc_path

log = logging.getLogger(__name__)

LARGE_DOC_THRESHOLD = 20_000


def _render_tree(nodes: list[dict]) -> str:
    if not nodes:
        return "(no documents)"

    lines = []
    for node in nodes:
        indent = " " * (int(node.get("depth", 0)) * 4)
        title = str(node.get("title", ""))
        if node.get("is_group"):
            lines.append(f"{indent}{title} [group]")
            continue

        section_count = int(node.get("section_count", 0))
        section_label = "section" if section_count == 1 else "sections"
        total_chars = int(node.get("total_chars", 0))
        doc_id = node.get("doc_id")
        id_suffix = f" [{doc_id}]" if doc_id else ""
        lines.append(f"{indent}{title}{id_suffix} {total_chars:,} chars  {section_count} {section_label}")
    return "\n".join(lines)


def _render_outline(sections: list[dict]) -> str:
    lines = []
    for section in sections:
        level = max(int(section.get("heading_level", 1)), 1)
        indent = " " * ((level - 1) * 2)
        heading = str(section.get("heading") or section.get("section_path") or "(untitled)")
        char_count = int(section.get("char_count", 0))
        lines.append(f"{indent}{heading} {char_count:,} chars")
    return "\n".join(lines)


async def browse(args: argparse.Namespace) -> None:
    pool = await create_pool()
    try:
        await ensure_schema(pool)
        nodes = await get_document_tree(pool, args.corpus, path=args.path, max_depth=args.depth)
        if args.json:
            print(json.dumps(nodes, indent=2))
            return

        print(args.corpus)
        print(_render_tree(nodes))
    finally:
        await pool.close()


async def read(args: argparse.Namespace) -> None:
    pool = await create_pool()
    try:
        await ensure_schema(pool)
        resolved_doc_path = await resolve_doc_path(pool, args.corpus, args.doc_path)
        if resolved_doc_path is None:
            print(f"Document '{args.doc_path}' not found in corpus '{args.corpus}'")
            return

        chunks = await get_document_chunks(pool, args.corpus, resolved_doc_path, section=args.section)
        if not chunks:
            print(f"Document '{args.doc_path}' not found in corpus '{args.corpus}'")
            return

        title = next(
            (str(chunk.get("heading", "")) for chunk in chunks if chunk.get("heading_level") == 1),
            resolved_doc_path,
        )
        source_url = str(chunks[0].get("source_url", ""))
        total_chars = sum(int(chunk.get("char_count", 0)) for chunk in chunks)
        section_count = len(chunks)

        if total_chars > LARGE_DOC_THRESHOLD and not args.force and args.section is None:
            payload = {
                "mode": "outline",
                "doc_path": resolved_doc_path,
                "title": title,
                "source_url": source_url,
                "total_chars": total_chars,
                "section_count": section_count,
                "sections": [
                    {
                        "heading": chunk.get("heading"),
                        "heading_level": chunk.get("heading_level"),
                        "section_path": chunk.get("section_path"),
                        "char_count": chunk.get("char_count"),
                    }
                    for chunk in chunks
                ],
                "hint": (
                    "Document is large. Request a specific section with --section "
                    "or set --force to retrieve the full content."
                ),
            }
            if args.json:
                print(json.dumps(payload, indent=2))
                return

            print(title)
            print(_render_outline(payload["sections"]))
            return

        payload = {
            "mode": "full",
            "doc_path": resolved_doc_path,
            "title": title,
            "content": "\n\n".join(str(chunk.get("content", "")) for chunk in chunks),
            "source_url": source_url,
            "total_chars": total_chars,
            "section_count": section_count,
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
    parser.add_argument("corpus", help="Corpus slug to browse")
    parser.add_argument("--path", help="Optional subtree path to browse")
    parser.add_argument("--depth", type=int, help="Maximum depth below the selected path")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    return parser


def build_read_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        prog="doc-hub-read",
        description="Read a document from a corpus.",
    )
    parser.add_argument("corpus", help="Corpus slug containing the document")
    parser.add_argument("doc_path", help="Document path or short document ID to read")
    parser.add_argument("--section", help="Optional section path to read")
    parser.add_argument("--force", action="store_true", help="Force full content for large documents")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
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
