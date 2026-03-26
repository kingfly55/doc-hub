#!/usr/bin/env python3
"""Retrieval quality evaluation for the doc-hub search system.

Runs a suite of hand-curated test queries against the live search index and
computes Precision@5 and Mean Reciprocal Rank (MRR) to measure retrieval quality.

Ported from ``pydantic_ai_docs/eval.py`` with the following adaptations:

- Accepts a ``corpus`` slug and passes it to ``search_docs()`` so results are
  scoped to the correct corpus (the original was single-corpus only).
- Supports ``--all`` to run evals for every corpus that has an eval file.
- Eval files are discovered from ``DOC_HUB_EVAL_DIR`` (env var) or
  ``{data_root}/eval/`` (XDG fallback).
- Imports from ``doc_hub.search`` / ``doc_hub.db``, not ``pydantic_ai_docs``.

Usage:
    # Eval for a specific corpus:
    doc-hub-eval --corpus pydantic-ai

    # Eval for all corpora that have eval files:
    doc-hub-eval --all

    # Default: run all available evals
    doc-hub-eval

    # With verbose output and JSON report:
    doc-hub-eval --corpus pydantic-ai --verbose --output report.json

    # Override thresholds:
    doc-hub-eval --corpus fastapi --min-precision 0.70 --min-mrr 0.50

    # As a library:
    import asyncio
    from doc_hub.eval import evaluate, EvalReport
    from doc_hub.db import create_pool

    async def main():
        pool = await create_pool()
        report = await evaluate(
            Path("eval/pydantic-ai.json"), pool, corpus="pydantic-ai", verbose=True
        )
        print(f"P@5: {report.precision_at_n:.3f}, MRR: {report.mrr:.3f}")

    asyncio.run(main())
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import]
from dotenv import load_dotenv

from doc_hub.paths import data_root
from doc_hub.search import SearchResult, search_docs

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Acceptance thresholds
# ---------------------------------------------------------------------------

DEFAULT_PRECISION_THRESHOLD = 0.80   # P@5 must be >= this to pass
DEFAULT_MRR_THRESHOLD = 0.60          # MRR must be >= this to pass

# ---------------------------------------------------------------------------
# Eval file discovery
# ---------------------------------------------------------------------------


def _eval_dir() -> Path:
    """Return the eval directory.

    Resolution order:
    1. ``DOC_HUB_EVAL_DIR`` env var
    2. ``{data_root}/eval/``
    """
    env_dir = os.environ.get("DOC_HUB_EVAL_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return data_root() / "eval"


def get_eval_file(corpus_slug: str) -> Path | None:
    """Return the eval JSON path for ``corpus_slug``, or None if not found."""
    path = _eval_dir() / f"{corpus_slug}.json"
    return path if path.exists() else None


def list_eval_corpora() -> list[str]:
    """Return a list of corpus slugs that have eval files."""
    eval_dir = _eval_dir()
    if not eval_dir.exists():
        return []
    return [p.stem for p in sorted(eval_dir.glob("*.json"))]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TestQuery:
    """A single test case with an expected result set."""

    id: str
    query: str
    expected_headings: list[str] = field(default_factory=list)
    expected_section_paths: list[str] = field(default_factory=list)
    min_similarity: float = 0.55
    notes: str = ""


@dataclass
class QueryResult:
    """Results for a single test query evaluation."""

    query_id: str
    query: str
    hit: bool                        # True if any top-N result is relevant
    reciprocal_rank: float           # 1/rank_of_first_hit, or 0.0
    top_similarity: float            # similarity of first result (or 0.0)
    below_sim_threshold: bool        # top result similarity < min_similarity
    results: list[SearchResult]      # raw results returned
    first_hit_rank: int | None       # 1-based rank of first hit, or None


@dataclass
class EvalReport:
    """Aggregated evaluation report across all test queries."""

    corpus: str                       # corpus slug this report covers
    total: int                        # total queries run
    hits: int                         # queries where top-N contained a relevant result
    precision_at_n: float             # hits / total
    mrr: float                        # mean reciprocal rank
    n: int                            # N for Precision@N (default 5)
    failed_queries: list[str]         # query IDs that got zero relevant results
    low_similarity_queries: list[str] # queries where top result sim < expected
    query_results: list[QueryResult] = field(default_factory=list)  # per-query detail
    passed: bool = False              # True if both thresholds met
    precision_threshold: float = DEFAULT_PRECISION_THRESHOLD
    mrr_threshold: float = DEFAULT_MRR_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict of this report."""
        return {
            "corpus": self.corpus,
            "total": self.total,
            "hits": self.hits,
            "precision_at_n": round(self.precision_at_n, 4),
            "mrr": round(self.mrr, 4),
            "n": self.n,
            "failed_queries": self.failed_queries,
            "low_similarity_queries": self.low_similarity_queries,
            "passed": self.passed,
            "thresholds": {
                "precision_at_n": self.precision_threshold,
                "mrr": self.mrr_threshold,
            },
        }


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------


