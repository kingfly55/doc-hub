# Pipeline Add & Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `doc-hub pipeline add` and `doc-hub pipeline logs` CLI subcommands so users can register and index corpora from the terminal.

**Architecture:** Both commands are added to the existing `register_pipeline_group()` in `cli/pipeline.py`. The `add` command builds a `Corpus` object from CLI args, upserts it via `db.upsert_corpus()`, and runs `run_pipeline()`. The `logs` command runs the pipeline with logging directed to stdout. A `slugify()` helper derives slugs from corpus names.

**Tech Stack:** Python, argparse, asyncio, asyncpg (existing deps only)

---

### Task 1: Add `slugify()` helper

**Files:**
- Modify: `src/doc_hub/cli/pipeline.py`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_unified_cli.py`:

```python
def test_slugify_basic_cases():
    from doc_hub.cli.pipeline import slugify

    assert slugify("Pydantic AI") == "pydantic-ai"
    assert slugify("FastAPI") == "fastapi"
    assert slugify("My  Great--Docs") == "my-great-docs"
    assert slugify("  Leading Trailing  ") == "leading-trailing"
    assert slugify("Anthropic SDK (Python)") == "anthropic-sdk-python"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/test_unified_cli.py::test_slugify_basic_cases -v`
Expected: FAIL with `ImportError: cannot import name 'slugify'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/doc_hub/cli/pipeline.py` after the existing imports:

```python
import re

def slugify(name: str) -> str:
    """Convert a human-readable name to a URL-safe slug.

    "Pydantic AI" -> "pydantic-ai"
    """
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/test_unified_cli.py::test_slugify_basic_cases -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/doc_hub/cli/pipeline.py tests/test_unified_cli.py
git commit -m "feat: add slugify helper for corpus name-to-slug derivation"
```

---

### Task 2: Add `pipeline add` subcommand (argparse wiring)

**Files:**
- Modify: `src/doc_hub/cli/pipeline.py`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_unified_cli.py`:

```python
def test_pipeline_add_parses_llms_txt_args():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "pipeline", "add", "Pydantic AI",
        "--strategy", "llms_txt",
        "--url", "https://ai.pydantic.dev/llms.txt",
    ])

    assert args.command_group == "pipeline"
    assert args.pipeline_command == "add"
    assert args.name == "Pydantic AI"
    assert args.strategy == "llms_txt"
    assert args.url == "https://ai.pydantic.dev/llms.txt"
    assert args.slug is None
    assert args.no_index is False


def test_pipeline_add_parses_local_dir_args():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "pipeline", "add", "My Docs",
        "--strategy", "local_dir",
        "--path", "/tmp/docs",
        "--slug", "my-docs",
        "--no-index",
    ])

    assert args.name == "My Docs"
    assert args.strategy == "local_dir"
    assert args.path == "/tmp/docs"
    assert args.slug == "my-docs"
    assert args.no_index is True


def test_pipeline_add_parses_git_repo_args():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "pipeline", "add", "Anthropic SDK",
        "--strategy", "git_repo",
        "--url", "https://github.com/anthropics/anthropic-sdk-python.git",
        "--branch", "main",
        "--docs-dir", "docs",
    ])

    assert args.strategy == "git_repo"
    assert args.url == "https://github.com/anthropics/anthropic-sdk-python.git"
    assert args.branch == "main"
    assert args.docs_dir == "docs"


def test_pipeline_add_parses_sitemap_args():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "pipeline", "add", "FastAPI",
        "--strategy", "sitemap",
        "--url", "https://fastapi.tiangolo.com/sitemap.xml",
    ])

    assert args.strategy == "sitemap"
    assert args.url == "https://fastapi.tiangolo.com/sitemap.xml"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/test_unified_cli.py::test_pipeline_add_parses_llms_txt_args tests/test_unified_cli.py::test_pipeline_add_parses_local_dir_args tests/test_unified_cli.py::test_pipeline_add_parses_git_repo_args tests/test_unified_cli.py::test_pipeline_add_parses_sitemap_args -v`
Expected: FAIL with `error: argument pipeline_command: invalid choice: 'add'`

