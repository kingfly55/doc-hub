# Database Status

## What to check

1. Can doc-hub connect?
2. Is the expected database selected?
3. Are required tables present?
4. Is `vchord` installed?
5. Are corpora registered?
6. Is chunk data present?

## Quick status command

```bash
./.agent/install-manager/scripts/check-db.sh
```

## Useful manual commands

### Registered corpora

```bash
doc-hub pipeline sync-all
```

Use this operationally only if you intend to run sync; otherwise inspect `doc_corpora` via psql if available.

### Tree-only rebuild for one corpus

```bash
doc-hub pipeline run --corpus <slug> --stage tree
```

### Full pipeline for one corpus

```bash
doc-hub pipeline run --corpus <slug>
```

## Status interpretations

- tables missing: schema was never initialized or wrong DB target is configured
- extension missing: wrong image/provider or failed extension install
- corpora absent: installation is healthy but unconfigured
- corpora present with zero chunks: registration exists but indexing hasn’t completed
