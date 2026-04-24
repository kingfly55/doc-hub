# Implementation Plan: First-Class Documentation Versioning

> **For agentic workers:** REQUIRED SUB-SKILL: Use `milestone-execution` to implement this plan one milestone at a time. Do not batch milestones unless the user explicitly asks. The plan intentionally stops before implementation.

**Goal:** Add first-class documentation versioning to doc-hub so indexed documentation can be selected, searched, browsed, refreshed, and exposed to LLM agents by explicit version or immutable snapshot.

**Core principle:** A corpus can have multiple immutable documentation snapshots. Human-readable source versions such as `18`, `v1.2.3`, `stable`, or `latest` are labels/aliases over immutable snapshots, not replacements for immutable snapshot identity.

**Primary UX requirement:** Agents must be told which version/snapshot was searched and what other versions exist, but doc-hub must never claim relevance in versions it did not search.

**Current-state summary:** doc-hub is corpus-scoped only. Filesystem paths, manifests, chunks, embedding cache, document tree rows, index metadata, search filters, browse/read, CLI output, and MCP tools all assume one mutable snapshot per corpus.

**Canonical inputs:**
- `ARCHITECTURE.md` — current pipeline, filesystem layout, module boundaries, plugin contracts.
- `docs/dev/database-schema.md` — current PostgreSQL schema and indexing constraints.
- `docs/dev/search-internals.md` — current search SQL, result model, and CLI/API search behavior.
- Repository audit from Haiku subagents on 2026-04-24 — file-level impact map for storage, fetchers, parsing, search, CLI, MCP, tests, and docs.

**Verification commands:**
- `uv run pytest tests/`
- `uv run ruff check src/`
- Integration verification, when a live DB and API keys are available: `uv run pytest tests/ -m integration`

---

## Non-Goals

- Do not search all versions by default.
- Do not silently fall back from a requested version to another version.
- Do not infer project dependency versions in this initial implementation. Leave room for a future project-aware wrapper/agent workflow.
- Do not require all fetchers to discover upstream semantic versions automatically in the first pass.
- Do not build a full deduplicated blob store yet. Content-addressed snapshot hashes are required; cross-version storage deduplication is optional future work.
- Do not migrate existing corpora into rich historical versions automatically. Existing corpora may be represented as a single initial snapshot.
- Do not redesign search ranking, embedding model selection, or parser chunking beyond the changes needed for version scoping.

---

## Design Decisions

### 1. Version vocabulary

Use four distinct concepts consistently:

| Term | Meaning |
|---|---|
| `corpus_slug` | Stable corpus identity, e.g. `react`, `fastapi`, `claude-code`. |
| `source_version` | Human/source label, e.g. `18`, `v1.2.3`, `main`, `latest`. May be mutable. |
| `snapshot_id` | Immutable doc-hub identifier for one fetched source state, derived from normalized source/version/content metadata. |
| `version_alias` | Mutable pointer such as `latest` or `stable` that resolves to one `snapshot_id`. |

A search, browse, read, or refresh operation must always resolve to either:
- exactly one selected snapshot, or
- an explicit multi-version scope requested by the caller.

### 2. Strict search semantics

`doc-hub docs search react@18 "query"` searches only the version/snapshot that `react@18` resolves to.

If there are no results, output may say:

```text
No results found in react@18.
Other versions are available: 17, 19, latest -> 19.
```

It must not say related results exist in unsearched versions.

Cross-version search must be explicit:

```bash
doc-hub docs search react "query" --all-versions
doc-hub docs search react "query" --versions 18,19
```

### 3. Snapshot-first persistence

Website-style sources may not expose a real upstream version. For every fetch, doc-hub should still produce an immutable snapshot identity using:
- normalized source URL or source identifier
- normalized fetch config relevant to source selection
- fetched URL/file set hash
- content hash or manifest hash
- fetcher name and source strategy

For websites, `latest` remains a mutable alias pointing to the latest immutable snapshot.

### 4. Backward-compatible ingestion, not backward-compatible ambiguity

Existing code and tests may initially continue to accept corpus-only operations by resolving to a default version/snapshot, but outputs must reveal that selected scope. Example:

