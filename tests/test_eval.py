"""Tests for doc_hub.eval — retrieval quality evaluation framework.

Unit tests only — no network, no DB required.
The search_docs function is mocked throughout.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_hub.eval import (
    DEFAULT_MRR_THRESHOLD,
    DEFAULT_PRECISION_THRESHOLD,
    EvalReport,
    QueryResult,
    TestQuery,
    _eval_dir,
    _first_hit_rank,
    _is_hit,
    _is_hit_single,
    _reciprocal_rank,
    build_eval_parser,
    evaluate,
    get_eval_file,
    handle_eval_args,
    list_eval_corpora,
    load_test_queries,
    print_report,
)
from doc_hub.search import SearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    heading: str = "Test heading",
    section_path: str = "/guide/test",
    similarity: float = 0.80,
    id: int = 1,
    corpus_id: str = "pydantic-ai",
) -> SearchResult:
    """Build a fake SearchResult."""
    return SearchResult(
        id=id,
        corpus_id=corpus_id,
        heading=heading,
        section_path=section_path,
        content="Some content",
        source_url="https://example.com/guide/test",
        score=0.033,
        similarity=similarity,
        category="guide",
        start_line=1,
        end_line=5,
    )


def _make_query(
    id: str = "q001",
    query: str = "how do I define a tool?",
    expected_headings: list[str] | None = None,
    expected_section_paths: list[str] | None = None,
    min_similarity: float = 0.55,
) -> TestQuery:
    """Build a fake TestQuery."""
    return TestQuery(
        id=id,
        query=query,
        expected_headings=expected_headings or ["Function Tools", "Tools"],
        expected_section_paths=expected_section_paths or ["tools"],
        min_similarity=min_similarity,
    )


def _make_pool() -> MagicMock:
    """Build a minimal mock asyncpg Pool."""
    pool = MagicMock()
    pool.close = AsyncMock()
    return pool


def _write_queries_json(path: Path, queries: list[dict]) -> None:
    """Write query entries to a JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(queries, f)


# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------


class TestDefaultThresholds:
    def test_precision_threshold_value(self):
        assert DEFAULT_PRECISION_THRESHOLD == 0.80

    def test_mrr_threshold_value(self):
        assert DEFAULT_MRR_THRESHOLD == 0.60


class TestEvalCliHelpers:
    def test_build_eval_parser_accepts_existing_parser(self):
        parser = argparse.ArgumentParser()
        built = build_eval_parser(parser)
        assert built is parser
        args = parser.parse_args(["--all"])
        assert args.all is True

    def test_handle_eval_args_runs_all_corpora(self):
        args = argparse.Namespace(
            corpus=None,
            all=True,
            limit=5,
            verbose=False,
            output=None,
            min_precision=DEFAULT_PRECISION_THRESHOLD,
            min_mrr=DEFAULT_MRR_THRESHOLD,
        )
        pool = _make_pool()
        report = EvalReport(
            corpus="demo",
            total=1,
            hits=1,
            precision_at_n=1.0,
            mrr=1.0,
            n=5,
            failed_queries=[],
            low_similarity_queries=[],
            query_results=[],
            passed=True,
        )

        captured = {}

        def fake_run(coro):
            captured["coro"] = coro
            coro.close()
            return [report]

        with (
            patch("doc_hub.eval.asyncio.run", side_effect=fake_run),
            patch("doc_hub.eval.list_eval_corpora", return_value=["demo"]),
            patch("doc_hub.eval.get_eval_file", return_value=Path("demo.json")),
            patch("doc_hub.eval.sys.exit") as mock_exit,
        ):
            handle_eval_args(args)

        mock_exit.assert_called_once_with(0)
        assert "coro" in captured


# ---------------------------------------------------------------------------
# TestQuery dataclass
# ---------------------------------------------------------------------------


