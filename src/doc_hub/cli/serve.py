from __future__ import annotations

import argparse

from doc_hub.mcp_server import build_mcp_parser, handle_mcp_args


def handle_mcp(args: argparse.Namespace) -> None:
    handle_mcp_args(args)


def register_serve_group(subparsers: argparse._SubParsersAction) -> None:
    serve_parser = subparsers.add_parser("serve", help="Serve doc-hub integrations")
    serve_subparsers = serve_parser.add_subparsers(dest="serve_command", required=True)

    mcp_parser = serve_subparsers.add_parser("mcp", help="Run the MCP server")
    build_mcp_parser(mcp_parser)
    mcp_parser.set_defaults(handler=handle_mcp)
