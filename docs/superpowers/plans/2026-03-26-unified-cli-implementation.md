# Unified CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current multi-script CLI with one canonical `doc-hub` command organized under `docs`, `pipeline`, and `serve`.

**Architecture:** Add a dedicated `doc_hub.cli` package that owns parser construction and command grouping while delegating operational behavior to the existing core modules. Refactor existing CLI modules just enough to expose reusable parser builders and callable handlers so the unified CLI preserves behavior without duplicating business logic.

**Tech Stack:** Python 3.11, argparse, asyncio, python-dotenv, pytest

---

## File Map

### Create
- `src/doc_hub/cli/__init__.py` — package marker for the new unified CLI
- `src/doc_hub/cli/main.py` — top-level `doc-hub` parser and dispatch
- `src/doc_hub/cli/shared.py` — common CLI bootstrap helpers (`load_dotenv`, logging helpers)
- `src/doc_hub/cli/docs.py` — `docs browse`, `docs read`, `docs search` command group
- `src/doc_hub/cli/pipeline.py` — `pipeline run`, `pipeline sync-all`, `pipeline eval` command group
- `src/doc_hub/cli/serve.py` — `serve mcp` command group
- `tests/test_unified_cli.py` — parser, dispatch, and behavior-preservation tests for the unified CLI

### Modify
- `pyproject.toml` — remove old script entries, add `doc-hub = "doc_hub.cli.main:main"`
- `src/doc_hub/search.py` — expose reusable parser builder / handler for unified CLI
- `src/doc_hub/browse.py` — expose reusable parser builders and callable handlers for unified CLI
- `src/doc_hub/pipeline.py` — expose reusable parser builders / handlers for `run` and `sync-all`
- `src/doc_hub/eval.py` — expose reusable parser builder / handler for unified CLI
- `src/doc_hub/mcp_server.py` — expose reusable parser builder / handler for unified CLI
- `docs/user/cli-reference.md` — rewrite around canonical `doc-hub` command
- `docs/user/mcp-server.md` — update examples to `doc-hub serve mcp`
- `README.md` — update install/usage examples to unified CLI
- `docs/user/getting-started.md` — update examples to unified CLI

---

### Task 1: Add failing unified CLI tests

**Files:**
- Create: `tests/test_unified_cli.py`
- Reference: `src/doc_hub/search.py`, `src/doc_hub/browse.py`, `src/doc_hub/pipeline.py`, `src/doc_hub/eval.py`, `src/doc_hub/mcp_server.py`

- [ ] **Step 1: Write the failing test file**

```python
from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest


def test_unified_cli_importable():
    from doc_hub.cli.main import main

    assert callable(main)


def test_top_level_groups_parse():
    from doc_hub.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["docs", "browse", "demo"])

    assert args.command_group == "docs"
    assert args.docs_command == "browse"
    assert args.corpus == "demo"


def test_docs_search_routes_to_search_handler():
    from doc_hub.cli.main import main

    with patch("doc_hub.cli.docs.handle_search") as mock_handler:
        main(["docs", "search", "retry logic"])

    mock_handler.assert_called_once()


def test_pipeline_eval_routes_to_eval_handler():
    from doc_hub.cli.main import main

    with patch("doc_hub.cli.pipeline.handle_eval") as mock_handler:
        main(["pipeline", "eval", "--all"])

    mock_handler.assert_called_once()


def test_serve_mcp_routes_to_mcp_handler():
    from doc_hub.cli.main import main

    with patch("doc_hub.cli.serve.handle_mcp") as mock_handler:
        main(["serve", "mcp", "--transport", "stdio"])

    mock_handler.assert_called_once()


def test_old_script_names_removed_from_pyproject():
    import tomllib
    from pathlib import Path

    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    scripts = data["project"]["scripts"]
    assert scripts == {"doc-hub": "doc_hub.cli.main:main"}
```

- [ ] **Step 2: Run the new test file to verify it fails**

Run: `uv run pytest tests/test_unified_cli.py -q`
Expected: FAIL because `doc_hub.cli.main` and unified handlers do not exist yet.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_unified_cli.py
git commit -m "test: add failing unified CLI tests"
```

---

### Task 2: Create the unified CLI package skeleton

**Files:**
- Create: `src/doc_hub/cli/__init__.py`
- Create: `src/doc_hub/cli/shared.py`
- Create: `src/doc_hub/cli/main.py`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Create the package marker**

```python
# src/doc_hub/cli/__init__.py
```

- [ ] **Step 2: Create shared bootstrap helpers**

```python
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv


def bootstrap_cli(*, default_level: int = logging.INFO) -> None:
    load_dotenv()
    level = logging.DEBUG if os.environ.get("LOGLEVEL") == "DEBUG" else default_level
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
```

- [ ] **Step 3: Create the top-level parser and dispatcher**

```python
from __future__ import annotations

import argparse

from doc_hub.cli.docs import register_docs_group
from doc_hub.cli.pipeline import register_pipeline_group
from doc_hub.cli.serve import register_serve_group
from doc_hub.cli.shared import bootstrap_cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doc-hub", description="Unified doc-hub CLI")
    subparsers = parser.add_subparsers(dest="command_group", required=True)
    register_docs_group(subparsers)
    register_pipeline_group(subparsers)
    register_serve_group(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    bootstrap_cli()
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler")
    handler(args)
```

- [ ] **Step 4: Run the focused tests**

Run: `uv run pytest tests/test_unified_cli.py -q`
Expected: FAIL, but now only because the group modules and handlers do not exist.

- [ ] **Step 5: Commit the CLI skeleton**

```bash
git add src/doc_hub/cli/__init__.py src/doc_hub/cli/shared.py src/doc_hub/cli/main.py
git commit -m "feat: add unified CLI skeleton"
```

---

### Task 3: Expose reusable browse/read/search handlers and wire the `docs` group

**Files:**
- Create: `src/doc_hub/cli/docs.py`
- Modify: `src/doc_hub/browse.py`
- Modify: `src/doc_hub/search.py`
- Test: `tests/test_unified_cli.py`
- Test: `tests/test_browse_cli.py`

- [ ] **Step 1: Add a reusable search parser builder in `search.py`**

Refactor the current CLI parser creation into a callable helper:

```python
def build_search_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("query", help="Search query")
    # keep the current search flags exactly as they are today
    return parser
```

- [ ] **Step 2: Add a reusable search handler in `search.py`**

```python
def handle_search_args(args: argparse.Namespace) -> None:
    # move the current main() behavior here without changing semantics
    ...
```

- [ ] **Step 3: Update `main()` in `search.py` to use the reusable pieces**

```python
def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Search doc-hub with hybrid vector + full-text search"
    )
    build_search_parser(parser)
    args = parser.parse_args(argv)
    handle_search_args(args)
```

- [ ] **Step 4: Add reusable browse/read handlers in `browse.py`**

Keep the existing `_build_browse_parser()` and `_build_read_parser()` behavior, but make them accept an optional parser so the unified CLI can embed them.

```python
def build_browse_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        prog="doc-hub docs browse",
        description="Browse the indexed document tree for a corpus.",
    )
    ...
    return parser


def build_read_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        prog="doc-hub docs read",
        description="Read a document from a corpus.",
    )
    ...
    return parser
```

- [ ] **Step 5: Update `browse_main()` and `read_main()` to use the reusable builders**

```python
def browse_main(argv: list[str] | None = None) -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_browse_parser().parse_args(argv)
    asyncio.run(browse(args))


def read_main(argv: list[str] | None = None) -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_read_parser().parse_args(argv)
    asyncio.run(read(args))
```

- [ ] **Step 6: Create the `docs` command group module**

```python
from __future__ import annotations

import argparse

from doc_hub.browse import browse, build_browse_parser, build_read_parser, read
from doc_hub.search import build_search_parser, handle_search_args


def handle_browse(args: argparse.Namespace) -> None:
    import asyncio
    asyncio.run(browse(args))


def handle_read(args: argparse.Namespace) -> None:
    import asyncio
    asyncio.run(read(args))


def handle_search(args: argparse.Namespace) -> None:
    handle_search_args(args)


def register_docs_group(subparsers: argparse._SubParsersAction) -> None:
    docs_parser = subparsers.add_parser("docs", help="Browse, read, and search documentation")
    docs_subparsers = docs_parser.add_subparsers(dest="docs_command", required=True)

    browse_parser = docs_subparsers.add_parser("browse", help="Browse the document tree")
    build_browse_parser(browse_parser)
    browse_parser.set_defaults(handler=handle_browse)

    read_parser = docs_subparsers.add_parser("read", help="Read a document")
    build_read_parser(read_parser)
    read_parser.set_defaults(handler=handle_read)

    search_parser = docs_subparsers.add_parser("search", help="Search documentation")
    build_search_parser(search_parser)
    search_parser.set_defaults(handler=handle_search)