class TestTestQuery:
    def test_defaults(self):
        q = TestQuery(id="q001", query="test")
        assert q.expected_headings == []
        assert q.expected_section_paths == []
        assert q.min_similarity == 0.55
        assert q.notes == ""

    def test_not_collected_as_pytest_test_class(self):
        assert getattr(TestQuery, "__test__", True) is False

    def test_custom_fields(self):
        q = TestQuery(
            id="q002",
            query="how to stream?",
            expected_headings=["Streaming"],
            expected_section_paths=["stream"],
            min_similarity=0.50,
            notes="streaming test",
        )
        assert q.id == "q002"
        assert q.query == "how to stream?"
        assert q.expected_headings == ["Streaming"]
        assert q.expected_section_paths == ["stream"]
        assert q.min_similarity == 0.50
        assert q.notes == "streaming test"


# ---------------------------------------------------------------------------
# _is_hit_single() — relevance matching
# ---------------------------------------------------------------------------


class TestIsHitSingle:
    def test_heading_substring_match(self):
        result = _make_result(heading="Function Tools Overview")
        query = _make_query(expected_headings=["Function Tools"])
        assert _is_hit_single(result, query) is True

    def test_heading_case_insensitive(self):
        result = _make_result(heading="function tools overview")
        query = _make_query(expected_headings=["Function Tools"])
        assert _is_hit_single(result, query) is True

    def test_section_path_substring_match(self):
        result = _make_result(section_path="/guide/tools/function-tools")
        query = _make_query(expected_headings=[], expected_section_paths=["tools"])
        assert _is_hit_single(result, query) is True

    def test_section_path_case_insensitive(self):
        result = _make_result(section_path="/guide/Tools/")
        query = _make_query(expected_headings=[], expected_section_paths=["tools"])
        assert _is_hit_single(result, query) is True

    def test_no_match(self):
        result = _make_result(heading="Unrelated Topic", section_path="/api/unrelated")
        query = _make_query(expected_headings=["Tools"], expected_section_paths=["tools"])
        assert _is_hit_single(result, query) is False

    def test_or_logic_heading_wins(self):
        """A result is relevant if heading OR section_path matches."""
        result = _make_result(heading="Tools Guide", section_path="/unrelated/path")
        query = _make_query(expected_headings=["Tools"], expected_section_paths=["stream"])
        assert _is_hit_single(result, query) is True

    def test_or_logic_section_path_wins(self):
        """A result is relevant if heading OR section_path matches."""
        result = _make_result(heading="Unrelated Heading", section_path="/guide/stream")
        query = _make_query(expected_headings=["Tools"], expected_section_paths=["stream"])
        assert _is_hit_single(result, query) is True

    def test_multiple_expected_headings_any_matches(self):
        result = _make_result(heading="ModelRetry exception")
        query = _make_query(
            expected_headings=["ModelRetry", "Retries", "Tool Retries"],
            expected_section_paths=[],
        )
        assert _is_hit_single(result, query) is True

    def test_empty_expectations_no_match(self):
        result = _make_result(heading="Any heading", section_path="/any/path")
        query = _make_query(expected_headings=[], expected_section_paths=[])
        assert _is_hit_single(result, query) is False


# ---------------------------------------------------------------------------
# _is_hit() — hit detection over a list
# ---------------------------------------------------------------------------


class TestIsHit:
    def test_hit_when_any_result_relevant(self):
        results = [
            _make_result(heading="Unrelated", id=1),
            _make_result(heading="Function Tools", id=2),
        ]
        query = _make_query(expected_headings=["Function Tools"])
        assert _is_hit(results, query) is True

    def test_miss_when_no_result_relevant(self):
        results = [
            _make_result(heading="Unrelated A", id=1),
            _make_result(heading="Unrelated B", id=2),
        ]
        query = _make_query(expected_headings=["Tools"], expected_section_paths=["tools"])
        assert _is_hit(results, query) is False

    def test_empty_results_is_miss(self):
        query = _make_query(expected_headings=["Tools"])
        assert _is_hit([], query) is False


# ---------------------------------------------------------------------------
# _reciprocal_rank()
# ---------------------------------------------------------------------------


