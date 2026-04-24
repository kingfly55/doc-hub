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
        if getattr(args, "url_suffix", None):
            config["url_suffix"] = args.url_suffix
        if getattr(args, "use_jina", False):
            config["non_md_strategy"] = "jina"
        elif getattr(args, "try_md", False):
            config["non_md_strategy"] = "try_md"
        if getattr(args, "clean", False):
            config["clean"] = True

    if strategy == "git_repo":
        if args.branch:
            config["branch"] = args.branch
        if args.docs_dir:
            config["docs_dir"] = args.docs_dir
        if getattr(args, "extensions", None):
            # Accept comma-separated string or list; normalise to list of ".ext" strings
            raw = args.extensions
            if isinstance(raw, str):
                exts = [e.strip().lstrip(".") for e in raw.split(",") if e.strip()]
                config["extensions"] = [f".{e}" for e in exts]
            else:
                config["extensions"] = list(raw)

    return config


def _detect_strategy(url_or_path: str) -> str | None:
    if not url_or_path.startswith("http"):
        return "local_dir"
    from urllib.parse import urlparse
    parsed = urlparse(url_or_path)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "github.com" in host:
        return "git_repo"
    if path.endswith(".xml") or path.endswith(".xml.gz"):
        return "sitemap"
    if path.endswith("llms.txt") or "llms-full" in path.split("/")[-1]:
        return "llms_txt"
    return None


