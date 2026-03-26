#!/usr/bin/env bash
set -euo pipefail

printf '== DocHub database check ==\n'
python - <<'PY'
import os
from urllib.parse import urlparse

url = os.getenv('DOC_HUB_DATABASE_URL')
if url:
    parsed = urlparse(url)
    print(f'DOC_HUB_DATABASE_URL=SET host={parsed.hostname} port={parsed.port} db={parsed.path.lstrip("/")}')
else:
    print(f'PGHOST={os.getenv("PGHOST", "localhost")}')
    print(f'PGPORT={os.getenv("PGPORT", "5432")}')
    print(f'PGDATABASE={os.getenv("PGDATABASE", "doc_hub")}')
    print(f'PGUSER={os.getenv("PGUSER", "postgres")}')
    print(f'PGPASSWORD={"SET" if os.getenv("PGPASSWORD") else "UNSET"}')
PY

uv run python - <<'PY'
import asyncio
from doc_hub.db import create_pool, ensure_schema

async def main():
    pool = await create_pool()
    try:
        await ensure_schema(pool)
        async with pool.acquire() as conn:
            ext = await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'vchord'")
            corpora = await conn.fetchval("SELECT COUNT(*) FROM pg_tables WHERE tablename = 'doc_corpora'")
            chunks = await conn.fetchval("SELECT COUNT(*) FROM pg_tables WHERE tablename = 'doc_chunks'")
            docs = await conn.fetchval("SELECT COUNT(*) FROM pg_tables WHERE tablename = 'doc_documents'")
            print(f'vchord={"present" if ext == 1 else "missing"}')
            print(f'doc_corpora_table={corpora == 1}')
            print(f'doc_chunks_table={chunks == 1}')
            print(f'doc_documents_table={docs == 1}')
    finally:
        await pool.close()

asyncio.run(main())
PY
