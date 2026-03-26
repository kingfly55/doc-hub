# Search Internals

**Source file:** `src/doc_hub/search.py`
**Audience:** Developer / AI agent

---

## 1. Overview

doc-hub hybrid search combines two retrieval methods and merges their ranked results:

1. **Vector KNN** — cosine similarity over L2-normalized embeddings (VectorChord `<=>` operator)
2. **BM25 full-text search** — PostgreSQL `tsvector`/`tsquery` with `ts_rank`
3. **Reciprocal Rank Fusion (RRF)** — merges ranked lists from both methods into a single score

Entry point: `search_docs()` (async) or `search_docs_sync()` (sync wrapper for CLI).

---

## 2. Query Embedding

**Function:** `_embed_query_async(query: str, embedder: Embedder | None = None) -> list[float]`

Steps:
1. If `embedder` is `None`, resolves the default from the plugin registry (prefers `"gemini"` if available, else `available[0]`).
2. Calls `embedder.embed_query(query)` — this uses the embedder's `task_type_query` task type.
3. Passes the raw vector through `l2_normalize()` from `doc_hub.embed`.

**Cross-corpus search caveat:** The query is embedded once with a single embedder. If different corpora were indexed with different embedders, similarity scores for non-matching corpora are meaningless. All corpora in a deployment must use the same embedder.

---

## 3. Full SQL Template

`_build_hybrid_sql(corpus, config)` returns the following SQL (parameters interpolated from `SearchConfig`):

```sql
WITH vector_results AS (
    SELECT id, heading, section_path, content, source_url, category, corpus_id,
           start_line, end_line,
           1 - (embedding <=> $1::vector) AS vec_similarity,
           ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS vec_rank
    FROM doc_chunks
    WHERE ($3::text IS NULL OR corpus_id = $3)
      AND ($4::text[] IS NULL OR category = ANY($4))
      AND ($5::text[] IS NULL OR category != ALL($5))
      AND ($6::text IS NULL OR source_url LIKE $6 || '%' ESCAPE '\')
      AND ($7::text IS NULL OR section_path LIKE $7 || '%' ESCAPE '\')
    ORDER BY embedding <=> $1::vector
    LIMIT {vector_limit}
),
text_results AS (
    SELECT id, heading, section_path, content, source_url, category, corpus_id,
           start_line, end_line,
           ts_rank(tsv, query) AS text_score,
           ROW_NUMBER() OVER (ORDER BY ts_rank(tsv, query) DESC) AS text_rank
    FROM doc_chunks, websearch_to_tsquery('{language}', $2) query
    WHERE tsv @@ query
      AND ($3::text IS NULL OR corpus_id = $3)
      AND ($4::text[] IS NULL OR category = ANY($4))
      AND ($5::text[] IS NULL OR category != ALL($5))
      AND ($6::text IS NULL OR source_url LIKE $6 || '%' ESCAPE '\')
      AND ($7::text IS NULL OR section_path LIKE $7 || '%' ESCAPE '\')
    ORDER BY ts_rank(tsv, query) DESC
    LIMIT {text_limit}
)
SELECT COALESCE(v.id, t.id) AS id,
       COALESCE(v.heading, t.heading) AS heading,
       COALESCE(v.section_path, t.section_path) AS section_path,
       COALESCE(v.content, t.content) AS content,
       COALESCE(v.source_url, t.source_url) AS source_url,
       COALESCE(v.category, t.category) AS category,
       COALESCE(v.corpus_id, t.corpus_id) AS corpus_id,
       COALESCE(v.start_line, t.start_line, 0) AS start_line,
       COALESCE(v.end_line, t.end_line, 0) AS end_line,
       COALESCE(v.vec_similarity, 0) AS vec_similarity,
       COALESCE(1.0 / ({rrfk} + v.vec_rank), 0) +
       COALESCE(1.0 / ({rrfk} + t.text_rank), 0) AS rrf_score
FROM vector_results v
FULL OUTER JOIN text_results t ON v.id = t.id
ORDER BY rrf_score DESC
LIMIT $8
OFFSET $9
```

### Bind parameter reference

| Param | Type              | Meaning                                             |
|-------|-------------------|-----------------------------------------------------|
| `$1`  | `text` (cast to `vector`) | Query embedding vector serialized as `"[f1,f2,...]"` |
| `$2`  | `text`            | Raw query text for `websearch_to_tsquery`           |
| `$3`  | `text \| NULL`    | `corpus_id` filter; `NULL` = search all corpora     |
| `$4`  | `text[] \| NULL`  | Category include list (`category = ANY($4)`); `NULL` = no filter |
| `$5`  | `text[] \| NULL`  | Category exclude list (`category != ALL($5)`); `NULL` = no filter |
| `$6`  | `text \| NULL`    | `source_url` prefix (pre-escaped); `NULL` = no filter |
| `$7`  | `text \| NULL`    | `section_path` prefix (pre-escaped); `NULL` = no filter |
| `$8`  | `int`             | `LIMIT` (result count)                              |
| `$9`  | `int`             | `OFFSET` (for pagination)                           |

