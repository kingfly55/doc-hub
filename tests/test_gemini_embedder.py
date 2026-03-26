"""Tests for doc_hub._builtins.embedders.gemini.GeminiEmbedder.

Unit tests only — Gemini API calls are mocked. No network access required.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_hub._builtins.embedders.gemini import GeminiEmbedder, _PER_MINUTE_WAIT, _PER_DAY_WAIT
from doc_hub.protocols import Embedder


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_gemini_embedder_conforms_to_embedder_protocol():
    """GeminiEmbedder must be recognized as an Embedder by isinstance check."""
    e = GeminiEmbedder()
    assert isinstance(e, Embedder), "GeminiEmbedder does not match Embedder protocol"


def test_gemini_embedder_model_name_default():
    """GeminiEmbedder.model_name defaults to 'gemini-embedding-001'."""
    e = GeminiEmbedder()
    assert e.model_name == "gemini-embedding-001"


def test_gemini_embedder_dimensions_default():
    """GeminiEmbedder.dimensions defaults to 768."""
    e = GeminiEmbedder()
    assert e.dimensions == 768


def test_gemini_embedder_task_type_document():
    """GeminiEmbedder.task_type_document is 'RETRIEVAL_DOCUMENT'."""
    e = GeminiEmbedder()
    assert e.task_type_document == "RETRIEVAL_DOCUMENT"


def test_gemini_embedder_task_type_query():
    """GeminiEmbedder.task_type_query is 'RETRIEVAL_QUERY'."""
    e = GeminiEmbedder()
    assert e.task_type_query == "RETRIEVAL_QUERY"


# ---------------------------------------------------------------------------
# Environment variable configuration
# ---------------------------------------------------------------------------


def test_gemini_embedder_model_from_env(monkeypatch):
    """GeminiEmbedder picks up GEMINI_EMBEDDING_MODEL env var."""
    monkeypatch.setenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-002")
    e = GeminiEmbedder()
    assert e.model_name == "gemini-embedding-002"


def test_gemini_embedder_dim_from_env(monkeypatch):
    """GeminiEmbedder picks up GEMINI_EMBEDDING_DIM env var."""
    monkeypatch.setenv("GEMINI_EMBEDDING_DIM", "1536")
    e = GeminiEmbedder()
    assert e.dimensions == 1536


# ---------------------------------------------------------------------------
# Lazy client creation
# ---------------------------------------------------------------------------


def test_get_client_raises_without_api_key(monkeypatch):
    """_get_client raises RuntimeError if GEMINI_API_KEY is not set."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    e = GeminiEmbedder()
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        e._get_client()


def test_get_client_creates_client_lazily(monkeypatch):
    """_get_client creates the client only when first called."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()
    assert e._client is None  # Not created at init time

    mock_client = MagicMock()
    with patch("google.genai.Client", return_value=mock_client):
        client = e._get_client()

    assert client is mock_client
    assert e._client is mock_client  # cached after first call


def test_get_client_reuses_existing_client(monkeypatch):
    """_get_client returns cached client on subsequent calls."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()

    mock_client = MagicMock()
    with patch("google.genai.Client", return_value=mock_client) as mock_constructor:
        e._get_client()
        e._get_client()  # second call

    # Client constructor should be called only once
    assert mock_constructor.call_count == 1


# ---------------------------------------------------------------------------
# embed_batch() — basic functionality
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_batch_returns_embeddings(monkeypatch):
    """embed_batch returns a list of embedding vectors on success."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()

    fake_embedding = [0.1] * 768
    mock_emb = MagicMock()
    mock_emb.values = fake_embedding
    mock_response = MagicMock()
    mock_response.embeddings = [mock_emb]

    mock_client = MagicMock()

    async def mock_to_thread(fn, *args, **kwargs):
        return mock_response

    e._client = mock_client

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await e.embed_batch(["hello world"])

    assert result == [fake_embedding]


@pytest.mark.asyncio
async def test_embed_batch_uses_retrieval_document_task_type(monkeypatch):
    """embed_batch uses RETRIEVAL_DOCUMENT task type."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()

    fake_embedding = [0.1] * 768
    mock_emb = MagicMock()
    mock_emb.values = fake_embedding
    mock_response = MagicMock()
    mock_response.embeddings = [mock_emb]

    captured_config = []

    async def mock_to_thread(fn, *args, **kwargs):
        # Extract the config kwarg
        captured_config.append(kwargs.get("config"))
        return mock_response

    e._client = MagicMock()

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        await e.embed_batch(["hello"])

    assert len(captured_config) == 1
    assert captured_config[0].task_type == "RETRIEVAL_DOCUMENT"