```text
Searched version: latest -> snapshot:sha256-...
Available versions: latest -> snapshot:sha256-...
```

Do not keep old ambiguous output for agent-facing commands.

### 5. Plugin boundary remains simple

The `Fetcher.fetch(corpus_slug, fetch_config, output_dir) -> Path` protocol should not be expanded until necessary. Prefer a shared manifest schema that fetchers write and parsers consume. If a protocol change becomes unavoidable, isolate it in a dedicated milestone with migration tests and docs updates.

---

## Proposed Data Model

### Filesystem layout

Move from one corpus snapshot directory:

```text
{data_root}/{slug}/
  raw/
  chunks/
```

to version-aware snapshot directories:

```text
{data_root}/{slug}/
  versions/
    {snapshot_id}/
      raw/
        manifest.json
        *.md
      chunks/
        chunks.jsonl
        embedded_chunks.jsonl
        embeddings_cache.jsonl
  aliases.json
```

`aliases.json` is a local convenience cache only. PostgreSQL remains authoritative for indexed/searchable state.

Required path helpers in `src/doc_hub/paths.py`:
- `corpus_dir(corpus_or_slug)` remains corpus-scoped.
- `versions_dir(corpus_or_slug)` returns `{slug}/versions`.
- `snapshot_dir(corpus_or_slug, snapshot_id)` returns `{slug}/versions/{snapshot_id}`.
- `raw_dir(corpus_or_slug, snapshot_id=None)` resolves old/default behavior while callers migrate.
- `chunks_dir(corpus_or_slug, snapshot_id=None)` resolves old/default behavior while callers migrate.
- `manifest_path(corpus_or_slug, snapshot_id=None)`.
- `embedded_chunks_path(corpus_or_slug, snapshot_id=None)`.
- `embeddings_cache_path(corpus_or_slug, snapshot_id=None)`.

During migration, avoid deleting the existing `{slug}/raw` and `{slug}/chunks` directories. Treat them as legacy layout until a migration/refresh creates versioned snapshots.

### Manifest schema

Each fetcher should write a manifest shaped like:

```json
{
  "schema_version": 2,
  "corpus_slug": "react",
  "fetch_strategy": "sitemap",
  "source": {
    "type": "website",
    "url": "https://react.dev/",
    "source_version": "latest",
    "resolved_version": null,
    "fetched_at": "2026-04-24T12:00:00Z",
    "http": {
      "etag": null,
      "last_modified": null
    }
  },
  "snapshot": {
    "snapshot_id": "sha256-...",
    "url_set_hash": "sha256:...",
    "content_hash": "sha256:...",
    "fetch_config_hash": "sha256:..."
  },
  "aliases": ["latest"],
  "files": [
    {
      "filename": "docs__index.md",
      "url": "https://react.dev/learn",
      "content_hash": "sha256:...",
      "fetched_at": "2026-04-24T12:00:00Z",
      "source_version": "latest",
      "resolved_version": null
    }
  ],
  "sections": []
}
```

Rules:
- Preserve unknown manifest fields in transformation steps such as cleaning.
- `schema_version` must be optional for old manifests and interpreted as version 1.
- `snapshot.snapshot_id` is immutable after the manifest is finalized.
- Fetchers may initially write a temporary manifest and then finalize `snapshot_id` after content hashes are known.

### PostgreSQL schema

Add version/snapshot identity explicitly. The exact DDL can be adjusted during implementation, but the plan assumes these tables or equivalent normalized structures:

```sql
CREATE TABLE IF NOT EXISTS doc_versions (
    corpus_id        text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    snapshot_id      text NOT NULL,
    source_version   text NOT NULL,
    resolved_version text,
    source_type      text NOT NULL,
    source_url       text NOT NULL,
    fetch_strategy   text NOT NULL,
    fetch_config_hash text NOT NULL,
    url_set_hash     text,
    content_hash     text NOT NULL,
    fetched_at       timestamptz NOT NULL,
    indexed_at       timestamptz,
    total_chunks     int DEFAULT 0,
    enabled          boolean DEFAULT true,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (corpus_id, snapshot_id)
);

CREATE TABLE IF NOT EXISTS doc_version_aliases (
    corpus_id   text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    alias       text NOT NULL,
    snapshot_id text NOT NULL,
    updated_at  timestamptz DEFAULT now(),
    PRIMARY KEY (corpus_id, alias),
    FOREIGN KEY (corpus_id, snapshot_id) REFERENCES doc_versions(corpus_id, snapshot_id) ON DELETE CASCADE
);
```

