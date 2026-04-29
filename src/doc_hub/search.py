#!/usr/bin/env python3
"""Hybrid search over doc-hub doc_chunks table.

Combines vector KNN search (via VectorChord) and PostgreSQL full-text search,
merged using Reciprocal Rank Fusion (RRF) with k=60.

Ported from pydantic_ai_docs/search.py with the following additions:
- Optional ``corpora`` parameter to scope searches to one or more corpora
- ``SearchResult`` includes ``id`` and ``corpus_id`` fields (for cross-corpus search)
- Pool helpers consolidated — use doc_hub.db.create_pool() (no duplication)
- ``make_search_agent()`` intentionally NOT ported (no pydantic-ai dependency)

Usage:
    # Via CLI:
    doc-hub-search "how do I handle retries?" --corpus pydantic-ai
    doc-hub-search "how do I add middleware?" --corpus fastapi
    doc-hub-search "Agent" --corpus pydantic-ai --corpus fastapi --category api --limit 10

    # As a library:
    import asyncio
    from doc_hub.search import search_docs_sync

    results = search_docs_sync("how do I define a tool?", limit=5)
    for r in results:
        print(r.heading, r.similarity)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import asyncpg  # type: ignore[import]
from dotenv import load_dotenv

if TYPE_CHECKING:
    from doc_hub.protocols import Embedder

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SearchConfig dataclass
# ---------------------------------------------------------------------------

VALID_PG_LANGUAGES = frozenset({
    "simple", "arabic", "armenian", "basque", "catalan", "danish", "dutch",
    "english", "finnish", "french", "german", "greek", "hindi", "hungarian",
    "indonesian", "irish", "italian", "lithuanian", "nepali", "norwegian",
    "portuguese", "romanian", "russian", "serbian", "spanish", "swedish",
    "tamil", "turkish", "yiddish",
})


@dataclass
class SearchConfig:
    """Advanced configuration for search_docs(). All values have safe defaults.

    Args:
        vector_limit: KNN candidate pool size (default: 20).
        text_limit: BM25 candidate pool size (default: 10).
        rrfk: Reciprocal Rank Fusion k constant (default: 60).
        language: PostgreSQL text-search language (default: 'english').
            Must be one of the valid PostgreSQL text-search configurations.
            The language is validated against a whitelist to prevent SQL injection.
    """
    vector_limit: int = 20      # KNN candidate pool size
    text_limit: int = 10        # BM25 candidate pool size
    rrfk: int = 60              # Reciprocal Rank Fusion k constant
    language: str = "english"   # PostgreSQL text-search language

    def __post_init__(self) -> None:
        if self.language not in VALID_PG_LANGUAGES:
            raise ValueError(
                f"Invalid language {self.language!r}. "
                f"Must be one of: {sorted(VALID_PG_LANGUAGES)}"
            )
        if self.vector_limit < 1 or self.text_limit < 1 or self.rrfk < 1:
            raise ValueError(
                "vector_limit, text_limit, and rrfk must be positive integers"
            )


# ---------------------------------------------------------------------------
# SearchResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """A single search result returned by search_docs()."""

    id: int
    corpus_id: str
    heading: str
    section_path: str
    content: str        # raw markdown content
    source_url: str
    score: float        # RRF score (for ranking transparency)
    similarity: float   # cosine similarity (for threshold filtering)
    category: str       # 'api' | 'guide' | 'example' | 'eval' | 'other'
    start_line: int     # 1-indexed line number in source file
    end_line: int       # 1-indexed last line number (inclusive)
    source_file: str    # original source file path (e.g. "guide__install.md")
    doc_path: str = ""  # resolved doc_path from doc_documents (for correct doc_id)
    snapshot_id: str = "legacy"
    source_version: str = "latest"


# ---------------------------------------------------------------------------
# Embedding the query (uses RETRIEVAL_QUERY task type)
# ---------------------------------------------------------------------------


async def _embed_query_async(
    query: str,
    embedder: "Embedder | None" = None,
) -> list[float]:
    """Embed a query string using the embedder plugin and L2-normalize it.

    If no embedder is provided, resolves the default embedder from
    the plugin registry. The default is determined by reading the
    first available embedder, or "gemini" if available.

    IMPORTANT: Cross-corpora search only works correctly when all corpora
    use the same embedder. The query is embedded once with this embedder,
    and the resulting vector is compared against all corpora's chunk
    embeddings. If corpora A uses a different embedder than this one,
    similarity scores for corpora A will be meaningless.

    Args:
        query: The query string to embed.
        embedder: An optional Embedder protocol instance. If None, resolved
            from the plugin registry.
    """
    from doc_hub.embed import l2_normalize  # noqa: PLC0415

    if embedder is None:
        from doc_hub.discovery import get_registry  # noqa: PLC0415
        registry = get_registry()
        available = registry.list_embedders()
        if not available:
            raise RuntimeError(
                "No embedder plugins are registered. Install an embedder plugin "
                "(e.g. doc-hub ships with 'gemini' by default)."
            )
        default_name = "gemini" if "gemini" in available else available[0]
        embedder = registry.get_embedder(default_name)

    log.debug("Embedding query via embedder %r: %r", embedder.model_name, query)

    raw_vec = await embedder.embed_query(query)
    return l2_normalize(raw_vec)


# ---------------------------------------------------------------------------
# LIKE escaping helper
# ---------------------------------------------------------------------------


def _escape_like(value: str) -> str:
    """Escape LIKE metacharacters so prefix match is literal."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Hybrid SQL builder
