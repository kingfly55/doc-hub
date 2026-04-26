"""Pipeline orchestration for doc-hub.

Coordinates the full docs pipeline:
    fetch → clean → parse → embed → index → tree

Each stage is independently executable via ``--stage``. Running without
``--stage`` executes all stages in order.

CLI flags ported from ``pydantic_ai_docs/pipeline.py``:
    --corpus           Corpus slug (required)
    --stage            Run only this stage: fetch|parse|embed|index|tree
    --clean            Wipe all local data for the corpus first
    --skip-download    Re-use existing raw/ directory (alias: --skip-fetch)
    --full-reindex     Delete stale DB rows after upsert
    --retry-failed     Retry previously failed downloads
    --workers          Download concurrency (default: 20)
    --retries          HTTP retry count per URL (default: 3)

Example usage:
    doc-hub-pipeline --corpus pydantic-ai
    doc-hub-pipeline --corpus pydantic-ai --stage fetch
    doc-hub-pipeline --corpus pydantic-ai --stage tree
    doc-hub-pipeline --corpus pydantic-ai --clean
    doc-hub-pipeline --corpus pydantic-ai --skip-download
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from doc_hub.fetchers import DEFAULT_RETRIES, DEFAULT_WORKERS, fetch
from doc_hub.models import Corpus
from doc_hub.paths import corpus_dir, raw_dir, chunks_dir
from doc_hub.versions import SnapshotManifest, load_snapshot_manifest

if TYPE_CHECKING:
    import asyncpg
    from doc_hub.index import IndexResult

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage: fetch
# ---------------------------------------------------------------------------


def _materialize_snapshot_raw(
    corpus: Corpus,
    source_dir: Path,
    manifest: SnapshotManifest,
) -> None:
    if not manifest.snapshot_id:
        return

    target_dir = raw_dir(corpus, snapshot_id=manifest.snapshot_id)
    if source_dir == target_dir:
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    for filename in manifest.files:
        source_file = source_dir / filename
        if source_file.exists():
            target_file = target_dir / filename
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)

    manifest_file = source_dir / "manifest.json"
    if manifest_file.exists():
        shutil.copy2(manifest_file, target_dir / "manifest.json")


async def run_fetch(
    corpus: Corpus,
    *,
    skip_download: bool = False,
    retry_failed: bool = False,
    workers: int = DEFAULT_WORKERS,
    retries: int = DEFAULT_RETRIES,
    snapshot_id: str | None = None,
) -> str | None:
    """Fetch docs for a corpus into the corpus raw directory.

    Args:
        corpus:         The corpus to fetch.
        skip_download:  If True, skip download and re-use existing raw/.
        retry_failed:   If True, only retry previously failed downloads.
                        (Note: retry_failed is handled inside the fetcher
                        via the manifest — this flag is plumbed through
                        for future use when the fetcher supports it.)
        workers:        Download concurrency (passed via fetch_config override).
        retries:        Per-URL HTTP retry count (passed via fetch_config override).

    Returns:
        None — raw files land in ``raw_dir(corpus)``.
    """
    if skip_download:
        log.info("[%s] Skipping fetch (--skip-download)", corpus.slug)
        source_dir = raw_dir(corpus, snapshot_id=snapshot_id)
        manifest = load_snapshot_manifest(source_dir)
        _materialize_snapshot_raw(corpus, source_dir, manifest)
        return manifest.snapshot_id or snapshot_id

    output = raw_dir(corpus, snapshot_id=snapshot_id)

    # Inject CLI overrides into fetch_config so fetchers can honour them
    # without mutating the original corpus object.
    overridden_config = dict(corpus.fetch_config)
    if "workers" not in overridden_config:
        overridden_config["workers"] = workers
    if "retries" not in overridden_config:
        overridden_config["retries"] = retries

    log.info("[%s] === STEP 1: Fetch ===", corpus.slug)
    fetched_dir = await fetch(corpus.slug, corpus.fetch_strategy, overridden_config, output)
    manifest = load_snapshot_manifest(fetched_dir)
    _materialize_snapshot_raw(corpus, fetched_dir, manifest)
    resolved_snapshot_id = manifest.snapshot_id or snapshot_id
    log.info("[%s] Fetch complete → %s", corpus.slug, fetched_dir)
    return resolved_snapshot_id


# ---------------------------------------------------------------------------
# Stage: clean (LLM-based markdown cleaning)
# ---------------------------------------------------------------------------


async def run_clean(corpus: Corpus, *, snapshot_id: str | None = None) -> None:
    """Clean fetched markdown files via LLM if the corpus has clean=true.

    Checks ``corpus.fetch_config["clean"]``. If falsy, skips silently.
    Requires DOC_HUB_CLEAN_MODEL, DOC_HUB_CLEAN_API_KEY, and
    DOC_HUB_CLEAN_BASE_URL environment variables to be set.

    Args:
        corpus: The corpus whose raw files should be cleaned.
    """
    if not corpus.fetch_config.get("clean"):
        log.debug("[%s] Skipping clean (clean not enabled in fetch_config)", corpus.slug)
        return

    from doc_hub.clean import clean_corpus  # noqa: PLC0415

    output = raw_dir(corpus, snapshot_id=snapshot_id)
    if not output.exists():
        log.warning("[%s] No raw directory found — skipping clean", corpus.slug)
        return

    log.info("[%s] === STEP 1b: Clean (LLM) ===", corpus.slug)
    results = await clean_corpus(output)
    ok = sum(1 for r in results if r.success)
    fail = sum(1 for r in results if not r.success)
    log.info("[%s] Clean complete: %d succeeded, %d failed", corpus.slug, ok, fail)


# ---------------------------------------------------------------------------
# Stage: parse
# ---------------------------------------------------------------------------


async def run_parse(corpus: Corpus, *, snapshot_id: str | None = None) -> list:
    """Parse downloaded markdown files into chunks.

    Reads raw files from raw_dir(corpus), splits by headings (via the
    parser plugin), applies two-pass chunk-size optimization, deduplicates
    by content hash, and writes chunks to chunks_dir(corpus)/chunks.jsonl.

    Args:
        corpus: The corpus to parse.

    Returns:
        List of parsed Chunk objects.
    """
    from doc_hub.parse import parse_docs  # noqa: PLC0415

    log.info("[%s] === STEP 2: Parse (parser=%s) ===", corpus.slug, corpus.parser)
    raw_path = raw_dir(corpus, snapshot_id=snapshot_id)
    base_url = corpus.fetch_config.get("base_url", "")
    chunks = parse_docs(
        corpus.slug, raw_path,
        parser_name=corpus.parser,
        base_url=base_url,
        snapshot_id=snapshot_id,
    )
    log.info("[%s] Parse complete → %d chunks", corpus.slug, len(chunks))
    return chunks


async def run_embed(
    corpus: Corpus,
    chunks: list | None = None,
    *,
    embedder=None,
    snapshot_id: str | None = None,
) -> list:
    """Embed chunks via the corpus's configured embedder plugin.

    Loads chunks from chunks_dir(corpus)/chunks.jsonl if not provided.
    Uses a per-corpus embedding cache to skip already-embedded chunks.
    Writes embedded chunks to chunks_dir(corpus)/embedded_chunks.jsonl.

    Args:
        corpus:  The corpus to embed.
        chunks:  Pre-parsed chunks from run_parse. If None, loads from JSONL.
        embedder: Optional pre-resolved Embedder plugin instance. If None,
                  resolved from the plugin registry using corpus.embedder.

    Returns:
        List of EmbeddedChunk objects with L2-normalized embedding vectors.
    """
    import json  # noqa: PLC0415

    from doc_hub.embed import embed_chunks  # noqa: PLC0415
    from doc_hub.parse import Chunk  # noqa: PLC0415

    log.info("[%s] === STEP 3: Embed (embedder=%s) ===", corpus.slug, corpus.embedder)

    # If chunks not provided, load from JSONL
    if chunks is None:
        chunks_path = chunks_dir(corpus, snapshot_id=snapshot_id) / "chunks.jsonl"
        if not chunks_path.exists():
            raise FileNotFoundError(
                f"chunks.jsonl not found at {chunks_path}. "
                "Run the parse stage first."
            )
        loaded_chunks: list[Chunk] = []
        with chunks_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    loaded_chunks.append(Chunk(**data))
        chunks = loaded_chunks
        log.info("[%s] Loaded %d chunks from %s", corpus.slug, len(chunks), chunks_path)

    if embedder is None:
        from doc_hub.discovery import get_registry  # noqa: PLC0415
        embedder = get_registry().get_embedder(corpus.embedder)

    embedded = await embed_chunks(corpus.slug, chunks, embedder, snapshot_id=snapshot_id)
    log.info("[%s] Embed complete → %d embedded chunks", corpus.slug, len(embedded))
    return embedded


async def run_index(
    corpus: Corpus,
    *,
    full_reindex: bool = False,
    embedded_chunks: list | None = None,
    pool: asyncpg.Pool | None = None,
    embedder=None,
    snapshot_id: str | None = None,
) -> IndexResult:
    """Upsert embedded chunks into PostgreSQL.

    Loads embedded chunks from chunks_dir(corpus)/embedded_chunks.jsonl if not
    provided directly.  Upserts into the shared ``doc_chunks`` table scoped by
    ``corpus_id``, updates ``doc_corpora`` stats, writes ``doc_index_meta`` rows,
    and runs a vector smoke-test to confirm the index is functional.

    Args:
        corpus:          The corpus to index.
        full_reindex:    If True, delete stale DB rows after upsert (rows whose
                         content_hash is no longer in the current chunk set).
        embedded_chunks: Pre-embedded chunks from run_embed.  If None, loads
                         from embedded_chunks.jsonl on disk.
        pool:            Optional pre-existing asyncpg pool. If None, a new pool
                         is created and closed after use.

    Returns:
        :class:`~doc_hub.index.IndexResult` with counts of inserted, updated,
        deleted rows and post-run total.
    """
    import json  # noqa: PLC0415

    from doc_hub.db import create_pool, ensure_schema  # noqa: PLC0415
    from doc_hub.embed import EmbeddedChunk  # noqa: PLC0415
    from doc_hub.index import upsert_chunks, verify_index  # noqa: PLC0415

    log.info("[%s] === STEP 4: Index ===", corpus.slug)

    # ------------------------------------------------------------------ #
    # Load embedded chunks if not provided                               #
    # ------------------------------------------------------------------ #
    if embedded_chunks is None:
        ec_path = chunks_dir(corpus, snapshot_id=snapshot_id) / "embedded_chunks.jsonl"
        if not ec_path.exists():
            raise FileNotFoundError(
                f"embedded_chunks.jsonl not found at {ec_path}. "
                "Run the embed stage first."
            )
        loaded: list[EmbeddedChunk] = []
        with ec_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    loaded.append(EmbeddedChunk(**data))
        embedded_chunks = loaded
        log.info(
            "[%s] Loaded %d embedded chunks from %s",
            corpus.slug,
            len(embedded_chunks),
            ec_path,
        )

    # ------------------------------------------------------------------ #
    # Open DB pool if not provided                                        #
    # ------------------------------------------------------------------ #
    _own_pool = pool is None
    if _own_pool:
        pool = await create_pool()

    try:
        await ensure_schema(pool)

        # Resolve embedder info for _write_meta
        embedder_model = ""
        embedder_dims = 0
        if embedder is not None:
            embedder_model = embedder.model_name
            embedder_dims = embedder.dimensions

        result = await upsert_chunks(
            pool, corpus, embedded_chunks,
            full=full_reindex,
            embedder_model=embedder_model,
            embedder_dims=embedder_dims,
        )

        log.info(
            "[%s] Upsert complete: inserted=%d, updated=%d, deleted=%d, total=%d",
            corpus.slug,
            result.inserted,
            result.updated,
            result.deleted,
            result.total,
        )

        await verify_index(pool, corpus, embedded_chunks)
        return result
    finally:
        if _own_pool:
            await pool.close()


async def run_build_tree(
    corpus: Corpus,
    *,
    pool: asyncpg.Pool | None = None,
    snapshot_id: str | None = None,
) -> dict[str, int]:
    """Build and persist the document hierarchy tree for a corpus."""
    from doc_hub.db import create_pool, ensure_schema  # noqa: PLC0415
    from doc_hub.documents import (  # noqa: PLC0415
        build_document_tree,
        delete_stale_documents,
        link_chunks_to_documents,
        upsert_documents,
    )
    from doc_hub.parse import Chunk  # noqa: PLC0415

    zero_result = {"documents": 0, "linked_chunks": 0, "deleted": 0}
    chunks_path = chunks_dir(corpus, snapshot_id=snapshot_id) / "chunks.jsonl"
    if not chunks_path.exists():
        return zero_result

    chunks: list[Chunk] = []
    with chunks_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(Chunk(**json.loads(line)))

    manifest_sections: list[dict] | None = None
    manifest_path = raw_dir(corpus, snapshot_id=snapshot_id) / "manifest.json"
    if manifest_path.exists():
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_sections = manifest_data.get("sections")

    tree = build_document_tree(chunks, manifest_sections=manifest_sections)
    if not tree:
        return zero_result

    _own_pool = pool is None
    if _own_pool:
        pool = await create_pool()

    try:
        await ensure_schema(pool)
        first_snapshot = getattr(tree[0], "snapshot_id", "legacy") if tree else "legacy"
        first_source_version = getattr(tree[0], "source_version", "latest") if tree else "latest"
        resolved_snapshot_id = snapshot_id or (first_snapshot if isinstance(first_snapshot, str) else "legacy")
        source_version = first_source_version if isinstance(first_source_version, str) else "latest"
        path_to_id = await upsert_documents(
            pool,
            corpus.slug,
            tree,
            snapshot_id=resolved_snapshot_id,
            source_version=source_version,
        )
        linked_chunks = await link_chunks_to_documents(
            pool,
            corpus.slug,
            path_to_id,
            snapshot_id=resolved_snapshot_id,
        )
        deleted = await delete_stale_documents(
            pool,
            corpus.slug,
            [node.doc_path for node in tree],
            snapshot_id=resolved_snapshot_id,
        )
        return {
            "documents": len(path_to_id),
            "linked_chunks": linked_chunks,
            "deleted": deleted,
        }
    finally:
        if _own_pool:
            await pool.close()


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


async def run_pipeline(
    corpus: Corpus,
    *,
    stage: str | None = None,
    clean: bool = False,
    skip_download: bool = False,
    full_reindex: bool = False,
    full: bool = False,
    retry_failed: bool = False,
    workers: int = DEFAULT_WORKERS,
    retries: int = DEFAULT_RETRIES,
    pool: asyncpg.Pool | None = None,
    embedder=None,
) -> IndexResult | None:
    """Run the doc-hub pipeline for a single corpus.

    Args:
        corpus:         The corpus to process.
        stage:          If set, run only this stage (fetch|parse|embed|index|tree).
        clean:          Wipe all local data for the corpus before starting.
        skip_download:  Skip the fetch step (re-use existing raw/).
        full_reindex:   Delete stale DB rows after upsert (long-form flag).
        full:           Alias for full_reindex (used by MCP refresh_corpus_tool).
        retry_failed:   Retry previously failed downloads only.
        workers:        Download concurrency.
        retries:        Per-URL HTTP retry count.
        pool:           Optional pre-existing asyncpg pool (used by MCP server
                        to share the lifespan pool). If None, index stage
                        creates and closes its own pool.
        embedder:       Optional pre-resolved Embedder plugin instance. If None,
                        resolved from registry using corpus.embedder at embed time.

    Returns:
        :class:`~doc_hub.index.IndexResult` when the index stage runs, or
        ``None`` when running only fetch/parse/embed/tree stages.
    """
    pipeline_start = time.time()

    # Merge full / full_reindex aliases
    do_full = full or full_reindex

    # ------------------------------------------------------------------ #
    # --clean: wipe all local data for this corpus first                  #
    # ------------------------------------------------------------------ #
    if clean:
        cdir = corpus_dir(corpus)
        if cdir.exists():
            shutil.rmtree(cdir)
            log.info("[%s] Cleaned %s", corpus.slug, cdir)

    # ------------------------------------------------------------------ #
    # Stage dispatch                                                       #
    # ------------------------------------------------------------------ #
    snapshot_id: str | None = None
    if stage == "fetch" or stage is None:
        fetched_snapshot_id = await run_fetch(
            corpus,
            skip_download=skip_download,
            retry_failed=retry_failed,
            workers=workers,
            retries=retries,
            snapshot_id=snapshot_id,
        )
        snapshot_id = fetched_snapshot_id if isinstance(fetched_snapshot_id, str) else None
        if stage == "fetch":
            _log_elapsed(corpus, pipeline_start)
            return None

    if stage == "clean" or stage is None:
        await run_clean(corpus, snapshot_id=snapshot_id)
        if stage == "clean":
            _log_elapsed(corpus, pipeline_start)
            return None

    parsed_chunks = None
    if stage == "parse" or stage is None:
        parsed_chunks = await run_parse(corpus, snapshot_id=snapshot_id)
        if stage == "parse":
            _log_elapsed(corpus, pipeline_start)
            return None

    # Resolve embedder once for both embed and index stages (consistent metadata)
    if (stage in ("embed", "index") or stage is None) and embedder is None:
        from doc_hub.discovery import get_registry  # noqa: PLC0415
        embedder = get_registry().get_embedder(corpus.embedder)

    embedded_chunks = None
    if stage == "embed" or stage is None:
        # Thread parsed_chunks from parse stage to avoid re-reading JSONL
        embedded_chunks = await run_embed(
            corpus,
            chunks=parsed_chunks,
            embedder=embedder,
            snapshot_id=snapshot_id,
        )
        if stage == "embed":
            _log_elapsed(corpus, pipeline_start)
            return None

    result = None
    if stage == "index" or stage is None:
        # Thread embedded_chunks from embed stage to avoid re-reading JSONL
        result = await run_index(
            corpus,
            full_reindex=do_full,
            embedded_chunks=embedded_chunks,
            pool=pool,
            embedder=embedder,
            snapshot_id=snapshot_id,
        )
        if stage == "index":
            _log_elapsed(corpus, pipeline_start)
            return result

    if stage == "tree":
        await run_build_tree(corpus, pool=pool, snapshot_id=snapshot_id)
        _log_elapsed(corpus, pipeline_start)
        return None

    if stage is None:
        await run_build_tree(corpus, pool=pool, snapshot_id=snapshot_id)

    if stage is not None and stage not in ("fetch", "clean", "parse", "embed", "index", "tree"):
        raise ValueError(
            f"Unknown stage: {stage!r}. Valid stages: fetch, clean, parse, embed, index, tree"
        )

    _log_elapsed(corpus, pipeline_start)
    return result


def _log_elapsed(corpus: Corpus, start: float) -> None:
    elapsed = time.time() - start
    log.info("[%s] Pipeline done in %.1fs", corpus.slug, elapsed)


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------


def _build_arg_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = parser or argparse.ArgumentParser(
        description="doc-hub pipeline: fetch → parse → embed → index → tree",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  doc-hub-pipeline --corpus pydantic-ai
  doc-hub-pipeline --corpus pydantic-ai --stage fetch
  doc-hub-pipeline --corpus pydantic-ai --stage tree
  doc-hub-pipeline --corpus pydantic-ai --clean
  doc-hub-pipeline --corpus pydantic-ai --skip-download --stage embed
""",
    )

    parser.add_argument(
        "--corpus",
        required=True,
        metavar="SLUG",
        help="Corpus slug (must exist in the doc_corpora table)",
    )
    parser.add_argument(
        "--stage",
        choices=["fetch", "clean", "parse", "embed", "index", "tree"],
        default=None,
        help="Run only this stage (default: run all stages)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Wipe all local data for this corpus before starting",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download/fetch step (re-use existing raw/ directory)",
    )
    parser.add_argument(
        "--full-reindex",
        action="store_true",
        help="Delete stale DB rows (chunks no longer in corpus) after upsert",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry only previously failed downloads",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Download concurrency (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"HTTP retry count per URL (default: {DEFAULT_RETRIES})",
    )

    return parser