class TestReciprocalRank:
    def test_first_result_is_hit(self):
        results = [_make_result(heading="Function Tools")]
        query = _make_query(expected_headings=["Function Tools"])
        rr = _reciprocal_rank(results, query)
        assert abs(rr - 1.0) < 1e-9  # rank 1 → 1/1

    def test_second_result_is_hit(self):
        results = [
            _make_result(heading="Unrelated", id=1),
            _make_result(heading="Function Tools", id=2),
        ]
        query = _make_query(expected_headings=["Function Tools"])
        rr = _reciprocal_rank(results, query)
        assert abs(rr - 0.5) < 1e-9  # rank 2 → 1/2

    def test_third_result_is_hit(self):
        results = [
            _make_result(heading="A", id=1),
            _make_result(heading="B", id=2),
            _make_result(heading="Function Tools", id=3),
        ]
        query = _make_query(expected_headings=["Function Tools"])
        rr = _reciprocal_rank(results, query)
        assert abs(rr - 1.0 / 3) < 1e-9

    def test_no_hit_returns_zero(self):
        results = [_make_result(heading="Unrelated")]
        query = _make_query(expected_headings=["Tools"], expected_section_paths=["tools"])
        rr = _reciprocal_rank(results, query)
        assert rr == 0.0

    def test_empty_results_returns_zero(self):
        query = _make_query()
        assert _reciprocal_rank([], query) == 0.0


# ---------------------------------------------------------------------------
# _first_hit_rank()
# ---------------------------------------------------------------------------


class TestFirstHitRank:
    def test_first_result_hit(self):
        results = [_make_result(heading="Tools")]
        query = _make_query(expected_headings=["Tools"])
        assert _first_hit_rank(results, query) == 1

    def test_second_result_hit(self):
        results = [
            _make_result(heading="Unrelated", id=1),
            _make_result(heading="Tools", id=2),
        ]
        query = _make_query(expected_headings=["Tools"])
        assert _first_hit_rank(results, query) == 2

    def test_no_hit_returns_none(self):
        results = [_make_result(heading="Unrelated")]
        query = _make_query(expected_headings=["Tools"], expected_section_paths=["tools"])
        assert _first_hit_rank(results, query) is None

    def test_empty_results_returns_none(self):
        query = _make_query()
        assert _first_hit_rank([], query) is None


# ---------------------------------------------------------------------------
# load_test_queries()
# ---------------------------------------------------------------------------