- [ ] **Step 3: Write the argparse wiring**

In `src/doc_hub/cli/pipeline.py`, add a `handle_add` stub and wire up the subcommand in `register_pipeline_group()`:

```python
def handle_add(args: argparse.Namespace) -> None:
    raise NotImplementedError("pipeline add handler not yet implemented")
```

Add to `register_pipeline_group()`, after the existing `eval_parser` block:

```python
    add_parser = pipeline_subparsers.add_parser("add", help="Register a new corpus and run indexing")
    add_parser.add_argument("name", help="Human-readable corpus name")
    add_parser.add_argument(
        "--strategy",
        required=True,
        choices=["llms_txt", "sitemap", "git_repo", "local_dir"],
        help="Fetcher strategy",
    )
    add_parser.add_argument("--slug", default=None, help="Override auto-derived slug")
    add_parser.add_argument("--no-index", action="store_true", help="Register only, skip pipeline run")
    # Strategy-specific flags (all optional at argparse level; validated in handler)
    add_parser.add_argument("--url", default=None, help="URL for llms_txt, sitemap, or git_repo strategies")
    add_parser.add_argument("--path", default=None, help="Local directory path for local_dir strategy")
    add_parser.add_argument("--url-pattern", default=None, help="Regex to filter doc URLs (llms_txt)")
    add_parser.add_argument("--base-url", default=None, help="Base URL for filename generation (llms_txt)")
    add_parser.add_argument("--workers", type=int, default=None, help="Download concurrency (llms_txt)")
    add_parser.add_argument("--retries", type=int, default=None, help="Per-URL retry count (llms_txt)")
    add_parser.add_argument("--branch", default=None, help="Git branch (git_repo)")
    add_parser.add_argument("--docs-dir", default=None, help="Docs subdirectory in repo (git_repo)")
    add_parser.set_defaults(handler=handle_add)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/test_unified_cli.py::test_pipeline_add_parses_llms_txt_args tests/test_unified_cli.py::test_pipeline_add_parses_local_dir_args tests/test_unified_cli.py::test_pipeline_add_parses_git_repo_args tests/test_unified_cli.py::test_pipeline_add_parses_sitemap_args -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/doc_hub/cli/pipeline.py tests/test_unified_cli.py
git commit -m "feat: wire up pipeline add subcommand argparse structure"
```

---

### Task 3: Implement `pipeline add` handler with strategy validation

**Files:**
- Modify: `src/doc_hub/cli/pipeline.py`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_unified_cli.py`:

```python
def test_pipeline_add_builds_config_and_upserts_llms_txt():
    from doc_hub.cli.pipeline import build_fetch_config, slugify

    config = build_fetch_config("llms_txt", argparse.Namespace(
        url="https://ai.pydantic.dev/llms.txt",
        path=None,
        url_pattern=None,
        base_url=None,
        workers=None,
        retries=None,
        branch=None,
        docs_dir=None,
    ))
    assert config == {"url": "https://ai.pydantic.dev/llms.txt"}


def test_pipeline_add_builds_config_llms_txt_with_optionals():
    from doc_hub.cli.pipeline import build_fetch_config

    config = build_fetch_config("llms_txt", argparse.Namespace(
        url="https://ai.pydantic.dev/llms.txt",
        path=None,
        url_pattern=r"https://ai\.pydantic\.dev/[^\s]+\.md",
        base_url="https://ai.pydantic.dev/",
        workers=10,
        retries=5,
        branch=None,
        docs_dir=None,
    ))
    assert config == {
        "url": "https://ai.pydantic.dev/llms.txt",
        "url_pattern": r"https://ai\.pydantic\.dev/[^\s]+\.md",
        "base_url": "https://ai.pydantic.dev/",
        "workers": 10,
        "retries": 5,
    }


def test_pipeline_add_builds_config_local_dir():
    from doc_hub.cli.pipeline import build_fetch_config

    config = build_fetch_config("local_dir", argparse.Namespace(
        url=None,
        path="/tmp/docs",
        url_pattern=None,
        base_url=None,
        workers=None,
        retries=None,
        branch=None,
        docs_dir=None,
    ))
    assert config == {"path": "/tmp/docs"}