# ---------------------------------------------------------------------------


def _build_hybrid_sql(
    corpora: list[str] | None = None,
    config: SearchConfig | None = None,
) -> str:
    """Build the hybrid search SQL with optional corpora filter.

    Uses the fixed-parameter approach from the existing code:
    all bind parameters are always present, optional filters use NULL to disable.

    Bind parameter numbering:
    - $1 -- query embedding vector (as string)
    - $2 -- query text (for websearch_to_tsquery)
    - $3 -- corpus_id array (list[str] | None, NULL = no corpus filter)
    - $4 -- categories include array (list[str] | None)
    - $5 -- exclude_categories array (list[str] | None)
    - $6 -- source_url_prefix (str | None, pre-escaped)
    - $7 -- section_path_prefix (str | None, pre-escaped)
    - $8 -- snapshot scope keys (list[str] | None, values are corpus_id:snapshot_id)
    - $9 -- limit
    - $10 -- offset

    Security: language is interpolated via f-string but validated against a
    whitelist in SearchConfig.__post_init__() to prevent SQL injection.
    The integer fields (vector_limit, text_limit, rrfk) are safe because
    Python ``int`` values cannot contain SQL metacharacters.

    NULL propagation safety: The IS NULL check MUST come first in each OR clause.
    PostgreSQL short-circuits OR: when $N is NULL, IS NULL returns TRUE immediately
    and the LIKE/ANY branch is never evaluated.
    """
    cfg = config or SearchConfig()
    return f"""
WITH vector_results AS (
    SELECT id, heading, section_path, content, source_url, category, corpus_id,
           start_line, end_line, source_file, document_id, snapshot_id, source_version,
           1 - (embedding <=> $1::vector) AS vec_similarity,
           ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS vec_rank
    FROM doc_chunks
    WHERE ($3::text[] IS NULL OR corpus_id = ANY($3))
      AND ($4::text[] IS NULL OR category = ANY($4))
      AND ($5::text[] IS NULL OR category != ALL($5))
      AND ($6::text IS NULL OR source_url LIKE $6 || '%' ESCAPE '\\')
      AND ($7::text IS NULL OR section_path LIKE $7 || '%' ESCAPE '\\')
      AND ($8::text[] IS NULL OR corpus_id || ':' || snapshot_id = ANY($8))
    ORDER BY embedding <=> $1::vector
    LIMIT {cfg.vector_limit}
),
text_results AS (
    SELECT id, heading, section_path, content, source_url, category, corpus_id,
           start_line, end_line, source_file, document_id, snapshot_id, source_version,
           ts_rank(tsv, query) AS text_score,
           ROW_NUMBER() OVER (ORDER BY ts_rank(tsv, query) DESC) AS text_rank
    FROM doc_chunks, websearch_to_tsquery('{cfg.language}', $2) query
    WHERE tsv @@ query
      AND ($3::text[] IS NULL OR corpus_id = ANY($3))
      AND ($4::text[] IS NULL OR category = ANY($4))
      AND ($5::text[] IS NULL OR category != ALL($5))
      AND ($6::text IS NULL OR source_url LIKE $6 || '%' ESCAPE '\\')
      AND ($7::text IS NULL OR section_path LIKE $7 || '%' ESCAPE '\\')
      AND ($8::text[] IS NULL OR corpus_id || ':' || snapshot_id = ANY($8))
    ORDER BY ts_rank(tsv, query) DESC
    LIMIT {cfg.text_limit}
),
merged AS (
    SELECT COALESCE(v.id, t.id) AS id,
           COALESCE(v.heading, t.heading) AS heading,
           COALESCE(v.section_path, t.section_path) AS section_path,
           COALESCE(v.content, t.content) AS content,
           COALESCE(v.source_url, t.source_url) AS source_url,
           COALESCE(v.category, t.category) AS category,
           COALESCE(v.corpus_id, t.corpus_id) AS corpus_id,
           COALESCE(v.start_line, t.start_line, 0) AS start_line,
           COALESCE(v.end_line, t.end_line, 0) AS end_line,
           COALESCE(v.source_file, t.source_file) AS source_file,
           COALESCE(v.document_id, t.document_id) AS document_id,
           COALESCE(v.snapshot_id, t.snapshot_id) AS snapshot_id,
           COALESCE(v.source_version, t.source_version) AS source_version,
           COALESCE(v.vec_similarity, 0) AS vec_similarity,
           COALESCE(1.0 / ({cfg.rrfk} + v.vec_rank), 0) +
           COALESCE(1.0 / ({cfg.rrfk} + t.text_rank), 0) AS rrf_score
    FROM vector_results v
    FULL OUTER JOIN text_results t ON v.id = t.id
)
SELECT m.*,
       COALESCE(d.doc_path, '') AS doc_path
FROM merged m
LEFT JOIN doc_documents d ON m.document_id = d.id
ORDER BY m.rrf_score DESC
LIMIT $9
OFFSET $10
"""