def _prompt(label: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    value = input(f"{label}{hint}: ").strip()
    return value or default


def _confirm(label: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    resp = input(f"{label} [{hint}]: ").strip().lower()
    return (resp in ("y", "yes")) if resp else default


def handle_add_interactive(args: argparse.Namespace) -> None:
    url_or_path = _prompt("URL or path")
    strategy = _detect_strategy(url_or_path)
    if strategy is None:
        print("Could not detect strategy. Choose one:")
        choices = ["llms_txt", "sitemap", "git_repo", "local_dir", "direct_url"]
        for i, c in enumerate(choices, 1):
            print(f"  {i}) {c}")
        choice = _prompt("Choice", "1")
        try:
            strategy = choices[int(choice) - 1]
        except (ValueError, IndexError):
            strategy = choice
        print(f"Strategy: {strategy}")
    else:
        print(f"\nDetected strategy: {strategy}")

    from urllib.parse import urlparse
    try:
        parsed = urlparse(url_or_path)
        host = parsed.netloc or ""
        path_part = parsed.path.strip("/").split("/")[0] if parsed.path.strip("/") else ""
        default_name = f"{host} {path_part}".strip() if path_part else host
    except Exception:
        default_name = url_or_path.split("/")[-1] or url_or_path

    name = _prompt("Corpus name", default_name)
    slug = _prompt("Slug", slugify(name))

    non_md_strategy = "direct"
    url_suffix = ""
    clean = False
    url_prefix = ""
    branch = "main"
    docs_dir = ""
    extensions = ""
    path = ""

    if strategy == "llms_txt":
        print("URLs in this file may not be markdown.")
        print("How should non-.md URLs be handled?")
        print("  1) Direct download (default)")
        print("  2) Try .md suffix, fall back to Jina on 404")
        print("  3) Always use Jina")
        choice = _prompt("Choice", "1")
        non_md_strategy = {"1": "direct", "2": "try_md", "3": "jina"}.get(choice, "direct")
        if non_md_strategy != "direct":
            clean = _confirm("Run LLM cleaning after download?", default=False)
        url_suffix = _prompt("URL suffix to append (e.g. .md, leave blank for none)", "")
    elif strategy == "sitemap":
        clean = _confirm("Run LLM cleaning after download?", default=False)
        url_prefix = _prompt("Filter to URL prefix (leave blank for all)", "")
    elif strategy == "git_repo":
        branch = _prompt("Branch", "main")
        docs_dir = _prompt("Docs subdirectory (leave blank for root)", "")
        extensions = _prompt("File extensions to fetch, comma-separated (default: .md)", ".md")
    elif strategy == "local_dir":
        path = _prompt("Local directory path")

    no_index = _confirm("Skip indexing for now?", default=False)

    print("\nSummary:")
    print(f"  Name:     {name}")
    print(f"  Slug:     {slug}")
    print(f"  Strategy: {strategy}")
    if strategy in ("llms_txt", "sitemap", "git_repo"):
        print(f"  URL:      {url_or_path}")
    if strategy == "local_dir":
        print(f"  Path:     {path or url_or_path}")
    if not _confirm("Proceed?", default=True):
        print("Aborted.")
        return

    fake_args = argparse.Namespace(
        name=name,
        slug=slug,
        strategy=strategy,
        no_index=no_index,
        interactive=False,
        url=url_or_path if strategy in ("llms_txt", "sitemap", "git_repo") else None,
        path=path or (url_or_path if strategy == "local_dir" else None),
        url_pattern=None,
        url_suffix=url_suffix or None,
        base_url=None,
        workers=None,
        retries=None,
        branch=branch if strategy == "git_repo" else None,
        docs_dir=docs_dir if strategy == "git_repo" else None,
        extensions=extensions if strategy == "git_repo" else None,
        use_jina=(non_md_strategy == "jina"),
        try_md=(non_md_strategy == "try_md"),
        clean=clean,
        url_prefix=url_prefix or None,
    )
    handle_add(fake_args)


def handle_add(args: argparse.Namespace) -> None:
    if getattr(args, "interactive", False):
        handle_add_interactive(args)
        return
    if args.name is None:
        print("Error: 'name' is required (or use --interactive for guided setup)", file=sys.stderr)
        raise SystemExit(1)
    if not args.strategy:
        print("Error: --strategy is required (or use --interactive for guided setup)", file=sys.stderr)
        raise SystemExit(1)
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


def handle_logs(args: argparse.Namespace) -> None:
    async def _logs() -> None:
        from doc_hub.db import create_pool, ensure_schema, get_corpus
        from doc_hub.pipeline import run_pipeline

        pool = await create_pool()
        try:
            await ensure_schema(pool)
            corpus = await get_corpus(pool, args.slug)
            if corpus is None:
                print(f"Error: corpus '{args.slug}' not found", file=sys.stderr)
                raise SystemExit(1)

            print(f"Running pipeline for {corpus.name} [{corpus.slug}]...")
            await run_pipeline(corpus, pool=pool)
        finally:
            await pool.close()

    asyncio.run(_logs())


def handle_clean(args: argparse.Namespace) -> None:
    async def _clean() -> None:
        from doc_hub.clean import clean_corpus, get_clean_config  # noqa: PLC0415
        from doc_hub.db import (  # noqa: PLC0415
            create_pool,
            ensure_schema,
            get_corpus,
            update_corpus_fetch_config,
        )
        from doc_hub.paths import raw_dir  # noqa: PLC0415
        from doc_hub.pipeline import run_pipeline  # noqa: PLC0415

        pool = await create_pool()
        try:
            await ensure_schema(pool)
            corpus = await get_corpus(pool, args.slug)
            if corpus is None:
                print(f"Error: corpus '{args.slug}' not found", file=sys.stderr)
                raise SystemExit(1)

            # Validate env vars early
            get_clean_config()

            output = raw_dir(corpus)
            if not output.exists():
                print(
                    f"Error: no fetched data for corpus '{args.slug}'. "
                    "Run the pipeline fetch stage first.",
                    file=sys.stderr,
                )
                raise SystemExit(1)

            results = await clean_corpus(output)

            ok = sum(1 for r in results if r.success)
            fail = sum(1 for r in results if not r.success)
            print(f"Clean complete: {ok} succeeded, {fail} failed")

            # Make cleaning sticky for future fetches
            if not corpus.fetch_config.get("clean"):
                corpus.fetch_config["clean"] = True
                await update_corpus_fetch_config(pool, corpus.slug, corpus.fetch_config)
                print(f"Set clean=true in fetch_config for '{corpus.slug}' — future fetches will auto-clean")

            # Re-run parse → embed → index → tree so the DB reflects cleaned content
            if ok > 0:
                print(f"Re-indexing '{corpus.slug}' with cleaned content...")
                await run_pipeline(
                    corpus,
                    skip_download=True,
                    pool=pool,
                )
        finally:
            await pool.close()

    asyncio.run(_clean())


def _require_user_auth() -> None:
    """Authenticate the current user via PAM before a destructive operation."""
    import getpass
    import os
    import pamela
    username = os.environ.get("USER") or getpass.getuser()
    password = getpass.getpass(f"Password for {username}: ")
    try:
        pamela.authenticate(username, password)
    except pamela.PAMError:
        print("Authentication failed.", file=sys.stderr)
        raise SystemExit(1)


def handle_remove(args: argparse.Namespace) -> None:
    _require_user_auth()

    async def _remove() -> None:
        import shutil
        from doc_hub.db import create_pool, delete_corpus, ensure_schema, get_corpus
        from doc_hub.paths import corpus_dir

        pool = await create_pool()
        try:
            await ensure_schema(pool)
            corpus = await get_corpus(pool, args.slug)
            if corpus is None:
                print(f"Error: corpus '{args.slug}' not found.", file=sys.stderr)
                raise SystemExit(1)

            deleted = await delete_corpus(pool, args.slug)
            if deleted:
                print(f"Removed corpus '{args.slug}' from database.")

            if not args.keep_data:
                cdir = corpus_dir(corpus)
                if cdir.exists():
                    shutil.rmtree(cdir)
                    print(f"Deleted local data: {cdir}")
                else:
                    print("No local data found.")
        finally:
            await pool.close()

    asyncio.run(_remove())


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
    add_parser.add_argument("name", nargs="?", default=None, help="Human-readable corpus name")
    add_parser.add_argument("--interactive", "-i", action="store_true", help="Guided interactive setup")
    add_parser.add_argument(
        "--strategy",
        default=None,
        choices=["llms_txt", "sitemap", "git_repo", "local_dir"],
        help="Fetcher strategy",
    )
    add_parser.add_argument("--slug", default=None, help="Override auto-derived slug")
    add_parser.add_argument("--no-index", action="store_true", help="Register only, skip pipeline run")
    add_parser.add_argument("--url", default=None, help="URL for llms_txt, sitemap, or git_repo strategies")
    add_parser.add_argument("--path", default=None, help="Local directory path for local_dir strategy")
    add_parser.add_argument("--url-pattern", default=None, help="Regex to filter doc URLs (llms_txt)")
    add_parser.add_argument("--url-suffix", default=None, help="Suffix appended to each extracted URL, e.g. '.md' (llms_txt)")
    add_parser.add_argument("--base-url", default=None, help="Base URL for filename generation (llms_txt)")
    add_parser.add_argument("--workers", type=int, default=None, help="Download concurrency (llms_txt)")
    add_parser.add_argument("--retries", type=int, default=None, help="Per-URL retry count (llms_txt)")
    add_parser.add_argument("--use-jina", action="store_true", help="Route non-.md URLs through Jina Reader (llms_txt)")
    add_parser.add_argument("--try-md", action="store_true", help="Try appending .md first, fall back to Jina on failure (llms_txt)")
    add_parser.add_argument("--clean", action="store_true", help="Run LLM cleaning pass after download (llms_txt, sitemap)")
    add_parser.add_argument("--branch", default=None, help="Git branch (git_repo)")
    add_parser.add_argument("--docs-dir", default=None, help="Docs subdirectory in repo (git_repo)")
    add_parser.add_argument("--extensions", default=None, help="Comma-separated file extensions to fetch, e.g. .mdx or .md,.mdx (git_repo, default: .md)")
    add_parser.set_defaults(handler=handle_add)

    clean_parser = pipeline_subparsers.add_parser(
        "clean", help="Clean fetched markdown via LLM (strips nav, footers, artifacts)",
    )
    clean_parser.add_argument("slug", help="Corpus slug")
    clean_parser.set_defaults(handler=handle_clean)

    logs_parser = pipeline_subparsers.add_parser("logs", help="Run pipeline with visible logs for a corpus")
    logs_parser.add_argument("slug", help="Corpus slug")
    logs_parser.set_defaults(handler=handle_logs)

    remove_parser = pipeline_subparsers.add_parser("remove", help="Remove a corpus and all its data (requires sudo)")
    remove_parser.add_argument("slug", help="Corpus slug to remove")
    remove_parser.add_argument("--keep-data", action="store_true", help="Delete from DB but keep local files on disk")
    remove_parser.set_defaults(handler=handle_remove)
