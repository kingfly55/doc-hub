Feature: Incremental sync pipeline
  The doc-hub pipeline syncs documentation from upstream sources into a
  PostgreSQL-backed search index. It supports incremental sync (only
  re-downloading and re-indexing what changed) to minimize API calls to
  the embedding provider (Gemini free tier has strict rate limits).

  The pipeline has four stages: fetch -> parse -> embed -> index.
  Each stage is corpus-scoped — operations on one corpus never affect
  another corpus's data.

  # -------------------------------------------------------------------
  # Fetch stage (fetchers.py)
  # -------------------------------------------------------------------

  Scenario: First run — no existing data
    Given a corpus "pydantic-ai" with fetch_strategy "llms_txt"
    And the output directory is empty (no manifest.json)
    When the fetch stage runs
    Then all upstream URLs are downloaded
    And each downloaded file's SHA-256 content hash is computed
    And manifest.json is written with url, filename, success, and content_hash for each file
    And the raw llms.txt content is saved as _llms.txt

  Scenario: Re-run with no changes upstream
    Given a corpus "pydantic-ai" with an existing manifest.json
    And all upstream URLs are unchanged (same content hashes)
    When the fetch stage runs
    Then all upstream URLs are re-downloaded (content is small, ~1MB total)
    And content hashes are compared against the manifest
    And no files are overwritten (hashes match)
    And the manifest is updated (timestamps may change but hashes stay the same)
    And the log reports 0 new, 0 changed, N unchanged

  Scenario: New document added upstream
    Given a corpus with an existing manifest.json
    And a new URL appears in the upstream llms.txt
    When the fetch stage runs
    Then the new URL is downloaded
    And the new file is written to the output directory
    And the manifest is updated to include the new file with its content_hash
    And the log reports 1 new

  Scenario: Existing document modified upstream
    Given a corpus with an existing manifest.json
    And an upstream document's content has changed (different SHA-256)
    When the fetch stage runs
    Then the file is re-downloaded (all URLs are always re-downloaded)
    And the new content hash differs from the manifest's stored hash
    And the local .md file is overwritten with the new content
    And the manifest is updated with the new content_hash
    And the log reports 1 changed

  Scenario: Document deleted upstream
    Given a corpus with an existing manifest.json
    And a URL that was in the manifest is no longer in the upstream llms.txt
    When the fetch stage runs
    Then the corresponding local .md file is deleted from disk
    And the manifest is updated without the removed file
    And the log reports 1 removed

  Scenario: Download failure for a URL
    Given a corpus with upstream URLs
    And one URL returns an HTTP error after all retries
    When the fetch stage runs
    Then the failed URL is recorded in the manifest with success=false and an error message
    And other URLs are downloaded successfully
    And the pipeline continues (fetch failures are non-fatal)

  Scenario: Backward compatibility with old manifest (no content_hash)
    Given an existing manifest.json written by the old fetcher (no content_hash field)
    When the fetch stage runs
    Then load_manifest returns content_hash=None for old entries
    And all files are re-downloaded (None hash is treated as "needs re-download")
    And the new manifest includes content_hash for all entries
    And subsequent runs use hash-based change detection

  Scenario: URL derivation — only url key in fetch_config
    Given a corpus with fetch_config containing only {"url": "https://docs.example.com/llms.txt"}
    When the fetch stage runs
    Then base_url is derived as "https://docs.example.com/"
    And url_pattern is derived as a regex matching .md URLs under that domain
    And fetching proceeds normally

  Scenario: Concurrent fetch of the same corpus
    Given two pipeline runs start for the same corpus simultaneously
    When both reach the index stage
    Then pg_advisory_xact_lock(hashtext(corpus_id)) serializes the upserts
    And the second run waits for the first to complete its transaction
    And both runs complete without data corruption

  # -------------------------------------------------------------------
  # Parse stage (parse.py)
  # -------------------------------------------------------------------

  Scenario: Parse with manifest — only listed files are parsed
    Given a corpus output directory with manifest.json listing files A and B
    And an orphaned file C exists on disk (not in the manifest)
    When the parse stage runs
    Then only files A and B are parsed into chunks
    And file C is ignored (not parsed, not embedded, not indexed)

  Scenario: Parse without manifest — glob fallback
    Given a corpus output directory with no manifest.json (e.g. local_dir strategy)
    And .md files exist in the directory
    When the parse stage runs
    Then all .md files are globbed and parsed
    And files starting with "_" are still skipped

  Scenario: Parse skips underscore-prefixed files
    Given a corpus output directory with _llms.txt and visible.md
    When the parse stage runs
    Then _llms.txt is not parsed
    And visible.md is parsed

  Scenario: Chunk deduplication
    Given two .md files with identical content sections
    When the parse stage runs
    Then chunks with duplicate content_hash values are deduplicated
    And only the first occurrence is kept

  Scenario: Chunk size optimization
    Given a .md file with sections shorter than 500 characters
    And a section longer than 2500 characters
    When the parse stage runs
    Then short sections are merged into their predecessor (Pass 1, min_chars=500)
    And long sections are split at paragraph boundaries (Pass 2, max_chars=2500)
    And no resulting chunk exceeds ~2600 characters (tolerance for boundary finding)

  # -------------------------------------------------------------------
  # Embed stage (embed.py)
  # -------------------------------------------------------------------

  Scenario: Embedding cache hit
    Given a chunk whose content_hash is already in the embedding cache
    And the cache entry matches the current embedding model and dimensions
    When the embed stage processes the chunk
    Then the cached embedding is reused (no API call)
    And the Gemini API is not called for this chunk

  Scenario: Embedding cache miss
    Given a chunk whose content_hash is not in the embedding cache
    When the embed stage processes the chunk
    Then the chunk's embedding_input text is sent to the Gemini API
    And the returned embedding vector is L2-normalized
    And the result is appended to the cache JSONL file

  Scenario: Modified document — embedding recalculation
    Given a document whose content changed upstream (new content_hash)
    When the pipeline runs through parse and embed
    Then the new content produces a new content_hash
    And the old content_hash's cache entry is unused (stale but harmless)
    And a new API call is made for the new content_hash
    And the new embedding is cached

  Scenario: Stale cache entries
    Given embedding cache entries for content_hashes that no longer exist in any chunk
    When the pipeline runs
    Then stale cache entries are not deleted (append-only cache)
    And stale entries waste only disk space (~800 bytes each), not API calls

  # -------------------------------------------------------------------
  # Index stage (index.py)
  # -------------------------------------------------------------------

  Scenario: Upsert new chunks
    Given embedded chunks for a corpus
    And the doc_chunks table has no existing rows for this corpus
    When the index stage runs
    Then all chunks are inserted with INSERT
    And the RETURNING xmax=0 check confirms true inserts
    And IndexResult.inserted equals the chunk count

  Scenario: Upsert modified chunks (same content_hash conflict)
    Given embedded chunks where content_hash matches an existing row
    When the index stage runs
    Then ON CONFLICT (corpus_id, content_hash) DO UPDATE fires
    And the row's content, heading, embedding, etc. are updated
    And IndexResult.updated is incremented

  Scenario: Full reindex — stale row cleanup
    Given a corpus with existing rows in doc_chunks
    And the current chunk set does not include some old content_hashes
    When the index stage runs with full=True
    Then rows whose content_hash is not in the current set are deleted
    And only rows for this corpus are affected (WHERE corpus_id = $1)
    And IndexResult.deleted reports the count

  Scenario: Full reindex — cross-corpus isolation
    Given corpus A and corpus B both have rows in doc_chunks
    When the index stage runs with full=True for corpus A
    Then only corpus A's stale rows are deleted
    And corpus B's rows are untouched

  Scenario: Post-index metadata update
    Given the index stage has completed upserts
    When post-transaction metadata is written
    Then doc_index_meta is updated with last_indexed_at, total_chunks, embedding_model, embedding_dimensions
    And doc_corpora.chunk_count is updated via update_corpus_stats

  # -------------------------------------------------------------------
  # Pipeline orchestration (pipeline.py)
  # -------------------------------------------------------------------

  Scenario: Normal pipeline run
    Given a registered corpus
    When doc-hub-pipeline --corpus <slug> runs
    Then stages execute in order: fetch -> parse -> embed -> index
    And the verify_index smoke test runs after indexing
    And the total chunk count is logged

  Scenario: --clean flag
    Given a corpus with existing fetched files and embedding cache
    When doc-hub-pipeline --corpus <slug> --clean runs
    Then the raw directory is deleted before fetch
    And the chunks directory is deleted before parse
    And the embedding cache is deleted before embed
    And a full pipeline runs from scratch

  Scenario: --full-reindex flag
    Given a corpus with existing data in doc_chunks
    When doc-hub-pipeline --corpus <slug> --full-reindex runs
    Then the index stage runs with full=True
    And stale rows for this corpus are deleted after upsert

  Scenario: --skip-download flag
    Given a corpus with existing fetched files
    When doc-hub-pipeline --corpus <slug> --skip-download runs
    Then the fetch stage is skipped entirely
    And parse/embed/index run on existing files

  Scenario: sync_all — per-corpus error isolation
    Given multiple registered corpora
    And one corpus's fetch raises an exception
    When sync_all() runs
    Then the failing corpus is logged and skipped
    And other corpora are processed normally
    And the overall sync does not abort

  # -------------------------------------------------------------------
  # End-to-end sync scenarios
  # -------------------------------------------------------------------

  Scenario: End-to-end — document modified upstream
    Given a corpus was previously indexed
    And one upstream document's content changes
    When the pipeline runs
    Then the fetch stage re-downloads the file and detects the hash change
    And the parse stage produces chunks with new content_hashes
    And the embed stage calls the Gemini API for new content_hashes (cache miss)
    And the index stage upserts the new chunks (ON CONFLICT DO UPDATE)
    And search results reflect the updated content

  Scenario: End-to-end — document deleted upstream
    Given a corpus was previously indexed
    And one upstream URL is removed from llms.txt
    When the pipeline runs
    Then the fetch stage deletes the local .md file
    And the manifest no longer lists the deleted file
    And the parse stage does not parse the deleted file (manifest filtering)
    And with --full-reindex, the index stage deletes stale rows from doc_chunks
    And search results no longer return the deleted content

  Scenario: End-to-end — new document added upstream
    Given a corpus was previously indexed
    And a new URL appears in llms.txt
    When the pipeline runs
    Then the fetch stage downloads the new file
    And the parse stage produces new chunks
    And the embed stage generates new embeddings (cache miss)
    And the index stage inserts the new chunks
    And search results include the new content

  Scenario: End-to-end — no changes upstream
    Given a corpus was previously indexed
    And nothing has changed upstream
    When the pipeline runs
    Then the fetch stage re-downloads all files but detects no hash changes
    And the parse stage produces the same chunks (same content_hashes)
    And the embed stage has 100% cache hits (no API calls)
    And the index stage upserts all chunks (ON CONFLICT DO UPDATE, no-op)
    And the total chunk count remains unchanged

  # -------------------------------------------------------------------
  # MCP server (mcp_server.py)
  # -------------------------------------------------------------------

  Scenario: MCP search tool
    Given the MCP server is running with a connected database
    When a client calls the search tool with a query
    Then the query is embedded using the Gemini API
    And hybrid search (KNN + BM25 via RRF) is performed
    And results are returned with heading, content, source_url, similarity, and score

  Scenario: MCP add_corpus tool
    Given the MCP server is running
    When a client calls add_corpus with slug, name, strategy, and config
    Then a new row is inserted into doc_corpora
    And the corpus is available for pipeline runs

  Scenario: MCP refresh tool
    Given the MCP server is running with a registered corpus
    When a client calls the refresh tool with a corpus slug
    Then the full pipeline runs for that corpus
    And the response includes the IndexResult statistics

  Scenario: MCP list_corpora tool
    Given the MCP server is running with registered corpora
    When a client calls list_corpora
    Then all corpora are returned with slug, name, strategy, chunk_count, and enabled status
