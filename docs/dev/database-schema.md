# Database Schema

**Source files:** `src/doc_hub/db.py`, `src/doc_hub/index.py`

Five core tables: `doc_corpora`, `doc_versions`, `doc_version_aliases`, `doc_chunks`, `doc_documents`, plus `doc_index_meta` for compatibility metadata. Schema is created idempotently by `ensure_schema(pool)`.

Extension required: **VectorChord** (`vchord CASCADE` — also installs pgvector as a dependency).

---

## `doc_corpora`

Registry of documentation corpora. One row per corpus.

```sql
CREATE TABLE IF NOT EXISTS doc_corpora (
    slug            text PRIMARY KEY,
    name            text NOT NULL,
    fetch_strategy  text NOT NULL,
    parser          text NOT NULL DEFAULT 'markdown',
    embedder        text NOT NULL DEFAULT 'gemini',
    fetch_config    jsonb NOT NULL,
    enabled         boolean DEFAULT true,
    last_indexed_at timestamptz,
    total_chunks    int DEFAULT 0
)
```

`doc_corpora` remains the stable corpus registry. Version-specific state belongs to `doc_versions`.

---

## `doc_versions`

Immutable documentation snapshots for a corpus.

```sql
CREATE TABLE IF NOT EXISTS doc_versions (
    corpus_id         text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    snapshot_id       text NOT NULL,
    source_version    text NOT NULL,
    resolved_version  text,
    source_type       text NOT NULL,
    source_url        text NOT NULL,
    fetch_strategy    text NOT NULL,
    fetch_config_hash text NOT NULL,
    url_set_hash      text,
    content_hash      text NOT NULL,
    fetched_at        timestamptz NOT NULL,
    indexed_at        timestamptz,
    total_chunks      int DEFAULT 0,
    enabled           boolean DEFAULT true,
    metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (corpus_id, snapshot_id)
)
```

| Column | Notes |
|---|---|
| `snapshot_id` | Immutable doc-hub snapshot identifier. |
| `source_version` | Human/source label such as `latest`, `18`, `main`, or `v1.2.3`. |
| `resolved_version` | Immutable upstream revision when available, such as a Git commit SHA. |
| `fetch_config_hash` | Hash of source-selection-relevant fetch config. |
| `url_set_hash` | Hash of normalized fetched URL/file set when available. |
| `content_hash` | Hash of the normalized fetched content set. |
| `fetched_at` | Source fetch timestamp, distinct from index time. |
| `indexed_at` | Last successful index timestamp for this snapshot. |

---

## `doc_version_aliases`

Mutable alias pointers such as `latest` or `stable`.

```sql
CREATE TABLE IF NOT EXISTS doc_version_aliases (
    corpus_id   text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    alias       text NOT NULL,
    snapshot_id text NOT NULL,
    updated_at  timestamptz DEFAULT now(),
    PRIMARY KEY (corpus_id, alias),
    FOREIGN KEY (corpus_id, snapshot_id) REFERENCES doc_versions(corpus_id, snapshot_id) ON DELETE CASCADE
)
```

Aliases are convenience selectors over immutable snapshots. Search and browse code must resolve an alias to a concrete `snapshot_id` before querying chunks or documents.

---

## `doc_chunks`

Main chunks table. One row per unique `(corpus_id, snapshot_id, content_hash)` tuple.

`_chunks_ddl()` is a function because the vector dimension is configurable via `DOC_HUB_VECTOR_DIM`. With the default dimension of 768:

```sql
CREATE TABLE IF NOT EXISTS doc_chunks (
    id             serial PRIMARY KEY,
    corpus_id      text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    content_hash   text NOT NULL,
    heading        text NOT NULL,
    content        text NOT NULL,
    tsv            tsvector GENERATED ALWAYS AS (
                       setweight(to_tsvector('english', heading), 'A') ||
                       setweight(to_tsvector('english', content), 'B')
                   ) STORED,
    embedding      vector(768) NOT NULL,
    source_file    text NOT NULL,
    source_url     text NOT NULL,
    snapshot_id    text NOT NULL DEFAULT 'legacy',
    source_version text NOT NULL DEFAULT 'latest',
    fetched_at     timestamptz,
    section_path   text NOT NULL,
    heading_level  smallint NOT NULL,
    start_line     int NOT NULL DEFAULT 0,
    end_line       int NOT NULL DEFAULT 0,
    char_count     int NOT NULL,
    category       text NOT NULL,
    document_id    int REFERENCES doc_documents(id) ON DELETE SET NULL,
    UNIQUE (corpus_id, snapshot_id, content_hash)
)
```

Important constraints:

- `UNIQUE (corpus_id, snapshot_id, content_hash)` prevents duplicate chunks within one snapshot while allowing identical content across versions.
- Full reindex stale deletion is scoped by both `corpus_id` and `snapshot_id`.
- `fetched_at` is source provenance; `indexed_at` belongs to `doc_versions`.

---

## `doc_documents`

Version-scoped document tree used by browse/read commands.

```sql
CREATE TABLE IF NOT EXISTS doc_documents (
    id serial PRIMARY KEY,
    corpus_id text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    snapshot_id text NOT NULL DEFAULT 'legacy',
    source_version text NOT NULL DEFAULT 'latest',
    doc_path text NOT NULL,
    title text NOT NULL,
    source_url text NOT NULL DEFAULT '',
    source_file text NOT NULL DEFAULT '',
    parent_id int REFERENCES doc_documents(id) ON DELETE SET NULL,
    depth smallint NOT NULL DEFAULT 0,
    sort_order int NOT NULL DEFAULT 0,
    is_group boolean NOT NULL DEFAULT false,
    total_chars int NOT NULL DEFAULT 0,
    section_count int NOT NULL DEFAULT 0,
    UNIQUE (corpus_id, snapshot_id, doc_path)
)
```

Document paths are only unique within a snapshot. The same `doc_path` may exist in multiple snapshots for the same corpus.

---

## `doc_index_meta`

Compatibility per-corpus key/value metadata. New version-level stats should prefer `doc_versions`.

```sql
CREATE TABLE IF NOT EXISTS doc_index_meta (
    corpus_id  text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    key        text NOT NULL,
    value      text NOT NULL,
    updated_at timestamptz DEFAULT now(),
    PRIMARY KEY (corpus_id, key)
)
```

Keys written by `_write_meta()` in `index.py`:

| Key | Value format |
|---|---|
| `last_indexed_at` | ISO 8601 timestamp (UTC) |
| `total_chunks` | Integer as string |
| `embedding_model` | Embedder `model_name` string |
| `embedding_dimensions` | Vector dimensions as string |

---

## Indexes

```sql
CREATE INDEX IF NOT EXISTS doc_chunks_corpus_id_idx
    ON doc_chunks (corpus_id);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_tsv_idx
    ON doc_chunks USING gin (tsv);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_category_idx
    ON doc_chunks (corpus_id, category);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_hash_idx
    ON doc_chunks (corpus_id, snapshot_id, content_hash);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_snapshot_idx
    ON doc_chunks (corpus_id, snapshot_id);

CREATE INDEX IF NOT EXISTS doc_chunks_source_url_idx
    ON doc_chunks (source_url text_pattern_ops);

CREATE INDEX IF NOT EXISTS doc_chunks_section_path_idx
    ON doc_chunks (section_path text_pattern_ops);

CREATE INDEX IF NOT EXISTS doc_chunks_heading_level_idx
    ON doc_chunks (heading_level);

CREATE INDEX IF NOT EXISTS doc_documents_corpus_id_idx
    ON doc_documents (corpus_id);

CREATE INDEX IF NOT EXISTS doc_documents_parent_id_idx
    ON doc_documents (parent_id);

CREATE INDEX IF NOT EXISTS doc_documents_corpus_sort_order_idx
    ON doc_documents (corpus_id, snapshot_id, sort_order);

CREATE INDEX IF NOT EXISTS doc_documents_corpus_path_idx
    ON doc_documents (corpus_id, snapshot_id, doc_path text_pattern_ops);
```

The GIN index remains on `tsv` alone because GIN indexes do not support composite keys. Version-scoped FTS combines the GIN scan with B-tree filters on `corpus_id` and `snapshot_id`.

---

## Legacy Migration Notes

`ensure_schema()` creates new tables and also repairs older schemas in place:

- `doc_corpora.parser` and `doc_corpora.embedder` are added if missing.
- `doc_chunks.snapshot_id`, `doc_chunks.source_version`, and `doc_chunks.fetched_at` are added if missing.
- `doc_documents.snapshot_id` and `doc_documents.source_version` are added if missing.

Legacy rows default to `snapshot_id = 'legacy'` and `source_version = 'latest'`. A later refresh can create immutable snapshot rows in `doc_versions`.

---

## Vector Dimension Configuration

The `embedding` column type is `vector({dim})` where `{dim}` comes from `DOC_HUB_VECTOR_DIM`, defaulting to 768. `ensure_schema()` checks the existing column dimension and raises `RuntimeError` if it differs from the configured dimension.

---

## JSONB Codec

asyncpg does not auto-serialize Python dicts to/from `jsonb`. `_init_connection()` registers a JSONB codec on every connection in the pool so Python `dict` values round-trip transparently.