def _is_hit_single(result: SearchResult, query: TestQuery) -> bool:
    """Return True if a single SearchResult is relevant for a TestQuery.

    A result is relevant if:
    - Its heading matches any expected_heading (case-insensitive substring), OR
    - Its section_path contains any expected_section_path substring (case-insensitive).
    """
    heading_lower = result.heading.lower()
    section_lower = result.section_path.lower()

    for expected in query.expected_headings:
        if expected.lower() in heading_lower:
            return True

    for expected_path in query.expected_section_paths:
        if expected_path.lower() in section_lower:
            return True

    return False


def _is_hit(results: list[SearchResult], query: TestQuery) -> bool:
    """Return True if any result in the list is relevant for the query."""
    return any(_is_hit_single(r, query) for r in results)


def _reciprocal_rank(results: list[SearchResult], query: TestQuery) -> float:
    """Compute reciprocal rank for a query's result list.

    Returns 1/(rank of first relevant result), or 0.0 if no relevant result.
    Rank is 1-indexed.
    """
    for i, r in enumerate(results):
        if _is_hit_single(r, query):
            return 1.0 / (i + 1)
    return 0.0


def _first_hit_rank(results: list[SearchResult], query: TestQuery) -> int | None:
    """Return 1-based rank of first relevant result, or None if no hit."""
    for i, r in enumerate(results):
        if _is_hit_single(r, query):
            return i + 1
    return None


# ---------------------------------------------------------------------------
# Load test queries
# ---------------------------------------------------------------------------


def load_test_queries(test_path: Path) -> list[TestQuery]:
    """Load and validate test queries from a JSON fixture file."""
    with open(test_path, encoding="utf-8") as f:
        raw = json.load(f)

    queries: list[TestQuery] = []
    for entry in raw:
        if "id" not in entry or "query" not in entry:
            raise ValueError(
                f"Test query entry missing required field 'id' or 'query': {entry!r}"
            )
        if "expected_headings" not in entry and "expected_section_paths" not in entry:
            raise ValueError(
                f"Test query {entry['id']!r} must have at least one of "
                f"'expected_headings' or 'expected_section_paths'"
            )
        queries.append(
            TestQuery(
                id=entry["id"],
                query=entry["query"],
                expected_headings=entry.get("expected_headings", []),
                expected_section_paths=entry.get("expected_section_paths", []),
                min_similarity=entry.get("min_similarity", 0.55),
                notes=entry.get("notes", ""),
            )
        )
    return queries


# ---------------------------------------------------------------------------
# Core evaluation function
# ---------------------------------------------------------------------------


