from __future__ import annotations

import argparse
import asyncio

from doc_hub.browse import browse, build_browse_parser, build_read_parser, read
from doc_hub.search import build_search_parser, handle_search_args


def handle_browse(args: argparse.Namespace) -> None:
    asyncio.run(browse(args))


def handle_read(args: argparse.Namespace) -> None:
    asyncio.run(read(args))


def handle_search(args: argparse.Namespace) -> None:
    handle_search_args(args)


def register_docs_group(subparsers: argparse._SubParsersAction) -> None:
    docs_parser = subparsers.add_parser("docs", help="Browse, read, and search documentation")
    docs_subparsers = docs_parser.add_subparsers(dest="docs_command", required=True)

    browse_parser = docs_subparsers.add_parser("browse", help="Browse the document tree")
    build_browse_parser(browse_parser)
    browse_parser.set_defaults(handler=handle_browse)

    read_parser = docs_subparsers.add_parser("read", help="Read a document")
    build_read_parser(read_parser)
    read_parser.set_defaults(handler=handle_read)

    search_parser = docs_subparsers.add_parser("search", help="Search documentation")
    build_search_parser(search_parser)
    search_parser.set_defaults(handler=handle_search)