Update existing tables:

```sql
ALTER TABLE doc_chunks ADD COLUMN snapshot_id text;
ALTER TABLE doc_chunks ADD COLUMN source_version text;
ALTER TABLE doc_chunks ADD COLUMN fetched_at timestamptz;

ALTER TABLE doc_documents ADD COLUMN snapshot_id text;
ALTER TABLE doc_documents ADD COLUMN source_version text;
```

Required uniqueness changes:
- `doc_chunks`: replace or supplement `UNIQUE (corpus_id, content_hash)` with `UNIQUE (corpus_id, snapshot_id, content_hash)`.
- `doc_documents`: ensure document identity is unique per `(corpus_id, snapshot_id, doc_id)` or equivalent.
- `doc_index_meta`: either add `snapshot_id` to the primary key or move version-level metadata into `doc_versions`.

Migration rule:
- Existing indexed data with no `snapshot_id` should be migrated to a synthetic snapshot such as `legacy-{hash}` or `snapshot:legacy` per corpus.
- Do not drop old data unless a targeted test verifies safe migration.

---

## Agent-Facing Output Contract

Every search response should expose search scope.

Human output preamble:

```text
Corpus: react
Searched versions: 18 -> snapshot:sha256-abc123
Available versions: 17, 18, 19, latest -> 19
```

JSON/MCP response shape should include a machine-readable metadata block:

```json
{
  "query": "useEffect cleanup",
  "scope": {
    "corpus": "react",
    "searched_versions": [
      {
        "requested": "18",
        "source_version": "18",
        "snapshot_id": "sha256-abc123",
        "selected_by": "explicit"
      }
    ],
    "available_versions": ["17", "18", "19"],
    "aliases": {"latest": "19"},
    "not_searched_versions": ["17", "19"]
  },
  "results": []
}
```

Each result should include:
- `corpus_id`
- `source_version`
- `snapshot_id`
- `source_url`
- existing result fields

Do not expose unsearched-version relevance claims.

---

## Milestone 1: Add Version Domain Types, Path Helpers, and Manifest Utilities

**Scope:** Establish local version/snapshot concepts without changing search behavior yet.

**Files to modify:**
- `src/doc_hub/models.py`
- `src/doc_hub/paths.py`
- `src/doc_hub/_builtins/parsers/markdown.py`
- `src/doc_hub/parse.py`
- `tests/test_models.py`
- `tests/test_paths.py`
- `tests/test_fetchers.py`

**Files to consider creating:**
- `src/doc_hub/versions.py` — shared version resolution, snapshot ID, alias helpers, manifest schema helpers.
- `tests/test_versions.py`

**Implementation steps:**
- [ ] Add dataclasses for version metadata, e.g. `DocVersion`, `VersionAlias`, and `SnapshotManifest`.
- [ ] Add a canonical snapshot ID builder that is deterministic and testable.
- [ ] Add manifest load/finalize helpers that read both legacy manifests and schema-version-2 manifests.
- [ ] Add version-aware path helpers while preserving old helper call sites via optional `snapshot_id`.
- [ ] Add tests for old layout and new layout path generation.
- [ ] Add tests for manifest schema version detection, unknown-field preservation, and snapshot ID determinism.

**Success criteria:**
- Existing tests still pass without changing DB schema.
- New helpers can represent a corpus with multiple snapshots on disk.
- Legacy manifests can still be loaded.

**Verification:**
- `uv run pytest tests/test_paths.py tests/test_models.py tests/test_versions.py -q`
- `uv run ruff check src/`

**Risks:**
- Optional `snapshot_id` parameters can hide incomplete migration. Mitigate by making all new code pass explicit snapshot IDs and leaving optional behavior only for legacy compatibility.

---

## Milestone 2: Add Version-Aware Database Schema and Migration

**Scope:** Persist snapshots, aliases, and version-scoped chunks/documents.