# ---------------------------------------------------------------------------
# Core search function
# ---------------------------------------------------------------------------


async def search_docs(
    query: str,
    *,
    pool: asyncpg.Pool,
    embedder: "Embedder | None" = None,
    corpora: list[str] | None = None,
    categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
    min_similarity: float = 0.55,
    source_url_prefix: str | None = None,
    section_path_prefix: str | None = None,
    snapshot_ids: dict[str, str] | None = None,
    snapshot_scope_keys: list[str] | None = None,
    config: SearchConfig | None = None,
) -> list[SearchResult]:
    """Hybrid vector + full-text search across doc_chunks.

    Args:
        query: Natural language or keyword query string.
        pool: An asyncpg connection pool (from doc_hub.db.create_pool()).
        embedder: An optional Embedder plugin instance for embedding the query.
            If None, the default embedder is resolved from the plugin registry.
            Pass a shared embedder (e.g. from an MCP server lifespan) to avoid
            re-instantiating on every call.
        corpora: Optional corpus slug list to filter by. None = no corpus filter.
        categories: Optional list of categories to filter ('api', 'guide',
            'example', 'eval', 'other'). None = no filter.
        exclude_categories: Optional list of categories to exclude. None = no filter.
        limit: Maximum number of results to return.
        offset: Number of results to skip (for pagination). Default 0.
        min_similarity: Minimum cosine similarity threshold. Results below this
            are filtered out in Python AFTER SQL execution (not in SQL WHERE).
            Default 0.55. The post-filter approach preserves correct RRF scoring
            for results that appear only in text_results (vec_similarity=0).
        source_url_prefix: Restrict results to source URLs starting with this string.
        section_path_prefix: Restrict results to section paths starting with this string.
        snapshot_ids: Optional mapping of corpus slug to exact snapshot ID to search.
        snapshot_scope_keys: Optional exact corpus:snapshot scope keys for multi-version search.
        config: Optional ``SearchConfig`` for advanced SQL tuning (vector_limit,
            text_limit, rrfk, language). Defaults to SearchConfig() which uses
            the same values as before (20/10/60/english).

    Returns:
        List of SearchResult objects sorted by RRF score descending.
        Returns [] if no results meet the min_similarity threshold.
    """
    # 1. Embed the query using the embedder plugin
    query_vec = await _embed_query_async(query, embedder)
    query_vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"

    # Escape LIKE metacharacters in prefix filters before passing to SQL
    if source_url_prefix is not None:
        source_url_prefix = _escape_like(source_url_prefix)
    if section_path_prefix is not None:
        section_path_prefix = _escape_like(section_path_prefix)

    snapshot_scope = snapshot_scope_keys
    if snapshot_scope is None and snapshot_ids:
        snapshot_scope = [f"{corpus}:{snapshot}" for corpus, snapshot in sorted(snapshot_ids.items())]

    log.debug(
        "Running hybrid search: query=%r, corpora=%r, categories=%r, "
        "exclude_categories=%r, limit=%d, offset=%d, min_similarity=%.2f, "
        "source_url_prefix=%r, section_path_prefix=%r, snapshot_scope=%r",
        query, corpora, categories, exclude_categories, limit, offset,
        min_similarity, source_url_prefix, section_path_prefix, snapshot_scope,
    )

    sql = _build_hybrid_sql(corpora=corpora, config=config)

    # 2. Run the hybrid SQL
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            sql,
            query_vec_str,       # $1 — query vector as string
            query,               # $2 — raw query text for websearch_to_tsquery
            corpora,              # $3 — corpus_id or None (search all)
            categories,          # $4 — category include array or None
            exclude_categories,  # $5 — category exclude array or None
            source_url_prefix,   # $6 — source URL prefix (pre-escaped) or None
            section_path_prefix, # $7 — section path prefix (pre-escaped) or None
            snapshot_scope,      # $8 — corpus:snapshot scope values or None
            limit,               # $9 — result count limit
            offset,              # $10 — offset for pagination
        )

    log.debug("Raw results before similarity filter: %d", len(rows))

    # 3. Build result objects and apply minimum similarity post-filter.
    # CRITICAL: min_similarity is applied in Python AFTER SQL execution,
    # NOT in the SQL WHERE clause. This is intentional — results that appear
    # only in text_results have vec_similarity=0 and would be incorrectly
    # filtered out if the threshold were in the SQL WHERE clause.
    raw_results = [
        SearchResult(
            id=row["id"],
            corpus_id=row["corpus_id"],
            heading=row["heading"],
            section_path=row["section_path"],
            content=row["content"],
            source_url=row["source_url"],
            score=float(row["rrf_score"]),
            similarity=float(row["vec_similarity"]),
            category=row["category"],
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            source_file=row["source_file"],
            doc_path=row["doc_path"],
            snapshot_id=row["snapshot_id"],
            source_version=row["source_version"],
        )
        for row in rows
    ]

    results = [r for r in raw_results if r.similarity >= min_similarity or r.similarity == 0.0]

    if not results:
        log.debug("No results met min_similarity=%.2f threshold", min_similarity)
        return []

    log.debug("Returning %d results after similarity filter", len(results))
    return results