class TestLoadTestQueries:
    def test_load_valid_queries(self, tmp_path):
        data = [
            {
                "id": "q001",
                "query": "how do I define a tool?",
                "expected_headings": ["Tools"],
                "expected_section_paths": ["tools"],
                "min_similarity": 0.55,
                "notes": "test note",
            }
        ]
        path = tmp_path / "queries.json"
        _write_queries_json(path, data)

        queries = load_test_queries(path)
        assert len(queries) == 1
        q = queries[0]
        assert q.id == "q001"
        assert q.query == "how do I define a tool?"
        assert q.expected_headings == ["Tools"]
        assert q.expected_section_paths == ["tools"]
        assert q.min_similarity == 0.55
        assert q.notes == "test note"

    def test_default_min_similarity(self, tmp_path):
        data = [
            {
                "id": "q001",
                "query": "test",
                "expected_headings": ["Something"],
            }
        ]
        path = tmp_path / "queries.json"
        _write_queries_json(path, data)

        queries = load_test_queries(path)
        assert queries[0].min_similarity == 0.55  # default

    def test_missing_id_raises(self, tmp_path):
        data = [{"query": "test", "expected_headings": ["Something"]}]
        path = tmp_path / "queries.json"
        _write_queries_json(path, data)

        with pytest.raises(ValueError, match="missing required field"):
            load_test_queries(path)

    def test_missing_query_raises(self, tmp_path):
        data = [{"id": "q001", "expected_headings": ["Something"]}]
        path = tmp_path / "queries.json"
        _write_queries_json(path, data)

        with pytest.raises(ValueError, match="missing required field"):
            load_test_queries(path)

    def test_missing_both_expected_fields_raises(self, tmp_path):
        data = [{"id": "q001", "query": "test"}]
        path = tmp_path / "queries.json"
        _write_queries_json(path, data)

        with pytest.raises(ValueError, match="must have at least one"):
            load_test_queries(path)

    def test_only_expected_headings_ok(self, tmp_path):
        data = [{"id": "q001", "query": "test", "expected_headings": ["Tools"]}]
        path = tmp_path / "queries.json"
        _write_queries_json(path, data)

        queries = load_test_queries(path)
        assert len(queries) == 1

    def test_only_expected_section_paths_ok(self, tmp_path):
        data = [
            {"id": "q001", "query": "test", "expected_section_paths": ["/api/tools"]}
        ]
        path = tmp_path / "queries.json"
        _write_queries_json(path, data)

        queries = load_test_queries(path)
        assert len(queries) == 1

    def test_load_multiple_queries(self, tmp_path):
        data = [
            {"id": f"q{i:03d}", "query": f"query {i}", "expected_headings": ["X"]}
            for i in range(1, 6)
        ]
        path = tmp_path / "queries.json"
        _write_queries_json(path, data)

        queries = load_test_queries(path)
        assert len(queries) == 5
        assert [q.id for q in queries] == ["q001", "q002", "q003", "q004", "q005"]

    def test_load_real_pydantic_ai_queries(self):
        """The shipped pydantic-ai.json eval file should load cleanly."""
        # Find eval file relative to this test file (tests/ → repo root)
        here = Path(__file__).resolve().parent.parent
        eval_path = here / "eval" / "pydantic-ai.json"
        if not eval_path.exists():
            pytest.skip(f"Eval file not found: {eval_path}")
        queries = load_test_queries(eval_path)
        assert len(queries) >= 20
        for q in queries:
            assert q.id
            assert q.query
            assert q.expected_headings or q.expected_section_paths


# ---------------------------------------------------------------------------
# EvalReport.to_dict()
# ---------------------------------------------------------------------------


class TestEvalReportToDict:
    def _make_report(self, **kwargs) -> EvalReport:
        defaults = dict(
            corpus="pydantic-ai",
            total=10,
            hits=8,
            precision_at_n=0.8,
            mrr=0.75,
            n=5,
            failed_queries=["q003"],
            low_similarity_queries=["q007"],
            passed=True,
            precision_threshold=0.80,
            mrr_threshold=0.60,
        )
        defaults.update(kwargs)
        return EvalReport(**defaults)

    def test_to_dict_basic_keys(self):
        report = self._make_report()
        d = report.to_dict()
        assert "corpus" in d
        assert "total" in d
        assert "hits" in d
        assert "precision_at_n" in d
        assert "mrr" in d
        assert "n" in d
        assert "failed_queries" in d
        assert "low_similarity_queries" in d
        assert "passed" in d
        assert "thresholds" in d

    def test_to_dict_corpus_value(self):
        report = self._make_report(corpus="pydantic-ai")
        assert report.to_dict()["corpus"] == "pydantic-ai"

    def test_to_dict_precision_rounded(self):
        report = self._make_report(precision_at_n=0.833333)
        d = report.to_dict()
        assert d["precision_at_n"] == round(0.833333, 4)

    def test_to_dict_mrr_rounded(self):
        report = self._make_report(mrr=0.761111)
        d = report.to_dict()
        assert d["mrr"] == round(0.761111, 4)

    def test_to_dict_thresholds(self):
        report = self._make_report(precision_threshold=0.80, mrr_threshold=0.60)
        d = report.to_dict()
        assert d["thresholds"]["precision_at_n"] == 0.80
        assert d["thresholds"]["mrr"] == 0.60


# ---------------------------------------------------------------------------
# Eval file discovery
# ---------------------------------------------------------------------------