**Files to modify:**
- `src/doc_hub/db.py`
- `src/doc_hub/models.py`
- `src/doc_hub/index.py`
- `src/doc_hub/documents.py`
- `docs/dev/database-schema.md`
- `tests/test_db.py`
- `tests/test_db_schema.py`
- `tests/test_index.py`
- `tests/test_documents.py`

**Implementation steps:**
- [ ] Add `doc_versions` and `doc_version_aliases` DDL.
- [ ] Add idempotent migrations for `snapshot_id`, `source_version`, and `fetched_at` columns in `doc_chunks` and `doc_documents`.
- [ ] Update uniqueness/indexes for version-scoped chunk/document identity.
- [ ] Add DB helpers:
  - `upsert_doc_version(pool, version)`
  - `list_doc_versions(pool, corpus_id)`
  - `get_doc_version(pool, corpus_id, selector)`
  - `upsert_version_alias(pool, corpus_id, alias, snapshot_id)`
  - `resolve_version_selector(pool, corpus_id, selector)`
- [ ] Update `update_corpus_stats()` or add `update_version_stats()` so indexed counts belong to a snapshot.
- [ ] Update advisory lock strategy from corpus-only to corpus+snapshot where appropriate, while still preventing concurrent conflicting writes to the same corpus alias.
- [ ] Migrate existing rows into a legacy snapshot in tests.
- [ ] Update `docs/dev/database-schema.md` with new tables, constraints, indexes, and migration notes.

**Success criteria:**
- Schema creation is idempotent on empty and pre-existing test schemas.
- Existing corpus CRUD remains functional.
- Multiple snapshots for one corpus can coexist in DB.
- Chunks and documents from two snapshots with the same `content_hash` do not overwrite each other.

**Verification:**
- `uv run pytest tests/test_db.py tests/test_db_schema.py tests/test_index.py tests/test_documents.py -q`
- `uv run ruff check src/`

**Risks:**
- Unique constraint migration can be destructive if done carelessly. Add tests around pre-version rows before touching live migration behavior.
- `doc_index_meta` may duplicate `doc_versions` metadata. Prefer a single source of truth for version-level stats.

---

## Milestone 3: Produce Versioned Manifests in Fetchers

**Scope:** Make all built-in fetchers emit version/provenance metadata.

**Files to modify:**
- `src/doc_hub/_builtins/fetchers/git_repo.py`
- `src/doc_hub/_builtins/fetchers/sitemap.py`
- `src/doc_hub/_builtins/fetchers/llms_txt.py`
- `src/doc_hub/_builtins/fetchers/direct_url.py`
- `src/doc_hub/_builtins/fetchers/local_dir.py`
- `src/doc_hub/_builtins/fetchers/jina.py`
- `src/doc_hub/fetchers.py`
- `src/doc_hub/protocols.py` only if unavoidable
- `tests/test_fetchers.py`
- `tests/test_url_filter.py`

**Implementation steps:**
- [ ] Add shared `fetched_at` generation at fetch-run start so files in one snapshot share a stable timestamp unless file-level timestamps are required.
- [ ] Update `DownloadResult` and manifest file entries to carry `fetched_at`, `source_version`, `resolved_version`, and HTTP provenance where available.
- [ ] `git_repo.py`: resolve branch/tag inputs to an immutable commit SHA before writing final manifest metadata.
- [ ] `sitemap.py`: compute URL-set hash and content hash for each snapshot. Stop deleting historical snapshot files in versioned layout.
- [ ] `llms_txt.py`: hash the llms.txt manifest itself, preserve sections per snapshot, and stop treating the active manifest as the only source of truth.
- [ ] `direct_url.py`: support manual `source_version` in fetch config; default to `latest` alias over snapshot when absent.
- [ ] `local_dir.py`: support manual `source_version` in fetch config; compute snapshot hash from file set and content.
- [ ] Preserve legacy manifest writes only where necessary for compatibility; prefer schema-version-2 manifests in new snapshot directories.
- [ ] Add tests for each built-in fetcher manifest shape.

**Success criteria:**
- Every built-in fetcher produces enough metadata to create a `doc_versions` row.
- Fetchers that refresh a mutable source do not delete old versioned snapshots.
- Old manifests still parse.

