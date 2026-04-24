from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_hub.corpora import describe_corpus_problem, format_corpus_suggestions


def test_format_corpus_suggestions_uses_close_slug_matches():
    corpora = [
        SimpleNamespace(slug="gascity-v0.14", name="gascity v0.14"),
        SimpleNamespace(slug="gascity-v1", name="gascity v1"),
        SimpleNamespace(slug="openproject-docs", name="OpenProject Docs"),
    ]

    message = format_corpus_suggestions("gastown", corpora)

    assert "Did you mean" in message
    assert "gascity-v0.14" in message
    assert "gascity-v1" in message


@pytest.mark.asyncio
async def test_describe_corpus_problem_reports_missing_with_suggestions():
    pool = MagicMock()
    corpora = [SimpleNamespace(slug="gascity-v1", name="gascity v1")]

    with (
        patch("doc_hub.db.get_corpus", AsyncMock(return_value=None)),
        patch("doc_hub.db.list_corpora", AsyncMock(return_value=corpora)),
    ):
        problem = await describe_corpus_problem(pool, "gastown")

    assert problem is not None
    assert "Corpus 'gastown' not found" in problem
    assert "Did you mean" in problem


@pytest.mark.asyncio
async def test_describe_corpus_problem_reports_empty_registered_corpus():
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=[0, 0])

    with patch("doc_hub.db.get_corpus", AsyncMock(return_value=SimpleNamespace(slug="temporal"))):
        problem = await describe_corpus_problem(pool, "temporal")

    assert problem == (
        "Corpus 'temporal' is registered but empty. It has no indexed chunks or documents; "
        "run or repair the pipeline before browsing, reading, or searching it."
    )


@pytest.mark.asyncio
async def test_describe_corpus_problem_returns_none_for_indexed_corpus():
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=[10, 2])

    with patch("doc_hub.db.get_corpus", AsyncMock(return_value=SimpleNamespace(slug="pydantic-ai"))):
        problem = await describe_corpus_problem(pool, "pydantic-ai")

    assert problem is None
