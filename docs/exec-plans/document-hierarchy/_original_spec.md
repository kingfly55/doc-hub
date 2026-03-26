# Plan: Inter-Document Hierarchy and Document Browsing

## Context

doc-hub is a semantic search engine — it excels at "ask a question, get chunks." But it has no support for browsing, navigating, or reading documentation in context. Documents don't exist as first-class entities. There are no parent-child relationships between documents. An LLM agent using doc-hub cannot browse a corpus's structure, read a full page, or understand how documentation is organized.

The data to build hierarchy largely exists already: URL paths encode parent-child relationships, llms.txt files often group URLs under section headers, and within-document heading structure is captured in `section_path`. The gap is that none of this is stored as queryable structure or exposed through tools.

**Goal:** Add a `doc_documents` table for inter-document hierarchy, build trees from fetcher hints + URL path inference, and expose browsing/reading via new MCP tools and CLI commands.

---

## Phase 1: DB Schema (`src/doc_hub/db.py`)

Add `doc_documents` table to `ensure_schema()`:

```sql
CREATE TABLE IF NOT EXISTS doc_documents (
    id           serial PRIMARY KEY,
    corpus_id    text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    doc_path     text NOT NULL,           -- "api/models/openai"
    title        text NOT NULL,
    source_url   text NOT NULL DEFAULT '',
    source_file  text NOT NULL DEFAULT '',
    parent_id    int REFERENCES doc_documents(id) ON DELETE CASCADE,
    depth        smallint NOT NULL DEFAULT 0,
    sort_order   int NOT NULL DEFAULT 0,
    is_group     boolean NOT NULL DEFAULT false,
    total_chars  int NOT NULL DEFAULT 0,
    section_count int NOT NULL DEFAULT 0,
    UNIQUE (corpus_id, doc_path)
);
```

Add nullable `document_id` FK to `doc_chunks`:
```sql
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS document_id int
    REFERENCES doc_documents(id) ON DELETE SET NULL;
```

Indexes: `(corpus_id)`, `(parent_id)`, `(corpus_id, doc_path text_pattern_ops)`, `(corpus_id, depth, sort_order)`, `(document_id)` on doc_chunks.

All DDL is idempotent (IF NOT EXISTS / IF NOT EXISTS). No migration framework needed.

---

## Phase 2: Tree Building Core (new `src/doc_hub/documents.py`)

The heart of the feature. Centralized logic that works for all fetcher types.

### Data structure

```python
@dataclass
class DocumentNode:
    doc_path: str           # "api/models/openai"
    title: str              # "OpenAI" (from first H1 or humanized path)
    source_url: str
    source_file: str
    parent_path: str | None # "api/models" or None for root
    depth: int
    sort_order: int
    is_group: bool          # True for virtual group nodes (no content)
    total_chars: int
    section_count: int
```

### Tree building algorithm

`build_document_tree(raw_dir, chunks, corpus_slug, fetch_strategy) -> list[DocumentNode]`

1. Load `manifest.json` sections if present (llms_txt hint)
2. Build `{source_file: doc_path}` map from chunks (e.g. `"api__models__openai.md"` -> `"api/models/openai"`)
3. **If sections available** (from llms.txt):
   - Create group nodes from section titles (e.g. `"_group/api-reference"`)
   - Assign documents to groups by matching URLs
   - Infer sub-hierarchy from URL path segments within each group
4. **If no sections** (local_dir, git_repo, or llms.txt without headers):
   - Infer full hierarchy from doc_paths using `_infer_hierarchy_from_paths()`
   - Create virtual group nodes for intermediate path segments with no corresponding file
5. Compute `total_chars` and `section_count` per document from chunks
6. Derive `title` from first H1 chunk heading, falling back to humanized path

### DB persistence

- `upsert_documents(pool, corpus, nodes)` -> `{doc_path: id}` — two-pass: insert all nodes, then set parent_ids
- `link_chunks_to_documents(pool, corpus, mapping)` -> count updated — UPDATE doc_chunks SET document_id
- `delete_stale_documents(pool, corpus, current_paths)` — remove docs no longer in the tree

### Query functions

- `get_document_tree(pool, corpus, path?, depth?)` -> tree as list of dicts
  - Fallback: if no doc_documents rows exist, build synthetic flat list from `SELECT DISTINCT source_file FROM doc_chunks`
- `get_document_chunks(pool, corpus, doc_path, section?)` -> chunks ordered by start_line
  - Joins via document_id; falls back to source_file match for unlinked chunks
- `get_document_sections(pool, corpus, doc_path)` -> within-doc heading outline with char counts

---

## Phase 3: Manifest Enhancement (`src/doc_hub/_builtins/fetchers/llms_txt.py`)

### New function: `_parse_sections(llms_txt_content, url_pattern) -> list[dict]`

Parse markdown headings from the llms.txt content and track which extracted URLs fall under each heading. Return:
```json
[{"title": "API Reference", "heading_level": 2, "urls": ["https://...", ...]}]
```

### Changes
- `write_manifest()` gains `sections` parameter, writes `"sections"` key to manifest.json
- `LlmsTxtFetcher.fetch()` calls `_parse_sections()` after URL extraction, passes result to `write_manifest()`
- ~30 lines of new code. The fetcher does NOT build the tree — just writes hints.

