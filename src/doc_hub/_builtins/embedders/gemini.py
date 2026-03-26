"""Built-in Gemini embedder plugin for doc-hub.

Uses the Google Generative AI (Gemini) API for text embedding.
Model: gemini-embedding-001 (768 dimensions by default).

Entry point name: "gemini"

Environment:
    GEMINI_API_KEY — required. Get a free key at https://aistudio.google.com/apikey

Rate limits (free tier):
    - 100 requests/minute
    - ~1000 requests per rolling window (~85 min)

The plugin handles retry logic for rate limit (429) and server (503)
errors with smart discrimination between per-minute and per-day quotas.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any

log = logging.getLogger(__name__)

# Rate limit wait times (Gemini free tier)
_PER_MINUTE_WAIT = 65.0   # per-minute quota
_PER_DAY_WAIT = 300.0     # per-day/rolling quota

_DEFAULT_MODEL = "gemini-embedding-001"
_DEFAULT_DIM = 768
_DEFAULT_MAX_RETRIES = 20


class GeminiEmbedder:
    """Embedder plugin using the Gemini embedding API.

    Entry point name: "gemini"

    Configuration via environment variables:
        GEMINI_API_KEY: API key (required)
        GEMINI_EMBEDDING_MODEL: Model name (default: gemini-embedding-001)
        GEMINI_EMBEDDING_DIM: Output dimensions (default: 768)

    The client is created lazily on first use to avoid import-time
    side effects and to respect env vars set after import.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._model = os.environ.get("GEMINI_EMBEDDING_MODEL", _DEFAULT_MODEL)
        self._dimensions = int(os.environ.get("GEMINI_EMBEDDING_DIM", str(_DEFAULT_DIM)))
        self._max_retries = _DEFAULT_MAX_RETRIES

    def _get_client(self) -> Any:
        """Lazily create the Gemini client."""
        if self._client is None:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "GEMINI_API_KEY environment variable not set. "
                    "Get a free key at https://aistudio.google.com/apikey"
                )
            import google.genai as genai  # type: ignore[import]
            self._client = genai.Client(api_key=api_key)
        return self._client

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def task_type_document(self) -> str:
        return "RETRIEVAL_DOCUMENT"

    @property
    def task_type_query(self) -> str:
        return "RETRIEVAL_QUERY"

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using the Gemini API.

        Handles retry logic with smart rate-limit discrimination:
        - PerMinute (429): wait 65s
        - PerDay (429): wait 300s
        - Other errors: exponential backoff

        Args:
            texts: List of text strings to embed.

        Returns:
            List of raw embedding vectors (NOT L2-normalized).
        """
        from google.genai import types as genai_types  # type: ignore[import]

        client = self._get_client()
        for attempt in range(self._max_retries):
            try:
                response = await asyncio.to_thread(
                    client.models.embed_content,
                    model=f"models/{self._model}",
                    contents=texts,
                    config=genai_types.EmbedContentConfig(
                        task_type=self.task_type_document,
                        output_dimensionality=self._dimensions,
                    ),
                )
                return [emb.values for emb in response.embeddings]
            except Exception as e:
                if attempt == self._max_retries - 1:
                    raise
                wait = self._compute_wait(e, attempt)
                log.warning(
                    "Embed batch attempt %d/%d failed: %s. Retrying in %.1fs",
                    attempt + 1, self._max_retries, e, wait,
                )
                await asyncio.sleep(wait)

        raise RuntimeError("embed_batch: unreachable")

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query using RETRIEVAL_QUERY task type.

        Uses simpler retry logic (4 attempts, exponential backoff)
        since queries are single requests, not batch operations.

        Args:
            query: Search query string.

        Returns:
            Raw embedding vector (NOT L2-normalized).
        """
        from google.genai import types as genai_types  # type: ignore[import]

        client = self._get_client()
        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                resp = await client.aio.models.embed_content(
                    model=f"models/{self._model}",
                    contents=[query],
                    config=genai_types.EmbedContentConfig(
                        task_type=self.task_type_query,
                        output_dimensionality=self._dimensions,
                    ),
                )
                if not resp.embeddings:
                    raise RuntimeError(
                        f"Gemini returned empty embeddings for query: {query!r}"
                    )
                return resp.embeddings[0].values
            except Exception as e:
                if attempt == max_attempts - 1:
                    raise
                err_str = str(e)
                is_retryable = any(
                    s in err_str for s in
                    ("429", "503", "RESOURCE_EXHAUSTED", "ServiceUnavailable")
                )
                if is_retryable:
                    wait = 1 * (2 ** attempt)
                    log.warning(
                        "Embed query attempt %d/%d failed: %s. Retrying in %ds",
                        attempt + 1, max_attempts, e, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    raise

        raise RuntimeError("embed_query: unreachable")

    @staticmethod
    def _compute_wait(exc: Exception, attempt: int) -> float:
        """Compute retry wait time based on error type."""
        err_str = str(exc)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            if "PerDay" in err_str:
                return _PER_DAY_WAIT
            return _PER_MINUTE_WAIT
        return (2 ** attempt) + random.uniform(0, 1)
