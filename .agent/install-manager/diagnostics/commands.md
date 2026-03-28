# Exact Diagnostic Commands

## Unified CLI help

```bash
doc-hub --help
doc-hub man
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
# Print bundled CLI reference directly
doc-hub man

# Browse and note short document IDs from the output
doc-hub docs browse <slug>

# Read by full document path or short ID from browse output
doc-hub docs read <slug> <doc_path_or_id>

# Search requires at least one corpus and accepts repeated --corpus
doc-hub docs search --corpus <slug> "query"
doc-hub docs search --corpus <slug-a> --corpus <slug-b> "query"
```