def test_pipeline_add_builds_config_git_repo():
    from doc_hub.cli.pipeline import build_fetch_config

    config = build_fetch_config("git_repo", argparse.Namespace(
        url="https://github.com/org/repo.git",
        path=None,
        url_pattern=None,
        base_url=None,
        workers=None,
        retries=None,
        branch="main",
        docs_dir="docs",
    ))
    assert config == {
        "url": "https://github.com/org/repo.git",
        "branch": "main",
        "docs_dir": "docs",
    }


def test_pipeline_add_builds_config_sitemap():
    from doc_hub.cli.pipeline import build_fetch_config

    config = build_fetch_config("sitemap", argparse.Namespace(
        url="https://example.com/sitemap.xml",
        path=None,
        url_pattern=None,
        base_url=None,
        workers=None,
        retries=None,
        branch=None,
        docs_dir=None,
    ))
    assert config == {"url": "https://example.com/sitemap.xml"}


def test_pipeline_add_missing_url_raises():
    from doc_hub.cli.pipeline import build_fetch_config

    try:
        build_fetch_config("llms_txt", argparse.Namespace(
            url=None,
            path=None,
            url_pattern=None,
            base_url=None,
            workers=None,
            retries=None,
            branch=None,
            docs_dir=None,
        ))
        assert False, "Expected SystemExit"
    except SystemExit:
        pass


def test_pipeline_add_missing_path_raises():
    from doc_hub.cli.pipeline import build_fetch_config

    try:
        build_fetch_config("local_dir", argparse.Namespace(
            url=None,
            path=None,
            url_pattern=None,
            base_url=None,
            workers=None,
            retries=None,
            branch=None,
            docs_dir=None,
        ))
        assert False, "Expected SystemExit"
    except SystemExit:
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/test_unified_cli.py -k "build_fetch_config" -v`
Expected: FAIL with `ImportError: cannot import name 'build_fetch_config'`

- [ ] **Step 3: Implement `build_fetch_config()` and the full `handle_add()`**

Add to `src/doc_hub/cli/pipeline.py`:

```python
def build_fetch_config(strategy: str, args: argparse.Namespace) -> dict:
    """Build a fetch_config dict from CLI args, validating required flags per strategy."""
    config: dict = {}

    if strategy in ("llms_txt", "sitemap", "git_repo"):
        if not args.url:
            print(f"Error: --url is required for strategy '{strategy}'", file=sys.stderr)
            raise SystemExit(1)
        config["url"] = args.url

    if strategy == "local_dir":
        if not args.path:
            print("Error: --path is required for strategy 'local_dir'", file=sys.stderr)
            raise SystemExit(1)
        config["path"] = args.path

    # Optional llms_txt flags
    if strategy == "llms_txt":
        if args.url_pattern:
            config["url_pattern"] = args.url_pattern
        if args.base_url:
            config["base_url"] = args.base_url
        if args.workers is not None:
            config["workers"] = args.workers
        if args.retries is not None:
            config["retries"] = args.retries

    # Optional git_repo flags
    if strategy == "git_repo":
        if args.branch:
            config["branch"] = args.branch
        if args.docs_dir:
            config["docs_dir"] = args.docs_dir

    return config
```

Update `handle_add()` to replace the `NotImplementedError` stub:

```python
def handle_add(args: argparse.Namespace) -> None:
    import sys

    fetch_config = build_fetch_config(args.strategy, args)
    slug = args.slug or slugify(args.name)

    async def _add() -> None:
        from doc_hub.db import create_pool, ensure_schema, upsert_corpus
        from doc_hub.models import Corpus

        corpus = Corpus(
            slug=slug,
            name=args.name,
            fetch_strategy=args.strategy,
            fetch_config=fetch_config,
        )

        pool = await create_pool()
        try:
            await ensure_schema(pool)
            await upsert_corpus(pool, corpus)
            print(f"Registered corpus: {corpus.name} [{corpus.slug}]")

            if not args.no_index:
                from doc_hub.pipeline import run_pipeline
                await run_pipeline(corpus, pool=pool)
        finally:
            await pool.close()

    asyncio.run(_add())