def handle_pipeline_run_args(args: argparse.Namespace) -> None:
    async def _run() -> None:
        from doc_hub.db import create_pool, ensure_schema, get_corpus  # noqa: PLC0415

        pool = await create_pool()
        try:
            await ensure_schema(pool)
            corpus = await get_corpus(pool, args.corpus)
            if corpus is None:
                log.error(
                    "Corpus %r not found in doc_corpora. "
                    "Register it first (e.g. via the MCP add_corpus tool).",
                    args.corpus,
                )
                raise SystemExit(1)

            await run_pipeline(
                corpus,
                stage=args.stage,
                clean=args.clean,
                skip_download=args.skip_download,
                full_reindex=args.full_reindex,
                retry_failed=args.retry_failed,
                workers=args.workers,
                retries=args.retries,
            )
        finally:
            await pool.close()

    asyncio.run(_run())


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: ``doc-hub-pipeline``."""
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    handle_pipeline_run_args(args)


async def sync_all(
    pool,
    embedder=None,
    *,
    full: bool = False,
) -> dict:
    """Run the pipeline for all enabled corpora.

    Iterates over all corpora with ``enabled = true`` in ``doc_corpora`` and
    runs the full pipeline for each in sequence.  One failing corpus does NOT
    prevent the remaining corpora from syncing — errors are caught per-corpus,
    logged with full traceback, and stored in the returned results dict.

    Args:
        pool:     asyncpg connection pool (already open).
        embedder: Optional pre-resolved Embedder plugin instance. If None,
                  each corpus resolves its own embedder from the registry.
        full:     When True, delete stale DB rows after upsert
                  (passed to ``run_pipeline`` as ``full_reindex``).

    Returns:
        Dict mapping corpus slug → :class:`~doc_hub.index.IndexResult` on
        success, or the caught :class:`Exception` on failure.

    Example::

        results = await sync_all(pool)
        for slug, result in results.items():
            if isinstance(result, Exception):
                print(f"{slug}: FAILED — {result}")
            else:
                print(f"{slug}: {result.inserted} new, {result.updated} updated")
    """
    from doc_hub.db import list_corpora  # noqa: PLC0415

    corpora = await list_corpora(pool, enabled_only=True)
    log.info("sync_all: found %d enabled corpus/corpora", len(corpora))

    results: dict = {}
    for corpus in corpora:
        log.info("sync_all: syncing %s ...", corpus.name)
        try:
            result = await run_pipeline(
                corpus,
                pool=pool,
                embedder=embedder,
                full=full,
            )
            results[corpus.slug] = result
            if result is not None:
                log.info(
                    "sync_all:   %s: %d new, %d updated, %d removed (total: %d)",
                    corpus.name,
                    result.inserted,
                    result.updated,
                    result.deleted,
                    result.total,
                )
            else:
                log.info("sync_all:   %s: pipeline returned no index result", corpus.name)
        except Exception as exc:
            log.exception(
                "sync_all:   %s: FAILED — %s (continuing to next corpus)",
                corpus.name,
                exc,
            )
            results[corpus.slug] = exc

    return results


async def sync_all_main_async() -> None:
    """Async implementation for ``doc-hub-sync-all``.

    Creates its own DB pool, calls :func:`sync_all`, then closes the pool.
    Prints a human-readable summary at the end.
    """
    from doc_hub.db import create_pool, ensure_schema  # noqa: PLC0415

    pool = await create_pool()
    try:
        await ensure_schema(pool)
        results = await sync_all(pool)

        # Print summary
        print("\nDoc-Hub Sync Summary:")
        for slug, result in results.items():
            if isinstance(result, Exception):
                print(f"  {slug}: FAILED — {result}")
            elif result is not None:
                print(
                    f"  {slug}: {result.inserted} new, {result.updated} updated, "
                    f"{result.deleted} removed (total: {result.total})"
                )
            else:
                print(f"  {slug}: completed (no index result)")
    finally:
        await pool.close()


def sync_all_main() -> None:
    """CLI entry point: ``doc-hub-sync-all``.

    Runs the full pipeline for every enabled corpus in the DB.
    """
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(sync_all_main_async())