# ---------------------------------------------------------------------------
# Version scope helpers
# ---------------------------------------------------------------------------


def _split_corpus_selector(selector: str) -> tuple[str, str | None]:
    corpus, sep, version = selector.partition("@")
    return corpus, version if sep else None


async def resolve_search_scope(
    pool: asyncpg.Pool,
    corpora: list[str],
    version: str | None = None,
    versions: list[str] | None = None,
    all_versions: bool = False,
) -> dict[str, Any]:
    from doc_hub.db import get_default_snapshot_id, list_doc_versions, resolve_version_selector

    requested: list[dict[str, str]] = []
    snapshot_ids: dict[str, str] = {}
    snapshot_scope_keys: list[str] = []
    available_versions: dict[str, list[str]] = {}
    aliases: dict[str, dict[str, str]] = {}

    parsed = [_split_corpus_selector(corpus) for corpus in corpora]
    explicit_modes = sum(bool(mode) for mode in [version, versions, all_versions])
    if explicit_modes > 1:
        raise ValueError("Specify only one of --version, --versions, or --all-versions")

    for corpus, inline_version in parsed:
        if inline_version is not None and explicit_modes:
            raise ValueError("Specify versions either with corpus@version or version flags, not both")

    for corpus, inline_version in parsed:
        rows = await list_doc_versions(pool, corpus)
        requested_versions: list[str | None]
        if all_versions:
            requested_versions = [str(row["source_version"]) for row in rows]
        elif versions:
            requested_versions = versions
        else:
            requested_versions = [inline_version or version]

        if not requested_versions:
            requested_versions = [None]

        selected_snapshots: list[tuple[str | None, str, str]] = []
        for requested_version in requested_versions:
            if requested_version is None:
                snapshot_id = await get_default_snapshot_id(pool, corpus)
                selected_by = "default"
            else:
                resolved = await resolve_version_selector(pool, corpus, requested_version)
                if resolved is None:
                    known = sorted({str(row["source_version"]) for row in rows} | {str(row["snapshot_id"]) for row in rows})
                    raise ValueError(
                        f"Version {requested_version!r} not found for corpus {corpus!r}. "
                        f"Available versions: {', '.join(known) if known else '(none)'}"
                    )
                snapshot_id = resolved
                selected_by = "explicit"
            selected_snapshots.append((requested_version, snapshot_id, selected_by))

        alias_map: dict[str, str] = {}
        versions: list[str] = []
        for row in rows:
            source_version = str(row["source_version"])
            if source_version not in versions:
                versions.append(source_version)
            try:
                row_aliases = row["aliases"]
            except (KeyError, TypeError):
                row_aliases = None
            if row_aliases:
                for alias in row_aliases:
                    alias_map[str(alias)] = str(row["source_version"])

        available_versions[corpus] = versions
        aliases[corpus] = alias_map
        if selected_snapshots:
            snapshot_ids[corpus] = selected_snapshots[0][1]
        seen_scope_for_corpus: set[str] = set()
        for requested_version, snapshot_id, selected_by in selected_snapshots:
            scope_key = f"{corpus}:{snapshot_id}"
            if scope_key not in seen_scope_for_corpus:
                snapshot_scope_keys.append(scope_key)
                seen_scope_for_corpus.add(scope_key)
            requested.append({
                "corpus": corpus,
                "requested": requested_version or "latest",
                "snapshot_id": snapshot_id,
                "selected_by": selected_by,
            })

    normalized_corpora = [corpus for corpus, _ in parsed]
    return {
        "corpora": normalized_corpora,
        "snapshot_ids": snapshot_ids,
        "snapshot_scope_keys": snapshot_scope_keys,
        "searched_versions": requested,
        "available_versions": available_versions,
        "aliases": aliases,
    }


