# doc-hub Feature Catalog

A consolidated reference of every user-visible feature currently in doc-hub, plus
a chronological timeline of when each feature was introduced.

Generated: 2026-04-07.

This is a point-in-time snapshot. Cross-check with `git log` for the latest state
before quoting dates or hashes.

---

## Feature catalog

Organized by subsystem: **fetchers → pipeline/parsing → search/DB/MCP**.

### Fetchers

Source: `src/doc_hub/_builtins/fetchers/`, `src/doc_hub/fetchers.py`, `src/doc_hub/protocols.py`.

| Feature | Fetcher(s) | Config keys | Description |
|---|---|---|---|
| `llms_txt` strategy | llms_txt | `url`, `url_pattern`, `base_url` | Parses an `llms.txt` index and downloads each listed doc URL |
| `sitemap` strategy | sitemap | `url` | Crawls `sitemap.xml` / `sitemap.xml.gz` (auto-decompress) |
| `git_repo` strategy | git_repo | `url`, `branch`, `subdir`, `extensions`, `github_token` | Clones GitHub repos via the Trees API |
| `direct_url` strategy | direct_url | `url` / `urls`, `filenames` | Downloads one or more URLs directly (e.g. `llms-full.txt`) |
| `local_dir` strategy | local_dir | `path` | Links/copies a local markdown directory |
| URL prefix inclusion | sitemap | `url_prefix` | Restrict to URLs starting with a given subdirectory |
| URL exclusion (literal list) | sitemap, llms_txt | `url_excludes` | List of corpus-relative paths to drop. Trailing `/` rewrites to `(?:/\|$)` so the bare page is also excluded |
| URL exclusion (raw regex) | sitemap, llms_txt | `url_exclude_pattern` | Raw regex matched against the corpus-relative path, OR'd with `url_excludes` |
| URL suffix transform | llms_txt | `url_suffix` | Appends e.g. `.md` for sites that list bare URLs but serve extensioned pages |
| Non-`.md` URL strategy | llms_txt | `non_md_strategy` = `direct` / `try_md` / `jina` | How to handle HTML pages listed in `llms.txt` |
| Jina Reader integration | llms_txt, sitemap | env `JINA_API_KEY` | HTML → markdown via `r.jina.ai` with 429 backoff |
| LLM cleaning pass | llms_txt, sitemap | `clean` | Strips navigation/footers/breadcrumbs via OpenAI-compatible API |
| Incremental sync via content hash | llms_txt, sitemap | `manifest.json` | SHA-256 diff detects new/changed/removed files between runs |
| Manifest sections | llms_txt, sitemap | `manifest.json` | Groups URLs by first path segment for tree construction |
| Concurrency + retries | all HTTP fetchers | `workers`, `retries` | Defaults: 5–20 workers, 3 retries |
| Custom `base_url` override | llms_txt, sitemap | `base_url` | Manual filename-derivation root |
| GitHub token fallback | git_repo | `github_token`, env `GITHUB_TOKEN` / `GH_TOKEN` | Per-repo config, env fallback |
| Custom file extensions | git_repo | `extensions`, `--extensions` | Fetch `.mdx`, `.rst`, etc. alongside `.md` |

### Pipeline, CLI, and document processing

Source: `src/doc_hub/pipeline.py`, `src/doc_hub/cli/`, `src/doc_hub/parse.py`,
`src/doc_hub/clean.py`, `src/doc_hub/_builtins/parsers/markdown.py`,
`src/doc_hub/embed.py`, `src/doc_hub/eval.py`.

