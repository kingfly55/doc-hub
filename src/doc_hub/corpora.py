"""Corpus lookup and validation helpers."""
from __future__ import annotations

from difflib import SequenceMatcher, get_close_matches


def _normalized(value: str) -> str:
    value = value.lower()
    aliases = {"gastown": "gascity"}
    for old, new in aliases.items():
        value = value.replace(old, new)
    return "".join(char for char in value if char.isalnum())


def format_corpus_suggestions(slug: str, corpora) -> str:
    candidates = [corpus.slug for corpus in corpora]
    names_by_slug = {corpus.slug: corpus.name for corpus in corpora}
    matches = get_close_matches(slug, candidates, n=3, cutoff=0.45)

    normalized_slug = _normalized(slug)
    scored = sorted(
        (
            (SequenceMatcher(None, normalized_slug, _normalized(candidate)).ratio(), candidate)
            for candidate in candidates
        ),
        reverse=True,
    )
    for score, candidate in scored:
        if score >= 0.55 and candidate not in matches:
            matches.append(candidate)
        if len(matches) >= 3:
            break

    if not matches:
        return ""

    suggestions = ", ".join(f"{names_by_slug[match]} [{match}]" for match in matches)
    return f" Did you mean: {suggestions}?"


async def describe_corpus_problem(pool, slug: str) -> str | None:
    from doc_hub.db import get_corpus, list_corpora

    corpus = await get_corpus(pool, slug)
    if corpus is None:
        corpora = await list_corpora(pool, enabled_only=True)
        return f"Corpus '{slug}' not found.{format_corpus_suggestions(slug, corpora)}"

    chunk_count = await pool.fetchval("SELECT COUNT(*) FROM doc_chunks WHERE corpus_id = $1", slug)
    document_count = await pool.fetchval("SELECT COUNT(*) FROM doc_documents WHERE corpus_id = $1", slug)
    if int(chunk_count or 0) == 0 and int(document_count or 0) == 0:
        return f"Corpus '{slug}' is registered but empty. It has no indexed chunks or documents; run or repair the pipeline before browsing, reading, or searching it."

    return None


async def validate_corpus_available(pool, slug: str) -> None:
    problem = await describe_corpus_problem(pool, slug)
    if problem is not None:
        raise ValueError(problem)


async def validate_corpora_available(pool, slugs: list[str]) -> None:
    for slug in slugs:
        await validate_corpus_available(pool, slug)
