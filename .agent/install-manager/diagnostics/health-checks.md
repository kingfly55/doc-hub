# Health Checks

Run these in order.

## 1. Environment

```bash
./.agent/install-manager/scripts/check-env.sh
```

## 2. Database

```bash
./.agent/install-manager/scripts/check-db.sh
```

## 3. MCP service / command path

```bash
./.agent/install-manager/scripts/check-mcp.sh
```

## 4. Unified CLI sanity

```bash
doc-hub --help
doc-hub docs search --help
doc-hub pipeline run --help
doc-hub serve mcp --help
```

## 5. Full local verification (when appropriate)

```bash
PGHOST=localhost PGPORT=5433 PGUSER=postgres PGPASSWORD=pydantic-docs PGDATABASE=postgres uv run pytest tests/ -x
```

Use installation-specific DB settings if they differ.
