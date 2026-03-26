"""Corpus model for doc-hub.

This module defines:
- Corpus: dataclass representing a registered documentation corpus
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Corpus:
    """Represents a registered documentation corpus.

    Constructed from a ``doc_corpora`` DB row via :meth:`from_row`.

    Attributes:
        slug: Unique identifier (primary key in ``doc_corpora``).
        name: Human-readable name for the corpus.
        fetch_strategy: Plugin name used to fetch source documents (e.g. "llms_txt").
        fetch_config: Strategy-specific configuration (stored as JSONB).
        parser: Parser plugin name (default: 'markdown').
        embedder: Embedder plugin name (default: 'gemini').
        enabled: Whether this corpus participates in automated syncs.
        last_indexed_at: ISO timestamp of the most recent index run, or None.
        total_chunks: Chunk count from the last index run.
    """

    slug: str
    name: str
    fetch_strategy: str          # plugin name (was FetchStrategy enum until M5)
    fetch_config: dict[str, Any]
    parser: str = "markdown"    # parser plugin name
    embedder: str = "gemini"    # embedder plugin name
    enabled: bool = True
    last_indexed_at: str | None = None
    total_chunks: int = 0

    @classmethod
    def from_row(cls, row: Any) -> "Corpus":
        """Construct a Corpus from an asyncpg Record or a plain dict.

        asyncpg Records support index access (row["key"]) but NOT .get()
        directly.  We handle both asyncpg Records and plain dicts by
        detecting whether ``.get()`` is available via hasattr and falling
        back to direct key access with explicit defaults when it isn't.

        The ``embedding_model`` column may still exist in some deployments
        (removed from DDL in M4 but no migration to drop it). If present,
        it is silently ignored.

        Args:
            row: An asyncpg Record (or any dict-like mapping) with the
                 columns returned by ``SELECT * FROM doc_corpora``.

        Returns:
            A fully-constructed :class:`Corpus` instance.
        """
        if hasattr(row, "get"):
            # Plain dict — use .get() for optional columns
            parser = row.get("parser") or "markdown"
            embedder = row.get("embedder") or "gemini"
            enabled = row.get("enabled")
            enabled = enabled if enabled is not None else True
            last_indexed_at = row.get("last_indexed_at")
            total_chunks = row.get("total_chunks") or 0
        else:
            # asyncpg Record — check keys() for optional columns
            row_keys = set(row.keys()) if hasattr(row, "keys") else set()
            parser = (row["parser"] if "parser" in row_keys else None) or "markdown"
            embedder = (row["embedder"] if "embedder" in row_keys else None) or "gemini"
            enabled = row["enabled"]
            last_indexed_at = row["last_indexed_at"]
            total_chunks = row["total_chunks"] or 0

        return cls(
            slug=row["slug"],
            name=row["name"],
            fetch_strategy=row["fetch_strategy"],  # plain str
            fetch_config=row["fetch_config"],
            parser=parser,
            embedder=embedder,
            enabled=enabled,
            last_indexed_at=last_indexed_at,
            total_chunks=total_chunks,
        )