# ---------------------------------------------------------------------------
# Sync wrapper (for tests and simple scripts)
# ---------------------------------------------------------------------------


async def _search_docs_with_pool(
    query: str,
    *,
    corpora: list[str] | None = None,
    categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
    min_similarity: float = 0.55,
    source_url_prefix: str | None = None,
    section_path_prefix: str | None = None,
    embedder: "Embedder | None" = None,
    version: str | None = None,
    versions: list[str] | None = None,
    all_versions: bool = False,
    config: SearchConfig | None = None,
    return_scope: bool = False,
) -> list[SearchResult] | tuple[list[SearchResult], dict[str, Any] | None]:
    """Create a pool, run search_docs(), close pool, and return results."""
    from doc_hub.db import create_pool  # local import to avoid circular at module level

    pool = await create_pool()
    try:
        snapshot_ids = None
        snapshot_scope_keys = None
        scope = None
        if corpora:
            from doc_hub.corpora import validate_corpora_available

            scope = await resolve_search_scope(pool, corpora, version=version, versions=versions, all_versions=all_versions)
            corpora = scope["corpora"]
            snapshot_ids = scope["snapshot_ids"]
            snapshot_scope_keys = scope["snapshot_scope_keys"]
            await validate_corpora_available(pool, corpora)
        results = await search_docs(
            query,
            pool=pool,
            embedder=embedder,
            corpora=corpora,
            categories=categories,
            exclude_categories=exclude_categories,
            limit=limit,
            offset=offset,
            min_similarity=min_similarity,
            source_url_prefix=source_url_prefix,
            section_path_prefix=section_path_prefix,
            snapshot_ids=snapshot_ids,
            snapshot_scope_keys=snapshot_scope_keys,
            config=config,
        )
        if return_scope:
            return results, scope
        return results
    finally:
        await pool.close()


