from __future__ import annotations

import argparse
import asyncio
import json

from doc_hub.browse import browse, build_browse_parser, build_read_parser, read
from doc_hub.db import create_pool, ensure_schema, list_corpora
from doc_hub.search import build_search_parser, handle_search_args


def handle_browse(args: argparse.Namespace) -> None:
    asyncio.run(browse(args))


def handle_read(args: argparse.Namespace) -> None:
    asyncio.run(read(args))


def handle_search(args: argparse.Namespace) -> None:
    handle_search_args(args)


async def list_docs(args: argparse.Namespace) -> None:
    pool = await create_pool()
    try:
        await ensure_schema(pool)
        corpora = await list_corpora(pool, enabled_only=False)
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "slug": corpus.slug,
                            "display_name": corpus.name,
                            "enabled": corpus.enabled,
                        }
                        for corpus in corpora
                    ],
                    indent=2,
                )
            )
            return

        if not corpora:
            print("(no corpora registered)")
            return

        for corpus in corpora:
            status = "enabled" if corpus.enabled else "disabled"
            print(f"{corpus.name} [{corpus.slug}] - {status}")
    finally:
        await pool.close()


def handle_list(args: argparse.Namespace) -> None:
    asyncio.run(list_docs(args))


def register_docs_group(subparsers: argparse._SubParsersAction) -> None:
    docs_parser = subparsers.add_parser("docs", help="Browse, read, and search documentation")
    docs_subparsers = docs_parser.add_subparsers(dest="docs_command", required=True)

    browse_parser = docs_subparsers.add_parser("browse", help="Browse the document tree")
    build_browse_parser(browse_parser)
    browse_parser.set_defaults(handler=handle_browse)

    read_parser = docs_subparsers.add_parser("read", help="Read a document")
    build_read_parser(read_parser)
    read_parser.set_defaults(handler=handle_read)

    list_parser = docs_subparsers.add_parser("list", help="List registered corpora")
    list_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    list_parser.set_defaults(handler=handle_list)

    search_parser = docs_subparsers.add_parser("search", help="Search documentation")
    build_search_parser(search_parser)
    search_parser.set_defaults(handler=handle_search)
