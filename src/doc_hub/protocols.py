"""Plugin protocols for doc-hub.

These are structural types (typing.Protocol). Plugins do NOT inherit from
these classes — they merely need to have matching method signatures. Static
type checkers (mypy, pyright) enforce conformance at development time.

Three plugin points:
- Fetcher: downloads documentation source files
- Parser: converts raw files into Chunk objects
- Embedder: embeds text into vector representations
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from doc_hub.parse import Chunk


@runtime_checkable
class Fetcher(Protocol):
    """Protocol for fetcher plugins.

    A fetcher downloads/locates documentation source files and writes
    them to an output directory.

    Fetchers receive the full corpus config dict and an output directory.
    They return the path to a directory of files ready for parsing.
    """

    async def fetch(
        self,
        corpus_slug: str,
        fetch_config: dict[str, Any],
        output_dir: Path,
    ) -> Path:
        """Download or locate source files.

        Args:
            corpus_slug: Unique corpus identifier (for logging).
            fetch_config: Strategy-specific configuration (from JSONB).
            output_dir: Directory where fetched files should be written.
                The directory may not exist yet — the fetcher is responsible
                for calling output_dir.mkdir(parents=True, exist_ok=True)
                before writing files. For local_dir-style fetchers that
                return a pre-existing path, output_dir may be ignored.

        Returns:
            Path to the directory containing fetched files (may be
            output_dir itself, or a different path for local_dir-style
            fetchers). The returned directory must contain .md files
            ready for the parse stage.
        """
        ...


@runtime_checkable
class Parser(Protocol):
    """Protocol for parser plugins.

    A parser reads raw files from a directory and produces a list of
    Chunk objects. Parsers handle ONLY the file-to-chunks conversion.

    The core pipeline handles:
    - Chunk size optimization (merge tiny, split mega)
    - Deduplication by content hash
    - Category derivation

    Parsers should NOT perform these operations.
    """

    def parse(
        self,
        input_dir: Path,
        *,
        corpus_slug: str,
        base_url: str,
    ) -> list[Chunk]:
        """Parse source files into raw chunks.

        Args:
            input_dir: Directory containing source files to parse.
            corpus_slug: Unique corpus identifier (for logging).
            base_url: Base URL for reconstructing source URLs from
                filenames (e.g. "https://ai.pydantic.dev/"). Used by
                parsers that need to derive source_url without a
                manifest.json. The built-in markdown parser reads
                source_url from the manifest when present, so base_url
                is only a fallback. The core pipeline passes this from
                corpus.fetch_config.get("base_url", "").

        Returns:
            List of raw Chunk objects (before size optimization).

            Each Chunk must have ALL fields set:
            - source_file: str — original filename (e.g. "models__openai.md")
            - source_url: str — original URL from manifest, or "" if unknown
            - section_path: str — heading hierarchy (e.g. "Config > API Keys")
            - heading: str — the section heading text
            - heading_level: int — 1-6 (0 for preamble/no-heading content)
            - content: str — full section text including the heading line
            - start_line: int — 1-indexed line number in source file
            - end_line: int — 1-indexed last line number (inclusive)
            - char_count: int — len(content)
            - content_hash: str — hashlib.sha256(content.encode()).hexdigest()
            - category: str — MUST be "" (empty string). Category derivation
              is the core pipeline's responsibility, not the parser's.
        """
        ...


@runtime_checkable
class Embedder(Protocol):
    """Protocol for embedder plugins.

    An embedder converts text strings into dense vector representations.
    It exposes its model name and output dimensionality so the core
    pipeline can manage caching and DB schema compatibility.

    The core pipeline handles:
    - Caching (keyed by content_hash + model + dimensions)
    - L2 normalization of output vectors
    - Batching and rate-limit orchestration
    - Writing embedded_chunks.jsonl

    Embedders should NOT cache, normalize, or batch internally.
    """

    @property
    def model_name(self) -> str:
        """Unique identifier for this embedding model.

        Used as part of the cache key. Changing this invalidates
        all cached embeddings for this model.
        """
        ...

    @property
    def dimensions(self) -> int:
        """Output vector dimensionality (e.g. 768, 1536, 384)."""
        ...

    @property
    def task_type_document(self) -> str:
        """Task type hint for document embedding (e.g. 'RETRIEVAL_DOCUMENT').

        Embedding APIs that support task types use this when embedding
        chunks for storage. Return empty string if not applicable.
        """
        ...

    @property
    def task_type_query(self) -> str:
        """Task type hint for query embedding (e.g. 'RETRIEVAL_QUERY').

        Used when embedding search queries. Return empty string if
        not applicable.
        """
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into vectors.

        The core pipeline calls this with batches of pre-formatted
        embedding input strings. The embedder should NOT L2-normalize
        the output — the core pipeline handles normalization.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (one per input text).
            Each vector has length == self.dimensions.

        Raises:
            Exception: On API errors. The core pipeline handles retry
                logic around this method.
        """
        ...

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query string.

        Uses task_type_query instead of task_type_document.
        Called during search, not during indexing.

        Args:
            query: The search query to embed.

        Returns:
            Embedding vector of length == self.dimensions.
        """
        ...
