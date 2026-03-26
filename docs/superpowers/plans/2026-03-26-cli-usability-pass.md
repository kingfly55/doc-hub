# doc-hub CLI Usability Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a built-in manpage display command, require one-or-more corpora for search, add browse/read short document IDs, and refresh install-manager guidance.

**Architecture:** Extend the existing `docs` CLI group with a small `man` command that reuses the bundled manpage as source text. Update search from a single optional corpus filter to a required repeatable corpus filter, and add deterministic short document IDs derived from corpus slug plus canonical document path so browse and read can share the same resolver.

**Tech Stack:** Python 3.11, argparse, asyncpg, pytest, packaged manpage assets, Markdown install-manager docs

---

## File map

- Modify: `src/doc_hub/cli/docs.py` — register and implement `docs man`
- Modify: `src/doc_hub/search.py` — require repeatable `--corpus`, update parser and SQL/filtering
- Modify: `src/doc_hub/browse.py` — render browse IDs and accept read-by-ID
- Modify: `src/doc_hub/documents.py` — add deterministic short ID helpers and tree/chunk lookup support
- Modify: `tests/test_unified_cli.py` — CLI routing and doc assertions
- Modify: `tests/...` existing browse/search tests or add focused test files as needed
- Modify: `README.md`
- Modify: `docs/user/cli-reference.md`
- Modify: `.agent/install-manager/install/clone-setup.md`
- Modify: `.agent/install-manager/install/environment.md`
- Modify: `.agent/install-manager/diagnostics/commands.md`
- Modify: `.agent/install-manager/memory/installation-state.md`
- Modify: `.agent/install-manager/memory/resolved-incidents.md`

### Task 1: Add `doc-hub docs man`

**Files:**
- Modify: `src/doc_hub/cli/docs.py`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Write the failing tests**

Add focused tests for both parser routing and rendered output. The output test should call the real handler and assert key lines from the bundled manpage appear.

```python
def test_docs_man_routes_to_man_handler():
    from doc_hub.cli.main import main

    with patch("doc_hub.cli.docs.handle_man") as mock_handler:
        main(["docs", "man"])

    mock_handler.assert_called_once()
```

```python
def test_docs_man_prints_bundled_manpage(capsys):
    from doc_hub.cli.docs import handle_man

    handle_man(argparse.Namespace())

    out = capsys.readouterr().out
    assert "doc-hub docs list" in out
    assert "doc-hub serve mcp" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /home/joenathan/.config/superpowers/worktrees/doc-hub/cli-usability-pass && uv run pytest tests/test_unified_cli.py -k "docs_man" -q
```

Expected: FAIL because `docs man` is not registered and no handler exists.

- [ ] **Step 3: Write minimal implementation**

Register a new `man` subcommand in `src/doc_hub/cli/docs.py`.
Implement a small loader that reads the bundled `man/doc-hub.1` text and converts it into a plain terminal-friendly form by stripping the tiny subset of roff macros already used in the file. Keep the conversion minimal and local.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /home/joenathan/.config/superpowers/worktrees/doc-hub/cli-usability-pass && uv run pytest tests/test_unified_cli.py -k "docs_man" -q
```

Expected: PASS.

### Task 2: Require repeatable `--corpus` for search

**Files:**
- Modify: `src/doc_hub/search.py`
- Test: existing search tests or a focused new test file under `tests/`

- [ ] **Step 1: Write the failing tests**

Add parser and execution tests that assert:
- `--corpus` is required
- repeated `--corpus` flags are accepted
- the search execution layer receives a list of corpora

Example parser assertions:

```python
def test_search_requires_at_least_one_corpus():
    parser = build_search_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["retry logic"])