| Feature | Command / flag | Description |
|---|---|---|
| Five-stage pipeline | `pipeline run --stage {fetch,parse,embed,index,tree}` | Independent re-runs of any stage |
| `pipeline add` | `pipeline add <name> --strategy ... --url ...` | Register a corpus and run the full pipeline |
| Interactive add | `pipeline add -i` | Guided wizard with auto-strategy detection from URL |
| Skip download | `pipeline run --skip-download` | Reuse existing `raw/` directory |
| Full reindex | `pipeline run --full-reindex` | Delete stale DB rows after chunk upsert |
| Clean wipe | `pipeline run --clean` | Wipe local data before re-running |
| `pipeline clean` | `pipeline clean <slug>` | Standalone LLM cleaning; sticks `clean=true` into corpus config |
| `pipeline remove` | `pipeline remove <slug> [--keep-data]` | PAM-gated corpus deletion |
| `pipeline sync-all` | `pipeline sync-all` | Pipeline over every enabled corpus |
| `pipeline logs` | `pipeline logs <slug>` | Re-run with visible log output |
| `pipeline eval` | `pipeline eval --corpus <slug> [--all]` | Precision@5 + MRR retrieval metrics |
| Markdown chunking | parser | Heading-split + two-pass size optimization (merge <500 chars, split >2500) |
| Chunk dedup | parser | SHA-256 content-hash deduplication |
| Category classification | parser | Auto-tags: `api` / `guide` / `example` / `eval` / `other` |
| YAML frontmatter titles | parser | Extracts `title:` from frontmatter |
| Embedding cache | embed | Per-corpus JSONL cache keyed by content hash |
| L2-normalized vectors | embed | Required for pgvector cosine distance |
| Sliding-window rate limiter | embed | `DOC_HUB_EMBED_RPM`, `DOC_HUB_EMBED_TPM`, `DOC_HUB_EMBED_BATCH_SIZE` |
| Clean retries + circuit breaker | clean | Exponential backoff + max-consecutive-failure cutoff |

### Search, database, embeddings, MCP

Source: `src/doc_hub/search.py`, `src/doc_hub/db.py`, `src/doc_hub/embed.py`,
`src/doc_hub/index.py`, `src/doc_hub/mcp_server.py`.

| Feature | Where | Description |
|---|---|---|
| Hybrid vector + BM25 search | `search.py` | VectorChord KNN + Postgres FTS fused via Reciprocal Rank Fusion (k=60) |
| Cross-corpus search | `search.py` | `corpora=[...]` list filter |
| Min-similarity filter | `search.py` | Post-SQL threshold (default 0.55) |
| Category filters | `search.py` | Include via `categories`, exclude via `exclude_categories` |
| Path / URL prefix scoping | `search.py` | `source_url_prefix`, `section_path_prefix` |
| Tunable SearchConfig | `search.py` | `vector_limit`, `text_limit`, `rrfk`, FTS `language` (25+ supported) |
| JSON search output | `search.py` | Full content + `doc_id` per result |
| `doc_corpora` table | `db.py` | Corpus registry with `fetch_config` JSONB + stats |
| `doc_chunks` table | `db.py` | Multi-corpus chunks, weighted tsvector (heading A + body B) |
| `doc_documents` table | `db.py` | Hierarchical document tree (parent/depth/sort_order) |
| `doc_index_meta` table | `db.py` | Per-corpus key/value metadata |
| Auto-created indexes | `db.py` | corpus_id, GIN tsv, category, hash, URL+path prefixes, heading level |
| Advisory locks | `db.py` | Per-corpus transaction serialization |
| Configurable vector dim | `db.py` | `DOC_HUB_VECTOR_DIM` (default 768) |
| VectorChord / pgvector bootstrap | `db.py` | `ensure_schema` creates the `vchord` extension and all tables |
| MCP tool: `search_docs_tool` | `mcp_server.py` | Hybrid search with corpus/category/limit filters |
| MCP tool: `list_corpora_tool` | `mcp_server.py` | List registered corpora + status |
| MCP tool: `add_corpus_tool` | `mcp_server.py` | Register a corpus with soft plugin validation |
| MCP tool: `refresh_corpus_tool` | `mcp_server.py` | Re-run the full pipeline (optional `full=True` cleanup) |
| MCP tool: `browse_corpus_tool` | `mcp_server.py` | Navigate the document hierarchy by path/depth |
| MCP tool: `get_document_tool` | `mcp_server.py` | Retrieve a document by `doc_path` |
| MCP transports | `mcp_server.py` | stdio / SSE / streamable-http |
| CLI: `docs search` | `cli/docs.py` | Hybrid search, optional `--json` |
| CLI: `docs list` | `cli/docs.py` | List registered corpora |
| CLI: `docs browse` | `cli/docs.py` | Browse doc tree with `--depth`, `--path`, `--json` |
| CLI: `docs read` | `cli/docs.py` | Read full document by short ID |