**Verification:**
- `uv run pytest tests/test_fetchers.py tests/test_url_filter.py -q`
- `uv run ruff check src/`

**Risks:**
- GitHub API/rate behavior for commit resolution may require unauthenticated fallback and `GITHUB_TOKEN` support. Keep existing auth behavior intact.
- HTTP `ETag`/`Last-Modified` are optional; snapshot identity must not depend on them.

---

## Milestone 4: Thread Version Metadata Through Parse, Clean, Embed, and Index

**Scope:** Ensure snapshot/version metadata survives all pipeline stages and reaches PostgreSQL.

**Files to modify:**
- `src/doc_hub/parse.py`
- `src/doc_hub/_builtins/parsers/markdown.py`
- `src/doc_hub/clean.py`
- `src/doc_hub/embed.py`
- `src/doc_hub/index.py`
- `src/doc_hub/pipeline.py`
- `tests/test_clean.py`
- `tests/test_embed.py`
- `tests/test_index.py`
- `tests/test_pipeline.py`
- `tests/test_pipeline_tree.py`

**Implementation steps:**
- [ ] Extend `Chunk` with `snapshot_id`, `source_version`, and `fetched_at`.
- [ ] Update `MarkdownParser._load_manifest()` to return rich file metadata, not just filename-to-URL mappings.
- [ ] Ensure merge/split/dedup operations preserve version metadata.
- [ ] Decide whether `embedding_input()` includes version. Default recommendation: do not include version in embedding text unless evaluation shows it improves retrieval; preserve version as metadata instead.
- [ ] Extend `EmbeddedChunk` with version metadata.
- [ ] Update embedding cache logic. Cache can remain keyed by `(content_hash, model, dimensions)` for vector reuse, but embedded output rows must preserve snapshot metadata. Add tests showing identical content in two versions reuses embedding but indexes as two version-scoped rows.
- [ ] Update `clean_corpus()` to preserve all manifest version/provenance fields and add `cleaned_at` only if needed.
- [ ] Update `upsert_chunks()` to write snapshot/version fields and delete stale chunks only within the selected snapshot.
- [ ] Update `run_fetch`, `run_clean`, `run_parse`, `run_embed`, `run_index`, and `run_build_tree` to pass/resolve `snapshot_id` explicitly.

**Success criteria:**
- A full pipeline run for one corpus creates one versioned snapshot and indexes version-scoped chunks/documents.
- Two snapshots of the same corpus can be parsed/embedded/indexed independently.
- Cleaning does not erase version metadata.

**Verification:**
- `uv run pytest tests/test_clean.py tests/test_embed.py tests/test_index.py tests/test_pipeline.py tests/test_pipeline_tree.py -q`
- `uv run ruff check src/`

**Risks:**
- Dedup by content hash can accidentally collapse chunks across versions. Keep dedup local to one parse run/snapshot.
- Stale deletion must never delete chunks from another snapshot.

---

## Milestone 5: Make Document Trees, Browse, and Read Version-Aware

**Scope:** Resolve document identity ambiguity and expose selected version in browse/read.

**Files to modify:**
- `src/doc_hub/documents.py`
- `src/doc_hub/browse.py`
- `src/doc_hub/cli/docs.py`
- `src/doc_hub/pipeline.py`
- `tests/test_documents.py`
- `tests/test_browse_cli.py`
- `tests/test_pipeline_tree.py`

**Implementation steps:**
- [ ] Change document ID generation to include snapshot identity or store `(corpus_id, snapshot_id, doc_id)` as the lookup key.
- [ ] Update `build_document_tree()`, `upsert_documents()`, `link_chunks_to_documents()`, and stale document deletion to operate within one snapshot.
- [ ] Update `get_document_tree()`, `get_document_chunks_by_doc_id()`, and `get_document_chunks()` to require or resolve a version selector.
- [ ] Add `--version` support to `doc-hub docs browse` and `doc-hub docs read`.
- [ ] Add `corpus@version` parsing where appropriate, e.g. `doc-hub docs browse react@18`.
- [ ] Include selected version/snapshot in human and JSON output.
- [ ] If no version is specified, resolve the corpus default/latest alias and show that resolution.

