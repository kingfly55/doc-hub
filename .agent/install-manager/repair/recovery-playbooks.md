# Recovery Playbooks

## Playbook: missing env vars

1. Run `./.agent/install-manager/scripts/check-env.sh`
2. Identify whether `PGPASSWORD` or `DOC_HUB_DATABASE_URL` is missing
3. Add the missing value to shell env or `.env`
4. Re-run the env check
5. Re-run `./.agent/install-manager/scripts/check-db.sh`

## Playbook: DB reachable but schema incomplete

1. Confirm correct DB target
2. Run a low-impact DB-backed command such as tree rebuild or targeted pipeline step
3. Re-run `./.agent/install-manager/scripts/check-db.sh`
4. If schema still looks wrong, inspect `ensure_schema()` behavior before proposing destructive actions

## Playbook: MCP service unhealthy

1. Run `./.agent/install-manager/scripts/check-mcp.sh`
2. If using systemd, inspect unit state and logs
3. Confirm `ExecStart` uses `doc-hub serve mcp ...`
4. Confirm env vars are available in the service context

## Playbook: search data stale

1. Verify the corpus exists
2. Run `doc-hub pipeline run --corpus <slug> --stage tree` if the issue appears hierarchy-only
3. Run `doc-hub pipeline run --corpus <slug>` if indexing appears stale
4. Use `doc-hub pipeline sync-all` only when multiple corpora need refresh
