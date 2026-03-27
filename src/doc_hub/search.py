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
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    - $8 -- limit
    - $9 -- offset

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
           start_line, end_line,
           1 - (embedding <=> $1::vector) AS vec_similarity,
           ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS vec_rank
    FROM doc_chunks
    WHERE ($3::text[] IS NULL OR corpus_id = ANY($3))
      AND ($4::text[] IS NULL OR category = ANY($4))
      AND ($5::text[] IS NULL OR category != ALL($5))
      AND ($6::text IS NULL OR source_url LIKE $6 || '%' ESCAPE '\\')
      AND ($7::text IS NULL OR section_path LIKE $7 || '%' ESCAPE '\\')
    ORDER BY embedding <=> $1::vector
    LIMIT {cfg.vector_limit}
),
text_results AS (
    SELECT id, heading, section_path, content, source_url, category, corpus_id,
           start_line, end_line,
           ts_rank(tsv, query) AS text_score,
           ROW_NUMBER() OVER (ORDER BY ts_rank(tsv, query) DESC) AS text_rank
    FROM doc_chunks, websearch_to_tsquery('{cfg.language}', $2) query
    WHERE tsv @@ query
      AND ($3::text[] IS NULL OR corpus_id = ANY($3))
      AND ($4::text[] IS NULL OR category = ANY($4))
      AND ($5::text[] IS NULL OR category != ALL($5))
      AND ($6::text IS NULL OR source_url LIKE $6 || '%' ESCAPE '\\')
      AND ($7::text IS NULL OR section_path LIKE $7 || '%' ESCAPE '\\')
    ORDER BY ts_rank(tsv, query) DESC
    LIMIT {cfg.text_limit}
)
SELECT COALESCE(v.id, t.id) AS id,
       COALESCE(v.heading, t.heading) AS heading,
       COALESCE(v.section_path, t.section_path) AS section_path,
       COALESCE(v.content, t.content) AS content,
       COALESCE(v.source_url, t.source_url) AS source_url,
       COALESCE(v.category, t.category) AS category,
       COALESCE(v.corpus_id, t.corpus_id) AS corpus_id,
       COALESCE(v.start_line, t.start_line, 0) AS start_line,
       COALESCE(v.end_line, t.end_line, 0) AS end_line,
       COALESCE(v.vec_similarity, 0) AS vec_similarity,
       COALESCE(1.0 / ({cfg.rrfk} + v.vec_rank), 0) +
       COALESCE(1.0 / ({cfg.rrfk} + t.text_rank), 0) AS rrf_score
FROM vector_results v
FULL OUTER JOIN text_results t ON v.id = t.id
ORDER BY rrf_score DESC
LIMIT $8
OFFSET $9
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

    log.debug(
        "Running hybrid search: query=%r, corpora=%r, categories=%r, "
        "exclude_categories=%r, limit=%d, offset=%d, min_similarity=%.2f, "
        "source_url_prefix=%r, section_path_prefix=%r",
        query, corpora, categories, exclude_categories, limit, offset,
        min_similarity, source_url_prefix, section_path_prefix,
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
            limit,               # $8 — result count limit
            offset,              # $9 — offset for pagination
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
    config: SearchConfig | None = None,
) -> list[SearchResult]:
    """Create a pool, run search_docs(), close pool, and return results."""
    from doc_hub.db import create_pool  # local import to avoid circular at module level

    pool = await create_pool()
    try:
        return await search_docs(
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
            config=config,
        )
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
    config: SearchConfig | None = None,
) -> list[SearchResult]:
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
            config=config,
        )
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


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
        "--json",
        action="store_true",
        help="Output results as JSON",
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

    results = search_docs_sync(
        args.query,
        corpora=args.corpora,
        categories=args.categories,
        exclude_categories=args.exclude_categories,
        limit=args.limit,
        offset=args.offset,
        min_similarity=args.min_similarity,
        source_url_prefix=args.source_url_prefix,
        section_path_prefix=args.section_path_prefix,
        config=config,
    )

    if args.json:
        import json
        print(
            json.dumps(
                [
                    {
                        "id": r.id,
                        "corpus_id": r.corpus_id,
                        "heading": r.heading,
                        "section_path": r.section_path,
                        "source_url": r.source_url,
                        "score": r.score,
                        "similarity": r.similarity,
                        "category": r.category,
                        "start_line": r.start_line,
                        "end_line": r.end_line,
                        "content_preview": r.content[:200],
                    }
                    for r in results
                ],
                indent=2,
            )
        )
        return

    if not results:
        print(f"No results found for: {args.query!r}")
        return

    print(f"\nSearch results for: {args.query!r}")
    print(f"Corpora: {', '.join(args.corpora)}")
    print(f"{'─' * 70}")
    for i, r in enumerate(results, 1):
        print(f"\n[{i}] {r.heading}")
        print(f"    Corpus:     {r.corpus_id}")
        print(f"    Path:       {r.section_path[:60]}")
        print(f"    Category:   {r.category}")
        print(f"    Lines:      {r.start_line}-{r.end_line}")
        print(f"    Similarity: {r.similarity:.3f}  |  RRF Score: {r.score:.5f}")
        print(f"    URL:        {r.source_url}")
        preview = r.content[:200].replace("\n", " ")
        print(f"    Preview:    {preview}...")
    print()


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: ``doc-hub-search``."""
    load_dotenv()
    parser = build_search_parser()
    args = parser.parse_args(argv)
    handle_search_args(args)


if __name__ == "__main__":
    main()