**Success criteria:**
- Browsing `react@18` and `react@19` can show different trees for the same corpus.
- Reading a doc path that exists in multiple snapshots is never ambiguous after version resolution.
- JSON output includes version/snapshot metadata.

**Verification:**
- `uv run pytest tests/test_documents.py tests/test_browse_cli.py tests/test_pipeline_tree.py -q`
- `uv run ruff check src/`

**Risks:**
- Changing doc IDs can break saved references. Mitigate by accepting old doc IDs only when they resolve unambiguously within the selected snapshot.

---

## Milestone 6: Add Version-Aware Search and CLI Output

**Scope:** Implement explicit version selection, strict scope reporting, and opt-in cross-version search.

**Files to modify:**
- `src/doc_hub/search.py`
- `src/doc_hub/cli/docs.py`
- `docs/dev/search-internals.md`
- `tests/test_search.py`
- `tests/test_unified_cli.py`

**Implementation steps:**
- [ ] Extend `SearchResult` with `snapshot_id` and `source_version`.
- [ ] Add a search scope type, e.g. `SearchScope`, that contains selected corpus/version/snapshot metadata and available versions.
- [ ] Add SQL filters for `snapshot_id` and/or selected versions in both vector and text CTEs.
- [ ] Preserve the NULL-propagation filter style and update bind parameter tests.
- [ ] Add CLI support:
  - `doc-hub docs search "query" --corpus react --version 18`
  - `doc-hub docs search react@18 "query"` if the command parser can support it cleanly
  - `doc-hub docs search "query" --corpus react --versions 18,19`
  - `doc-hub docs search "query" --corpus react --all-versions`
- [ ] Default unspecified version to configured/default alias, normally `latest`, and print selected scope.
- [ ] On no results, list available versions but do not search or claim matches in them.
- [ ] Group `--all-versions` output by version/snapshot or include clear per-result version labels.
- [ ] Update JSON output shape to include a metadata/scope block.
- [ ] Update `docs/dev/search-internals.md` with new bind parameters and scope semantics.

**Success criteria:**
- Strict searches only search selected snapshots.
- Cross-version search only happens on explicit request.
- Agents can inspect response metadata and know exactly what was searched.
- Existing category/source URL/section filters still work in combination with version filters.

**Verification:**
- `uv run pytest tests/test_search.py tests/test_unified_cli.py -q`
- `uv run ruff check src/`

**Risks:**
- Search argument parsing may become confusing if both `--corpus` and `corpus@version` are supported. Add conflict validation tests.
- JSON shape changes may break downstream consumers. Document the breaking change clearly.

---

## Milestone 7: Add Version Listing, Alias Management, and Refresh Semantics

**Scope:** Give users and agents a way to discover and manage versions.

**Files to modify:**
- `src/doc_hub/cli/docs.py`
- `src/doc_hub/cli/pipeline.py`
- `src/doc_hub/pipeline.py`
- `src/doc_hub/db.py`
- `src/doc_hub/mcp_server.py` if MCP listing is implemented here instead of Milestone 8
- `tests/test_unified_cli.py`
- `tests/test_pipeline.py`
- `tests/test_mcp_server.py` if MCP touched

**Implementation steps:**
- [ ] Add `doc-hub docs versions <corpus>`.
- [ ] Update `doc-hub docs list` to show concise version availability without becoming noisy.
- [ ] Add JSON output for version listing with aliases and snapshots.
- [ ] Define refresh behavior:
  - `pipeline run --corpus react` fetches a new snapshot for the default source version/alias.
  - If content hash matches an existing snapshot, do not create a duplicate version row; update alias if needed.
  - If content differs, create a new snapshot and move `latest` or configured alias to it.
- [ ] Add optional registration-time version config in `src/doc_hub/cli/pipeline.py`, e.g. `--source-version`, only where it fits the fetcher.
- [ ] Add alias update helpers only if required for CLI refresh behavior. Avoid broad alias-management UI unless needed.

**Success criteria:**
- Users can list available versions and see alias mappings.
- Refreshing a mutable website source creates or reuses immutable snapshots deterministically.
- Duplicate snapshots are not created when content has not changed.