def search_docs_sync(
    query: str,
    *,
    corpora: list[str] | None = None,
    categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
    min_similarity: float = 0.55,
    source_url_prefix: str | None = None,
    section_path_prefix: str | None = None,
    version: str | None = None,
    versions: list[str] | None = None,
    all_versions: bool = False,
    config: SearchConfig | None = None,
    return_scope: bool = False,
) -> list[SearchResult] | tuple[list[SearchResult], dict[str, Any] | None]:
    """Synchronous wrapper around search_docs for use in non-async contexts.

    Creates a temporary pool, runs the search, and closes the pool.
    For repeated searches, prefer using search_docs() with a shared pool.

    .. warning::
        Uses ``asyncio.run()``, which raises ``RuntimeError`` if called from
        within an already-running event loop. Only call this from the CLI
        entry point (or other non-async contexts). For MCP tool handlers or
        any async context, call ``search_docs()`` directly.
    """
    return asyncio.run(
        _search_docs_with_pool(
            query,
            corpora=corpora,
            categories=categories,
            exclude_categories=exclude_categories,
            limit=limit,
            offset=offset,
            min_similarity=min_similarity,
            source_url_prefix=source_url_prefix,
            section_path_prefix=section_path_prefix,
            version=version,
            versions=versions,
            all_versions=all_versions,
            config=config,
            return_scope=return_scope,
        )
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _truncate_content(content: str, max_content_chars: int | None) -> tuple[str, bool, int]:
    original_length = len(content)
    if max_content_chars is None or max_content_chars < 0 or original_length <= max_content_chars:
        return content, False, original_length
    return content[:max_content_chars], True, original_length


def search_result_to_dict(result: SearchResult, *, max_content_chars: int | None = None) -> dict[str, Any]:
    from doc_hub.documents import derive_doc_id, doc_path_from_source_file

    doc_path = result.doc_path or doc_path_from_source_file(result.source_file)
    doc_id = derive_doc_id(result.corpus_id, doc_path, snapshot_id=result.snapshot_id)
    content, content_truncated, original_content_chars = _truncate_content(result.content, max_content_chars)
    return {
        "id": result.id,
        "chunk_id": result.id,
        "corpus_id": result.corpus_id,
        "doc_id": doc_id,
        "doc_path": doc_path,
        "read_target": {
            "corpus": result.corpus_id,
            "doc_id": doc_id,
            "version": result.source_version,
        },
        "heading": result.heading,
        "section_path": result.section_path,
        "source_url": result.source_url,
        "snapshot_id": result.snapshot_id,
        "source_version": result.source_version,
        "score": result.score,
        "similarity": result.similarity,
        "category": result.category,
        "start_line": result.start_line,
        "end_line": result.end_line,
        "line_range": {"start": result.start_line, "end": result.end_line},
        "content": content,
        "content_truncated": content_truncated,
        "original_content_chars": original_content_chars,
    }


def build_search_diagnostics(results: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    top_result = results[0] if results else {}
    top_similarity = top_result.get("similarity")
    top_score = top_result.get("score")
    return {
        "top_similarity": top_similarity,
        "top_score": top_score,
        "low_confidence": not results or bool(top_similarity is not None and 0 < top_similarity < args.min_similarity + 0.1),
        "has_version_scope": bool(args.version or args.versions or args.all_versions or any("@" in corpus for corpus in args.corpora)),
        "categories_returned": sorted({str(result["category"]) for result in results if result.get("category")}),
        "corpora_returned": sorted({str(result["corpus_id"]) for result in results if result.get("corpus_id")}),
        "content_truncated_count": sum(1 for result in results if result.get("content_truncated")),
        "notes": [],
    }


def suggest_next_action(results: list[dict[str, Any]], diagnostics: dict[str, Any], args: argparse.Namespace) -> str:
    if not results:
        return "no_results"
    if results[0].get("content_truncated"):
        return "read_top_doc"
    top_similarity = diagnostics.get("top_similarity")
    if top_similarity is not None and top_similarity >= max(args.min_similarity, 0.7):
        return "answer_from_results"
    if args.categories == ["api"]:
        return "try_category_guide"
    if args.categories == ["guide"]:
        return "try_category_api"
    if args.min_similarity > 0.4 and len(results) < max(2, min(args.limit, 3)):
        return "lower_min_similarity"
    return "broaden_search"


def build_search_response(args: argparse.Namespace, results: list[SearchResult], scope: dict[str, Any] | None = None) -> dict[str, Any]:
    rendered_results = [search_result_to_dict(result, max_content_chars=args.max_content_chars) for result in results]
    diagnostics = build_search_diagnostics(rendered_results, args)
    next_action = suggest_next_action(rendered_results, diagnostics, args)
    filters = {
        "corpora": scope["corpora"] if scope else args.corpora,
        "categories": args.categories,
        "exclude_categories": args.exclude_categories,
        "version": args.version,
        "versions": [version.strip() for version in args.versions.split(",") if version.strip()] if args.versions else None,
        "all_versions": args.all_versions,
        "source_url_prefix": args.source_url_prefix,
        "section_path_prefix": args.section_path_prefix,
        "min_similarity": args.min_similarity,
        "limit": args.limit,
        "offset": args.offset,
        "max_content_chars": args.max_content_chars,
    }
    if scope:
        filters["version_scope"] = scope.get("searched_versions")
    return {
        "status": "success" if rendered_results else "no_results",
        "query": args.query,
        "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "invocation": {
            "tool": "doc-hub docs search",
            "argv": sys.argv[1:] if sys.argv else None,
            "cwd": None,
        },
        "executed_queries": [{"query": args.query, "filters": filters}],
        "result_count": len(rendered_results),
        "results": rendered_results,
        "diagnostics": diagnostics,
        "suggested_next_action": next_action,
    }


def build_search_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        description="Search doc-hub with hybrid vector + full-text search"
    )
    parser.add_argument("query", help="Search query")
    parser.add_argument(
        "--corpus",
        action="append",
        dest="corpora",
        required=True,
        metavar="SLUG",
        help="Corpus slug to search. Repeat to search multiple corpora.",
    )
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        metavar="CATEGORY",
        default=None,
        help=(
            "Filter by category (repeatable): api, guide, example, eval, other. "
            "Example: --category api --category guide"
        ),
    )
    parser.add_argument(
        "--exclude-category",
        action="append",
        dest="exclude_categories",
        metavar="CATEGORY",
        default=None,
        help="Exclude a category (repeatable): api, guide, example, eval, other.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of results to return (default: 5)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first N results for pagination (default: 0).",
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.55,
        help="Minimum cosine similarity threshold (default: 0.55)",
    )
    parser.add_argument(
        "--source-url-prefix",
        default=None,
        help="Restrict results to source URLs starting with this string.",
    )
    parser.add_argument(
        "--section-path-prefix",
        default=None,
        help="Restrict results to section paths starting with this string.",
    )
    parser.add_argument(
        "--vector-limit",
        type=int,
        default=None,
        help="KNN candidate pool size (default: 20). Advanced tuning.",
    )
    parser.add_argument(
        "--text-limit",
        type=int,
        default=None,
        help="BM25 candidate pool size (default: 10). Advanced tuning.",
    )
    parser.add_argument(
        "--rrfk",
        type=int,
        default=None,
        help="Reciprocal Rank Fusion k constant (default: 60). Advanced tuning.",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="PostgreSQL text-search language (default: english). Advanced tuning.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Version selector to search for every requested corpus.",
    )
    parser.add_argument(
        "--versions",
        default=None,
        help="Comma-separated version selectors to search explicitly.",
    )
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="Search every indexed version for the requested corpora.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--schema",
        choices=["v1", "v2"],
        default="v1",
        help="JSON schema version to emit with --json (default: v1). Use v2 for structured agent responses.",
    )
    parser.add_argument(
        "--json-object",
        action="store_true",
        help="Emit the structured JSON object response; equivalent to --json --schema v2.",
    )
    parser.add_argument(
        "--max-content-chars",
        type=int,
        default=1000,
        help="Maximum characters of content per JSON result; use -1 for full content (default: 1000).",
    )
    return parser