```

```python
def test_search_accepts_multiple_corpora():
    parser = build_search_parser()
    args = parser.parse_args(["--corpus", "pydantic-ai", "--corpus", "fastapi", "retry logic"])
    assert args.corpora == ["pydantic-ai", "fastapi"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run the focused search tests and confirm failure.

- [ ] **Step 3: Write minimal implementation**

Update `src/doc_hub/search.py` so:
- parser uses repeatable `--corpus` with `required=True`
- search internals accept `corpora: list[str]`
- SQL filtering uses `corpus_id = ANY($N)` or equivalent array filtering
- human-readable output prints the requested corpora set clearly

- [ ] **Step 4: Run tests to verify they pass**

Run the focused search tests green.

- [ ] **Step 5: Run broader search regressions**

Run the relevant search-related test file(s) green.

### Task 3: Add browse/read short document IDs

**Files:**
- Modify: `src/doc_hub/documents.py`
- Modify: `src/doc_hub/browse.py`
- Test: existing browse/read test file(s) or a focused new test file under `tests/`

- [ ] **Step 1: Write the failing tests**

Add focused tests that assert:
- browse output includes a short ID next to concrete docs
- browse JSON includes `doc_id`
- read accepts a short ID and resolves to the same document as the canonical path
- group nodes do not receive IDs

Use a deterministic fixture so the expected short IDs are known.

- [ ] **Step 2: Run tests to verify they fail**

Run the focused browse/read tests and confirm failure.

- [ ] **Step 3: Write minimal implementation**

In `src/doc_hub/documents.py`:
- add a helper that derives a stable short ID from corpus slug + doc path
- add collision handling that extends the ID deterministically within a corpus only if needed
- include `doc_id` in tree rows for concrete documents
- add a resolver that maps corpus + `doc_id` back to canonical `doc_path`

In `src/doc_hub/browse.py`:
- show the ID in browse text output for concrete documents
- include it in browse JSON
- allow `read` to accept either canonical path or short ID and resolve before fetching chunks

- [ ] **Step 4: Run tests to verify they pass**

Run the focused browse/read tests green.

- [ ] **Step 5: Run broader browse/read regressions**

Run the relevant browse/read test file(s) green.

### Task 4: Update docs and install-manager guidance

**Files:**
- Modify: `README.md`
- Modify: `docs/user/cli-reference.md`
- Modify: `.agent/install-manager/install/clone-setup.md`
- Modify: `.agent/install-manager/install/environment.md`
- Modify: `.agent/install-manager/diagnostics/commands.md`
- Modify: `.agent/install-manager/memory/installation-state.md`
- Modify: `.agent/install-manager/memory/resolved-incidents.md`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Write the failing doc assertions**

Extend documentation assertions so they require the new surfaces and notes:
- `doc-hub docs man`
- repeated `--corpus` usage
- browse/read short ID mention
- install-manager note that `docs man` is the built-in fallback and that MANPATH may need the uv tool man dir

- [ ] **Step 2: Run tests to verify they fail**

Run the focused doc assertions and confirm failure.

- [ ] **Step 3: Write minimal documentation updates**

Update user docs and install-manager docs/memory with the exact verified workflow.
Keep prose concise and operational.

- [ ] **Step 4: Run tests to verify they pass**

Run the focused doc assertions green.

### Task 5: End-to-end verification, merge, install, and publish

**Files:**
- Modify only files above if verification reveals necessary small fixes

- [ ] **Step 1: Reinstall from the worktree**

Run:

```bash
uv tool install --force /home/joenathan/.config/superpowers/worktrees/doc-hub/cli-usability-pass
```

Expected: the global `doc-hub` command is rebuilt from the worktree.

- [ ] **Step 2: Verify end-to-end CLI behavior**

Run commands equivalent to:

```bash
cd /tmp && doc-hub docs man >/dev/null
cd /tmp && doc-hub docs search --corpus pydantic-ai "retry logic" >/dev/null
cd /tmp && doc-hub docs browse pydantic-ai >/tmp/doc-hub-browse.txt
```

Then extract one shown short ID from browse output and verify:

```bash
doc-hub docs read pydantic-ai <short-id> >/dev/null
```

- [ ] **Step 3: Run the full test suite**

Run:

```bash
set -a && source "/home/joenathan/.local/share/doc-hub/env" && set +a && cd "/home/joenathan/.config/superpowers/worktrees/doc-hub/cli-usability-pass" && uv run pytest tests/ -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

Create a single commit for the usability pass.

- [ ] **Step 5: Push branch**

Push `ops/cli-usability-pass`.

- [ ] **Step 6: Merge to main, push main, and update local install**

After verification, fast-forward local `main`, verify merged `main`, push `main`, reinstall the global tool from main, and confirm the installed behavior still works.