async def evaluate(
    test_path: Path,
    pool: asyncpg.Pool,
    corpus: str,
    limit: int = 5,
    verbose: bool = False,
    precision_threshold: float = DEFAULT_PRECISION_THRESHOLD,
    mrr_threshold: float = DEFAULT_MRR_THRESHOLD,
) -> EvalReport:
    """Run all test queries and return precision@N and MRR.

    Args:
        test_path: Path to the test fixture JSON file.
        pool: An asyncpg connection pool (from doc_hub.db.create_pool()).
        corpus: Corpus slug to scope search results (e.g. "pydantic-ai").
            IMPORTANT: This is required to avoid cross-corpus false positives.
        limit: Number of results to retrieve per query (N in Precision@N).
        verbose: If True, print per-query results to stdout.
        precision_threshold: Minimum P@N required to pass.
        mrr_threshold: Minimum MRR required to pass.

    Returns:
        EvalReport containing aggregated metrics and per-query details.
    """
    queries = load_test_queries(test_path)
    log.info(
        "Loaded %d test queries from %s (corpus=%s)", len(queries), test_path, corpus
    )

    query_results: list[QueryResult] = []
    total_rr = 0.0
    hits_count = 0
    failed_query_ids: list[str] = []
    low_similarity_ids: list[str] = []

    if verbose:
        print(f"\nRunning {len(queries)} evaluation queries for corpus={corpus!r}...\n")

    for i, query in enumerate(queries, 1):
        log.debug("Evaluating query %s: %r", query.id, query.query)

        try:
            results = await search_docs(
                query.query,
                limit=limit,
                min_similarity=0.0,   # don't filter by sim during eval — we score all
                corpus=corpus,        # scope to the specific corpus
                pool=pool,
            )
        except Exception as exc:
            log.error("Search failed for query %s: %s", query.id, exc)
            # Treat as zero results
            results = []

        hit = _is_hit(results[:limit], query)
        rr = _reciprocal_rank(results[:limit], query)
        first_rank = _first_hit_rank(results[:limit], query)
        top_sim = results[0].similarity if results else 0.0
        below_threshold = top_sim < query.min_similarity

        if hit:
            hits_count += 1
        else:
            failed_query_ids.append(query.id)

        if below_threshold and results:
            low_similarity_ids.append(query.id)

        total_rr += rr

        qr = QueryResult(
            query_id=query.id,
            query=query.query,
            hit=hit,
            reciprocal_rank=rr,
            top_similarity=top_sim,
            below_sim_threshold=below_threshold,
            results=results[:limit],
            first_hit_rank=first_rank,
        )
        query_results.append(qr)

        if verbose:
            status = "✓ HIT" if hit else "✗ MISS"
            rank_str = f"rank {first_rank}" if first_rank else "no hit"
            print(
                f"  [{i:02d}/{len(queries)}] [{query.id}] {status}  "
                f"RR={rr:.3f}  sim={top_sim:.3f}  {rank_str}"
            )
            print(f"         Query: {query.query}")
            if results:
                r0 = results[0]
                print(f"         Top:   {r0.heading!r} (path: {r0.section_path[:60]})")
            if not hit:
                print(
                    f"         Expected headings: {query.expected_headings[:3]} | "
                    f"paths: {query.expected_section_paths[:3]}"
                )
            print()

    total = len(queries)
    precision = hits_count / total if total > 0 else 0.0
    mrr = total_rr / total if total > 0 else 0.0
    passed = precision >= precision_threshold and mrr >= mrr_threshold

    return EvalReport(
        corpus=corpus,
        total=total,
        hits=hits_count,
        precision_at_n=precision,
        mrr=mrr,
        n=limit,
        failed_queries=failed_query_ids,
        low_similarity_queries=low_similarity_ids,
        query_results=query_results,
        passed=passed,
        precision_threshold=precision_threshold,
        mrr_threshold=mrr_threshold,
    )


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------


