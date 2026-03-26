from __future__ import annotations

import argparse

from doc_hub.eval import build_eval_parser, handle_eval_args
from doc_hub.pipeline import _build_arg_parser, handle_pipeline_run_args, sync_all_main_async


def handle_run(args: argparse.Namespace) -> None:
    handle_pipeline_run_args(args)


def handle_sync_all(args: argparse.Namespace) -> None:
    import asyncio
    asyncio.run(sync_all_main_async())


def handle_eval(args: argparse.Namespace) -> None:
    handle_eval_args(args)


def register_pipeline_group(subparsers: argparse._SubParsersAction) -> None:
    pipeline_parser = subparsers.add_parser("pipeline", help="Run, sync, and evaluate corpora")
    pipeline_subparsers = pipeline_parser.add_subparsers(dest="pipeline_command", required=True)

    run_parser = pipeline_subparsers.add_parser("run", help="Run the indexing pipeline")
    _build_arg_parser(run_parser)
    run_parser.set_defaults(handler=handle_run)

    sync_parser = pipeline_subparsers.add_parser("sync-all", help="Run the pipeline for all enabled corpora")
    sync_parser.set_defaults(handler=handle_sync_all)

    eval_parser = pipeline_subparsers.add_parser("eval", help="Evaluate retrieval quality")
    build_eval_parser(eval_parser)
    eval_parser.set_defaults(handler=handle_eval)