# ---------------------------------------------------------------------------
# embed_batch() — retry logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_batch_retry_per_minute_waits_65s(monkeypatch):
    """embed_batch waits _PER_MINUTE_WAIT seconds for PerMinute rate limit errors."""
    assert _PER_MINUTE_WAIT == 65.0

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()
    e._client = MagicMock()

    call_count = 0
    sleep_calls = []

    async def mock_to_thread(fn, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("429 RESOURCE_EXHAUSTED: PerMinute quota exceeded")
        mock_emb = MagicMock()
        mock_emb.values = [0.1] * 768
        mock_resp = MagicMock()
        mock_resp.embeddings = [mock_emb]
        return mock_resp

    async def mock_sleep(secs):
        sleep_calls.append(secs)

    with (
        patch("asyncio.to_thread", side_effect=mock_to_thread),
        patch("doc_hub._builtins.embedders.gemini.asyncio.sleep", side_effect=mock_sleep),
    ):
        result = await e.embed_batch(["text"])

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == _PER_MINUTE_WAIT


@pytest.mark.asyncio
async def test_embed_batch_retry_per_day_waits_300s(monkeypatch):
    """embed_batch waits _PER_DAY_WAIT seconds for PerDay rate limit errors."""
    assert _PER_DAY_WAIT == 300.0

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()
    e._client = MagicMock()

    call_count = 0
    sleep_calls = []

    async def mock_to_thread(fn, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("429 RESOURCE_EXHAUSTED: PerDay quota exceeded")
        mock_emb = MagicMock()
        mock_emb.values = [0.1] * 768
        mock_resp = MagicMock()
        mock_resp.embeddings = [mock_emb]
        return mock_resp

    async def mock_sleep(secs):
        sleep_calls.append(secs)

    with (
        patch("asyncio.to_thread", side_effect=mock_to_thread),
        patch("doc_hub._builtins.embedders.gemini.asyncio.sleep", side_effect=mock_sleep),
    ):
        result = await e.embed_batch(["text"])

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == _PER_DAY_WAIT


@pytest.mark.asyncio
async def test_embed_batch_max_retries(monkeypatch):
    """embed_batch raises after _DEFAULT_MAX_RETRIES=20 attempts."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()
    e._client = MagicMock()

    call_count = 0

    async def mock_to_thread(fn, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise Exception("Always fail")

    async def mock_sleep(secs):
        pass

    with (
        patch("asyncio.to_thread", side_effect=mock_to_thread),
        patch("doc_hub._builtins.embedders.gemini.asyncio.sleep", side_effect=mock_sleep),
    ):
        with pytest.raises(Exception, match="Always fail"):
            await e.embed_batch(["text"])

    assert call_count == 20


# ---------------------------------------------------------------------------
# embed_query() — basic functionality
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_query_returns_embedding(monkeypatch):
    """embed_query returns an embedding vector on success."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()

    fake_embedding = [0.5] * 768
    mock_emb = MagicMock()
    mock_emb.values = fake_embedding
    mock_resp = MagicMock()
    mock_resp.embeddings = [mock_emb]

    mock_client = MagicMock()
    mock_client.aio = MagicMock()
    mock_client.aio.models = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(return_value=mock_resp)
    e._client = mock_client

    result = await e.embed_query("test query")
    assert result == fake_embedding


@pytest.mark.asyncio
async def test_embed_query_uses_retrieval_query_task_type(monkeypatch):
    """embed_query uses RETRIEVAL_QUERY task type."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()

    fake_embedding = [0.5] * 768
    mock_emb = MagicMock()
    mock_emb.values = fake_embedding
    mock_resp = MagicMock()
    mock_resp.embeddings = [mock_emb]

    mock_client = MagicMock()
    mock_client.aio = MagicMock()
    mock_client.aio.models = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(return_value=mock_resp)
    e._client = mock_client

    await e.embed_query("test query")

    call_kwargs = mock_client.aio.models.embed_content.call_args
    config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
    assert config.task_type == "RETRIEVAL_QUERY"


@pytest.mark.asyncio
async def test_embed_query_raises_on_empty_embeddings(monkeypatch):
    """embed_query raises RuntimeError if API returns empty embeddings."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()

    mock_resp = MagicMock()
    mock_resp.embeddings = []

    mock_client = MagicMock()
    mock_client.aio = MagicMock()
    mock_client.aio.models = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(return_value=mock_resp)
    e._client = mock_client

    with pytest.raises(RuntimeError, match="empty embeddings"):
        await e.embed_query("test")


@pytest.mark.asyncio
async def test_embed_query_retries_on_429(monkeypatch):
    """embed_query retries on 429 rate limit errors."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()

    fake_embedding = [0.5] * 768
    mock_emb = MagicMock()
    mock_emb.values = fake_embedding
    mock_resp = MagicMock()
    mock_resp.embeddings = [mock_emb]

    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("429: RESOURCE_EXHAUSTED")
        return mock_resp

    mock_client = MagicMock()
    mock_client.aio = MagicMock()
    mock_client.aio.models = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(side_effect=side_effect)
    e._client = mock_client

    with patch("doc_hub._builtins.embedders.gemini.asyncio.sleep", new_callable=AsyncMock):
        result = await e.embed_query("test")

    assert call_count == 3
    assert result == fake_embedding


@pytest.mark.asyncio
async def test_embed_query_non_retryable_error_raises_immediately(monkeypatch):
    """embed_query does not retry on non-rate-limit errors."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    e = GeminiEmbedder()

    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise ValueError("Bad API key")

    mock_client = MagicMock()
    mock_client.aio = MagicMock()
    mock_client.aio.models = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(side_effect=side_effect)
    e._client = mock_client

    with pytest.raises(ValueError, match="Bad API key"):
        await e.embed_query("test")

    assert call_count == 1


# ---------------------------------------------------------------------------
# _compute_wait() — rate limit classification
# ---------------------------------------------------------------------------


def test_compute_wait_per_minute():
    """_compute_wait returns _PER_MINUTE_WAIT for PerMinute 429 errors."""
    exc = Exception("429 RESOURCE_EXHAUSTED: PerMinute limit")
    wait = GeminiEmbedder._compute_wait(exc, attempt=0)
    assert wait == _PER_MINUTE_WAIT


def test_compute_wait_per_day():
    """_compute_wait returns _PER_DAY_WAIT for PerDay 429 errors."""
    exc = Exception("429 RESOURCE_EXHAUSTED: PerDay limit exceeded")
    wait = GeminiEmbedder._compute_wait(exc, attempt=0)
    assert wait == _PER_DAY_WAIT


def test_compute_wait_exponential_backoff():
    """_compute_wait returns exponential backoff for non-rate-limit errors."""
    exc = Exception("Network error")
    wait = GeminiEmbedder._compute_wait(exc, attempt=0)
    # For attempt=0: 2**0 + jitter = 1 + jitter, so between 1.0 and 2.0
    assert 1.0 <= wait < 2.0


def test_compute_wait_not_per_minute_or_per_day():
    """Non-rate-limit errors don't get _PER_MINUTE_WAIT or _PER_DAY_WAIT."""
    exc = Exception("Network error")
    wait = GeminiEmbedder._compute_wait(exc, attempt=0)
    assert wait != _PER_MINUTE_WAIT
    assert wait != _PER_DAY_WAIT
