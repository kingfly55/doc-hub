# Plan: Inter-Document Hierarchy and Document Browsing

## Context

doc-hub is a semantic search engine — it excels at "ask a question, get chunks." But it has no support for browsing, navigating, or reading documentation in context. Documents do not exist as first-class entities. There are no parent-child relationships between documents. An LLM agent using doc-hub cannot browse a corpus's structure, read a full page, or understand how documentation is organized.

Some of the data to build hierarchy already exists: URL paths encode parent-child relationships, llms.txt files often group URLs under section headings, and within-document heading structure is already captured in `section_path`. The missing piece is turning that into durable, queryable document structure that survives indexing and is exposed through MCP and CLI interfaces.

**Goal:** Add a `doc_documents` table for inter-document hierarchy, build trees from fetcher hints + path inference where path information exists, and expose browsing/reading via new MCP tools and CLI commands. The full pipeline becomes: fetch → parse → embed → index → tree.

---

## Milestones

### Milestone 1 — DB Schema Extension
- **File**: 1.md
- **Status**: complete
- **Summary**: Added `doc_documents`, document indexes, nullable `doc_chunks.document_id`, and legacy `doc_corpora` schema migration handling in `db.py`

### Milestone 2 — DocumentNode Dataclass and Tree-Building Algorithm
- **File**: 2.md
- **Status**: complete
- **Summary**: Created `documents.py` with `DocumentNode`, path helpers, section-aware grouping helpers, and a deterministic preorder tree builder

### Milestone 3 — Document DB Persistence and Query Functions
- **File**: 3.md
- **Status**: complete
- **Summary**: Added persistence, chunk-linking, tree queries, document read queries, and synthetic fallback logic to `documents.py`

### Milestone 4 — Manifest Section Parsing
- **File**: 4.md
- **Status**: complete
- **Summary**: Added `_parse_sections()` to `llms_txt.py` and persisted section metadata into `manifest.json`

### Milestone 5 — Pipeline Integration
- **File**: 5.md
- **Status**: complete
- **Summary**: Added `run_build_tree()` to `pipeline.py`, ran it after `run_index()`, and supported `--stage tree`

### Milestone 6 — MCP Browsing Tools
- **File**: 6.md
- **Status**: complete
- **Summary**: Added `browse_corpus_tool` and `get_document_tool` to `mcp_server.py`

### Milestone 7 — CLI Browse and Read Commands
- **File**: 7.md
- **Status**: complete
- **Summary**: Created `browse.py` with `doc-hub-browse` and `doc-hub-read` console entry points

---

## Dependency Graph

