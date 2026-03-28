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

## Recommended machine-wide env file

When `doc-hub` is installed on PATH and may be run from outside this repository, keep a durable env file under the doc-hub XDG data directory:

```bash
mkdir -p ~/.local/share/doc-hub
cat > ~/.local/share/doc-hub/env <<'EOF'
PGHOST=localhost
PGPORT=5433
PGUSER=postgres
PGPASSWORD=your-password
PGDATABASE=postgres
GEMINI_API_KEY=your-key-here
EOF
```

`doc-hub` loads this file after any repo-local `.env`, so local clone overrides still work.

For installed CLI usage, `doc-hub man` does not require DB or Gemini credentials and is the built-in fallback when `man doc-hub` is unavailable because the shell has not picked up the tool-installed manpath yet.

## Optional: LLM cleaning

Required only when using `doc-hub pipeline clean` or auto-clean during fetch:

```bash
export DOC_HUB_CLEAN_MODEL=gpt-4o-mini
export DOC_HUB_CLEAN_API_KEY=sk-your-key
export DOC_HUB_CLEAN_BASE_URL=https://api.openai.com/v1
# Optional: override the default cleaning prompt
# export DOC_HUB_CLEAN_PROMPT="Your custom prompt"
```

## Verify env state

```bash
./.agent/install-manager/scripts/check-env.sh
```

## Interpretation

- Missing `PGPASSWORD` or `DOC_HUB_DATABASE_URL` means DB operations will fail
- Missing `GEMINI_API_KEY` means indexing/embed/search operations that need embeddings will fail