```

- [ ] **Step 7: Run focused tests**

Run: `uv run pytest tests/test_unified_cli.py tests/test_browse_cli.py -q`
Expected: some tests may still fail for missing pipeline/serve groups, but docs-group tests should pass.

- [ ] **Step 8: Commit the docs group wiring**

```bash
git add src/doc_hub/cli/docs.py src/doc_hub/browse.py src/doc_hub/search.py tests/test_unified_cli.py tests/test_browse_cli.py
git commit -m "feat: add unified docs CLI group"
```

---

### Task 4: Expose reusable pipeline and eval handlers and wire the `pipeline` group

**Files:**
- Create: `src/doc_hub/cli/pipeline.py`
- Modify: `src/doc_hub/pipeline.py`
- Modify: `src/doc_hub/eval.py`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Refactor `pipeline.py` parser builder for reuse**

Use the existing `_build_arg_parser()` as the base and let it accept a custom `prog` and parser instance when needed.

- [ ] **Step 2: Extract a reusable `handle_pipeline_run_args(args)` function**

Move the current `main()` body into a reusable handler that preserves current behavior.

- [ ] **Step 3: Extract a reusable `handle_sync_all_args(args)` function**

Because `sync-all` currently has no flags, keep it simple:

```python
def handle_sync_all_args(args: argparse.Namespace) -> None:
    asyncio.run(sync_all_main_async())
```

- [ ] **Step 4: Refactor `eval.py` into reusable parser + handler pieces**

```python
def build_eval_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    # move the current eval CLI flags here unchanged
    return parser


def handle_eval_args(args: argparse.Namespace) -> None:
    # move the current main() behavior here unchanged
    ...
```

- [ ] **Step 5: Create the `pipeline` command group module**

```python
from __future__ import annotations

import argparse

from doc_hub.eval import build_eval_parser, handle_eval_args
from doc_hub.pipeline import _build_arg_parser, handle_pipeline_run_args, handle_sync_all_args


def handle_run(args: argparse.Namespace) -> None:
    handle_pipeline_run_args(args)


def handle_sync_all(args: argparse.Namespace) -> None:
    handle_sync_all_args(args)


def handle_eval(args: argparse.Namespace) -> None:
    handle_eval_args(args)


def register_pipeline_group(subparsers: argparse._SubParsersAction) -> None:
    pipeline_parser = subparsers.add_parser("pipeline", help="Run, sync, and evaluate corpora")
    pipeline_subparsers = pipeline_parser.add_subparsers(dest="pipeline_command", required=True)

    run_parser = pipeline_subparsers.add_parser("run", help="Run the indexing pipeline")
    for action in _build_arg_parser()._actions[1:]:
        if action.dest != "help":
            run_parser._add_action(action)
    run_parser.set_defaults(handler=handle_run)

    sync_parser = pipeline_subparsers.add_parser("sync-all", help="Run the pipeline for all enabled corpora")
    sync_parser.set_defaults(handler=handle_sync_all)

    eval_parser = pipeline_subparsers.add_parser("eval", help="Evaluate retrieval quality")
    build_eval_parser(eval_parser)
    eval_parser.set_defaults(handler=handle_eval)
```

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/test_unified_cli.py -q`
Expected: only serve-group coverage may still be failing.

- [ ] **Step 7: Commit the pipeline group wiring**

```bash
git add src/doc_hub/cli/pipeline.py src/doc_hub/pipeline.py src/doc_hub/eval.py tests/test_unified_cli.py
git commit -m "feat: add unified pipeline CLI group"
```

---

### Task 5: Expose reusable MCP handler and wire the `serve` group

**Files:**
- Create: `src/doc_hub/cli/serve.py`
- Modify: `src/doc_hub/mcp_server.py`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Make MCP arg parsing reusable**

Rename or wrap the existing parser helper so it can be reused by the unified CLI without changing behavior.

```python
def build_mcp_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        prog="doc-hub serve mcp",
        description="doc-hub MCP server — documentation search for LLMs",
    )
    ...
    return parser
```

- [ ] **Step 2: Extract a reusable `handle_mcp_args(args)` function**

Move the current `main()` behavior into a handler while preserving transport behavior.

- [ ] **Step 3: Update `main()` in `mcp_server.py` to use the reusable pieces**