```text
Milestone 1 (DB Schema)
    └─→ Milestone 3 (DB Persistence + Queries)
            └─→ Milestone 5 (Pipeline Integration)
            └─→ Milestone 6 (MCP Tools)
            └─→ Milestone 7 (CLI Commands)

Milestone 4 (Manifest Sections)
    └─→ Milestone 2 (Tree builder consumes manifest sections)
            └─→ Milestone 3 (DB Persistence + Queries)
            └─→ Milestone 5 (Pipeline Integration)

Milestone 2 can be prototyped before Milestone 4 lands, but its section-group branch must match the exact `sections` contract described in Milestone 4.

**Ordering constraints that matter for correctness:**
- Milestone 3 must not start before Milestones 1 and 2 are complete.
- Milestone 5 must not run against a real DB before Milestones 1 and 3 are complete.
- Milestones 6 and 7 depend on Milestone 3’s query API, not just the schema.

---

## Key Design Constraints

### 1. Tree order must be preorder, not `(depth, sort_order)`

The browsing surfaces (`browse_corpus_tool`, `doc-hub-browse`) need a flat list whose iteration order already reflects tree traversal. If rows are globally sorted by `depth`, all root nodes appear first, then all children, which breaks indentation-based rendering.

The implementation must therefore:
- assign a **global monotonic `sort_order`** during tree construction in preorder,
- persist that exact value in `doc_documents.sort_order`, and
- query rows with `ORDER BY sort_order`.

`depth` is still useful for rendering and subtree limits, but it is not the primary ordering key.

### 2. Path inference is only as good as available path information

Current codebase behavior is not fully symmetric across fetchers:
- `llms_txt` produces flat filenames using `url_to_filename()` (`models__openai.md`), so path inference can reconstruct nested paths.
- `local_dir` currently returns the source directory directly, and `MarkdownParser.parse()` only scans `input_dir.glob("*.md")` when no manifest exists. That means nested directories are **not** parsed today, and top-level files keep plain names like `agents.md`.
- Therefore, this plan must not claim that current `local_dir` automatically yields nested hierarchy from filesystem directories. It yields a valid browseable tree, but often a flat one unless filenames already encode `__` path separators.

### 3. Synthetic fallback is for browsing compatibility, not perfect reconstruction

When a corpus has indexed chunks but no `doc_documents` rows yet, browsing must still work. The fallback should be deterministic and safe, but it cannot reconstruct section grouping or parent-child relationships that were never persisted. The fallback is therefore a flat document list derived from chunk rows.

### 4. No live DB or live MCP server required for verification

All new verification must be satisfiable with unit tests and mocks. Optional manual smoke tests may use a local DB, but success criteria cannot depend on external services.

---

## Backward Compatibility

- `doc_chunks.document_id` is nullable, so existing chunk rows remain valid and search/index behavior is unchanged.
- Existing search code keeps reading `doc_chunks`; no search queries depend on `doc_documents`.
- `get_document_tree()` falls back to a synthetic flat list built from `doc_chunks` when no `doc_documents` rows exist for a corpus.
- `get_document_chunks()` must work for both post-tree corpora (`document_id` set) and pre-tree corpora (`document_id IS NULL`) by falling back to `source_file` reconstruction.
- Running `doc-hub-pipeline --corpus SLUG --stage tree` backfills document rows for already-indexed corpora.
- `sync_all()` automatically picks up tree-building because full `run_pipeline(stage=None)` runs the tree stage after indexing.
- `write_manifest(..., sections=None)` remains backward compatible because the new argument is optional and `load_manifest()` already ignores unknown top-level keys.

### Fetcher generality

Tree-building must work for all current and future fetchers, but the output quality depends on what metadata the fetcher/parser pipeline preserves:
- **`llms_txt`**: full support for section grouping plus path inference from `source_file`.
- **`local_dir`**: guaranteed support for flat document browsing; nested hierarchy only appears if parsed `source_file` values encode path segments (for example, pre-flattened filenames containing `__`). Do not assume recursive directory parsing exists today.
- **Future fetchers with `manifest.json` + `sections`**: can participate in section grouping immediately.
- **Future fetchers without a manifest**: still work through flat/path-based inference from `source_file`; if there is no path information in `source_file`, the fallback hierarchy is flat.

---

## Files Summary

| Action | File |
|--------|------|
| Modify | `src/doc_hub/db.py` — schema DDL, indexes, `ensure_schema()` ordering |
| **Create** | `src/doc_hub/documents.py` — tree builder, DB persistence, queries |
| Modify | `src/doc_hub/_builtins/fetchers/llms_txt.py` — section parsing + manifest writing |
| Modify | `src/doc_hub/pipeline.py` — `run_build_tree()` and `tree` stage dispatch |
| Modify | `src/doc_hub/mcp_server.py` — browse/read tools |
| **Create** | `src/doc_hub/browse.py` — browse/read CLI |
| Modify | `pyproject.toml` — CLI entry points |
| **Create** | `tests/test_db_schema.py` |
| **Create** | `tests/test_documents.py` |
| Modify | `tests/test_fetchers.py` |
| Modify | `tests/test_mcp_server.py` |
| **Create** | `tests/test_pipeline_tree.py` |
| **Create** | `tests/test_browse_cli.py` |

## Completion Summary

The full hierarchy/browse/read plan is implemented.

Final verification completed with fresh evidence:

1. `uv run pytest tests/test_db_schema.py tests/test_documents.py tests/test_fetchers.py tests/test_pipeline_tree.py tests/test_mcp_server.py tests/test_browse_cli.py -x` → passed
2. `PGHOST=localhost PGPORT=5433 PGUSER=postgres PGPASSWORD=pydantic-docs PGDATABASE=postgres uv run pytest tests/test_db.py -m integration -q` → `10 passed`
3. `PGHOST=localhost PGPORT=5433 PGUSER=postgres PGPASSWORD=pydantic-docs PGDATABASE=postgres uv run pytest tests/ -x` → `660 passed, 1 warning`
4. MCP tool registration check for `search_docs_tool`, `list_corpora_tool`, `add_corpus_tool`, `refresh_corpus_tool`, `browse_corpus_tool`, and `get_document_tool` → passed

## Verification

Required verification is test-first and mock-based:

1. `uv run pytest tests/test_db_schema.py tests/test_documents.py tests/test_fetchers.py tests/test_pipeline_tree.py tests/test_mcp_server.py tests/test_browse_cli.py -x`
2. `uv run pytest tests/ -x`
3. `uv run python -c "from doc_hub.mcp_server import server; names = [t.name for t in server._tool_manager.list_tools()]; assert set(names) == {'search_docs_tool', 'list_corpora_tool', 'add_corpus_tool', 'refresh_corpus_tool', 'browse_corpus_tool', 'get_document_tool'}"`
4. Optional local smoke test, only when a DB and indexed corpus are already available:
   - `uv run doc-hub-pipeline --corpus pydantic-ai --stage tree`
   - `uv run doc-hub-browse pydantic-ai`
   - `uv run doc-hub-read pydantic-ai agents`

The optional smoke test is not part of the pass/fail gate for the implementation milestone because the repository test environment may not have a live database or corpus data available.
