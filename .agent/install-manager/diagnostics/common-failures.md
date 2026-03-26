# Common Failures

## `PGPASSWORD environment variable not set`

Likely cause:
- DB env vars are incomplete and `DOC_HUB_DATABASE_URL` is unset

Check:
- `./.agent/install-manager/scripts/check-env.sh`

Fix:
- set `PGPASSWORD`
- or set `DOC_HUB_DATABASE_URL`

## Vector dimension mismatch error

Likely cause:
- `DOC_HUB_VECTOR_DIM` does not match the existing DB schema

Check:
- `./.agent/install-manager/scripts/check-db.sh`
- read `docs/user/configuration.md` for dimension rules

Fix:
- align `DOC_HUB_VECTOR_DIM` to the installed schema
- or intentionally rebuild the affected table/indexed data if approved

## Corpus not found

Likely cause:
- no `doc_corpora` row exists for that slug

Fix:
- register the corpus first via MCP or SQL

## MCP won’t start

Likely cause:
- env not loaded
- DB config missing
- bad systemd unit or wrong command path

Check:
- `./.agent/install-manager/scripts/check-mcp.sh`
- `journalctl --user -u doc-hub-mcp.service -f`

## Search works poorly or returns nothing

Likely cause:
- corpus not indexed
- wrong corpus flag
- low-quality or absent source data
- embed/index never completed

Check:
- corpus registration
- chunk counts
- run targeted `doc-hub pipeline run --corpus <slug>` if appropriate
