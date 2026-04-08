"""Tests for the url_filter helper shared by sitemap and llms_txt fetchers."""

from __future__ import annotations

import logging

import pytest

from doc_hub._builtins.fetchers.url_filter import (
    _compile_literal_excludes,
    apply_exclusions,
    build_exclude_filter,
)

BASE = "https://docs.example.com/"


# ---------------------------------------------------------------------------
# _compile_literal_excludes
# ---------------------------------------------------------------------------


def test_compile_literal_excludes_escapes_metacharacters():
    """Regex metacharacters in literals are escaped so they match literally."""
    pattern = _compile_literal_excludes(["a.b+c"])
    assert pattern == r"a\.b\+c"


def test_compile_literal_excludes_rewrites_trailing_slash():
    """Trailing slash becomes (?:/|$) so the bare page is also matched."""
    pattern = _compile_literal_excludes(["api/reference/"])
    assert pattern == r"api/reference(?:/|$)"


def test_compile_literal_excludes_ors_multiple():
    pattern = _compile_literal_excludes(["api/reference/", "changelog"])
    assert pattern == r"api/reference(?:/|$)|changelog"


def test_compile_literal_excludes_skips_empty_strings():
    assert _compile_literal_excludes(["", "foo", ""]) == r"foo"


def test_compile_literal_excludes_empty_list():
    assert _compile_literal_excludes([]) == ""


# ---------------------------------------------------------------------------
# build_exclude_filter — None when no config
# ---------------------------------------------------------------------------


def test_build_exclude_filter_returns_none_when_empty():
    assert build_exclude_filter(BASE) is None
    assert build_exclude_filter(BASE, url_excludes=[]) is None
    assert build_exclude_filter(BASE, url_exclude_pattern="") is None
    assert build_exclude_filter(BASE, url_excludes=[""]) is None


# ---------------------------------------------------------------------------
# build_exclude_filter — literal list behaviour
# ---------------------------------------------------------------------------


def test_literal_subdir_excludes_descendants_and_bare_page():
    excluder = build_exclude_filter(BASE, url_excludes=["api/reference/"])
    assert excluder is not None
    # Descendants match.
    assert excluder("https://docs.example.com/api/reference/users") is True
    # Bare page matches (trailing-slash rewrite).
    assert excluder("https://docs.example.com/api/reference") is True
    # Sibling subdir does not match.
    assert excluder("https://docs.example.com/api/guides/quickstart") is False
    # False-positive trap: myapi/ must NOT match (re.match is anchored).
    assert excluder("https://docs.example.com/myapi/overview") is False
    # "api" mid-path must NOT match.
    assert excluder("https://docs.example.com/guide/api/intro") is False
    # Prefix-confusion trap: api/reference-old must NOT match
    # because the trailing-slash rewrite requires /, end, or nothing.
    assert excluder("https://docs.example.com/api/reference-old") is False


def test_literal_without_trailing_slash_is_open_ended():
    """A literal without trailing slash matches any URL that starts with it."""
    excluder = build_exclude_filter(BASE, url_excludes=["changelog"])
    assert excluder is not None
    assert excluder("https://docs.example.com/changelog") is True
    assert excluder("https://docs.example.com/changelog/v2") is True
    # But "mychangelog" is not at the start of the relpath, so NOT matched.
    assert excluder("https://docs.example.com/mychangelog") is False


def test_literal_with_regex_metacharacters_is_literal():
    excluder = build_exclude_filter(BASE, url_excludes=["a.b"])
    assert excluder is not None
    assert excluder("https://docs.example.com/a.b/x") is True
    # Dot should not match an arbitrary character.
    assert excluder("https://docs.example.com/aXb/x") is False


# ---------------------------------------------------------------------------
# build_exclude_filter — raw regex pattern
# ---------------------------------------------------------------------------


def test_raw_pattern_versioned_paths():
    excluder = build_exclude_filter(BASE, url_exclude_pattern=r"v\d+/")
    assert excluder is not None
    assert excluder("https://docs.example.com/v1/legacy/auth") is True
    assert excluder("https://docs.example.com/v10/legacy/auth") is True
    # `v2` mid-path must NOT match (re.match is anchored at start).
    assert excluder("https://docs.example.com/changelog/v2") is False