**Verification:**
- `uv run pytest tests/test_unified_cli.py tests/test_pipeline.py -q`
- `uv run ruff check src/`

**Risks:**
- Alias movement is mutable state and can surprise users. Always show alias movement in CLI output.

---

## Milestone 8: Update MCP Tools for Agent Version Awareness

**Scope:** Make the MCP interface version-aware and safe for LLM agents.

**Files to modify:**
- `src/doc_hub/mcp_server.py`
- `tests/test_mcp_server.py`
- `docs/user/mcp-server.md`

**Implementation steps:**
- [ ] Add version parameters to search, browse, get-document, refresh, and add-corpus tools where relevant.
- [ ] Update `list_corpora_tool` to include version availability, or add a dedicated `list_versions_tool` if payload size/clarity requires it.
- [ ] Update `search_docs_tool` responses to include a `scope` object with searched versions, available versions, aliases, and not-searched versions.
- [ ] Ensure no MCP response claims relevance in unsearched versions.
- [ ] Update tool descriptions so agents know to request explicit versions when a user or project context provides one.
- [ ] Add tests for strict version search, no-result metadata, and explicit all-version search.

**Success criteria:**
- Agent clients can tell which version/snapshot a tool response came from.
- Agents can discover available versions without a separate shell command.
- Existing tool behavior remains understandable for clients that do not pass a version.

**Verification:**
- `uv run pytest tests/test_mcp_server.py -q`
- `uv run ruff check src/`

**Risks:**
- Tool schema changes can break clients with exact key assertions. Prefer additive changes where possible, but do not preserve ambiguous old outputs.

---

## Milestone 9: Update User, Developer, and Manpage Documentation

**Scope:** Reconcile documentation after code behavior changes.

**Files to modify:**
- `README.md`
- `ARCHITECTURE.md`
- `AGENTS.md`
- `docs/user/cli-reference.md`
- `docs/user/mcp-server.md`
- `docs/dev/database-schema.md`
- `docs/dev/search-internals.md`
- `docs/dev/feature-catalog.md`
- `docs/writing-fetchers.md`
- `docs/dev/plugin-authoring.md`
- `docs/dev/protocols-reference.md` if parser/fetcher contracts change
- `man/doc-hub.1`

**Implementation steps:**
- [ ] Update architecture diagrams and filesystem layout.
- [ ] Update schema docs after final DDL lands.
- [ ] Update search docs with version scope and strict semantics.
- [ ] Add CLI examples for:
  - listing versions
  - strict version search
  - default/latest search with visible scope
  - explicit cross-version search
  - browse/read by version
- [ ] Update MCP docs with version-aware response examples.
- [ ] Update fetcher authoring docs with manifest schema version 2.
- [ ] Update manpage command summaries.

**Success criteria:**
- Docs no longer describe doc-hub as corpus-only.
- User-facing examples do not encourage ambiguous latest-only searches.
- Plugin docs tell fetcher authors how to produce version/provenance metadata.

**Verification:**
- `uv run pytest tests/ -q`
- `uv run ruff check src/`
- Manual grep for stale phrases such as `one row per corpus` where versioned semantics now apply.

**Risks:**
- Broad docs updates can drift into unrelated cleanup. Keep edits scoped to versioning contracts and examples.

---

## Milestone 10: End-to-End Validation and Migration Hardening

**Scope:** Validate the feature as a complete system and harden edge cases.

**Files to modify:**
- `tests/test_pipeline.py`
- `tests/test_search.py`
- `tests/test_mcp_server.py`
- `tests/test_browse_cli.py`
- `tests/test_db_schema.py`
- Any files found by failures in full-suite verification

**Implementation steps:**
- [ ] Add an end-to-end test that creates one corpus with two snapshots, indexes both, searches each strictly, and verifies no cross-version leakage.
- [ ] Add a test for no results in one version with other versions available; assert only availability is reported.
- [ ] Add a test for duplicate content across versions; assert embeddings may be reused but index rows remain version-scoped.
- [ ] Add a test for refresh with unchanged website content; assert no duplicate snapshot row.
- [ ] Add a test for alias movement after changed website content.
- [ ] Run full unit suite and fix version-related regressions.
- [ ] If integration environment is available, run integration suite.

