"""Tests for doc_hub.pipeline — orchestration skeleton and fetch stage.

Unit tests only — no network, no DB required. DB and HTTP calls are mocked.
"""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_hub.fetchers import DEFAULT_RETRIES, DEFAULT_WORKERS
from doc_hub.models import Corpus
from doc_hub.versions import snapshot_manifest_from_downloads, write_snapshot_manifest
from doc_hub.pipeline import (
    _build_arg_parser,
    handle_pipeline_run_args,
    run_fetch,
    run_pipeline,
    sync_all,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_corpus(
    slug: str = "test",
    strategy: str = "llms_txt",
    fetch_config: dict | None = None,
) -> Corpus:
    if fetch_config is None:
        fetch_config = {
            "url": "https://ai.pydantic.dev/llms.txt",
            "url_pattern": r"https://ai\.pydantic\.dev/[^\s\)]+\.md",
            "base_url": "https://ai.pydantic.dev",
        }
    return Corpus(
        slug=slug,
        name="Test Corpus",
        fetch_strategy=strategy,
        fetch_config=fetch_config,
    )


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------


def test_parser_requires_corpus():
    """--corpus is a required argument."""
    parser = _build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_corpus_flag():
    """--corpus sets corpus slug."""
    parser = _build_arg_parser()
    args = parser.parse_args(["--corpus", "pydantic-ai"])
    assert args.corpus == "pydantic-ai"


def test_parser_default_workers():
    """--workers defaults to DEFAULT_WORKERS."""
    parser = _build_arg_parser()
    args = parser.parse_args(["--corpus", "pydantic-ai"])
    assert args.workers == DEFAULT_WORKERS


def test_parser_default_retries():
    """--retries defaults to DEFAULT_RETRIES."""
    parser = _build_arg_parser()
    args = parser.parse_args(["--corpus", "pydantic-ai"])
    assert args.retries == DEFAULT_RETRIES


def test_parser_stage_choices():
    """--stage accepts only fetch|parse|embed|index."""
    parser = _build_arg_parser()
    for stage in ("fetch", "parse", "embed", "index"):
        args = parser.parse_args(["--corpus", "x", "--stage", stage])
        assert args.stage == stage


def test_parser_invalid_stage():
    """--stage with unknown value raises SystemExit."""
    parser = _build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--corpus", "x", "--stage", "invalid"])


def test_build_arg_parser_accepts_existing_parser():
    parser = argparse.ArgumentParser()
    built = _build_arg_parser(parser)
    assert built is parser
    args = parser.parse_args(["--corpus", "demo"])
    assert args.corpus == "demo"


def test_handle_pipeline_run_args_uses_asyncio_run():
    args = argparse.Namespace(
        corpus="demo",
        stage=None,
        clean=False,
        skip_download=False,
        full_reindex=False,
        retry_failed=False,
        workers=DEFAULT_WORKERS,
        retries=DEFAULT_RETRIES,
    )

    captured = {}

    def fake_run(coro):
        captured["coro"] = coro
        coro.close()

    with patch("doc_hub.pipeline.asyncio.run", side_effect=fake_run) as mock_asyncio_run:
        handle_pipeline_run_args(args)

    mock_asyncio_run.assert_called_once()
    assert "coro" in captured


def test_parser_clean_flag():
    """--clean sets clean=True."""
    parser = _build_arg_parser()
    args = parser.parse_args(["--corpus", "x", "--clean"])
    assert args.clean is True


def test_parser_skip_download_flag():
    """--skip-download sets skip_download=True."""
    parser = _build_arg_parser()
    args = parser.parse_args(["--corpus", "x", "--skip-download"])
    assert args.skip_download is True


def test_parser_full_reindex_flag():
    """--full-reindex sets full_reindex=True."""
    parser = _build_arg_parser()
    args = parser.parse_args(["--corpus", "x", "--full-reindex"])
    assert args.full_reindex is True


def test_parser_retry_failed_flag():
    """--retry-failed sets retry_failed=True."""
    parser = _build_arg_parser()
    args = parser.parse_args(["--corpus", "x", "--retry-failed"])
    assert args.retry_failed is True


def test_parser_custom_workers():
    """--workers accepts an integer."""
    parser = _build_arg_parser()
    args = parser.parse_args(["--corpus", "x", "--workers", "5"])
    assert args.workers == 5


def test_parser_custom_retries():
    """--retries accepts an integer."""
    parser = _build_arg_parser()
    args = parser.parse_args(["--corpus", "x", "--retries", "10"])
    assert args.retries == 10


# ---------------------------------------------------------------------------
# run_fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_fetch_skip_download(tmp_path):
    """run_fetch does nothing when skip_download=True."""
    corpus = _make_corpus()

    with patch("doc_hub.pipeline.fetch") as mock_fetch:
        mock_fetch.return_value = tmp_path
        await run_fetch(corpus, skip_download=True)
        mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_run_fetch_calls_fetch(tmp_path):
    """run_fetch calls fetch() with corpus_slug, strategy, config, and raw_dir."""
    corpus = _make_corpus()

    with (
        patch("doc_hub.pipeline.fetch", new=AsyncMock(return_value=tmp_path)) as mock_fetch,
        patch("doc_hub.pipeline.raw_dir", return_value=tmp_path),
    ):
        await run_fetch(corpus)
        mock_fetch.assert_called_once()
        call_args = mock_fetch.call_args
        # New signature: fetch(corpus_slug, fetch_strategy, fetch_config, output_dir)
        assert call_args[0][0] == corpus.slug
        assert call_args[0][1] == corpus.fetch_strategy
        assert call_args[0][3] == tmp_path


@pytest.mark.asyncio
async def test_run_fetch_injects_workers_into_config(tmp_path):
    """run_fetch injects workers into fetch_config if not already set."""
    corpus = _make_corpus()
    captured_config = []

    async def capture_fetch(slug, strategy, config, output_dir):
        captured_config.append(config)
        return output_dir

    with (
        patch("doc_hub.pipeline.fetch", side_effect=capture_fetch),
        patch("doc_hub.pipeline.raw_dir", return_value=tmp_path),
    ):
        await run_fetch(corpus, workers=5)
        assert len(captured_config) == 1
        assert captured_config[0].get("workers") == 5


@pytest.mark.asyncio
async def test_run_fetch_injects_retries_into_config(tmp_path):
    """run_fetch injects retries into fetch_config if not already set."""
    corpus = _make_corpus()
    captured_config = []

    async def capture_fetch(slug, strategy, config, output_dir):
        captured_config.append(config)
        return output_dir

    with (
        patch("doc_hub.pipeline.fetch", side_effect=capture_fetch),
        patch("doc_hub.pipeline.raw_dir", return_value=tmp_path),
    ):
        await run_fetch(corpus, retries=7)
        assert len(captured_config) == 1
        assert captured_config[0].get("retries") == 7


@pytest.mark.asyncio
async def test_run_fetch_does_not_override_existing_config(tmp_path):
    """run_fetch does NOT override workers/retries already in fetch_config."""
    fetch_config = {
        "url": "https://example.com/llms.txt",
        "url_pattern": r".*\.md",
        "base_url": "https://example.com",
        "workers": 3,
        "retries": 1,
    }
    corpus = _make_corpus(fetch_config=fetch_config)
    captured_config = []

    async def capture_fetch(slug, strategy, config, output_dir):
        captured_config.append(config)
        return output_dir

    with (
        patch("doc_hub.pipeline.fetch", side_effect=capture_fetch),
        patch("doc_hub.pipeline.raw_dir", return_value=tmp_path),
    ):
        await run_fetch(corpus, workers=20, retries=3)
        assert len(captured_config) == 1
        # Config-level values must take precedence
        assert captured_config[0]["workers"] == 3
        assert captured_config[0]["retries"] == 1


@pytest.mark.asyncio
async def test_run_fetch_materializes_resolved_snapshot_raw(tmp_path):
    corpus = _make_corpus()
    legacy_raw, snapshot_id, versioned_raw = _write_legacy_snapshot_raw(tmp_path, corpus)

    def fake_raw_dir(c, snapshot_id=None):
        if snapshot_id is None:
            return legacy_raw
        return tmp_path / "corpus" / "versions" / snapshot_id / "raw"

    async def fake_fetch(slug, strategy, config, output_dir):
        return output_dir

    with (
        patch("doc_hub.pipeline.fetch", side_effect=fake_fetch),
        patch("doc_hub.pipeline.raw_dir", side_effect=fake_raw_dir),
    ):
        resolved = await run_fetch(corpus)

    assert resolved == snapshot_id
    assert (versioned_raw / "guide.md").read_text() == "# Guide\n"
    assert (versioned_raw / "manifest.json").exists()


@pytest.mark.asyncio
async def test_run_fetch_skip_download_materializes_resolved_snapshot_raw(tmp_path):
    corpus = _make_corpus()
    legacy_raw, snapshot_id, versioned_raw = _write_legacy_snapshot_raw(tmp_path, corpus)

    def fake_raw_dir(c, snapshot_id=None):
        if snapshot_id is None:
            return legacy_raw
        return tmp_path / "corpus" / "versions" / snapshot_id / "raw"

    with patch("doc_hub.pipeline.raw_dir", side_effect=fake_raw_dir):
        resolved = await run_fetch(corpus, skip_download=True)

    assert resolved == snapshot_id
    assert (versioned_raw / "guide.md").read_text() == "# Guide\n"
    assert (versioned_raw / "manifest.json").exists()


def _write_legacy_snapshot_raw(tmp_path, corpus):
    legacy_raw = tmp_path / "corpus" / "raw"
    legacy_raw.mkdir(parents=True)
    (legacy_raw / "guide.md").write_text("# Guide\n")
    manifest = snapshot_manifest_from_downloads(
        corpus_slug=corpus.slug,
        fetch_strategy=corpus.fetch_strategy,
        source_type="llms_txt",
        source_url="https://example.com/llms.txt",
        files=[{
            "filename": "guide.md",
            "url": "https://example.com/guide.md",
            "success": True,
            "content_hash": "abc123",
        }],
        fetch_config=corpus.fetch_config,
    )
    manifest_data = write_snapshot_manifest(manifest, legacy_raw)
    snapshot_id = manifest_data["snapshot"]["snapshot_id"]
    versioned_raw = tmp_path / "corpus" / "versions" / snapshot_id / "raw"
    return legacy_raw, snapshot_id, versioned_raw


# ---------------------------------------------------------------------------
# run_pipeline — stage dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_pipeline_fetch_stage_only(tmp_path):
    """run_pipeline --stage fetch only calls run_fetch."""
    corpus = _make_corpus()

    with (
        patch("doc_hub.pipeline.run_fetch", new=AsyncMock()) as mock_fetch,
        patch("doc_hub.pipeline.run_parse", new=AsyncMock()) as mock_parse,
        patch("doc_hub.pipeline.run_embed", new=AsyncMock()) as mock_embed,
        patch("doc_hub.pipeline.run_index", new=AsyncMock()) as mock_index,
    ):
        await run_pipeline(corpus, stage="fetch")
        mock_fetch.assert_called_once()
        mock_parse.assert_not_called()
        mock_embed.assert_not_called()
        mock_index.assert_not_called()


@pytest.mark.asyncio
async def test_run_pipeline_unknown_stage_raises():
    """run_pipeline raises ValueError for unknown stage values."""
    corpus = _make_corpus()

    with pytest.raises(ValueError, match="Unknown stage"):
        await run_pipeline(corpus, stage="frobnicate")


@pytest.mark.asyncio
async def test_run_pipeline_clean_wipes_corpus_dir(tmp_path):
    """run_pipeline --clean removes the corpus directory."""
    corpus = _make_corpus()
    fake_corpus_dir = tmp_path / "test"
    fake_corpus_dir.mkdir()
    (fake_corpus_dir / "raw").mkdir()
    assert fake_corpus_dir.exists()

    with (
        patch("doc_hub.pipeline.corpus_dir", return_value=fake_corpus_dir),
        patch("doc_hub.pipeline.run_fetch", new=AsyncMock()),
        patch("doc_hub.pipeline.run_parse", new=AsyncMock()),
        patch("doc_hub.pipeline.run_embed", new=AsyncMock()),
        patch("doc_hub.pipeline.run_index", new=AsyncMock()),
    ):
        await run_pipeline(corpus, stage="fetch", clean=True)

    assert not fake_corpus_dir.exists()


@pytest.mark.asyncio
async def test_run_pipeline_clean_nonexistent_dir_ok(tmp_path):
    """run_pipeline --clean with missing corpus_dir does not raise."""
    corpus = _make_corpus()
    fake_corpus_dir = tmp_path / "does_not_exist"
    assert not fake_corpus_dir.exists()

    with (
        patch("doc_hub.pipeline.corpus_dir", return_value=fake_corpus_dir),
        patch("doc_hub.pipeline.run_fetch", new=AsyncMock()),
    ):
        # Should not raise even if directory doesn't exist
        await run_pipeline(corpus, stage="fetch", clean=True)


@pytest.mark.asyncio
async def test_run_pipeline_passes_skip_download_to_run_fetch():
    """run_pipeline propagates skip_download to run_fetch."""
    corpus = _make_corpus()
    captured_kwargs = {}

    async def capture_run_fetch(c, **kwargs):
        captured_kwargs.update(kwargs)

    with patch("doc_hub.pipeline.run_fetch", side_effect=capture_run_fetch):
        await run_pipeline(corpus, stage="fetch", skip_download=True)

    assert captured_kwargs.get("skip_download") is True


@pytest.mark.asyncio
async def test_run_pipeline_passes_workers_to_run_fetch():
    """run_pipeline propagates workers to run_fetch."""
    corpus = _make_corpus()
    captured_kwargs = {}

    async def capture_run_fetch(c, **kwargs):
        captured_kwargs.update(kwargs)

    with patch("doc_hub.pipeline.run_fetch", side_effect=capture_run_fetch):
        await run_pipeline(corpus, stage="fetch", workers=8)

    assert captured_kwargs.get("workers") == 8


@pytest.mark.asyncio
async def test_run_pipeline_passes_retries_to_run_fetch():
    """run_pipeline propagates retries to run_fetch."""
    corpus = _make_corpus()
    captured_kwargs = {}

    async def capture_run_fetch(c, **kwargs):
        captured_kwargs.update(kwargs)

    with patch("doc_hub.pipeline.run_fetch", side_effect=capture_run_fetch):
        await run_pipeline(corpus, stage="fetch", retries=5)

    assert captured_kwargs.get("retries") == 5


# ---------------------------------------------------------------------------
# sync_all
# ---------------------------------------------------------------------------


def _make_index_result(inserted=0, updated=0, deleted=0, total=0):
    """Create a mock IndexResult-like object."""
    result = MagicMock()
    result.inserted = inserted
    result.updated = updated
    result.deleted = deleted
    result.total = total
    return result


@pytest.mark.asyncio
async def test_sync_all_empty_corpora():
    """sync_all returns empty dict when no enabled corpora exist."""
    mock_pool = MagicMock()

    with patch("doc_hub.db.list_corpora", new=AsyncMock(return_value=[])):
        results = await sync_all(mock_pool)

    assert results == {}


@pytest.mark.asyncio
async def test_sync_all_single_corpus_success():
    """sync_all runs pipeline for a single corpus and returns its result."""
    corpus = _make_corpus(slug="pydantic-ai")
    mock_pool = MagicMock()
    expected_result = _make_index_result(inserted=5, updated=2, deleted=0, total=100)

    with (
        patch("doc_hub.db.list_corpora", new=AsyncMock(return_value=[corpus])),
        patch("doc_hub.pipeline.run_pipeline", new=AsyncMock(return_value=expected_result)),
    ):
        results = await sync_all(mock_pool)

    assert "pydantic-ai" in results
    assert results["pydantic-ai"] is expected_result


@pytest.mark.asyncio
async def test_sync_all_multiple_corpora():
    """sync_all processes all enabled corpora and returns results for each."""
    corpus_a = _make_corpus(slug="corpus-a")
    corpus_b = _make_corpus(slug="corpus-b")
    mock_pool = MagicMock()
    result_a = _make_index_result(inserted=3, total=50)
    result_b = _make_index_result(inserted=7, total=200)

    pipeline_calls = []

    async def fake_run_pipeline(corpus, **kwargs):
        pipeline_calls.append(corpus.slug)
        if corpus.slug == "corpus-a":
            return result_a
        return result_b

    with (
        patch(
            "doc_hub.db.list_corpora",
            new=AsyncMock(return_value=[corpus_a, corpus_b]),
        ),
        patch("doc_hub.pipeline.run_pipeline", side_effect=fake_run_pipeline),
    ):
        results = await sync_all(mock_pool)

    assert set(results.keys()) == {"corpus-a", "corpus-b"}
    assert results["corpus-a"] is result_a
    assert results["corpus-b"] is result_b
    assert pipeline_calls == ["corpus-a", "corpus-b"]


@pytest.mark.asyncio
async def test_sync_all_error_isolation():
    """sync_all continues syncing remaining corpora when one fails."""
    corpus_a = _make_corpus(slug="corpus-a")
    corpus_b = _make_corpus(slug="corpus-b")
    corpus_c = _make_corpus(slug="corpus-c")
    mock_pool = MagicMock()
    result_a = _make_index_result(inserted=1, total=10)
    result_c = _make_index_result(inserted=2, total=20)

    boom = RuntimeError("corpus-b exploded")
    pipeline_calls = []

    async def fake_run_pipeline(corpus, **kwargs):
        pipeline_calls.append(corpus.slug)
        if corpus.slug == "corpus-b":
            raise boom
        if corpus.slug == "corpus-a":
            return result_a
        return result_c

    with (
        patch(
            "doc_hub.db.list_corpora",
            new=AsyncMock(return_value=[corpus_a, corpus_b, corpus_c]),
        ),
        patch("doc_hub.pipeline.run_pipeline", side_effect=fake_run_pipeline),
    ):
        results = await sync_all(mock_pool)

    # All three corpora should be in results
    assert set(results.keys()) == {"corpus-a", "corpus-b", "corpus-c"}
    # Successful corpora have their IndexResult
    assert results["corpus-a"] is result_a
    assert results["corpus-c"] is result_c
    # Failed corpus has the exception
    assert isinstance(results["corpus-b"], RuntimeError)
    assert results["corpus-b"] is boom
    # All three pipelines were attempted
    assert pipeline_calls == ["corpus-a", "corpus-b", "corpus-c"]


@pytest.mark.asyncio
async def test_sync_all_passes_pool_to_run_pipeline():
    """sync_all passes the pool argument down to run_pipeline."""
    corpus = _make_corpus(slug="test-corpus")
    mock_pool = MagicMock()
    captured_kwargs = {}

    async def capture_run_pipeline(c, **kwargs):
        captured_kwargs.update(kwargs)
        return _make_index_result()

    with (
        patch("doc_hub.db.list_corpora", new=AsyncMock(return_value=[corpus])),
        patch("doc_hub.pipeline.run_pipeline", side_effect=capture_run_pipeline),
    ):
        await sync_all(mock_pool)

    assert captured_kwargs.get("pool") is mock_pool


@pytest.mark.asyncio
async def test_sync_all_passes_embedder_to_run_pipeline():
    """sync_all passes embedder down to run_pipeline."""
    corpus = _make_corpus(slug="test-corpus")
    mock_pool = MagicMock()
    mock_embedder = MagicMock()
    captured_kwargs = {}

    async def capture_run_pipeline(c, **kwargs):
        captured_kwargs.update(kwargs)
        return _make_index_result()

    with (
        patch("doc_hub.db.list_corpora", new=AsyncMock(return_value=[corpus])),
        patch("doc_hub.pipeline.run_pipeline", side_effect=capture_run_pipeline),
    ):
        await sync_all(mock_pool, embedder=mock_embedder)

    assert captured_kwargs.get("embedder") is mock_embedder


@pytest.mark.asyncio
async def test_sync_all_passes_full_to_run_pipeline():
    """sync_all passes full=True down to run_pipeline when specified."""
    corpus = _make_corpus(slug="test-corpus")
    mock_pool = MagicMock()
    captured_kwargs = {}

    async def capture_run_pipeline(c, **kwargs):
        captured_kwargs.update(kwargs)
        return _make_index_result()

    with (
        patch("doc_hub.db.list_corpora", new=AsyncMock(return_value=[corpus])),
        patch("doc_hub.pipeline.run_pipeline", side_effect=capture_run_pipeline),
    ):
        await sync_all(mock_pool, full=True)

    assert captured_kwargs.get("full") is True


@pytest.mark.asyncio
async def test_sync_all_full_defaults_false():
    """sync_all defaults full=False."""
    corpus = _make_corpus(slug="test-corpus")
    mock_pool = MagicMock()
    captured_kwargs = {}

    async def capture_run_pipeline(c, **kwargs):
        captured_kwargs.update(kwargs)
        return _make_index_result()

    with (
        patch("doc_hub.db.list_corpora", new=AsyncMock(return_value=[corpus])),
        patch("doc_hub.pipeline.run_pipeline", side_effect=capture_run_pipeline),
    ):
        await sync_all(mock_pool)

    assert captured_kwargs.get("full") is False


@pytest.mark.asyncio
async def test_sync_all_only_syncs_enabled_corpora():
    """sync_all queries list_corpora with enabled_only=True."""
    mock_pool = MagicMock()
    list_corpora_calls = []

    async def fake_list_corpora(pool, enabled_only=True):
        list_corpora_calls.append({"pool": pool, "enabled_only": enabled_only})
        return []

    with patch("doc_hub.db.list_corpora", side_effect=fake_list_corpora):
        await sync_all(mock_pool)

    assert len(list_corpora_calls) == 1
    assert list_corpora_calls[0]["enabled_only"] is True