def test_raw_pattern_end_anchor_excludes_exact_page_only():
    """User can write `changelog$` to exclude only the exact page."""
    excluder = build_exclude_filter(BASE, url_exclude_pattern=r"changelog$")
    assert excluder is not None
    assert excluder("https://docs.example.com/changelog") is True
    assert excluder("https://docs.example.com/changelog/v2") is False


def test_invalid_regex_raises_value_error():
    with pytest.raises(ValueError, match="Invalid url_exclude regex"):
        build_exclude_filter(BASE, url_exclude_pattern=r"(unclosed")


# ---------------------------------------------------------------------------
# build_exclude_filter — combining literals and raw pattern
# ---------------------------------------------------------------------------


def test_literals_and_pattern_are_ord():
    excluder = build_exclude_filter(
        BASE,
        url_excludes=["api/reference/"],
        url_exclude_pattern=r"v\d+/",
    )
    assert excluder is not None
    assert excluder("https://docs.example.com/api/reference/users") is True
    assert excluder("https://docs.example.com/v1/legacy/auth") is True
    assert excluder("https://docs.example.com/intro") is False


# ---------------------------------------------------------------------------
# build_exclude_filter — base_url edge cases
# ---------------------------------------------------------------------------


def test_bare_base_url_matches_empty_relpath():
    """A URL equal to the base_url (with or without trailing slash) has
    an empty relpath, so only a pattern that matches empty string will
    exclude it."""
    excluder = build_exclude_filter(BASE, url_exclude_pattern=r".*")
    assert excluder is not None
    assert excluder("https://docs.example.com/") is True
    assert excluder("https://docs.example.com") is True


def test_bare_base_url_not_excluded_by_path_pattern():
    excluder = build_exclude_filter(BASE, url_excludes=["api/"])
    assert excluder is not None
    assert excluder("https://docs.example.com/") is False
    assert excluder("https://docs.example.com") is False


def test_base_url_without_trailing_slash_is_normalized():
    excluder = build_exclude_filter(
        "https://docs.example.com",  # no trailing slash
        url_excludes=["api/"],
    )
    assert excluder is not None
    assert excluder("https://docs.example.com/api/reference") is True
    assert excluder("https://docs.example.com/intro") is False


def test_base_url_with_path_prefix():
    """When base_url has its own path, relpath is computed relative to it."""
    excluder = build_exclude_filter(
        "https://docs.example.com/v2/",
        url_excludes=["api/"],
    )
    assert excluder is not None
    assert excluder("https://docs.example.com/v2/api/reference") is True
    assert excluder("https://docs.example.com/v2/intro") is False


def test_cross_host_url_falls_back_to_full_url_match_and_warns(caplog):
    excluder = build_exclude_filter(BASE, url_excludes=["https://other.example.com/"])
    assert excluder is not None
    with caplog.at_level(logging.WARNING, logger="doc_hub._builtins.fetchers.url_filter"):
        assert excluder("https://other.example.com/page") is True
    # Exactly one warning, even if called again.
    with caplog.at_level(logging.WARNING, logger="doc_hub._builtins.fetchers.url_filter"):
        excluder("https://other.example.com/other")
    warnings = [r for r in caplog.records if "does not share base_url" in r.getMessage()]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# apply_exclusions — list-level helper
# ---------------------------------------------------------------------------


def test_apply_exclusions_preserves_order_and_counts_dropped():
    urls = [
        "https://docs.example.com/intro",
        "https://docs.example.com/api/reference/users",
        "https://docs.example.com/api/guides/quickstart",
        "https://docs.example.com/v1/legacy/auth",
        "https://docs.example.com/changelog",
    ]
    kept, dropped = apply_exclusions(
        urls,
        BASE,
        url_excludes=["api/reference/", "changelog"],
        url_exclude_pattern=r"v\d+/",
    )
    assert kept == [
        "https://docs.example.com/intro",
        "https://docs.example.com/api/guides/quickstart",
    ]
    assert dropped == 3


def test_apply_exclusions_noop_when_no_config():
    urls = ["https://docs.example.com/a", "https://docs.example.com/b"]
    kept, dropped = apply_exclusions(urls, BASE)
    assert kept is urls  # same object — no allocation when nothing to do
    assert dropped == 0