**Success criteria:**
- Full test suite passes.
- No known corpus-only leakage remains in search, browse/read, tree building, or stale deletion.
- Agent-facing outputs identify selected scope everywhere relevant.

**Verification:**
- `uv run pytest tests/`
- `uv run ruff check src/`
- Optional: `uv run pytest tests/ -m integration`

**Risks:**
- Some legacy tests may assert exact output shapes. Prefer updating them to assert the new contract rather than adding compatibility shims.

---

## Cross-Cutting Test Matrix

| Area | Required tests |
|---|---|
| Paths | Versioned and legacy layouts; manifest/cache path helpers. |
| Manifests | Legacy load; schema v2 load; snapshot ID determinism; unknown-field preservation. |
| DB | Empty schema creation; migration from old schema; duplicate content across snapshots; alias resolution. |
| Fetchers | Git commit resolution; sitemap snapshot hash; llms.txt manifest hash; direct/local manual version labels. |
| Clean | Version metadata preservation; `clean_hash` updates do not drop provenance. |
| Parse | Chunk version fields; merge/split preserve metadata; legacy manifest fallback. |
| Embed | Cache reuse across versions without output metadata loss. |
| Index | Version-scoped upsert and stale deletion. |
| Documents | Version-scoped doc IDs/tree/chunk links. |
| Search | Strict version filter; no fallback; explicit all-version; JSON scope metadata. |
| CLI | `docs versions`; `--version`; `--versions`; `--all-versions`; output conflict validation. |
| MCP | Version-aware tool schemas and responses; no unsearched relevance claims. |
| Docs | Updated command examples, schema docs, plugin docs, manpage. |

---

## Open Questions for Implementation

1. Should `latest` be a reserved alias for every corpus, or only created when the source is mutable?
2. Should semantic `source_version` values be unique per corpus, or can one source version point to multiple snapshots over time via history?
3. Should `doc-hub docs list` show all versions by default or only a compact count plus default alias?
4. Should old `doc_id` values remain accepted in `docs read` when no version is specified and only one snapshot exists?
5. Should fetchers expose version discovery as a separate operation later, e.g. `doc-hub docs discover-versions <corpus>`?
6. Should snapshot pruning be implemented now or left for a future storage-management plan?

Recommended initial answers:
- Create `latest` for mutable sources and for compatibility, but show it as an alias.
- Allow alias history in data model later, but only one current alias target in this implementation.
- Keep `docs list` compact; use `docs versions <corpus>` for details.
- Accept old doc IDs only when resolution is unambiguous.
- Leave automated version discovery and pruning for future plans.

---

## Adversarial Self-Review

### Weak assumption: snapshot ID can be known before writing files

Fetchers may need to write files before computing content hashes. The plan should require a finalize step that writes a temporary manifest first, computes hashes, then writes the final manifest and DB version row.

### Weak assumption: embedding cache key is unsafe

The existing cache key `(content_hash, model, dimensions)` is safe for vector reuse because identical content produces identical embedding input only if `embedding_input()` remains version-independent. The dangerous part is not cache reuse; it is losing snapshot metadata in embedded output. Tests must distinguish those.

### Weak assumption: aliases fit in local JSON

`aliases.json` must be a cache only. DB alias tables are authoritative because MCP/search need shared indexed state.

### Hidden migration risk: generated columns and vector indexes

Changing uniqueness and adding columns around `doc_chunks` must avoid recreating the vector column or invalidating VectorChord assumptions. Migration tests should cover existing schemas.

### Hidden UX risk: too much version metadata in normal output

Human output should show a compact scope preamble and per-result version only when needed. JSON/MCP can be more verbose.

### Hidden agent risk: all-version searches can become expensive

`--all-versions` should be opt-in and may need limits/grouping. The initial implementation should preserve normal `limit` semantics clearly, preferably per searched version or with documented global behavior.

---

## Handoff

After this plan is accepted, implementation should start with Milestone 1 using `milestone-execution`. The first executor should not begin by editing search or MCP code; version identity must be established in paths/manifests/schema first or downstream surfaces will invent incompatible contracts.
