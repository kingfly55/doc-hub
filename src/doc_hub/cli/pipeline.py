from __future__ import annotations

import argparse
import asyncio
import re
import sys

from doc_hub.eval import build_eval_parser, handle_eval_args
from doc_hub.pipeline import _build_arg_parser, handle_pipeline_run_args, sync_all_main_async


def slugify(name: str) -> str:
    """Convert a human-readable name to a URL-safe slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def build_fetch_config(strategy: str, args: argparse.Namespace) -> dict:
    """Build a fetch_config dict from CLI args, validating required flags per strategy."""
    config: dict = {}

    if strategy in ("llms_txt", "sitemap", "git_repo"):
        if not args.url:
            print(f"Error: --url is required for strategy '{strategy}'", file=sys.stderr)
            raise SystemExit(1)
        config["url"] = args.url

    if strategy == "local_dir":
        if not args.path:
            print("Error: --path is required for strategy 'local_dir'", file=sys.stderr)
            raise SystemExit(1)
        config["path"] = args.path

    if strategy == "llms_txt":
        if args.url_pattern:
            config["url_pattern"] = args.url_pattern
        if args.base_url:
            config["base_url"] = args.base_url
        if args.workers is not None:
            config["workers"] = args.workers
        if args.retries is not None:
            config["retries"] = args.retries

    if strategy == "git_repo":
        if args.branch:
            config["branch"] = args.branch
        if args.docs_dir:
            config["docs_dir"] = args.docs_dir

    return config


def handle_add(args: argparse.Namespace) -> None:
    fetch_config = build_fetch_config(args.strategy, args)
    slug = args.slug or slugify(args.name)

    async def _add() -> None:
        from doc_hub.db import create_pool, ensure_schema, upsert_corpus
        from doc_hub.models import Corpus
        from doc_hub.pipeline import run_pipeline

        corpus = Corpus(
            slug=slug,
            name=args.name,
            fetch_strategy=args.strategy,
            fetch_config=fetch_config,
        )

        pool = await create_pool()
        try:
            await ensure_schema(pool)
            await upsert_corpus(pool, corpus)
            print(f"Registered corpus: {corpus.name} [{corpus.slug}]")

            if not args.no_index:
                await run_pipeline(corpus, pool=pool)
        finally:
            await pool.close()

    asyncio.run(_add())


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

    add_parser = pipeline_subparsers.add_parser("add", help="Register a new corpus and run indexing")
    add_parser.add_argument("name", help="Human-readable corpus name")
    add_parser.add_argument(
        "--strategy",
        required=True,
        choices=["llms_txt", "sitemap", "git_repo", "local_dir"],
        help="Fetcher strategy",
    )
    add_parser.add_argument("--slug", default=None, help="Override auto-derived slug")
    add_parser.add_argument("--no-index", action="store_true", help="Register only, skip pipeline run")
    add_parser.add_argument("--url", default=None, help="URL for llms_txt, sitemap, or git_repo strategies")
    add_parser.add_argument("--path", default=None, help="Local directory path for local_dir strategy")
    add_parser.add_argument("--url-pattern", default=None, help="Regex to filter doc URLs (llms_txt)")
    add_parser.add_argument("--base-url", default=None, help="Base URL for filename generation (llms_txt)")
    add_parser.add_argument("--workers", type=int, default=None, help="Download concurrency (llms_txt)")
    add_parser.add_argument("--retries", type=int, default=None, help="Per-URL retry count (llms_txt)")
    add_parser.add_argument("--branch", default=None, help="Git branch (git_repo)")
    add_parser.add_argument("--docs-dir", default=None, help="Docs subdirectory in repo (git_repo)")
    add_parser.set_defaults(handler=handle_add)
