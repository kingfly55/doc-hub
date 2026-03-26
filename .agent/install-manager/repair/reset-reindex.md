# Reset and Reindex Guidance

Use these actions carefully.

## Lowest-risk rebuild

Rebuild only the document tree:

```bash
doc-hub pipeline run --corpus <slug> --stage tree
```

Use this when:
- search chunks are present
- document hierarchy or browse/read state appears stale

## Reindex one corpus

```bash
doc-hub pipeline run --corpus <slug>
```

Use this when:
- fetched or parsed content is outdated
- embeddings need regeneration
- chunk/index state is stale for one corpus

## Reindex all corpora

```bash
doc-hub pipeline sync-all
```

Use this when:
- many corpora need refresh
- installation-wide index state is stale

## Full clean for one corpus

```bash
doc-hub pipeline run --corpus <slug> --clean
```

Use only when:
- local raw/chunk cache state is believed to be corrupt
- operator accepts re-fetch cost

## Dimension mismatch repair

If `DOC_HUB_VECTOR_DIM` mismatches the live schema:
- safest first fix: align the env var to the existing schema
- destructive fix: rebuild affected indexed data only with operator approval