---

## Feature timeline

Chronological (oldest first). Only commits that introduce or meaningfully
expand a user-visible feature are listed — pure refactors, test-only commits,
and routine bug fixes are omitted.

| Date | Commit | Feature / Subject |
|---|---|---|
| 2026-03-26 | `ac8763d` | Initial repo — core pipeline + single CLI |
| 2026-03-26 | `1c5645c` | Unified `doc-hub` CLI surface (`pipeline`, `docs`, `serve`) |
| 2026-03-26 | `90852a2` | Document hierarchy — `doc_documents` table, browse/read tools |
| 2026-03-26 | `ddaf81e` | Global install + MCP service integration |
| 2026-03-26 | `e5feb45` | Manpage + `docs list` |
| 2026-03-26 | `f053422` | CLI usability (error messages, interactive prompts) |
| 2026-03-27 | `3a3b99b` | `pipeline add` subcommand with auto-strategy detection |
| 2026-03-28 | `0ead5c7` | SitemapFetcher — Jina Reader, gzip, content-hash manifest |
| 2026-03-28 | `377e28c` | LLM-based markdown cleaning pass |
| 2026-03-28 | `fd45fa2` | Search JSON output — full content + `doc_id` |
| 2026-03-28 | `679ba77` | Embed rate limiter — sliding-window RPM/TPM with env knobs |
| 2026-03-31 | `6dfcde1` | `url_suffix`, DirectUrlFetcher, YAML frontmatter titles |
| 2026-04-03 | `bdca348` | `git_repo` fetcher, sitemap `url_prefix`, read-by-doc-id |
| 2026-04-05 | `d305f9c` | Jina module extraction, `non_md_strategy` (`direct`/`try_md`/`jina`) |
| 2026-04-05 | `cd8516f` | Clean retries — exponential backoff + circuit breaker |
| 2026-04-05 | `a87df06` | `pipeline remove` (PAM-gated), interactive add, `--use-jina`/`--try-md` |
| 2026-04-05 | `971f682` | `GITHUB_TOKEN` / `GH_TOKEN` fallback for `git_repo` auth |
| 2026-04-05 | `0821339` | `git_repo --extensions` flag for custom file types |
| 2026-04-07 | _(this change)_ | URL exclusion filter — `url_excludes` / `url_exclude_pattern` for sitemap + llms_txt |

---

## Observations

- **Development compresses into ~12 active days** (2026-03-26 → 2026-04-07). Seven features landed on 2026-04-05 alone.
- **Recent velocity is fetcher-surface-heavy**: `git_repo` extensions, `--use-jina`/`--try-md`, `pipeline remove`, URL exclusion.
- **CLI wiring lags `fetch_config` additions.** `url_suffix` is exposed as `--url-suffix`, `non_md_strategy` is split into `--use-jina` / `--try-md`, and the new `url_excludes` / `url_exclude_pattern` are currently **MCP/SQL-only**. Worth a pass if CLI parity matters.
- **Core read-path is stable.** `search.py`, `db.py` schema, and `mcp_server.py` tool surface haven't moved since late March. All recent churn is on the ingest side.

## Gaps to consider

- `--url-excludes` / `--url-exclude-pattern` CLI flags (wire into `src/doc_hub/cli/pipeline.py` argparse + `build_fetch_config`).
- Cross-host URL exclusion warning currently logs once per filter; consider per-URL debug logging if someone hits the footgun.
- `local_dir` and `direct_url` fetchers have no exclusion support. The `build_exclude_filter` helper is reusable — wiring is ~10 lines per fetcher if demand emerges.
