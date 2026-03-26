# Fix Incremental Sync Gaps in doc-hub

## Context

The doc-hub pipeline's incremental sync has two critical gaps:

1. **Modified content is missed** — the fetcher diffs by URL only, so if upstream doc content changes without a URL change, the stale local file is never re-downloaded.
2. **Deleted docs are never cleaned up** — removed URLs are logged but local `.md` files aren't deleted, and the parser globs all `*.md` files regardless of the manifest, so orphaned files get re-indexed indefinitely.

Both gaps mean `doc-hub pipeline run --corpus X` (the normal incremental run) silently serves stale or deleted content. The only workaround today is `--clean` which nukes everything and forces a full re-embed (expensive on free-tier Gemini).

## Prior Art

Several existing open-source projects solve overlapping parts of this problem. Notable ones:

- **[Context7 (Upstash)](https://github.com/upstash/context7)** — Hosted SaaS with 500+ pre-indexed libraries. Fetches version-specific docs on demand. Most mature but not self-hosted.
- **[docs-mcp-server (arabold)](https://github.com/arabold/docs-mcp-server)** — Open-source, multi-source (websites, GitHub, local, zip), semantic chunking, hybrid search, SQLite backend. Supports OpenAI/Gemini/Bedrock embeddings. 1.8k+ stars.
- **[documentation-mcp (mikkelkrogsholm)](https://glama.ai/mcp/servers/mikkelkrogsholm/documentation-mcp)** — Fetch→parse→embed→search pipeline with Ollama embeddings, SQLite + sqlite-vec, hybrid search with RRF. Nearly identical pipeline stages.
- **[mcp-ragdocs (qpd-v)](https://github.com/qpd-v/mcp-ragdocs)** — MCP server using Qdrant for vector search. Simpler add-and-search model. Multiple active forks.
- **[mcp-crawl4ai-rag](https://github.com/coleam00/mcp-crawl4ai-rag)** — Web crawling + RAG for AI agents via Crawl4AI + Qdrant.

### doc-hub's differentiators

| Feature | doc-hub | arabold/docs-mcp | Context7 |
|---------|---------|-------------------|----------|
| DB backend | PostgreSQL + VectorChord | SQLite | Hosted |
| Hybrid search | KNN + BM25 via RRF | Vector + full-text | Unknown |
| Multi-corpus isolation | corpus_id FK + advisory locks | Library-level | Library IDs |
| Concurrent access | Yes (PostgreSQL) | No (SQLite) | Yes (hosted) |
| Self-hosted | Yes | Yes | No |

The PostgreSQL/VectorChord backend and proper multi-corpus isolation with advisory locks are doc-hub's main advantages — these matter for running as shared infrastructure rather than a local dev tool.

## Approach

### Fix 1: Content-hash-based change detection in the fetcher

**Strategy**: Re-download all existing URLs on each sync (they're small `.md` files — ~1MB total for a typical corpus), compare SHA-256 hashes against the manifest, and only update files that actually changed.

**Why not ETag/If-None-Match**: Higher complexity (store ETags, handle servers that don't support them, handle 304 vs 200), and many doc hosts (GitHub Pages, Netlify) don't return stable ETags for generated content. Content hashing is universal and simpler.

**Changes to `fetchers.py`**:

1. **`DownloadResult`** — add `content_hash: str | None = None` field
2. **`_download_one`** — compute `hashlib.sha256(content).hexdigest()` after download, return it in the result
3. **`write_manifest`** — include `content_hash` per file entry in the JSON
4. **`load_manifest`** — return `dict[str, dict]` where value is `{"url": ..., "content_hash": ...}` instead of just `dict[str, str]`. Old manifests without `content_hash` degrade gracefully (hash is `None`, treated as "needs re-download").
5. **`compute_manifest_diff`** — adapt to the new manifest structure. Still returns `(new_urls, removed_filenames)` but the caller now also re-downloads existing URLs.
6. **`fetch_llms_txt`** — download ALL upstream URLs (new + existing), compare hashes against manifest for existing ones. Only overwrite `.md` files whose hash changed. Log changed/unchanged counts.

**Backward compatibility**: Old manifests without `content_hash` get treated as "needs re-download" on first run. After one sync, the manifest is updated with hashes. Zero breakage.

### Fix 2: Delete removed files + manifest-filtered parsing

**Changes to `fetchers.py`**:

7. **`fetch_llms_txt`** — after computing `removed_filenames`, delete the corresponding `.md` files from `output_dir` via `Path.unlink()`.

**Changes to `parse.py`**:

8. **`parse_docs`** — when a manifest exists and is non-empty, only parse files listed in it (not all `*.md` on disk). Fall back to globbing when no manifest exists (supports `local_dir` strategy which has no manifest).

### Not fixing (low priority)

- **Embed cache pruning** — stale cache entries waste only disk space (~800 bytes each), not API calls or search quality. Can be addressed later with an optional `--compact-cache` flag.

## Files to modify

| File | Changes |
|------|---------|
| `src/doc_hub/fetchers.py` | Extend DownloadResult, manifest I/O, content-hash comparison, file deletion |
| `src/doc_hub/parse.py` | Filter parse_docs by manifest |
| `tests/test_fetchers.py` | Tests for content detection, file deletion, backward compat |
| `tests/test_parse.py` | Tests for manifest-filtered parsing, glob fallback |

## Deliverable 2: BDD specification

Write `docs/sync-behavior.feature` — a complete Gherkin BDD document specifying all sync behaviors across the pipeline stages, covering:
- New docs, modified docs, deleted docs, URL changes, first run, no-change re-run
- `--clean`, `--full-reindex`, `--skip-download` flag interactions
- Manifest backward compatibility
- Cross-corpus isolation
- Embedding cache behavior
- Error scenarios (download failures, empty corpus)

## Verification

1. `uv run --package doc-hub pytest packages/doc-hub/tests/test_fetchers.py -v` — all existing + new tests pass
2. `uv run --package doc-hub pytest packages/doc-hub/tests/test_parse.py -v` — all existing + new tests pass
3. `uv run --package doc-hub pytest packages/doc-hub/tests/ -v` — full suite passes (429+ tests)
4. Manual verification: the pydantic-ai corpus `{"url": "https://ai.pydantic.dev/llms.txt"}` still works with the refactored `load_manifest` return type
