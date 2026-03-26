# Database Initialization

## How initialization works

Database setup is performed automatically by `ensure_schema()` in `src/doc_hub/db.py`.

When a DB-backed command runs, doc-hub can:
- create the `vchord` extension
- create required tables
- create indexes
- validate vector dimension compatibility
- migrate some legacy schema cases

## First-run initialization check

```bash
doc-hub pipeline run --corpus pydantic-ai --stage tree
```

This assumes the corpus already exists. If it does not, register it first via MCP or SQL.

## Direct connectivity sanity check

```bash
./.agent/install-manager/scripts/check-db.sh
```

## Expected outcomes

- connection succeeds
- extension status is reported
- key tables like `doc_corpora`, `doc_chunks`, `doc_documents` are visible once initialized
