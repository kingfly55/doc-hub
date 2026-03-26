"""MCP server for doc-hub.

Exposes four tools to LLMs via the Model Context Protocol (MCP):
    - search_docs_tool     — Hybrid vector + full-text search across indexed docs
    - list_corpora_tool    — List all registered documentation corpora
    - add_corpus_tool      — Register a new corpus (or update an existing one)
    - refresh_corpus_tool  — Re-run the full pipeline for a corpus

Transports:

    **stdio** (default) — for Claude Code / Claude Desktop:

        doc-hub-mcp

    **SSE** — for persistent HTTP service (e.g. systemd):

        doc-hub-mcp --transport sse --port 8340

    **streamable-http** — newer MCP HTTP transport:

        doc-hub-mcp --transport streamable-http --port 8340

MCP config examples::

    # stdio (spawn per session)
    {
      "mcpServers": {
        "doc-hub": {
          "command": "uv",
          "args": ["run", "--package", "doc-hub", "doc-hub-mcp"],
          "env": { "GEMINI_API_KEY": "<key>" }
        }
      }
    }

    # SSE (connect to running service)
    {
      "mcpServers": {
        "doc-hub": {
          "type": "sse",
          "url": "http://localhost:8340/sse"
        }
      }
    }

Implementation notes:
- All tool implementations extract their core logic into ``_*_impl()``
  functions for direct testability without the MCP framework.
- Shared resources (DB pool) are managed via FastMCP lifespan.
- Lifespan state is accessed via ``ctx.request_context.lifespan_context``.
- The embedder plugin is resolved per-corpus from the plugin registry at
  pipeline execution time. No shared embedder client in AppState.
"""

from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncpg  # type: ignore[import]
from dotenv import load_dotenv
from mcp.server.fastmcp import Context, FastMCP

from doc_hub import db
from doc_hub.db import create_pool, ensure_schema
from doc_hub.models import Corpus
from doc_hub.pipeline import run_pipeline
from doc_hub.search import search_docs

log = logging.getLogger(__name__)

DEFAULT_PORT = 8340


# ---------------------------------------------------------------------------
# AppState — shared lifespan state
# ---------------------------------------------------------------------------


