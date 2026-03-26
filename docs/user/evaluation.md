# Retrieval Evaluation

doc-hub includes a built-in evaluation tool (`doc-hub pipeline eval`) that measures how well the search index returns relevant results for a set of known queries. This guide covers how to write eval files, run evaluations, and interpret the results.

---

## What is retrieval evaluation?

Retrieval evaluation answers the question: *for queries where we know what the correct answer should be, does the search system return it?*

You provide a set of test queries with expected results. The evaluator runs each query against the live search index and computes two metrics:

- **Precision@N** — fraction of queries where at least one relevant result appeared in the top N results
- **MRR (Mean Reciprocal Rank)** — average of `1 / rank_of_first_relevant_result` across all queries; rewards finding relevant results at higher positions

The default N is 5 (Precision@5). Default pass thresholds: P@5 ≥ 0.80, MRR ≥ 0.60.

---

## Eval file format

An eval file is a JSON array of test query objects. Each object represents one test case.

### Required fields

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique identifier for this query (e.g. `"q001"`) |
| `query` | `str` | The search query text |

At least one of `expected_headings` or `expected_section_paths` must also be present.

### Expectation fields (at least one required)

| Field | Type | Description |
|---|---|---|
| `expected_headings` | `list[str]` | Heading substrings that a relevant result's heading should contain |
| `expected_section_paths` | `list[str]` | Section path substrings that a relevant result's `section_path` should contain |

### Optional fields

| Field | Type | Default | Description |
|---|---|---|---|
| `min_similarity` | `float` | `0.55` | Minimum similarity score expected for the top result; used to flag low-confidence results |
| `notes` | `str` | `""` | Human-readable description of the test case intent |

### Example eval file

```json
[
  {
    "id": "q001",
    "query": "how do I define a tool that accepts parameters?",
    "expected_headings": ["Defining Tools", "Tool Parameters", "Function Tools", "Tools"],
    "expected_section_paths": ["Tools", "tools"],
    "min_similarity": 0.55,
    "notes": "Should return content from the tools guide about defining function tools with parameters"
  },
  {
    "id": "q002",
    "query": "what is RunContext and what fields does it have?",
    "expected_headings": ["RunContext", "Run Context", "Context"],
    "expected_section_paths": ["RunContext", "run_context", "Tools > Context"],
    "notes": "API reference lookup for the RunContext class"
  },
  {
    "id": "q003",
    "query": "how do I stream output from an agent?",
    "expected_headings": ["Streaming", "Stream", "Streamed Responses"],
    "expected_section_paths": ["Streaming", "stream"]
  }
]
```

---

## Eval file location

The evaluator looks for eval files in this order:

1. **`DOC_HUB_EVAL_DIR` env var** — if set, uses that directory directly
2. **`{data_root}/eval/`** — default fallback (XDG data root)

File naming convention: `{corpus-slug}.json`

Examples:
- `eval/pydantic-ai.json` → corpus slug `pydantic-ai`
- `eval/fastapi.json` → corpus slug `fastapi`

The `list_eval_corpora()` function scans the eval directory for `*.json` files and returns their stems as corpus slugs. The CLI uses this to discover available eval files when no `--corpus` is specified.

---

## Running evaluations

### Evaluate a specific corpus

```
doc-hub pipeline eval --corpus pydantic-ai
```

### Evaluate all corpora with eval files

```
doc-hub pipeline eval --all
```

### Default (no flags) — same as `--all`

```
doc-hub pipeline eval
```

### With verbose output and JSON report

```
doc-hub pipeline eval --corpus pydantic-ai --verbose --output report.json
```

`--verbose` prints per-query status (hit/miss, reciprocal rank, top similarity, and heading of the top result) as queries run.

`--output` writes the final report as JSON to the specified path. Single corpus produces a JSON object; multiple corpora produce a JSON array.

### Override pass thresholds

```
doc-hub pipeline eval --corpus fastapi --min-precision 0.70 --min-mrr 0.50
```

### All CLI flags

| Flag | Default | Description |
|---|---|---|
| `--corpus SLUG` | — | Evaluate a single corpus by slug |
| `--all` | — | Evaluate all corpora with eval files (mutually exclusive with `--corpus`) |
| `--limit N` | `5` | Number of results to retrieve per query (N in Precision@N) |
| `--verbose` | off | Show per-query results |
| `--output PATH` | — | Write JSON report to this file |
| `--min-precision FLOAT` | `0.80` | Minimum Precision@N to pass |
| `--min-mrr FLOAT` | `0.60` | Minimum MRR to pass |

**Exit codes:** 0 if all evaluated corpora pass, 1 if any fail or no evaluations ran.

---

## Understanding the output

A typical report looks like:

