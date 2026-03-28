# Implementation Plan: LLM Cleaning Step

**Spec:** `docs/superpowers/specs/2026-03-28-llm-cleaning-step-design.md`

## Steps

### Step 1: Add `openai` dependency to `pyproject.toml`

Add `openai>=1.0` to the `dependencies` list.

**Files:** `pyproject.toml`

### Step 2: Create `src/doc_hub/clean.py` — core cleaning module

Implement:
- `DEFAULT_CLEAN_PROMPT` constant (the full prompt from the spec)
- `CleanConfig` dataclass: `model`, `api_key`, `base_url`, `prompt`
- `get_clean_config() -> CleanConfig` — reads env vars, raises `ValueError` if required vars missing
- `clean_markdown(content: str, config: CleanConfig) -> str` — calls OpenAI-compatible API with `AsyncOpenAI`
- `CleanResult` NamedTuple: `filename`, `success`, `error`
- `clean_corpus(output_dir: Path, workers: int = 5) -> list[CleanResult]` — loads manifest, compares `content_hash` vs `clean_hash`, cleans only changed files, updates manifest with `clean_hash` values

**Files:** `src/doc_hub/clean.py`

### Step 3: Add `update_corpus_fetch_config` to `db.py`

Add a DB function that updates only the `fetch_config` JSONB column for a given corpus slug.

**Files:** `src/doc_hub/db.py`

### Step 4: Integrate cleaning into the sitemap fetcher

In `SitemapFetcher.fetch()`, after downloading all pages via Jina, check `fetch_config.get("clean")`. If true, call `clean_corpus()` on the output directory.

**Files:** `src/doc_hub/_builtins/fetchers/sitemap.py`

### Step 5: Add `pipeline clean` CLI subcommand

- Add `handle_clean()` handler in `src/doc_hub/cli/pipeline.py`
- Register the `clean` subparser under `pipeline` with a positional `slug` argument
- Handler: loads corpus from DB, validates clean env vars, runs `clean_corpus()`, sets `clean: true` in fetch_config via `update_corpus_fetch_config()`

**Files:** `src/doc_hub/cli/pipeline.py`

### Step 6: Write tests

- Test `get_clean_config()` with env vars set/unset
- Test `clean_markdown()` with mocked `AsyncOpenAI`
- Test `clean_corpus()` manifest diff logic (skip already-clean files, clean changed files)
- Test `pipeline clean` CLI handler integration

**Files:** `tests/test_clean.py`

### Step 7: Update documentation

- `man/doc-hub.1` — add `pipeline clean` command entry
- `docs/user/cli-reference.md` — add `pipeline clean` section
- `docs/user/configuration.md` — add cleaning env vars section
- `.agent/install-manager/install/environment.md` — add optional cleaning env vars

**Files:** `man/doc-hub.1`, `docs/user/cli-reference.md`, `docs/user/configuration.md`, `.agent/install-manager/install/environment.md`