`{vector_limit}`, `{text_limit}`, `{rrfk}`, `{language}` are Python f-string interpolations (not bind params). `language` is validated against `VALID_PG_LANGUAGES` before interpolation. Integer fields are safe because Python `int` cannot contain SQL metacharacters.

---

## 4. Vector KNN Search (CTE: `vector_results`)

- Operator `<=>` is cosine distance (VectorChord/pgvector). Lower = more similar.
- `1 - (embedding <=> $1::vector)` converts distance to cosine similarity (higher = more similar).
- `ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector)` assigns `vec_rank` (1 = best).
- Default `vector_limit = 20` — candidate pool size before RRF fusion.

The `embedding` column stores L2-normalized vectors. Normalization is applied by `l2_normalize()` in `embed.py` before storage. The `<=>` operator computes cosine distance correctly only on normalized vectors.

---

## 5. BM25 Full-Text Search (CTE: `text_results`)

- `websearch_to_tsquery('{language}', $2)` parses the query string (supports `AND`, `OR`, quoted phrases, `-` negation — same syntax as web search engines).
- `tsv @@ query` is the full-text match operator.
- `ts_rank(tsv, query)` scores matches using PostgreSQL's BM25-like ranking.
- `ROW_NUMBER() OVER (ORDER BY ts_rank(...) DESC)` assigns `text_rank` (1 = best).
- Default `text_limit = 10` — candidate pool size before RRF fusion.

The `tsv` column is a `GENERATED ALWAYS AS ... STORED` tsvector defined in `db.py`:

```sql
tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english', heading), 'A') ||
    setweight(to_tsvector('english', content), 'B')
) STORED
```

Weight A (heading) ranks higher than weight B (content) in `ts_rank` scoring.

---

## 6. Reciprocal Rank Fusion (RRF)

```sql
FULL OUTER JOIN text_results t ON v.id = t.id
```

`FULL OUTER JOIN` preserves rows that appear in only one of the two CTEs. `COALESCE` handles `NULL` ranks for results that appear in only one method:

```sql
COALESCE(1.0 / ({rrfk} + v.vec_rank), 0) +
COALESCE(1.0 / ({rrfk} + t.text_rank), 0) AS rrf_score
```

RRF formula: `score = 1/(k + rank_1) + 1/(k + rank_2)` where absent ranks contribute 0.

Default `k = 60` (RRF constant). Higher k reduces the influence of top-ranked documents; lower k amplifies it.

Results ordered by `rrf_score DESC` — higher score = better combined rank.

---

## 7. Filters

All filters use the NULL-propagation pattern: `($N::type IS NULL OR column op $N)`.

The `IS NULL` check MUST come first. PostgreSQL short-circuits `OR`: when `$N` is `NULL`, `IS NULL` returns `TRUE` immediately and the right side is never evaluated.

| Filter               | Param | SQL condition                                            |
|----------------------|-------|----------------------------------------------------------|
| corpus scope         | `$3`  | `$3::text IS NULL OR corpus_id = $3`                     |
| category include     | `$4`  | `$4::text[] IS NULL OR category = ANY($4)`               |
| category exclude     | `$5`  | `$5::text[] IS NULL OR category != ALL($5)`              |
| source URL prefix    | `$6`  | `$6::text IS NULL OR source_url LIKE $6 \|\| '%' ESCAPE '\'` |
| section path prefix  | `$7`  | `$7::text IS NULL OR section_path LIKE $7 \|\| '%' ESCAPE '\'` |

The same filter block is duplicated in both `vector_results` and `text_results` CTEs.

---

## 8. LIKE Escaping

**Function:** `_escape_like(value: str) -> str`

Escapes LIKE metacharacters so prefix filters match literally:

| Input char | Escaped |
|------------|---------|
| `\`        | `\\`    |
| `%`        | `\%`    |
| `_`        | `\_`    |

Applied to `source_url_prefix` and `section_path_prefix` in `search_docs()` before binding to SQL parameters.

---

## 9. Post-filtering

`min_similarity` is applied in **Python after SQL execution**, not in the SQL `WHERE` clause:

```python
results = [r for r in raw_results if r.similarity >= min_similarity]
```

**Why post-filter:** Results that appear only in `text_results` have `vec_similarity = 0` (set by `COALESCE(v.vec_similarity, 0)`). A SQL `WHERE vec_similarity >= threshold` would incorrectly exclude all text-only results. Post-filtering lets text-only results pass through if they have a strong enough RRF score, while still removing low-quality vector matches.

Default `min_similarity = 0.55`.

---

## 10. `SearchConfig` dataclass

```python
@dataclass
class SearchConfig:
    vector_limit: int = 20      # KNN candidate pool size
    text_limit: int = 10        # BM25 candidate pool size
    rrfk: int = 60              # Reciprocal Rank Fusion k constant
    language: str = "english"   # PostgreSQL text-search language
```

`__post_init__` validates:
- `language` must be in `VALID_PG_LANGUAGES` (raises `ValueError`)
- `vector_limit`, `text_limit`, `rrfk` must be positive integers

### `VALID_PG_LANGUAGES`

```python
VALID_PG_LANGUAGES = frozenset({
    "simple", "arabic", "armenian", "basque", "catalan", "danish", "dutch",
    "english", "finnish", "french", "german", "greek", "hindi", "hungarian",
    "indonesian", "irish", "italian", "lithuanian", "nepali", "norwegian",
    "portuguese", "romanian", "russian", "serbian", "spanish", "swedish",
    "tamil", "turkish", "yiddish",
})
```

29 entries. These correspond to PostgreSQL's built-in text search configurations. `language` is interpolated into the SQL f-string but validated against this whitelist to prevent SQL injection.

---

## 11. `SearchResult` dataclass

```python
@dataclass
class SearchResult:
    id: int
    corpus_id: str
    heading: str
    section_path: str
    content: str        # raw markdown content
    source_url: str
    score: float        # RRF score (for ranking transparency)
    similarity: float   # cosine similarity (for threshold filtering)
    category: str       # 'api' | 'guide' | 'example' | 'eval' | 'other'
    start_line: int     # 1-indexed line number in source file
    end_line: int       # 1-indexed last line number (inclusive)
```

`score` is the RRF composite score. `similarity` is cosine similarity (`vec_similarity` from SQL). Both are exposed so callers can reason about ranking vs. semantic relevance independently.

---

## 12. `search_docs()` — full signature

```python
async def search_docs(
    query: str,
    *,
    pool: asyncpg.Pool,
    embedder: Embedder | None = None,
    corpus: str | None = None,
    categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
    min_similarity: float = 0.55,
    source_url_prefix: str | None = None,
    section_path_prefix: str | None = None,
    config: SearchConfig | None = None,
) -> list[SearchResult]:
```

- `pool`: Required. Obtain from `doc_hub.db.create_pool()`.
- `embedder`: Optional. Pass a shared instance (e.g. from MCP lifespan) to avoid re-instantiation on every call. If `None`, resolved from registry.
- `corpus`: `None` = search all corpora.
- `categories`: `None` = no filter. Valid values: `"api"`, `"guide"`, `"example"`, `"eval"`, `"other"`.
- `exclude_categories`: `None` = no filter.
- `config`: `None` uses `SearchConfig()` defaults (20/10/60/english).

Returns `[]` if no results meet `min_similarity`.

---

## 13. Sync Wrapper

```python
def search_docs_sync(
    query: str,
    *,
    corpus: str | None = None,
    categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
    min_similarity: float = 0.55,
    source_url_prefix: str | None = None,
    section_path_prefix: str | None = None,
    config: SearchConfig | None = None,
) -> list[SearchResult]:
```

Wraps `search_docs()` via `asyncio.run()`. Creates a temporary pool, runs the search, closes the pool.

**Raises `RuntimeError`** if called from within a running event loop. For MCP tool handlers and any async context, call `search_docs()` directly with a shared pool.

---

## 14. Execution Flow

```
search_docs(query, pool, ...)
  │
  ├─ _embed_query_async(query, embedder)
  │    ├─ embedder.embed_query(query)     # uses task_type_query
  │    └─ l2_normalize(raw_vec)           # from doc_hub.embed
  │
  ├─ _escape_like(source_url_prefix)      # if provided
  ├─ _escape_like(section_path_prefix)    # if provided
  │
  ├─ _build_hybrid_sql(corpus, config)    # returns SQL string with interpolated config
  │
  ├─ pool.acquire() → conn.fetch(sql, $1..$9)
  │    ├─ vector_results CTE  (KNN, LIMIT vector_limit)
  │    ├─ text_results CTE    (BM25, LIMIT text_limit)
  │    └─ FULL OUTER JOIN → RRF score → ORDER BY rrf_score → LIMIT/OFFSET
  │
  └─ post-filter: [r for r in rows if r.similarity >= min_similarity]
```