```

Also add `import sys` to the top of the file if not already present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/test_unified_cli.py -k "build_fetch_config or pipeline_add" -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/doc_hub/cli/pipeline.py tests/test_unified_cli.py
git commit -m "feat: implement pipeline add handler with strategy validation"
```

---

### Task 4: Test `handle_add` end-to-end with mocks

**Files:**
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_unified_cli.py`:

```python
def test_pipeline_add_registers_and_runs_pipeline():
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())

    with (
        patch("doc_hub.cli.pipeline.create_pool", AsyncMock(return_value=pool)) as mock_pool,
        patch("doc_hub.cli.pipeline.ensure_schema", AsyncMock()) as mock_schema,
        patch("doc_hub.cli.pipeline.upsert_corpus", AsyncMock()) as mock_upsert,
        patch("doc_hub.cli.pipeline.run_pipeline", AsyncMock()) as mock_pipeline,
    ):
        main([
            "pipeline", "add", "Pydantic AI",
            "--strategy", "llms_txt",
            "--url", "https://ai.pydantic.dev/llms.txt",
        ])

    mock_upsert.assert_called_once()
    corpus = mock_upsert.call_args[0][1]
    assert corpus.slug == "pydantic-ai"
    assert corpus.name == "Pydantic AI"
    assert corpus.fetch_strategy == "llms_txt"
    assert corpus.fetch_config == {"url": "https://ai.pydantic.dev/llms.txt"}
    mock_pipeline.assert_called_once()


def test_pipeline_add_no_index_skips_pipeline():
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())

    with (
        patch("doc_hub.cli.pipeline.create_pool", AsyncMock(return_value=pool)),
        patch("doc_hub.cli.pipeline.ensure_schema", AsyncMock()),
        patch("doc_hub.cli.pipeline.upsert_corpus", AsyncMock()),
        patch("doc_hub.cli.pipeline.run_pipeline", AsyncMock()) as mock_pipeline,
    ):
        main([
            "pipeline", "add", "Pydantic AI",
            "--strategy", "llms_txt",
            "--url", "https://ai.pydantic.dev/llms.txt",
            "--no-index",
        ])

    mock_pipeline.assert_not_called()


def test_pipeline_add_custom_slug():
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())

    with (
        patch("doc_hub.cli.pipeline.create_pool", AsyncMock(return_value=pool)),
        patch("doc_hub.cli.pipeline.ensure_schema", AsyncMock()),
        patch("doc_hub.cli.pipeline.upsert_corpus", AsyncMock()) as mock_upsert,
        patch("doc_hub.cli.pipeline.run_pipeline", AsyncMock()),
    ):
        main([
            "pipeline", "add", "Pydantic AI",
            "--strategy", "llms_txt",
            "--url", "https://ai.pydantic.dev/llms.txt",
            "--slug", "pai",
        ])

    corpus = mock_upsert.call_args[0][1]
    assert corpus.slug == "pai"
```

- [ ] **Step 2: Run tests to verify they pass**

These tests should already pass since the handler was implemented in Task 3. Run to confirm:

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/test_unified_cli.py::test_pipeline_add_registers_and_runs_pipeline tests/test_unified_cli.py::test_pipeline_add_no_index_skips_pipeline tests/test_unified_cli.py::test_pipeline_add_custom_slug -v`
Expected: PASS

If they fail, fix the import paths in `handle_add()` — the mocks need to patch the names as imported in `doc_hub.cli.pipeline`, not in `doc_hub.db`. Adjust `handle_add()` to use top-level imports:

At the top of `src/doc_hub/cli/pipeline.py`, add:
```python
from doc_hub.db import create_pool, ensure_schema, upsert_corpus
from doc_hub.models import Corpus
from doc_hub.pipeline import run_pipeline as _run_pipeline
```

And update `handle_add()` to use these directly instead of local imports inside `_add()`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_unified_cli.py src/doc_hub/cli/pipeline.py
git commit -m "test: add end-to-end tests for pipeline add handler"
```

---

### Task 5: Add `pipeline logs` subcommand

**Files:**
- Modify: `src/doc_hub/cli/pipeline.py`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_unified_cli.py`:

```python
def test_pipeline_logs_parses_args():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["pipeline", "logs", "pydantic-ai"])

    assert args.command_group == "pipeline"
    assert args.pipeline_command == "logs"
    assert args.slug == "pydantic-ai"


def test_pipeline_logs_runs_pipeline_with_logging(capsys):
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())
    corpus = SimpleNamespace(
        slug="pydantic-ai",
        name="Pydantic AI",
        fetch_strategy="llms_txt",
        fetch_config={"url": "https://ai.pydantic.dev/llms.txt"},
        parser="markdown",
        embedder="gemini",
        enabled=True,
        last_indexed_at=None,
        total_chunks=42,
    )

    with (
        patch("doc_hub.cli.pipeline.create_pool", AsyncMock(return_value=pool)),
        patch("doc_hub.cli.pipeline.ensure_schema", AsyncMock()),
        patch("doc_hub.cli.pipeline.get_corpus", AsyncMock(return_value=corpus)),
        patch("doc_hub.cli.pipeline.run_pipeline", AsyncMock()) as mock_pipeline,
    ):
        main(["pipeline", "logs", "pydantic-ai"])

    mock_pipeline.assert_called_once()


def test_pipeline_logs_corpus_not_found(capsys):
    from doc_hub.cli.main import main

    pool = SimpleNamespace(close=AsyncMock())

    with (
        patch("doc_hub.cli.pipeline.create_pool", AsyncMock(return_value=pool)),
        patch("doc_hub.cli.pipeline.ensure_schema", AsyncMock()),
        patch("doc_hub.cli.pipeline.get_corpus", AsyncMock(return_value=None)),
    ):
        try:
            main(["pipeline", "logs", "nonexistent"])
            assert False, "Expected SystemExit"
        except SystemExit as e:
            assert e.code == 1

    assert "not found" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/test_unified_cli.py -k "pipeline_logs" -v`
Expected: FAIL with `error: argument pipeline_command: invalid choice: 'logs'`

- [ ] **Step 3: Implement `pipeline logs`**

Add to `src/doc_hub/cli/pipeline.py`:

```python
def handle_logs(args: argparse.Namespace) -> None:
    async def _logs() -> None:
        pool = await create_pool()
        try:
            await ensure_schema(pool)
            corpus = await get_corpus(pool, args.slug)
            if corpus is None:
                print(f"Error: corpus '{args.slug}' not found", file=sys.stderr)
                raise SystemExit(1)

            print(f"Running pipeline for {corpus.name} [{corpus.slug}]...")
            await run_pipeline(corpus, pool=pool)
        finally:
            await pool.close()

    asyncio.run(_logs())
```

Add to `register_pipeline_group()`:

```python
    logs_parser = pipeline_subparsers.add_parser("logs", help="Run pipeline with visible logs for a corpus")
    logs_parser.add_argument("slug", help="Corpus slug")
    logs_parser.set_defaults(handler=handle_logs)
```

Make sure `get_corpus` is imported at the top of the file:

```python
from doc_hub.db import create_pool, ensure_schema, get_corpus, upsert_corpus
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/test_unified_cli.py -k "pipeline_logs" -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/doc_hub/cli/pipeline.py tests/test_unified_cli.py
git commit -m "feat: add pipeline logs subcommand"
```

---

### Task 6: Run full test suite and verify no regressions

**Files:**
- None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/test_unified_cli.py -v`
Expected: All tests PASS, including all pre-existing tests

- [ ] **Step 2: Run a quick smoke test of the CLI help**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m doc_hub.cli.main pipeline add --help`
Expected: Shows help with `name`, `--strategy`, `--url`, `--path`, `--slug`, `--no-index`, etc.

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m doc_hub.cli.main pipeline logs --help`
Expected: Shows help with `slug` positional argument

- [ ] **Step 3: Verify existing tests still pass**

Run: `cd /home/joenathan/Desktop/Projects/code/utilities/agent/doc-hub && python -m pytest tests/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 4: Commit any fixes if needed, then final commit**

```bash
git add -A
git commit -m "chore: verify full test suite passes with pipeline add/logs"
```