Other fetchers: no changes required. Pipeline fallback handles them.

---

## Phase 4: Pipeline Integration (`src/doc_hub/pipeline.py`)

### New function: `run_build_tree(corpus, chunks?, pool?)`

Runs after `run_index()` in the full pipeline. Calls `build_document_tree()` then persists via `upsert_documents()` + `link_chunks_to_documents()`.

### Changes to `run_pipeline()`
- After index stage completes, call `run_build_tree()`
- Add `"tree"` as valid `--stage` choice so users can rebuild trees without re-indexing/re-embedding

---

## Phase 5: MCP Tools (`src/doc_hub/mcp_server.py`)

### `browse_corpus_tool(corpus, path?, depth?)`

Returns document tree as list of dicts: `{doc_path, title, source_url, depth, is_group, total_chars, section_count, children_count}`.

### `get_document_tool(corpus, doc_path, section?, force?)`

Returns full document content (all chunks concatenated in order).

**Large document handling:** If `total_chars > 20_000` and `force=False` and `section=None`:
- Returns `{"mode": "outline", ...}` with section headings and char counts
- Includes hint: "Use section='...' to read a specific section, or force=True"

Otherwise returns `{"mode": "full", "content": "...", ...}`.

Both tools get `_*_impl()` wrappers for direct testing (matching existing pattern).

---

## Phase 6: CLI (new `src/doc_hub/browse.py`, `pyproject.toml`)

### `doc-hub-browse CORPUS [--path PATH] [--depth N] [--json]`

Prints tree view:
```
pydantic-ai
+-- API Reference                  [group]
|   +-- Agent                      5,432 chars  12 sections
|   +-- Models                     [group]
|       +-- OpenAI                 2,100 chars   4 sections
+-- Guides                         [group]
    +-- Getting Started            3,800 chars   8 sections
```

### `doc-hub-read CORPUS DOC_PATH [--section S] [--force] [--json]`

Reads a document. If over threshold without `--force`:
```
Document "Getting Started" has 12 chunks (8,200 chars).
Consider browsing specific sections:

  Prerequisites (450 chars)
  Installation (1,200 chars)
    Option A: Virtual Environment (380 chars)
    Option B: Isolated CLI Tool (420 chars)
  Configuration (2,100 chars)
  ...

Use --section 'Installation' to read a specific section,
or --force to read the entire document.
```

### pyproject.toml additions
```toml
doc-hub-browse = "doc_hub.browse:main"
doc-hub-read   = "doc_hub.browse:read_main"
```

---

## Phase 7: Tests

New `tests/test_documents.py`:
- `test_build_tree_from_url_paths` — flat files infer hierarchy
- `test_build_tree_with_manifest_sections` — llms.txt sections create groups
- `test_build_tree_flat_fallback` — no hierarchy info -> flat list
- `test_title_derivation` — from H1 heading vs filename
- `test_doc_path_from_source_file` — `__` encoding conversion
- `test_virtual_group_nodes` — intermediate paths create groups

Update `tests/test_fetchers.py`:
- `test_parse_sections_from_llms_txt` — verify section extraction
- `test_manifest_includes_sections` — verify manifest format

Update `tests/test_mcp_server.py`:
- `test_browse_corpus_tool` / `test_get_document_tool`

Integration tests (mark `@pytest.mark.integration`):
- `test_upsert_documents_and_link_chunks`
- `test_get_document_tree_query`
- `test_synthetic_tree_fallback`

---

## Backward Compatibility

- `document_id` on `doc_chunks` is nullable — existing chunks stay NULL, search is unaffected
- `browse_corpus_tool` falls back to synthetic flat list from `doc_chunks` when no `doc_documents` rows exist
- `get_document_tool` falls back to `source_file` matching when `document_id` is NULL
- Running `doc-hub-pipeline --corpus SLUG --stage tree` backfills documents for existing corpora
- `doc-hub-sync-all` automatically builds trees for all corpora

---

## Files Summary

| Action | File |
|--------|------|
| Modify | `src/doc_hub/db.py` — DDL + ensure_schema |
| **Create** | `src/doc_hub/documents.py` — tree building + DB CRUD |
| Modify | `src/doc_hub/_builtins/fetchers/llms_txt.py` — section parsing |
| Modify | `src/doc_hub/pipeline.py` — run_build_tree + stage dispatch |
| Modify | `src/doc_hub/mcp_server.py` — 2 new tools |
| **Create** | `src/doc_hub/browse.py` — CLI: browse + read |
| Modify | `pyproject.toml` — 2 new entry points |
| **Create** | `tests/test_documents.py` |
| Modify | `tests/test_fetchers.py`, `tests/test_mcp_server.py` |

## Verification

1. `uv run pytest tests/ -x` — all existing + new tests pass
2. `uv run doc-hub-pipeline --corpus pydantic-ai --stage tree` — builds tree for existing corpus
3. `uv run doc-hub-browse pydantic-ai` — prints tree
4. `uv run doc-hub-read pydantic-ai "agents"` — shows content or outline
5. `uv run doc-hub-read pydantic-ai "agents" --section "Tools"` — reads specific section
6. MCP tools: `browse_corpus_tool` and `get_document_tool` callable via test harness
