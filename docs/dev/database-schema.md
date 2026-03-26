# Database Schema

**Source files:** `src/doc_hub/db.py`, `src/doc_hub/index.py`

Three tables: `doc_corpora`, `doc_chunks`, `doc_index_meta`. Seven indexes. Schema created
idempotently by `ensure_schema(pool)`.

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

| Column | Type | Notes |
|---|---|---|
| `slug` | `text PK` | Unique corpus identifier; used as FK in other tables |
| `name` | `text` | Human-readable display name |
| `fetch_strategy` | `text` | Name of the registered fetcher plugin |
| `parser` | `text` | Name of the registered parser plugin (default: `'markdown'`) |
| `embedder` | `text` | Name of the registered embedder plugin (default: `'gemini'`) |
| `fetch_config` | `jsonb` | Strategy-specific config dict; passed verbatim to `Fetcher.fetch()` |
| `enabled` | `boolean` | Whether this corpus participates in `sync_all()` (default: `true`) |
| `last_indexed_at` | `timestamptz` | Set by `update_corpus_stats()` after each index run; nullable |
| `total_chunks` | `int` | Set by `update_corpus_stats()` after each index run (default: `0`) |

**CRUD helpers** (all in `db.py`):

| Function | Description |
|---|---|
| `get_corpus(pool, slug)` | Returns `Corpus` or `None` |
| `list_corpora(pool, enabled_only=True)` | Returns `list[Corpus]`; filters `enabled = true` by default |
| `upsert_corpus(pool, corpus)` | `INSERT … ON CONFLICT (slug) DO UPDATE`; does NOT touch `last_indexed_at` / `total_chunks` |
| `update_corpus_stats(pool, slug, total_chunks)` | Sets `last_indexed_at = now()` and `total_chunks` |

---

## `doc_chunks`

Main chunks table. One row per unique `(corpus_id, content_hash)` pair.

`_chunks_ddl()` is a **function** (not a constant) because the vector dimension is configurable
via `DOC_HUB_VECTOR_DIM`. With the default dimension of 768:

```sql
CREATE TABLE IF NOT EXISTS doc_chunks (
    id           serial PRIMARY KEY,
    corpus_id    text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    content_hash text NOT NULL,
    heading      text NOT NULL,
    content      text NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (
                     setweight(to_tsvector('english', heading), 'A') ||
                     setweight(to_tsvector('english', content), 'B')
                 ) STORED,
    embedding    vector(768) NOT NULL,
    source_file  text NOT NULL,
    source_url   text NOT NULL,
    section_path text NOT NULL,
    heading_level smallint NOT NULL,
    start_line   int NOT NULL DEFAULT 0,
    end_line     int NOT NULL DEFAULT 0,
    char_count   int NOT NULL,
    category     text NOT NULL,
    UNIQUE (corpus_id, content_hash)
)
```

| Column | Type | Notes |
|---|---|---|
| `id` | `serial PK` | Auto-incrementing surrogate key |
| `corpus_id` | `text FK` | References `doc_corpora(slug)` with `ON DELETE CASCADE` |
| `content_hash` | `text` | SHA-256 hex of the chunk content; part of unique constraint |
| `heading` | `text` | Section heading extracted by parser |
| `content` | `text` | Full chunk text |
| `tsv` | `tsvector GENERATED STORED` | Weighted full-text index: heading → weight A, content → weight B |
| `embedding` | `vector({dim})` | L2-normalized embedding; dimension from `DOC_HUB_VECTOR_DIM` |
| `source_file` | `text` | Relative path of the source file within the corpus raw dir |
| `source_url` | `text` | Reconstructed URL for this chunk |
| `section_path` | `text` | `/`-separated heading path within the document |
| `heading_level` | `smallint` | 1–6; 0 for preamble |
| `start_line` | `int` | 1-indexed; `0` if unknown |
| `end_line` | `int` | 1-indexed, inclusive; `0` if unknown |
| `char_count` | `int` | `len(content)` |
| `category` | `text` | Derived by `parse.py`; one of: `api`, `example`, `eval`, `guide`, `other` |