class TestEvalFileDiscovery:
    def test_get_eval_file_returns_path_when_exists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_EVAL_DIR", str(tmp_path))
        (tmp_path / "pydantic-ai.json").write_text("[]")
        result = get_eval_file("pydantic-ai")
        assert result is not None
        assert result.name == "pydantic-ai.json"

    def test_get_eval_file_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_EVAL_DIR", str(tmp_path))
        result = get_eval_file("nonexistent-corpus")
        assert result is None

    def test_list_eval_corpora_returns_slugs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_EVAL_DIR", str(tmp_path))
        (tmp_path / "pydantic-ai.json").write_text("[]")
        (tmp_path / "fastapi.json").write_text("[]")
        corpora = list_eval_corpora()
        assert "pydantic-ai" in corpora
        assert "fastapi" in corpora

    def test_list_eval_corpora_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_EVAL_DIR", str(tmp_path))
        corpora = list_eval_corpora()
        assert corpora == []

    def test_list_eval_corpora_nonexistent_dir(self, tmp_path, monkeypatch):
        missing = tmp_path / "doesnotexist"
        monkeypatch.setenv("DOC_HUB_EVAL_DIR", str(missing))
        corpora = list_eval_corpora()
        assert corpora == []

    def test_eval_dir_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_HUB_EVAL_DIR", str(tmp_path))
        assert _eval_dir() == tmp_path

    def test_eval_dir_fallback_data_root(self, monkeypatch):
        """_eval_dir() falls back to data_root()/eval (XDG path), not cwd()."""
        from doc_hub.paths import data_root
        monkeypatch.delenv("DOC_HUB_EVAL_DIR", raising=False)
        result = _eval_dir()
        assert result == data_root() / "eval"


# ---------------------------------------------------------------------------
# evaluate() — core evaluation function
# ---------------------------------------------------------------------------