@dataclass
class AppState:
    """Resources shared across all tool invocations within a server session.

    Created once in the lifespan context manager and injected into every
    tool via ``ctx.request_context.lifespan_context``.
    """

    pool: asyncpg.Pool
    """Active asyncpg connection pool backed by the doc-hub PostgreSQL DB."""


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Create shared resources on startup; close them on shutdown.

    The GEMINI_API_KEY check is deferred to the GeminiEmbedder plugin — it
    fails lazily on first embed call, not at server startup. This allows the
    MCP server to start and serve search/list/add_corpus requests even when
    no API key is configured.
    """
    load_dotenv()
    pool = await create_pool()
    await ensure_schema(pool)
    log.info("doc-hub MCP server started (pool ready, schema verified)")

    try:
        yield AppState(pool=pool)
    finally:
        await pool.close()
        log.info("doc-hub MCP server shutdown (pool closed)")


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

server = FastMCP("doc-hub", lifespan=lifespan, host="127.0.0.1", port=DEFAULT_PORT)


# ---------------------------------------------------------------------------
# Tool: search_docs_tool
# ---------------------------------------------------------------------------


@server.tool()
async def search_docs_tool(
    query: str,
    ctx: Context,
    corpus: str | None = None,
    categories: list[str] | None = None,
    limit: int = 5,
    max_content_chars: int = 800,
) -> list[dict]:
    """Search indexed documentation. Searches all corpora by default,
    or a specific corpus if specified.

    Args:
        query: The search query
        corpus: Optional corpus slug to filter (e.g. "pydantic-ai", "fastapi")
        categories: Optional category filter (e.g. ["api", "guide"])
        limit: Maximum number of results (default 5)
        max_content_chars: Maximum content chars per result (default 800)
    """
    state: AppState = ctx.request_context.lifespan_context
    return await _search_tool_impl(
        query,
        corpus=corpus,
        categories=categories,
        limit=limit,
        max_content_chars=max_content_chars,
        pool=state.pool,
    )


async def _search_tool_impl(
    query: str,
    *,
    corpus: str | None,
    categories: list[str] | None,
    limit: int,
    max_content_chars: int,
    pool: asyncpg.Pool,
) -> list[dict]:
    """Core search logic, callable directly in tests without MCP framework.

    Does NOT catch exceptions — lets them propagate to FastMCP for
    structured error responses.

    The embedder for query embedding is resolved from the plugin registry
    by ``search_docs()`` → ``_embed_query_async()`` automatically.

    Args:
        query:            Search query string.
        corpus:           Optional corpus slug filter (None = all corpora).
        categories:       Optional list of category filters.
        limit:            Maximum number of results.
        max_content_chars: Truncate ``content`` field to this many characters.
        pool:             asyncpg connection pool.

    Returns:
        List of result dicts with keys: heading, section_path, content,
        source_url, corpus_id, score, similarity, category.
    """
    results = await search_docs(
        query,
        pool=pool,
        corpus=corpus,
        categories=categories,
        limit=limit,
    )
    return [
        {
            "heading": r.heading,
            "section_path": r.section_path,
            "content": r.content[:max_content_chars],
            "source_url": r.source_url,
            "corpus_id": r.corpus_id,
            "score": round(r.score, 4),
            "similarity": round(r.similarity, 3),
            "category": r.category,
            "start_line": r.start_line,
            "end_line": r.end_line,
        }
        for r in results
    ]


# ---------------------------------------------------------------------------
# Tool: list_corpora_tool
# ---------------------------------------------------------------------------


@server.tool()
async def list_corpora_tool(ctx: Context) -> list[dict]:
    """List all registered documentation corpora with their status."""
    state: AppState = ctx.request_context.lifespan_context
    return await _list_corpora_impl(pool=state.pool)


async def _list_corpora_impl(*, pool: asyncpg.Pool) -> list[dict]:
    """Core list-corpora logic, callable directly in tests without MCP framework.

    Args:
        pool: asyncpg connection pool.

    Returns:
        List of corpus dicts with keys: slug, name, strategy, enabled,
        total_chunks, last_indexed_at.
    """
    corpora = await db.list_corpora(pool, enabled_only=False)
    return [
        {
            "slug": c.slug,
            "name": c.name,
            "strategy": c.fetch_strategy,
            "enabled": c.enabled,
            "total_chunks": c.total_chunks,
            "last_indexed_at": c.last_indexed_at,
        }
        for c in corpora
    ]


# ---------------------------------------------------------------------------
# Tool: add_corpus_tool
# ---------------------------------------------------------------------------


@server.tool()
async def add_corpus_tool(
    slug: str,
    name: str,
    strategy: str,
    config: dict,
    ctx: Context,
    parser: str = "markdown",
    embedder: str = "gemini",
) -> dict:
    """Register a new documentation corpus and optionally trigger indexing.

    Args:
        slug: Unique identifier (e.g. "fastapi", "langchain")
        name: Human-readable name (e.g. "FastAPI")
        strategy: Fetcher plugin name (e.g. "llms_txt", "local_dir")
        config: Strategy-specific config (e.g. {"url": "...", "url_pattern": "..."})
        parser: Parser plugin name (default "markdown")
        embedder: Embedder plugin name (default "gemini")
    """
    state: AppState = ctx.request_context.lifespan_context
    return await _add_corpus_impl(
        slug=slug,
        name=name,
        strategy=strategy,
        config=config,
        parser=parser,
        embedder=embedder,
        pool=state.pool,
    )


async def _add_corpus_impl(
    *,
    slug: str,
    name: str,
    strategy: str,
    config: dict,
    parser: str = "markdown",
    embedder: str = "gemini",
    pool: asyncpg.Pool,
) -> dict:
    """Core add-corpus logic, callable directly in tests without MCP framework.

    Validates plugin names (soft — warns but does not error if not found)
    and upserts the corpus row.

    Args:
        slug:     Unique corpus identifier.
        name:     Human-readable corpus name.
        strategy: Fetcher plugin name (e.g. "llms_txt", "local_dir").
        config:   Strategy-specific configuration dict (stored as JSONB).
        parser:   Parser plugin name (default "markdown").
        embedder: Embedder plugin name (default "gemini").
        pool:     asyncpg connection pool.

    Returns:
        ``{"status": "registered", "slug": slug}`` on success.
    """
    # Soft validation — warn if plugins aren't found, but don't error.
    # They might be installed later before the pipeline runs.
    from doc_hub.discovery import get_registry  # noqa: PLC0415
    registry = get_registry()

    for plugin_name, kind in [(strategy, "fetcher"), (parser, "parser"), (embedder, "embedder")]:
        available = getattr(registry, f"list_{kind}s")()
        if plugin_name not in available:
            log.warning(
                "Plugin %s %r not currently registered. "
                "Available: %s. Install the plugin before running the pipeline.",
                kind, plugin_name, available,
            )

    corpus = Corpus(
        slug=slug,
        name=name,
        fetch_strategy=strategy,
        fetch_config=config,
        parser=parser,
        embedder=embedder,
    )
    await db.upsert_corpus(pool, corpus)
    log.info(
        "Corpus registered via MCP: slug=%s, strategy=%s, parser=%s, embedder=%s",
        slug, strategy, parser, embedder,
    )
    return {"status": "registered", "slug": slug}


# ---------------------------------------------------------------------------
# Tool: refresh_corpus_tool
# ---------------------------------------------------------------------------


@server.tool()
async def refresh_corpus_tool(
    slug: str,
    ctx: Context,
    full: bool = False,
) -> dict:
    """Re-run the full pipeline for a corpus: fetch -> parse -> embed -> index.

    Args:
        slug: Corpus to refresh
        full: If true, delete stale chunks not in current fetch
    """
    state: AppState = ctx.request_context.lifespan_context
    return await _refresh_corpus_impl(
        slug=slug,
        full=full,
        pool=state.pool,
    )


async def _refresh_corpus_impl(
    *,
    slug: str,
    full: bool,
    pool: asyncpg.Pool,
) -> dict:
    """Core refresh-corpus logic, callable directly in tests without MCP framework.

    Looks up the corpus, validates it is enabled, then runs the full
    fetch → parse → embed → index pipeline.  The embedder plugin is resolved
    from the corpus's ``embedder`` field by the pipeline.

    Args:
        slug: Corpus slug to refresh.
        full: If True, delete stale chunks after index.
        pool: asyncpg connection pool.

    Returns:
        Status dict with keys: status, slug, chunks_indexed, inserted,
        updated, deleted — or ``{"error": "<message>"}`` on failure.
    """
    corpus = await db.get_corpus(pool, slug)
    if not corpus:
        return {"error": f"Corpus '{slug}' not found"}
    if not corpus.enabled:
        return {"error": f"Corpus '{slug}' is disabled"}

    log.info("Refreshing corpus via MCP: slug=%s, full=%s", slug, full)
    result = await run_pipeline(
        corpus,
        pool=pool,
        full=full,
    )

    if result is None:
        # Should not happen since run_pipeline always runs the index stage
        # when no --stage is specified. Guard defensively.
        return {
            "status": "complete",
            "slug": slug,
            "chunks_indexed": 0,
            "inserted": 0,
            "updated": 0,
            "deleted": 0,
        }

    return {
        "status": "complete",
        "slug": slug,
        "chunks_indexed": result.total,
        "inserted": result.inserted,
        "updated": result.updated,
        "deleted": result.deleted,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the MCP server."""
    parser = argparse.ArgumentParser(
        prog="doc-hub-mcp",
        description="doc-hub MCP server — documentation search for LLMs",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for SSE/HTTP transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port for SSE/HTTP transports (default: {DEFAULT_PORT})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Start the MCP server.

    This is the entry point for the ``doc-hub-mcp`` console script defined
    in ``pyproject.toml``.

    Supports three transports:
        - ``stdio`` (default): For Claude Code / Claude Desktop integration.
        - ``sse``: HTTP+SSE server for persistent service (e.g. systemd).
        - ``streamable-http``: Newer MCP HTTP transport.

    Examples::

        doc-hub-mcp                          # stdio (default)
        doc-hub-mcp --transport sse          # SSE on :8340
        doc-hub-mcp --transport sse --port 9000  # SSE on :9000
    """
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if args.transport != "stdio":
        server.settings.host = args.host
        server.settings.port = args.port
        log.info(
            "Starting doc-hub MCP server (%s) on %s:%d",
            args.transport,
            args.host,
            args.port,
        )

    server.run(transport=args.transport)