**Key constraints:**

- `UNIQUE (corpus_id, content_hash)` — prevents duplicate chunks within a corpus; drives `ON CONFLICT` upsert in `index.py`.
- `ON DELETE CASCADE` from `doc_corpora` — deleting a corpus row deletes all its chunks.
- `heading` and `content` columns appear **before** `tsv` in DDL so PostgreSQL can resolve the generated expression at `CREATE TABLE` time.

---

## `doc_index_meta`

Per-corpus key/value metadata. Written after each index run.

```sql
CREATE TABLE IF NOT EXISTS doc_index_meta (
    corpus_id  text NOT NULL REFERENCES doc_corpora(slug) ON DELETE CASCADE,
    key        text NOT NULL,
    value      text NOT NULL,
    updated_at timestamptz DEFAULT now(),
    PRIMARY KEY (corpus_id, key)
)
```

| Column | Type | Notes |
|---|---|---|
| `corpus_id` | `text FK` | References `doc_corpora(slug)` with `ON DELETE CASCADE` |
| `key` | `text` | Metadata key; part of composite PK |
| `value` | `text` | Metadata value (always stored as text) |
| `updated_at` | `timestamptz` | Set to `now()` on each upsert |

**Keys written by `_write_meta()` in `index.py`:**

| Key | Value format |
|---|---|
| `last_indexed_at` | ISO 8601 timestamp (UTC) |
| `total_chunks` | Integer as string |
| `embedding_model` | Embedder `model_name` string |
| `embedding_dimensions` | Vector dimensions as string |

`_write_meta()` uses `INSERT … ON CONFLICT (corpus_id, key) DO UPDATE` so each key is
upserted independently. Called automatically by `upsert_chunks()` after every index run.

---

## Indexes

All created via `IF NOT EXISTS` — idempotent.

```sql
CREATE INDEX IF NOT EXISTS doc_chunks_corpus_id_idx
    ON doc_chunks (corpus_id);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_tsv_idx
    ON doc_chunks USING gin (tsv);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_category_idx
    ON doc_chunks (corpus_id, category);

CREATE INDEX IF NOT EXISTS doc_chunks_corpus_hash_idx
    ON doc_chunks (corpus_id, content_hash);

CREATE INDEX IF NOT EXISTS doc_chunks_source_url_idx
    ON doc_chunks (source_url text_pattern_ops);

CREATE INDEX IF NOT EXISTS doc_chunks_section_path_idx
    ON doc_chunks (section_path text_pattern_ops);

CREATE INDEX IF NOT EXISTS doc_chunks_heading_level_idx
    ON doc_chunks (heading_level);
```

| Index | Type | Purpose |
|---|---|---|
| `doc_chunks_corpus_id_idx` | B-tree | Corpus-scoped queries and joins |
| `doc_chunks_corpus_tsv_idx` | GIN | Full-text search via `tsv @@ query` |
| `doc_chunks_corpus_category_idx` | B-tree (composite) | Category filter scoped by corpus |
| `doc_chunks_corpus_hash_idx` | B-tree (composite) | Hash lookups and stale-deletion scoped by corpus |
| `doc_chunks_source_url_idx` | B-tree (`text_pattern_ops`) | `LIKE 'prefix%'` scans on `source_url` |
| `doc_chunks_section_path_idx` | B-tree (`text_pattern_ops`) | `LIKE 'prefix%'` scans on `section_path` |
| `doc_chunks_heading_level_idx` | B-tree | Filter by heading level |

**GIN limitation:** GIN indexes do not support composite keys. The GIN index is on `tsv`
alone. Corpus-scoped FTS queries combine the GIN scan with the separate B-tree
`doc_chunks_corpus_id_idx`. This is a known PostgreSQL constraint, not a design gap.

**`text_pattern_ops`:** Used for `source_url` and `section_path` to support locale-independent
LIKE prefix scans (e.g., `source_url LIKE 'https://docs.example.com/%'`).

---

## Vector Dimension Configuration

The `embedding` column type is `vector({dim})` where `{dim}` comes from:

```python
# db.py
def get_vector_dim() -> int:
    raw = os.getenv("DOC_HUB_VECTOR_DIM", "768")
    ...
    return dim
```

`DOC_HUB_VECTOR_DIM` defaults to `768`. Must be a positive integer.

**Dimension mismatch detection:** `ensure_schema()` queries `pg_attribute` to read the
`atttypmod` of the existing `embedding` column after `CREATE TABLE IF NOT EXISTS`. If the
configured dimension differs from the existing column dimension, it raises `RuntimeError`:

```
Existing doc_chunks table has vector(768) but DOC_HUB_VECTOR_DIM=1536. To fix this, either:
  1. Set DOC_HUB_VECTOR_DIM=768 to match the existing table, or
  2. DROP TABLE doc_chunks and let doc-hub recreate it with the new dimension.
     (This will delete all indexed data — re-index all corpora after.)
```

This prevents silent failures where `CREATE TABLE IF NOT EXISTS` preserves the old schema
but subsequent INSERTs fail with cryptic dimension mismatch errors.

---

## JSONB Codec

asyncpg does **not** auto-serialize Python dicts to/from `jsonb`. Without a codec,
asyncpg raises `TypeError` on write and returns raw JSON strings on read.

`_init_connection()` registers a custom codec on every new connection:

```python
# db.py
async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
```

This is passed as the `init` callback to `asyncpg.create_pool()`, so every connection in
the pool gets it automatically. After registration, Python `dict` ↔ JSONB round-trips
transparently at all call sites.

`upsert_corpus()` also calls `json.dumps(corpus.fetch_config)` explicitly as a
belt-and-suspenders measure — safe even when the codec is active.

---

## Connection Pool

```python
# db.py
pool = await asyncpg.create_pool(
    resolved_dsn,
    min_size=1,
    max_size=10,
    init=_init_connection,
)
```

Pool size: `min_size=1, max_size=10`.

**DSN resolution order** (in `_build_dsn()`):

1. Explicit `dsn` argument to `create_pool()`
2. `DOC_HUB_DATABASE_URL` env var (full connection string)
3. Individual `PG*` env vars: `PGHOST` (default `localhost`), `PGPORT` (default `5432`),
   `PGDATABASE` (default `doc_hub`), `PGUSER` (default `postgres`), `PGPASSWORD` (no default — must be set)

---

## Advisory Locks

`upsert_chunks()` acquires a per-corpus advisory lock at the start of each index transaction:

```sql
SELECT pg_advisory_xact_lock(hashtext($1))
```

where `$1` is the corpus slug. The lock is **transaction-scoped** — released automatically
on commit or rollback.

This prevents concurrent index operations on the same corpus (e.g., overlapping MCP refresh
and cron sync) from interleaving writes. Locks for different corpora are independent.

---

## `xmax = 0` INSERT/UPDATE Detection

The upsert SQL in `index.py` uses a `RETURNING` clause to distinguish true inserts from
conflict-triggered updates:

```sql
INSERT INTO doc_chunks (...)
VALUES (...)
ON CONFLICT (corpus_id, content_hash) DO UPDATE SET ...
RETURNING (xmax = 0) AS is_insert
```

`xmax = 0` is true for a freshly inserted row; for an `ON CONFLICT DO UPDATE` row, `xmax`
holds the transaction ID of the updating transaction and is non-zero. This is used to
maintain accurate `inserted` vs `updated` counts in `IndexResult`.

Note: asyncpg's `execute()` always returns `'INSERT 0 1'` for both branches of an
`ON CONFLICT DO UPDATE` — the status string alone cannot distinguish inserts from updates,
hence the `RETURNING` approach.

---

## `_parse_command_count()`

```python
# index.py
def _parse_command_count(status: str) -> int:
```

Parses asyncpg execute status strings like `'DELETE 5'`, `'INSERT 0 1'`, `'UPDATE 3'`.
The last whitespace-separated token is the affected row count. Returns `0` on parse failure.

Used to extract the deleted-row count from the stale-cleanup `DELETE` in full-mode indexing.