def print_report(report: EvalReport) -> None:
    """Print a human-readable evaluation report."""
    n = report.n
    bar = "=" * 40

    print(f"\n{bar}")
    print(f"RETRIEVAL QUALITY EVALUATION — {report.corpus.upper()}")
    print(f"{bar}")
    print(f"Queries run:      {report.total}")
    print(f"Hits in top-{n}:    {report.hits}")
    print(f"Precision@{n}:      {report.precision_at_n:.3f}")
    print(f"MRR:              {report.mrr:.3f}")

    if report.failed_queries:
        print(f"\nFailed queries (no relevant result in top {n}):")
        for qid in report.failed_queries:
            matching = [qr for qr in report.query_results if qr.query_id == qid]
            if matching:
                print(f"  [{qid}] {matching[0].query!r}")
            else:
                print(f"  [{qid}]")

    if report.low_similarity_queries:
        print("\nLow similarity queries (top result below min threshold):")
        for qid in report.low_similarity_queries:
            matching = [qr for qr in report.query_results if qr.query_id == qid]
            if matching:
                qr = matching[0]
                print(f"  [{qid}] {qr.query!r} — top sim: {qr.top_similarity:.3f}")

    p_threshold = report.precision_threshold
    mrr_threshold = report.mrr_threshold
    p_status = "✓" if report.precision_at_n >= p_threshold else "✗"
    mrr_status = "✓" if report.mrr >= mrr_threshold else "✗"

    print()
    if report.passed:
        print(
            f"STATUS: PASS ✓  "
            f"(P@{n}={report.precision_at_n:.3f} ≥ {p_threshold}, "
            f"MRR={report.mrr:.3f} ≥ {mrr_threshold})"
        )
    else:
        print("STATUS: FAIL ✗")
        if report.precision_at_n < p_threshold:
            print(
                f"  {p_status} P@{n}={report.precision_at_n:.3f} < {p_threshold} "
                f"(need {p_threshold - report.precision_at_n:.3f} more)"
            )
        if report.mrr < mrr_threshold:
            print(
                f"  {mrr_status} MRR={report.mrr:.3f} < {mrr_threshold} "
                f"(need {mrr_threshold - report.mrr:.3f} more)"
            )
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for retrieval quality evaluation."""
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Evaluate doc-hub retrieval quality (Precision@5, MRR)"
    )

    # Corpus selection (mutually exclusive)
    corpus_group = parser.add_mutually_exclusive_group()
    corpus_group.add_argument(
        "--corpus",
        default=None,
        metavar="SLUG",
        help="Corpus slug to evaluate (e.g. pydantic-ai)",
    )
    corpus_group.add_argument(
        "--all",
        action="store_true",
        help="Run eval for all corpora that have eval files",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of results to retrieve per query (default: 5)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show per-query results",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write JSON evaluation report(s) to this path",
    )
    parser.add_argument(
        "--min-precision",
        type=float,
        default=DEFAULT_PRECISION_THRESHOLD,
        help=f"Minimum Precision@N threshold (default: {DEFAULT_PRECISION_THRESHOLD})",
    )
    parser.add_argument(
        "--min-mrr",
        type=float,
        default=DEFAULT_MRR_THRESHOLD,
        help=f"Minimum MRR threshold (default: {DEFAULT_MRR_THRESHOLD})",
    )
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("LOGLEVEL") == "DEBUG" else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    # Determine which corpora to evaluate
    if args.corpus:
        slugs = [args.corpus]
    elif args.all or True:
        # Default (no --corpus) also runs all available evals
        slugs = list_eval_corpora()
        if not slugs:
            print(
                f"No eval files found in {_eval_dir()}. "
                "Create an eval file (e.g. eval/pydantic-ai.json) to get started.",
                file=sys.stderr,
            )
            sys.exit(1)

    async def _run() -> list[EvalReport]:
        from doc_hub.db import create_pool  # local import to avoid circular at module level

        pool = await create_pool()
        reports: list[EvalReport] = []
        try:
            for slug in slugs:
                eval_path = get_eval_file(slug)
                if eval_path is None:
                    print(
                        f"WARNING: No eval file found for corpus {slug!r} "
                        f"(looked in {_eval_dir() / (slug + '.json')})",
                        file=sys.stderr,
                    )
                    continue
                report = await evaluate(
                    eval_path,
                    pool=pool,
                    corpus=slug,
                    limit=args.limit,
                    verbose=args.verbose,
                    precision_threshold=args.min_precision,
                    mrr_threshold=args.min_mrr,
                )
                reports.append(report)
        finally:
            await pool.close()
        return reports

    reports = asyncio.run(_run())

    if not reports:
        print("No evaluations were run.", file=sys.stderr)
        sys.exit(1)

    all_passed = True
    for report in reports:
        print_report(report)
        if not report.passed:
            all_passed = False

    if args.output:
        output_path = Path(args.output)
        data = (
            reports[0].to_dict()
            if len(reports) == 1
            else [r.to_dict() for r in reports]
        )
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Report written to: {output_path}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