def handle_search_args(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("LOGLEVEL") == "DEBUG" else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    config = None
    if any(x is not None for x in [args.vector_limit, args.text_limit, args.rrfk, args.language]):
        config = SearchConfig(
            vector_limit=args.vector_limit if args.vector_limit is not None else 20,
            text_limit=args.text_limit if args.text_limit is not None else 10,
            rrfk=args.rrfk if args.rrfk is not None else 60,
            language=args.language if args.language is not None else "english",
        )

    version_list = None
    if args.versions:
        version_list = [version.strip() for version in args.versions.split(",") if version.strip()]

    use_structured_json = getattr(args, "json_object", False) or getattr(args, "schema", "v1") == "v2"
    if getattr(args, "json_object", False):
        args.json = True
        args.schema = "v2"

    try:
        search_output = search_docs_sync(
            args.query,
            corpora=args.corpora,
            categories=args.categories,
            exclude_categories=args.exclude_categories,
            limit=args.limit,
            offset=args.offset,
            min_similarity=args.min_similarity,
            source_url_prefix=args.source_url_prefix,
            section_path_prefix=args.section_path_prefix,
            version=args.version,
            versions=version_list,
            all_versions=args.all_versions,
            config=config,
            return_scope=use_structured_json,
        )
    except (ValueError, RuntimeError) as exc:
        if args.json:
            import json
            import sys
            print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        else:
            import sys
            print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if use_structured_json:
        results, scope = search_output
    else:
        results = search_output
        scope = None

    if args.json:
        import json
        if use_structured_json:
            print(json.dumps(build_search_response(args, results, scope), indent=2))
        else:
            print(json.dumps([search_result_to_dict(r, max_content_chars=None) for r in results], indent=2))
        return

    if not results:
        print(f"No results found for: {args.query!r}")
        return

    from doc_hub.documents import derive_doc_id, doc_path_from_source_file

    print(f"\nSearch results for: {args.query!r}")
    print(f"Corpora: {', '.join(args.corpora)}")
    print(f"{'─' * 70}")
    for i, r in enumerate(results, 1):
        doc_id = derive_doc_id(
            r.corpus_id,
            r.doc_path or doc_path_from_source_file(r.source_file),
            snapshot_id=r.snapshot_id,
        )
        print(f"\n[{i}] {r.heading}")
        print(f"    Chunk ID:   {r.id}")
        print(f"    Doc ID:     {doc_id}")
        print(f"    Corpus:     {r.corpus_id}")
        print(f"    Version:    {r.source_version} ({r.snapshot_id})")
        print(f"    Path:       {r.section_path[:60]}")
        print(f"    Category:   {r.category}")
        print(f"    Lines:      {r.start_line}-{r.end_line}")
        print(f"    Similarity: {r.similarity:.3f}  |  RRF Score: {r.score:.5f}")
        print(f"    URL:        {r.source_url}")
        print(f"\n{r.content}")
        print(f"{'─' * 70}")
    print()


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: ``doc-hub-search``."""
    load_dotenv()
    parser = build_search_parser()
    args = parser.parse_args(argv)
    handle_search_args(args)


if __name__ == "__main__":
    main()
