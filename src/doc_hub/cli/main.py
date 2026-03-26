from __future__ import annotations

import argparse

from doc_hub.cli.docs import register_docs_group
from doc_hub.cli.pipeline import register_pipeline_group
from doc_hub.cli.serve import register_serve_group
from doc_hub.cli.shared import bootstrap_cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doc-hub", description="Unified doc-hub CLI")
    subparsers = parser.add_subparsers(dest="command_group", required=True)
    register_docs_group(subparsers)
    register_pipeline_group(subparsers)
    register_serve_group(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    bootstrap_cli()
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler")
    handler(args)
