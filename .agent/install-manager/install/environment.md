# Environment Setup

## Minimum required variables

```bash
export PGPASSWORD=your_db_password
export GEMINI_API_KEY=your_key_here
```

## Common local Docker example

```bash
export PGHOST=localhost
export PGPORT=5433
export PGUSER=postgres
export PGPASSWORD=mysecretpassword
export PGDATABASE=postgres
export GEMINI_API_KEY=your_key_here
```

## Optional full connection string

```bash
export DOC_HUB_DATABASE_URL="postgresql://postgres:your_password@localhost:5433/postgres"
```

## Recommended `.env`

Create `.env` in the repo root:

```dotenv
GEMINI_API_KEY=your-key-here
PGHOST=localhost
PGPORT=5433
PGUSER=postgres
PGPASSWORD=mysecretpassword
PGDATABASE=postgres
```

## Verify env state

```bash
./.agent/install-manager/scripts/check-env.sh
```

## Interpretation

- Missing `PGPASSWORD` or `DOC_HUB_DATABASE_URL` means DB operations will fail
- Missing `GEMINI_API_KEY` means indexing/embed/search operations that need embeddings will fail
