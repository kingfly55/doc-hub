# LLM Cleaning Step Design

**Date:** 2026-03-28
**Module:** `src/doc_hub/clean.py`
**Approach:** Standalone cleaning module (Approach A)

## Purpose

Add an LLM-based cleaning step that strips navigation junk, footers, breadcrumbs, and other artifacts from the markdown pages returned by Jina Reader. The cleaning is:

1. **Opt-in during fetch** — when `clean: true` is set in a corpus's `fetch_config`, fetched pages are automatically cleaned before being written to disk.
2. **Standalone via CLI** — `doc-hub pipeline clean <corpus-slug>` cleans an already-fetched corpus. Running this command also makes cleaning sticky for future fetches by writing `clean: true` into the corpus's `fetch_config` in the database.
3. **Incremental** — only files whose content has changed since last clean are re-processed, tracked via a `clean_hash` field in the manifest.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DOC_HUB_CLEAN_MODEL` | Yes (when cleaning) | — | Model slug for the OpenAI-compatible API |
| `DOC_HUB_CLEAN_API_KEY` | Yes (when cleaning) | — | API key for the cleaning LLM endpoint |
| `DOC_HUB_CLEAN_BASE_URL` | Yes (when cleaning) | — | Base URL for the OpenAI-compatible API |
| `DOC_HUB_CLEAN_PROMPT` | No | Built-in default | System prompt for the cleaning LLM |

All four are only required when cleaning is actually triggered (either via `pipeline clean` or `clean: true` in fetch_config). Fetching without cleaning works as before with no new env vars.

## Default Cleaning Prompt

When `DOC_HUB_CLEAN_PROMPT` is not set, the following built-in prompt is used:

> You are a web content extractor. You will be given raw markdown scraped from a webpage. Your job is to return ONLY the main page content as clean markdown.
>
> ## What to strip
> - Navigation menus, sidebars, and breadcrumbs
> - Search bars, theme toggles, and UI chrome
> - Footer content (copyright, attributions, donation links)
> - "Links/Buttons" reference sections listing URLs by index
> - Scraping artifacts like 【1†label†url】 markers
> - Table of contents that merely re-list headings
> - Previous/Next page navigation links
> - Repeated or duplicated menu content
>
> ## What to preserve
> - The page title as a single top-level heading
> - All body text, paragraphs, and explanations — verbatim
> - Tables that are part of the content (not navigation)
> - Code blocks and inline code in full
> - Section headings within the main content
> - Tips, notes, warnings, and callouts
> - Images or image references
> - Long lists and enumerations — in full
>
> ## Critical output rules
> 1. Return ONLY the cleaned markdown. Nothing else.
> 2. No preamble, postamble, or meta-commentary.
> 3. Do NOT wrap in markdown code fences.
> 4. Do NOT summarize, paraphrase, or rewrite.
> 5. Do NOT add, invent, or infer content.
> 6. Do NOT reorder sections or change formatting.
> 7. Do NOT truncate with "..." — reproduce in full.
> 8. If ambiguous, keep it. Err on inclusion.

## Architecture

### New module: `src/doc_hub/clean.py`

Core functions:

- `clean_markdown(content: str, model: str, api_key: str, base_url: str, prompt: str) -> str` — sends a single markdown document to the LLM and returns the cleaned version. Uses `openai.AsyncOpenAI`.
- `clean_corpus(output_dir: Path, manifest: dict, workers: int) -> list[CleanResult]` — walks `.md` files, compares `content_hash` vs `clean_hash` in manifest, cleans only changed files with bounded concurrency.
- `get_clean_config() -> CleanConfig` — reads env vars, validates, returns a config dataclass. Raises `ValueError` if required vars are missing.

### Manifest changes

The per-file entries in `manifest.json` gain a `clean_hash` field:

```json
{
  "filename": "getting-started.md",
  "url": "https://example.com/getting-started",
  "success": true,
  "content_hash": "abc123...",
  "clean_hash": "abc123..."
}
```

- `clean_hash` equals the `content_hash` at the time the file was last cleaned.
- A file needs re-cleaning when `content_hash != clean_hash` (or `clean_hash` is null).
- After cleaning, `clean_hash` is set to the current `content_hash`.

The `write_manifest` function in `llms_txt.py` will accept optional `clean_hashes` to merge into the output.

### CLI: `doc-hub pipeline clean <corpus-slug>`

1. Resolve corpus from DB by slug.
2. Validate cleaning env vars are set.
3. Load manifest from the corpus output directory.
4. Call `clean_corpus()` on files where `content_hash != clean_hash`.
5. Update manifest with new `clean_hash` values.
6. Set `clean: true` in the corpus's `fetch_config` and persist to DB (makes future fetches auto-clean).

### Inline fetch cleaning (sitemap fetcher)

After `_download_all_via_jina` returns results, if `fetch_config.get("clean")` is true:

1. Validate cleaning env vars.
2. Call `clean_corpus()` on all successfully downloaded files.
3. Merge `clean_hash` values into the manifest before writing.

### Sticky behavior

Running `doc-hub pipeline clean <corpus-slug>` sets `clean: true` in the corpus's `fetch_config` in the database. All subsequent fetches for that corpus will auto-clean because the fetcher reads `fetch_config["clean"]`.

### DB changes

A new function `update_corpus_fetch_config(pool, slug, fetch_config)` updates just the `fetch_config` JSONB column for a corpus. This is used by `pipeline clean` to persist the sticky flag.

## New dependency

`openai>=1.0` added to `pyproject.toml` dependencies.

## Concurrency

Cleaning reuses the same bounded-concurrency pattern as the Jina downloader: `asyncio.Semaphore` with a configurable worker count (default 5). Each file gets one LLM API call.

## Error handling

- If a file fails to clean (LLM error, timeout), log a warning and leave the file as-is. The `clean_hash` is not updated, so it will be retried on the next clean run.
- If required env vars are missing when cleaning is triggered, raise `ValueError` with a clear message listing what's needed.

## Testing

- Unit tests for `clean_markdown` with a mock OpenAI client.
- Unit tests for `clean_corpus` manifest diffing logic (skip already-clean files).
- Integration test for `pipeline clean` CLI command.
- Test that `clean: true` stickiness works (clean sets it, subsequent fetch reads it).

## Documentation updates

- `man/doc-hub.1` — add `pipeline clean` subcommand.
- `docs/user/cli-reference.md` — add `pipeline clean` section.
- `docs/user/configuration.md` — document the four env vars.
- `.agent/install-manager/` — update environment docs with new env vars.