```python
def main(argv: list[str] | None = None) -> None:
    args = build_mcp_parser().parse_args(argv)
    handle_mcp_args(args)
```

- [ ] **Step 4: Create the `serve` command group module**

```python
from __future__ import annotations

import argparse

from doc_hub.mcp_server import build_mcp_parser, handle_mcp_args


def handle_mcp(args: argparse.Namespace) -> None:
    handle_mcp_args(args)


def register_serve_group(subparsers: argparse._SubParsersAction) -> None:
    serve_parser = subparsers.add_parser("serve", help="Serve doc-hub integrations")
    serve_subparsers = serve_parser.add_subparsers(dest="serve_command", required=True)

    mcp_parser = serve_subparsers.add_parser("mcp", help="Run the MCP server")
    build_mcp_parser(mcp_parser)
    mcp_parser.set_defaults(handler=handle_mcp)
```

- [ ] **Step 5: Run focused unified CLI tests**

Run: `uv run pytest tests/test_unified_cli.py -q`
Expected: PASS

- [ ] **Step 6: Commit the serve group wiring**

```bash
git add src/doc_hub/cli/serve.py src/doc_hub/mcp_server.py tests/test_unified_cli.py
git commit -m "feat: add unified serve CLI group"
```

---

### Task 6: Replace old script entrypoints with canonical `doc-hub`

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Replace `[project.scripts]` with the canonical entrypoint**

```toml
[project.scripts]
doc-hub = "doc_hub.cli.main:main"
```

- [ ] **Step 2: Run the entrypoint regression test**

Run: `uv run pytest tests/test_unified_cli.py -k pyproject -q`
Expected: PASS

- [ ] **Step 3: Commit the entrypoint change**

```bash
git add pyproject.toml tests/test_unified_cli.py
git commit -m "refactor: make doc-hub the canonical CLI entrypoint"
```

---

### Task 7: Rewrite docs around the unified CLI

**Files:**
- Modify: `README.md`
- Modify: `docs/user/cli-reference.md`
- Modify: `docs/user/mcp-server.md`
- Modify: `docs/user/getting-started.md`

- [ ] **Step 1: Update README examples**

Replace split-command examples with unified forms:

```bash
doc-hub docs search "how do I handle retries?" --corpus pydantic-ai
doc-hub pipeline run --corpus pydantic-ai
doc-hub serve mcp
```

- [ ] **Step 2: Rewrite `docs/user/cli-reference.md` to the new hierarchy**

The document should be organized by:
- `doc-hub docs browse`
- `doc-hub docs read`
- `doc-hub docs search`
- `doc-hub pipeline run`
- `doc-hub pipeline sync-all`
- `doc-hub pipeline eval`
- `doc-hub serve mcp`

- [ ] **Step 3: Update `docs/user/mcp-server.md` examples**

Use:

```bash
doc-hub serve mcp
doc-hub serve mcp --transport sse --port 8340
```

- [ ] **Step 4: Update `docs/user/getting-started.md` command examples**

Use the unified CLI consistently for pipeline and docs operations.

- [ ] **Step 5: Run a focused docs sanity check**

Run: `grep -R "doc-hub-search\|doc-hub-pipeline\|doc-hub-mcp\|doc-hub-browse\|doc-hub-read\|doc-hub-eval\|doc-hub-sync-all" README.md docs/user`
Expected: only historical references remain where intentionally discussed, not as current usage guidance.

- [ ] **Step 6: Commit the docs rewrite**

```bash
git add README.md docs/user/cli-reference.md docs/user/mcp-server.md docs/user/getting-started.md
git commit -m "docs: rewrite docs for unified doc-hub CLI"
```

---

### Task 8: Full verification

**Files:**
- Test: `tests/test_unified_cli.py`
- Test: existing CLI-related suites and full suite

- [ ] **Step 1: Run focused unified CLI and affected command tests**

Run: `uv run pytest tests/test_unified_cli.py tests/test_browse_cli.py tests/test_mcp_server.py tests/test_pipeline_tree.py tests/test_fetchers.py -q`
Expected: PASS

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest tests/ -x`
Expected: PASS

- [ ] **Step 3: Inspect working tree**

Run: `git status --short`
Expected: only intended unified CLI changes are present.

- [ ] **Step 4: Commit the final verified state if any uncommitted verification-driven fixes were made**

```bash
git add -A
git commit -m "test: finalize unified CLI migration"
```

Use this step only if verification required additional fixes after the previous commits.