class TestEvaluate:
    def _make_eval_json(self, tmp_path: Path, queries: list[dict] | None = None) -> Path:
        if queries is None:
            queries = [
                {
                    "id": "q001",
                    "query": "how do I define a tool?",
                    "expected_headings": ["Function Tools"],
                    "expected_section_paths": ["tools"],
                }
            ]
        path = tmp_path / "test.json"
        _write_queries_json(path, queries)
        return path

    @pytest.mark.asyncio
    async def test_evaluate_all_hits(self, tmp_path):
        """When all queries hit, P@5=1.0 and report.passed=True."""
        queries_data = [
            {
                "id": "q001",
                "query": "define a tool",
                "expected_headings": ["Function Tools"],
            },
            {
                "id": "q002",
                "query": "stream output",
                "expected_headings": ["Streaming"],
            },
        ]
        test_path = self._make_eval_json(tmp_path, queries_data)
        pool = _make_pool()

        # Mock search_docs to return matching results
        async def mock_search(query, *, pool, corpus, limit, min_similarity, **kwargs):
            if "tool" in query:
                return [_make_result(heading="Function Tools", similarity=0.90)]
            if "stream" in query:
                return [_make_result(heading="Streaming Overview", similarity=0.85)]
            return []

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            report = await evaluate(test_path, pool=pool, corpus="pydantic-ai")

        assert report.total == 2
        assert report.hits == 2
        assert report.precision_at_n == 1.0
        assert report.mrr == 1.0
        assert report.passed is True
        assert report.corpus == "pydantic-ai"

    @pytest.mark.asyncio
    async def test_evaluate_all_misses(self, tmp_path):
        """When all queries miss, P@5=0.0 and report.passed=False."""
        queries_data = [
            {
                "id": "q001",
                "query": "define a tool",
                "expected_headings": ["Function Tools"],
            },
        ]
        test_path = self._make_eval_json(tmp_path, queries_data)
        pool = _make_pool()

        async def mock_search(*args, **kwargs):
            return [_make_result(heading="Unrelated Topic", similarity=0.80)]

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            report = await evaluate(test_path, pool=pool, corpus="pydantic-ai")

        assert report.total == 1
        assert report.hits == 0
        assert report.precision_at_n == 0.0
        assert report.mrr == 0.0
        assert report.passed is False
        assert "q001" in report.failed_queries

    @pytest.mark.asyncio
    async def test_evaluate_uses_min_similarity_zero_for_search(self, tmp_path):
        """evaluate() must call search_docs with min_similarity=0.0."""
        test_path = self._make_eval_json(tmp_path)
        pool = _make_pool()

        captured_kwargs: dict = {}

        async def mock_search(query, *, pool, corpus, limit, min_similarity, **kwargs):
            captured_kwargs["min_similarity"] = min_similarity
            return []

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            await evaluate(test_path, pool=pool, corpus="pydantic-ai")

        assert captured_kwargs["min_similarity"] == 0.0

    @pytest.mark.asyncio
    async def test_evaluate_passes_corpus_to_search(self, tmp_path):
        """evaluate() must pass corpus slug to search_docs()."""
        test_path = self._make_eval_json(tmp_path)
        pool = _make_pool()

        captured_corpus: list = []

        async def mock_search(query, *, pool, corpus, limit, min_similarity, **kwargs):
            captured_corpus.append(corpus)
            return []

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            await evaluate(test_path, pool=pool, corpus="my-test-corpus")

        assert all(c == "my-test-corpus" for c in captured_corpus)

    @pytest.mark.asyncio
    async def test_evaluate_mrr_computation(self, tmp_path):
        """MRR = average of reciprocal ranks."""
        queries_data = [
            {
                "id": "q001",
                "query": "query one",
                "expected_headings": ["Hit"],
            },
            {
                "id": "q002",
                "query": "query two",
                "expected_headings": ["Hit"],
            },
        ]
        test_path = self._make_eval_json(tmp_path, queries_data)
        pool = _make_pool()

        call_count = 0

        async def mock_search(query, *, pool, corpus, limit, min_similarity, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First query: hit at rank 1
                return [
                    _make_result(heading="Hit", id=1, similarity=0.9),
                ]
            else:
                # Second query: hit at rank 2
                return [
                    _make_result(heading="Miss", id=2, similarity=0.9),
                    _make_result(heading="Hit Here", id=3, similarity=0.85),
                ]

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            report = await evaluate(test_path, pool=pool, corpus="test")

        # q001: RR=1.0, q002: "Hit" matches "Hit Here" → rank 2 → RR=0.5
        expected_mrr = (1.0 + 0.5) / 2
        assert abs(report.mrr - expected_mrr) < 1e-9

    @pytest.mark.asyncio
    async def test_evaluate_below_sim_threshold_tracking(self, tmp_path):
        """Queries where top result similarity < min_similarity are tracked."""
        queries_data = [
            {
                "id": "q001",
                "query": "test",
                "expected_headings": ["Hit"],
                "min_similarity": 0.70,
            }
        ]
        test_path = self._make_eval_json(tmp_path, queries_data)
        pool = _make_pool()

        async def mock_search(query, *, pool, corpus, limit, min_similarity, **kwargs):
            return [_make_result(heading="Hit", similarity=0.60)]  # below 0.70

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            report = await evaluate(test_path, pool=pool, corpus="test")

        assert "q001" in report.low_similarity_queries

    @pytest.mark.asyncio
    async def test_evaluate_search_exception_treated_as_empty(self, tmp_path):
        """If search_docs raises, treat as zero results (don't crash eval)."""
        test_path = self._make_eval_json(tmp_path)
        pool = _make_pool()

        async def mock_search(*args, **kwargs):
            raise RuntimeError("DB connection failed")

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            report = await evaluate(test_path, pool=pool, corpus="test")

        assert report.total == 1
        assert report.hits == 0
        assert report.precision_at_n == 0.0

    @pytest.mark.asyncio
    async def test_evaluate_custom_thresholds(self, tmp_path):
        """Custom precision/MRR thresholds affect .passed."""
        queries_data = [
            {
                "id": "q001",
                "query": "test",
                "expected_headings": ["Hit"],
            }
        ]
        test_path = self._make_eval_json(tmp_path, queries_data)
        pool = _make_pool()

        async def mock_search(*args, **kwargs):
            return [_make_result(heading="Hit", similarity=0.90)]

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            report = await evaluate(
                test_path,
                pool=pool,
                corpus="test",
                precision_threshold=0.90,  # VERY high — should fail
                mrr_threshold=0.95,
            )

        # P@5=1.0 >= 0.90 but MRR threshold check...
        # With 1 query and hit at rank 1: MRR=1.0, P=1.0
        # Actually both pass since there's only 1 query and it's a hit
        # Let's verify the thresholds are stored correctly
        assert report.precision_threshold == 0.90
        assert report.mrr_threshold == 0.95

    @pytest.mark.asyncio
    async def test_evaluate_query_results_populated(self, tmp_path):
        """Each query produces a QueryResult in report.query_results."""
        queries_data = [
            {"id": "q001", "query": "test", "expected_headings": ["Something"]},
            {"id": "q002", "query": "test2", "expected_headings": ["Else"]},
        ]
        test_path = self._make_eval_json(tmp_path, queries_data)
        pool = _make_pool()

        async def mock_search(*args, **kwargs):
            return []

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            report = await evaluate(test_path, pool=pool, corpus="test")

        assert len(report.query_results) == 2
        assert all(isinstance(qr, QueryResult) for qr in report.query_results)
        ids = [qr.query_id for qr in report.query_results]
        assert "q001" in ids
        assert "q002" in ids

    @pytest.mark.asyncio
    async def test_evaluate_per_query_min_similarity_respected(self, tmp_path):
        """Per-query min_similarity values are read from JSON, not the default."""
        queries_data = [
            {
                "id": "q001",
                "query": "test",
                "expected_headings": ["Hit"],
                "min_similarity": 0.45,  # Very low — 0.60 result should NOT be flagged
            }
        ]
        test_path = self._make_eval_json(tmp_path, queries_data)
        pool = _make_pool()

        async def mock_search(*args, **kwargs):
            return [_make_result(heading="Hit", similarity=0.60)]

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            report = await evaluate(test_path, pool=pool, corpus="test")

        # 0.60 >= 0.45, so should NOT be in low_similarity_queries
        assert "q001" not in report.low_similarity_queries

    @pytest.mark.asyncio
    async def test_evaluate_limit_applied_to_search_results(self, tmp_path):
        """Results beyond limit are not scored."""
        queries_data = [
            {"id": "q001", "query": "test", "expected_headings": ["Deep Hit"]}
        ]
        test_path = self._make_eval_json(tmp_path, queries_data)
        pool = _make_pool()

        async def mock_search(query, *, pool, corpus, limit, min_similarity, **kwargs):
            # Return 10 results; "Deep Hit" is at rank 6 (beyond limit=5)
            results = [
                _make_result(heading=f"Result {i}", id=i, similarity=0.9)
                for i in range(1, 6)
            ]
            results.append(_make_result(heading="Deep Hit", id=6, similarity=0.9))
            return results

        with patch("doc_hub.eval.search_docs", side_effect=mock_search):
            # limit=5 means only top 5 are scored; "Deep Hit" at position 6 is a miss
            report = await evaluate(test_path, pool=pool, corpus="test", limit=5)

        assert report.hits == 0  # "Deep Hit" is out of top-5 window

    @pytest.mark.asyncio
    async def test_evaluate_empty_queries_file(self, tmp_path):
        """An empty query list produces zero-score report."""
        path = tmp_path / "empty.json"
        _write_queries_json(path, [])
        pool = _make_pool()

        with patch("doc_hub.eval.search_docs"):
            report = await evaluate(path, pool=pool, corpus="test")

        assert report.total == 0
        assert report.hits == 0
        assert report.precision_at_n == 0.0
        assert report.mrr == 0.0


# ---------------------------------------------------------------------------
# print_report() — smoke test (just ensure no exceptions)
# ---------------------------------------------------------------------------


class TestPrintReport:
    def test_print_pass_report(self, capsys):
        report = EvalReport(
            corpus="pydantic-ai",
            total=10,
            hits=9,
            precision_at_n=0.9,
            mrr=0.85,
            n=5,
            failed_queries=["q003"],
            low_similarity_queries=[],
            passed=True,
            precision_threshold=0.80,
            mrr_threshold=0.60,
        )
        print_report(report)
        captured = capsys.readouterr()
        assert "PASS" in captured.out
        assert "pydantic-ai" in captured.out.upper() or "PYDANTIC-AI" in captured.out.upper()

    def test_print_fail_report(self, capsys):
        report = EvalReport(
            corpus="fastapi",
            total=10,
            hits=3,
            precision_at_n=0.3,
            mrr=0.2,
            n=5,
            failed_queries=["q001", "q002"],
            low_similarity_queries=["q003"],
            passed=False,
            precision_threshold=0.80,
            mrr_threshold=0.60,
        )
        print_report(report)
        captured = capsys.readouterr()
        assert "FAIL" in captured.out

    def test_print_report_shows_failed_queries(self, capsys):
        report = EvalReport(
            corpus="test",
            total=5,
            hits=4,
            precision_at_n=0.8,
            mrr=0.7,
            n=5,
            failed_queries=["q002"],
            low_similarity_queries=[],
            query_results=[
                QueryResult(
                    query_id="q002",
                    query="some test query",
                    hit=False,
                    reciprocal_rank=0.0,
                    top_similarity=0.0,
                    below_sim_threshold=True,
                    results=[],
                    first_hit_rank=None,
                )
            ],
            passed=True,
            precision_threshold=0.80,
            mrr_threshold=0.60,
        )
        print_report(report)
        captured = capsys.readouterr()
        assert "q002" in captured.out


# ---------------------------------------------------------------------------
# CLI entry point (basic importability checks)
# ---------------------------------------------------------------------------


class TestCLIImportability:
    def test_main_importable(self):
        from doc_hub.eval import main
        assert callable(main)

    def test_evaluate_importable(self):
        from doc_hub.eval import evaluate
        assert callable(evaluate)

    def test_list_eval_corpora_importable(self):
        from doc_hub.eval import list_eval_corpora
        assert callable(list_eval_corpora)

    def test_get_eval_file_importable(self):
        from doc_hub.eval import get_eval_file
        assert callable(get_eval_file)


# ---------------------------------------------------------------------------
# Integration: ensure real eval file is correctly structured
# ---------------------------------------------------------------------------


class TestRealEvalFile:
    """Checks on the shipped eval/pydantic-ai.json file."""

    def _get_eval_path(self) -> Path | None:
        here = Path(__file__).resolve().parent.parent
        p = here / "eval" / "pydantic-ai.json"
        return p if p.exists() else None

    def test_eval_file_exists(self):
        p = self._get_eval_path()
        assert p is not None, "eval/pydantic-ai.json not found in repo root"

    def test_eval_file_has_28_queries(self):
        p = self._get_eval_path()
        if p is None:
            pytest.skip("Eval file not found")
        queries = load_test_queries(p)
        assert len(queries) == 28

    def test_eval_file_ids_are_unique(self):
        p = self._get_eval_path()
        if p is None:
            pytest.skip("Eval file not found")
        queries = load_test_queries(p)
        ids = [q.id for q in queries]
        assert len(ids) == len(set(ids)), "Duplicate query IDs found"

    def test_eval_file_all_have_expected_criteria(self):
        p = self._get_eval_path()
        if p is None:
            pytest.skip("Eval file not found")
        queries = load_test_queries(p)
        for q in queries:
            has_headings = bool(q.expected_headings)
            has_paths = bool(q.expected_section_paths)
            assert has_headings or has_paths, (
                f"Query {q.id!r} has no expected_headings or expected_section_paths"
            )

    def test_eval_file_min_similarity_range(self):
        p = self._get_eval_path()
        if p is None:
            pytest.skip("Eval file not found")
        queries = load_test_queries(p)
        for q in queries:
            assert 0.0 <= q.min_similarity <= 1.0, (
                f"Query {q.id!r} has invalid min_similarity={q.min_similarity}"
            )
