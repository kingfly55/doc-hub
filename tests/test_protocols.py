"""Tests for doc_hub.protocols — structural typing contracts for plugins."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from doc_hub.parse import Chunk
from doc_hub.protocols import Embedder, Fetcher, Parser


# ---------------------------------------------------------------------------
# Conforming mocks
# ---------------------------------------------------------------------------


class _MockFetcher:
    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        return output_dir


class _MockParser:
    def parse(
        self,
        input_dir: Path,
        *,
        corpus_slug: str,
        base_url: str,
    ) -> list[Chunk]:
        return []


class _MockEmbedder:
    @property
    def model_name(self) -> str:
        return "mock-model"

    @property
    def dimensions(self) -> int:
        return 768

    @property
    def task_type_document(self) -> str:
        return "RETRIEVAL_DOCUMENT"

    @property
    def task_type_query(self) -> str:
        return "RETRIEVAL_QUERY"

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dimensions for _ in texts]

    async def embed_query(self, query: str) -> list[float]:
        return [0.0] * self.dimensions


# ---------------------------------------------------------------------------
# Protocol conformance tests
# ---------------------------------------------------------------------------


def test_fetcher_protocol_conformance() -> None:
    assert isinstance(_MockFetcher(), Fetcher)


def test_parser_protocol_conformance() -> None:
    assert isinstance(_MockParser(), Parser)


def test_embedder_protocol_conformance() -> None:
    assert isinstance(_MockEmbedder(), Embedder)


def test_non_conforming_rejected() -> None:
    assert not isinstance(object(), Fetcher)
    assert not isinstance(object(), Parser)
    assert not isinstance(object(), Embedder)


def test_missing_method_rejected() -> None:
    class _NoFetch:
        pass

    assert not isinstance(_NoFetch(), Fetcher)


def test_chunk_importable() -> None:
    from doc_hub.parse import Chunk  # noqa: F401 (re-import for test isolation)

    assert Chunk is not None
