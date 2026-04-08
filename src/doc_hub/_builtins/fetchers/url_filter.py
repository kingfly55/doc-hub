"""URL exclusion filter shared by sitemap and llms_txt fetchers.

Matching is anchored at the start of the URL's path *relative to* a corpus
``base_url`` — the same transform used for filename derivation. For a URL
``https://docs.example.com/api/reference/users`` with base
``https://docs.example.com/``, the matched string is ``api/reference/users``.

Two config keys feed the filter:

* ``url_excludes`` — list of literal path strings. Each is ``re.escape``'d
  and joined with ``|``. A trailing ``/`` on a literal is rewritten to
  ``(?:/|$)`` so that ``api/reference/`` matches both the bare page
  ``api/reference`` and any sub-page ``api/reference/users``, but not
  ``api/reference-old``.
* ``url_exclude_pattern`` — raw regex string, used as-is.

If both are provided, they are OR'd together. A URL is excluded when the
combined regex matches the corpus-relative path with ``re.match`` (anchored
at the start, open-ended at the end).

Caveat: if a URL does not share the configured ``base_url`` prefix (e.g. a
sitemap that lists URLs from a different host), the relative path falls back
to the full URL string and a warning is logged once.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

log = logging.getLogger(__name__)


def _compile_literal_excludes(literals: list[str]) -> str:
    """Compile a list of literal path strings into an alternation regex.

    Empty strings are skipped. Trailing ``/`` is rewritten to ``(?:/|$)``
    so users don't have to think about whether the bare page exists.
    """
    parts: list[str] = []
    for lit in literals:
        if not lit:
            continue
        if lit.endswith("/"):
            stem = lit.rstrip("/")
            parts.append(re.escape(stem) + r"(?:/|$)")
        else:
            parts.append(re.escape(lit))
    return "|".join(parts)


def build_exclude_filter(
    base_url: str,
    url_excludes: list[str] | None = None,
    url_exclude_pattern: str | None = None,
) -> Callable[[str], bool] | None:
    """Build a predicate ``(url) -> bool`` that returns True when the URL
    should be excluded.

    Returns ``None`` when no exclusion config is provided, so callers can
    skip the filter entirely without per-URL overhead.

    Raises ``ValueError`` if the resulting regex is invalid.
    """
    parts: list[str] = []
    if url_excludes:
        literal_pattern = _compile_literal_excludes(url_excludes)
        if literal_pattern:
            parts.append(literal_pattern)
    if url_exclude_pattern:
        parts.append(url_exclude_pattern)
    if not parts:
        return None

    combined = "|".join(f"(?:{p})" for p in parts)
    try:
        regex = re.compile(combined)
    except re.error as exc:
        raise ValueError(f"Invalid url_exclude regex {combined!r}: {exc}") from exc

    base_with_slash = base_url.rstrip("/") + "/"
    base_no_slash = base_url.rstrip("/")
    warned = False

    def is_excluded(url: str) -> bool:
        nonlocal warned
        if url.startswith(base_with_slash):
            relpath = url[len(base_with_slash):].strip("/")
        elif url == base_no_slash:
            relpath = ""
        else:
            if not warned:
                warned = True
                log.warning(
                    "URL %r does not share base_url %r — exclusion patterns "
                    "will be matched against the full URL for this and similar entries",
                    url, base_url,
                )
            relpath = url.strip("/")
        return regex.match(relpath) is not None

    return is_excluded


def apply_exclusions(
    urls: list[str],
    base_url: str,
    url_excludes: list[str] | None = None,
    url_exclude_pattern: str | None = None,
) -> tuple[list[str], int]:
    """Filter ``urls`` and return ``(kept, dropped_count)``.

    Order is preserved. When no exclusion config is set, returns the input
    list unchanged with a dropped count of 0.
    """
    excluder = build_exclude_filter(base_url, url_excludes, url_exclude_pattern)
    if excluder is None:
        return urls, 0
    kept: list[str] = []
    dropped = 0
    for url in urls:
        if excluder(url):
            dropped += 1
        else:
            kept.append(url)
    return kept, dropped
