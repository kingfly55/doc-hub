# Exact Diagnostic Commands

## Unified CLI help

```bash
doc-hub --help
doc-hub docs browse --help
doc-hub docs read --help
doc-hub docs search --help
doc-hub pipeline run --help
doc-hub pipeline sync-all --help
doc-hub pipeline eval --help
doc-hub serve mcp --help
```

## Environment check

```bash
./.agent/install-manager/scripts/check-env.sh
```

## Database check

```bash
./.agent/install-manager/scripts/check-db.sh
```

## MCP check

```bash
./.agent/install-manager/scripts/check-mcp.sh
```

## Pipeline operations

```bash
doc-hub pipeline run --corpus <slug>
doc-hub pipeline run --corpus <slug> --stage tree
doc-hub pipeline sync-all
```

## Docs operations

```bash
doc-hub docs browse <slug>
doc-hub docs read <slug> <doc_path>
doc-hub docs search "query" --corpus <slug>
```
