# Using a Cloud Database

doc-hub works with any hosted PostgreSQL provider. This guide covers connecting to Neon, Supabase, and Railway instead of a local Docker container.

---

## Why a cloud database?

- **Persistent data**: no local container to start/stop; data survives machine reboots and CI teardowns.
- **Shared across environments**: one database serves your laptop, a teammate's machine, and CI pipelines without copying data.
- **No Docker dependency**: useful in environments where Docker is unavailable or inconvenient.

---

## Requirements

| Requirement | Notes |
|-------------|-------|
| PostgreSQL 15+ | Recommended |
| `vchord` extension | Preferred. `ensure_schema()` installs it via `CREATE EXTENSION IF NOT EXISTS vchord CASCADE`. The `CASCADE` automatically installs `pgvector` as a dependency. |
| `pgvector` only | Acceptable if the provider does not support `vchord`. See [pgvector-only providers](#pgvector-only-providers) below. |

---

## Connection string setup

doc-hub resolves the database connection in this order (`_build_dsn()` in `db.py`):

1. Explicit `dsn` argument passed in code
2. `DOC_HUB_DATABASE_URL` environment variable (full connection string)
3. Individual `PG*` environment variables

### Option A: `DOC_HUB_DATABASE_URL`

Set the full connection string the provider gives you:

```bash
export DOC_HUB_DATABASE_URL="postgresql://user:password@host:5432/dbname?sslmode=require"
```

This overrides all `PG*` variables.

### Option B: individual `PG*` variables

```bash
export PGHOST=your-host.example.com
export PGPORT=5432
export PGDATABASE=doc_hub
export PGUSER=your_user
export PGPASSWORD=your_password   # no default — must be set
```

If `PGPASSWORD` is not set and `DOC_HUB_DATABASE_URL` is not set, doc-hub raises:

```
RuntimeError: PGPASSWORD environment variable not set.
Set it directly or use DOC_HUB_DATABASE_URL for the full connection string.
```

### Special characters in passwords

When using individual `PG*` variables, `_build_dsn()` URL-encodes `PGUSER` and `PGPASSWORD` via `urllib.parse.quote_plus`. Characters like `@`, `/`, `%`, and `+` in passwords are handled automatically. If you supply `DOC_HUB_DATABASE_URL` directly, you must URL-encode special characters yourself.

---

## Neon

Neon uses pgbouncer for connection pooling on the "pooled" connection string and provides a direct connection string for session-mode operations.

**Connection string format** from the Neon console:

```
postgresql://<user>:<password>@<endpoint>.neon.tech/<dbname>?sslmode=require
```

**Recommended**: use the **direct** connection string (not the pooled one) for doc-hub. asyncpg opens its own connection pool (`min_size=1, max_size=10`) and pgbouncer's transaction-mode pooling can interfere with prepared statements.

If you must use the pooled connection, add `?sslmode=require&pgbouncer=true` and be aware that prepared statement support is limited.

**Extension installation:**

Neon supports `pgvector` natively. VectorChord (`vchord`) availability depends on your Neon plan and region — check the Neon extension list in your project's settings. If `vchord` is not available, see [pgvector-only providers](#pgvector-only-providers).

**`.env` example:**

```dotenv
DOC_HUB_DATABASE_URL=postgresql://alice:s3cr3t@ep-cool-darkness-123456.us-east-2.aws.neon.tech/doc_hub?sslmode=require
GEMINI_API_KEY=your-key-here
DOC_HUB_VECTOR_DIM=768
```

---

## Supabase

Supabase provides two connection strings per project: a **direct** connection (port 5432) and a **pooled** connection via pgbouncer (port 6543). Use the **direct** connection for doc-hub.

**Finding the connection string**: Project settings → Database → Connection string → URI (uncheck "Use connection pooling").

**Extension installation:**

`pgvector` is pre-installed on all Supabase projects. VectorChord (`vchord`) is not currently available on Supabase — see [pgvector-only providers](#pgvector-only-providers).

**`.env` example:**

```dotenv
DOC_HUB_DATABASE_URL=postgresql://postgres:your-password@db.abcdefghijkl.supabase.co:5432/postgres
GEMINI_API_KEY=your-key-here
DOC_HUB_VECTOR_DIM=768
```

---

## Railway

Railway exposes the connection string as `DATABASE_URL` in your service's variable panel.

**Finding the connection string**: Service → Variables → `DATABASE_URL`.

**Extension installation:**

Railway uses a standard PostgreSQL image. You must install extensions manually before running doc-hub:

```sql
-- Connect as superuser and run:
CREATE EXTENSION IF NOT EXISTS vchord CASCADE;
-- or, if vchord is unavailable:
CREATE EXTENSION IF NOT EXISTS vector;
```

Alternatively, let `ensure_schema()` handle it on first startup — it runs `CREATE EXTENSION IF NOT EXISTS vchord CASCADE` automatically.

**`.env` example:**

```dotenv
DOC_HUB_DATABASE_URL=postgresql://postgres:password@monorail.proxy.rlwy.net:12345/railway
GEMINI_API_KEY=your-key-here
DOC_HUB_VECTOR_DIM=768
```

---

## pgvector-only providers

If your provider supports `pgvector` but not `vchord`, `ensure_schema()` will fail because it attempts `CREATE EXTENSION IF NOT EXISTS vchord CASCADE`. VectorChord is a hard requirement for the vector index — there is no built-in fallback to `pgvector`-only mode.

**Options:**

1. Choose a provider that supports `vchord` (Neon on supported plans, Railway with a custom image, self-managed PostgreSQL with the `tensorchord/vchord-postgres` Docker image).
2. If you control the PostgreSQL instance, install VectorChord manually: follow the [VectorChord installation docs](https://github.com/tensorchord/VectorChord).

---

## Vector dimension considerations

The `embedding` column in `doc_chunks` is created as `vector(N)` where `N` comes from `DOC_HUB_VECTOR_DIM` (default: `768`).

```bash
export DOC_HUB_VECTOR_DIM=768   # must match the embedder's output dimension
```

**This value must be consistent** across all environments that connect to the same database. If a machine uses a different value, `ensure_schema()` detects the mismatch and raises:

```
RuntimeError: Existing doc_chunks table has vector(768) but DOC_HUB_VECTOR_DIM=1536. To fix this, either:
  1. Set DOC_HUB_VECTOR_DIM=768 to match the existing table, or
  2. DROP TABLE doc_chunks and let doc-hub recreate it with the new dimension.
     (This will delete all indexed data — re-index all corpora after.)
```

The detection works by querying `pg_attribute.atttypmod` for the existing `embedding` column. `CREATE TABLE IF NOT EXISTS` silently preserves the old schema, so without this check a dimension mismatch would surface as a cryptic INSERT failure instead.

The Gemini `gemini-embedding-001` embedder produces 768-dimensional vectors; `DOC_HUB_VECTOR_DIM=768` is the correct value for it.

---

## Migration from local to cloud

`ensure_schema()` is idempotent — it creates tables and indexes if they don't exist, and is safe to run on every startup. Schema migration to a new cloud database is automatic.

**Data is not migrated automatically.** You must re-index all corpora after switching databases:

```bash
# Re-index a single corpus
doc-hub pipeline run --corpus <slug>

# Re-index all enabled corpora
doc-hub pipeline sync-all
```

**Recommended migration steps:**

1. Set `DOC_HUB_DATABASE_URL` (or `PG*` vars) to point at the cloud database.
2. Verify the connection:
   ```bash
   psql "$DOC_HUB_DATABASE_URL" -c "SELECT version();"
   ```
3. Run the pipeline — `ensure_schema()` creates the schema on first connect:
   ```bash
   doc-hub pipeline run --corpus <slug>
   ```
4. Repeat for remaining corpora, or use `doc-hub pipeline sync-all` to process all at once.

Local data in `data/<slug>/raw/` and `data/<slug>/chunks/` is reused by default. Pass `--clean` to force a full re-fetch from the source.