```
========================================
RETRIEVAL QUALITY EVALUATION — PYDANTIC-AI
========================================
Queries run:      25
Hits in top-5:    22
Precision@5:      0.880
MRR:              0.743

Failed queries (no relevant result in top 5):
  [q007] 'how do I configure the model temperature?'
  [q018] 'what is the difference between sync and async agents?'

Low similarity queries (top result below min threshold):
  [q012] 'custom retry logic' — top sim: 0.412

STATUS: PASS ✓  (P@5=0.880 ≥ 0.8, MRR=0.743 ≥ 0.6)
```

### Report sections

- **Queries run** — total number of test queries executed
- **Hits in top-N** — number of queries where a relevant result appeared in the top N
- **Precision@N** — `hits / total`
- **MRR** — mean of `1 / rank_of_first_relevant_result` across all queries
- **Failed queries** — query IDs (and text) where no relevant result appeared in the top N
- **Low similarity queries** — queries where the top result's similarity score was below the query's `min_similarity` threshold; these may indicate the corpus lacks relevant content
- **STATUS** — PASS if both P@N and MRR meet thresholds; FAIL otherwise, with per-metric detail on how far short

---

## How matching works

A search result is considered a **hit** for a test query if either:

- The result's `heading` field contains any string in `expected_headings` (case-insensitive substring match), **or**
- The result's `section_path` field contains any string in `expected_section_paths` (case-insensitive substring match)

This is implemented in `_is_hit_single()`. For example, `expected_headings: ["Tools"]` will match a result with heading `"Function Tools"` or `"Defining Tools"`.

**Note:** During evaluation, `search_docs()` is called with `min_similarity=0.0`, which disables the normal similarity pre-filter. This ensures all top-N results are scored and evaluated regardless of similarity, so low-scoring results still count as misses rather than being silently dropped before evaluation.

### Reciprocal rank

For each query, reciprocal rank is `1 / rank` where rank is the 1-based position of the first relevant result in the result list. If no relevant result is found, reciprocal rank is `0.0`.

Examples:
- First result is relevant → RR = 1.0
- Second result is relevant → RR = 0.5
- Fifth result is relevant → RR = 0.2
- No relevant result → RR = 0.0

---

## Metrics explained

### Precision@N

```
Precision@N = hits / total_queries
```

Measures what fraction of queries had at least one relevant result in the top N. A score of 0.88 means 88% of queries returned a relevant result within the top 5 positions.

Default threshold: **P@5 ≥ 0.80**

### MRR (Mean Reciprocal Rank)

```
MRR = mean(1 / rank_of_first_hit) across all queries
    = 0.0 for queries with no hit
```

MRR rewards finding the relevant result at a higher position. A corpus where the relevant result is typically the first result will have MRR close to 1.0; a corpus where relevant results typically appear at rank 3–5 will have MRR around 0.25–0.35.

Default threshold: **MRR ≥ 0.60**

---

## JSON report format

When `--output` is used, the report is written as JSON. The structure comes from `EvalReport.to_dict()`.

### Single corpus (`--corpus pydantic-ai --output report.json`)

```json
{
  "corpus": "pydantic-ai",
  "total": 25,
  "hits": 22,
  "precision_at_n": 0.88,
  "mrr": 0.7432,
  "n": 5,
  "failed_queries": ["q007", "q018"],
  "low_similarity_queries": ["q012"],
  "passed": true,
  "thresholds": {
    "precision_at_n": 0.8,
    "mrr": 0.6
  }
}
```

### Multiple corpora (`--all --output report.json`)

The output is a JSON array, one object per corpus, each with the same structure as above.

---

## Tips for writing good eval files

**Cover diverse query types:**
- Keyword queries: `"RunContext fields"`
- Natural language: `"how do I handle tool errors?"`
- API-specific: `"what does ModelRetry do?"`
- Conceptual: `"difference between structured and unstructured output"`

**Be specific with `expected_headings`:** Match the actual section headings in the indexed documentation. Overly generic terms (e.g., `"Introduction"`) may match unrelated sections. Use `expected_section_paths` for broader path-based matching when headings are less predictable.

**Prefer `expected_section_paths` for hierarchy matching:** If a concept lives under a known path like `"Tools > Context"`, adding that to `expected_section_paths` catches results from any subsection without listing every possible heading variant.

**Use `notes` to document intent:** Future-you (and CI) will thank you when a query fails and you need to understand what it was testing.

**Aim for 20–30 queries per corpus** as a starting baseline. Add new queries whenever you discover a search failure in real use.

**Validate before committing:** `load_test_queries()` raises `ValueError` if any entry is missing `id`, `query`, or both expectation fields. Run `doc-hub pipeline eval --corpus your-corpus` locally to catch malformed entries early.
