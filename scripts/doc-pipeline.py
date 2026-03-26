#!/usr/bin/env python3
"""Documentation generation pipeline for doc-hub.

Two-stage pipeline:
  Stage 1 (Plan):  Read codebase → produce doc structure outline
  Stage 2 (Write): One agent per doc file, parallelized

Developer docs follow agent-first principles from harness-engineering:
  - AGENTS.md as short table of contents (~100 lines)
  - ARCHITECTURE.md as top-level domain map
  - Progressive disclosure: map, not manual
  - Repository knowledge as system of record

Usage:
    python scripts/doc-pipeline.py
    python scripts/doc-pipeline.py --skip-plan   # plan exists
    python scripts/doc-pipeline.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("doc-pipeline")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT  # standalone repo — package root IS repo root
LOG_DIR = REPO_ROOT / ".pipeline-logs"
PLAN_FILE = REPO_ROOT / "docs" / "exec-plans" / "completed" / "plugin-architecture" / "_doc_plan.md"

DEFAULT_PLAN_MODEL = "claude-opus-4-6"
DEFAULT_WRITE_MODEL = "claude-sonnet-4-6"
MAX_PARALLEL = 3  # concurrent write agents

# Every source file the agents might need
SOURCE_FILES = [
    "src/doc_hub/__init__.py",
    "src/doc_hub/protocols.py",
    "src/doc_hub/discovery.py",
    "src/doc_hub/models.py",
    "src/doc_hub/paths.py",
    "src/doc_hub/db.py",
    "src/doc_hub/fetchers.py",
    "src/doc_hub/parse.py",
    "src/doc_hub/embed.py",
    "src/doc_hub/index.py",
    "src/doc_hub/search.py",
    "src/doc_hub/pipeline.py",
    "src/doc_hub/mcp_server.py",
    "src/doc_hub/eval.py",
    "src/doc_hub/_builtins/fetchers/llms_txt.py",
    "src/doc_hub/_builtins/fetchers/local_dir.py",
    "src/doc_hub/_builtins/fetchers/sitemap.py",
    "src/doc_hub/_builtins/fetchers/git_repo.py",
    "src/doc_hub/_builtins/parsers/markdown.py",
    "src/doc_hub/_builtins/embedders/gemini.py",
    "pyproject.toml",
    "README.md",
    "docs/dev/plugin-authoring.md",
]

SOURCE_FILES_BULLET = "\n".join(f"- {f}" for f in SOURCE_FILES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_log(name: str, content: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_path = LOG_DIR / f"{ts}_{name}.log"
    log_path.write_text(content)
    log.info("Log: %s (%d chars)", log_path.name, len(content))
    return log_path


async def run_agent(
    prompt: str,
    *,
    name: str,
    model: str,
    allowed_tools: list[str],
    max_turns: int = 50,
) -> str:
    """Run a Claude Code agent via subprocess."""
    import shutil

    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError("claude CLI not found in PATH")

    log.info("Agent: %s (model=%s, max_turns=%d)", name, model, max_turns)
    start = datetime.now(timezone.utc)

    cmd = [
        claude_path,
        "--print",
        "--model", model,
        "--max-turns", str(max_turns),
        "--dangerously-skip-permissions",
        "--verbose",
    ]
    for tool in allowed_tools:
        cmd.extend(["--allowedTools", tool])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(REPO_ROOT),
    )

    stdout_bytes, stderr_bytes = await proc.communicate(prompt.encode())
    output = stdout_bytes.decode(errors="replace")
    stderr_str = stderr_bytes.decode(errors="replace")

    if stderr_str.strip():
        log.info("Agent %s stderr (first 500): %s", name, stderr_str[:500])

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    log.info("Agent %s done in %.1fs (%d chars)", name, elapsed, len(output))

    write_log(name, output)
    return output


# ---------------------------------------------------------------------------
# Stage 1: Plan documentation structure
# ---------------------------------------------------------------------------

PLAN_PROMPT = """You are a senior technical writer planning documentation for doc-hub, a plugin-based
documentation search engine for Python.

## Your Task

Read the full codebase, then produce a documentation plan as a single markdown file.

The plan must define TWO documentation sets:

### 1. User Documentation (`docs/user/`)

For people who want to **install and use** doc-hub. They are NOT looking at the source code.
Write docs for these files:

- `docs/user/getting-started.md` — Install, configure DB, add first corpus, run first search.
  Cover pip install, env vars, PostgreSQL+VectorChord setup, adding a corpus, searching.
- `docs/user/configuration.md` — All env vars, DOC_HUB_DATABASE_URL, XDG paths, data directory.
- `docs/user/cli-reference.md` — All 5 console scripts with flags, examples, exit codes.
- `docs/user/mcp-server.md` — Running the MCP server, all 4 tools, transport modes, Claude Desktop integration.
- `docs/user/evaluation.md` — Writing eval files, running evals, interpreting P@5 and MRR.
- `docs/user/cloud-database.md` — Using hosted PostgreSQL (Neon, Supabase, Railway) with VectorChord.

### 2. Developer Documentation (agent-first)

For humans AND AI agents working on doc-hub. Follow these principles from OpenAI's harness-engineering:

**AGENTS.md** (~100 lines) — Short table of contents. NOT an encyclopedia. Pointers to deeper docs.
Progressive disclosure: agents start here, then follow links. Must include:
  - Quick map of src/doc_hub/ modules and their responsibilities
  - How to run tests
  - Where to find plugin protocols, architecture, and DB schema docs
  - Link to each doc in docs/dev/

**ARCHITECTURE.md** — Domain map. Module dependency graph. Layer rules (which modules may import which).
Data flow diagram (fetch→parse→embed→index→search). Plugin boundary description.

Developer reference docs in `docs/dev/`:
- `docs/dev/plugin-authoring.md` — Complete guide: write a fetcher, parser, or embedder from scratch.
  Include protocol signatures, entry point registration, local plugin alternative, testing plugins.
  This replaces `docs/writing-fetchers.md` with a comprehensive version covering all plugin types.
- `docs/dev/protocols-reference.md` — Every protocol method, every parameter, every return type.
  Include runtime_checkable behavior, structural typing explanation.
- `docs/dev/database-schema.md` — All tables, columns, indexes, constraints. DDL explained.
  Vector dimension configuration. Migration notes.
- `docs/dev/testing-guide.md` — How to run tests, what markers exist, how to mock DB/embedder/fetcher.
  How to write tests for new plugins. Integration test requirements.
- `docs/dev/search-internals.md` — How hybrid search works: vector KNN, BM25, RRF fusion, scoring.

## Instructions

1. Read ALL source files listed below to understand the actual API surfaces, function signatures,
   CLI flags, env vars, etc. Be precise — the plan drives what gets written.

Source files to read:
{source_files}

2. For EACH planned document, write:
   - **File path** (relative to repo root)
   - **Audience** (user or developer/agent)
   - **Purpose** (1 sentence)
   - **Sections** (ordered list of section headings with 1-2 sentence description of content)
   - **Key source files to reference** (which .py files the writing agent must read)
   - **Specific items to cover** (exact function names, env vars, CLI flags, etc.)

3. Write the plan to: {plan_file}

Be exhaustive in the "Specific items to cover" section — the writing agents will only know
what you tell them here. If a CLI flag, env var, protocol method, or SQL table needs to be
documented, list it explicitly.

Do NOT write the actual documentation — only the plan.
"""


async def run_plan(model: str) -> None:
    log.info("=" * 60)
    log.info("STAGE 1: PLAN — Documentation structure")
    log.info("=" * 60)

    PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)

    prompt = PLAN_PROMPT.format(
        source_files=SOURCE_FILES_BULLET,
        plan_file=PLAN_FILE,
    )

    await run_agent(
        prompt,
        name="doc_plan",
        model=model,
        allowed_tools=["Read", "Write", "Glob", "Grep"],
        max_turns=60,
    )

    if not PLAN_FILE.exists():
        log.error("PLAN FAILED: %s not created!", PLAN_FILE)
        sys.exit(1)

    log.info("Plan written: %s (%d bytes)", PLAN_FILE, PLAN_FILE.stat().st_size)


# ---------------------------------------------------------------------------
# Stage 2: Write documentation (parallelized)
# ---------------------------------------------------------------------------

WRITE_PROMPT_TEMPLATE = """You are a technical writer producing documentation for doc-hub.

## Document to Write

**File**: {doc_path}
**Audience**: {audience}

## Documentation Plan (what to cover)

{plan_section}

## Instructions

1. Read the source files listed in the plan section above. They are relative to the repo root.

2. Write the document to: {abs_doc_path}
   Create parent directories if needed.

3. Follow these rules:
   - Be precise. Use actual function signatures, actual flag names, actual env var names from code.
   - Include runnable code examples where appropriate.
   - For user docs: assume the reader has NOT read the source code. Be complete and self-contained.
   - For developer/agent docs: optimize for agent legibility first. Use structured headings,
     explicit file paths, exact function signatures. An agent reading this doc should be able
     to make changes without asking follow-up questions.
   - Do NOT invent features, flags, or APIs that don't exist in the code.
   - Do NOT include boilerplate like "Contributing" or "License" sections unless the plan says to.
   - Keep the tone direct and technical. No marketing language.

{extra_instructions}

4. After writing the file, verify it exists and output:
   DOC_COMPLETE file={doc_path}
"""

AGENTS_MD_EXTRA = """
CRITICAL: This is AGENTS.md — the agent entry point. Follow harness-engineering principles:

- Keep it under 120 lines. It's a MAP, not an encyclopedia.
- Start with a 2-3 line summary of what doc-hub is.
- Then a module map: one line per module in src/doc_hub/, describing its responsibility.
- Then sections pointing to deeper docs: "For plugin development, see docs/dev/plugin-authoring.md"
- Include: how to run tests, how to lint, what the key entry points are.
- End with a "Where to look" section mapping common tasks → specific files/docs.

Example structure:
```
# doc-hub

One-line description.

## Module Map
| Module | Responsibility |
...

## Quick Reference
- Tests: `pytest tests/`
- Lint: `ruff check src/`
...

## Deep Dives
- [Plugin Authoring](docs/dev/plugin-authoring.md) — ...
- [Architecture](ARCHITECTURE.md) — ...
...

## Where to Look
| I want to... | Look at... |
...
```
"""

ARCHITECTURE_EXTRA = """
CRITICAL: This is ARCHITECTURE.md — the structural map of the codebase.

Include:
1. **Data flow diagram** (ASCII): fetch → parse → embed → index → search
2. **Module dependency graph** — which modules import which. Be precise (read imports).
3. **Layer rules** — what's allowed to import what.
4. **Plugin boundary** — where plugins plug in, what the core handles vs plugins.
5. **Database tables** — brief overview with pointers to full schema doc.
6. **Key data types** — Corpus, Chunk, EmbeddedChunk, SearchResult flow.

Use ASCII diagrams, tables, and structured headings. Agents need to navigate this fast.
"""


# Documents to write. Each tuple: (relative_path, audience, extra_instructions)
DOCS_TO_WRITE = [
    ("AGENTS.md", "developer/agent", AGENTS_MD_EXTRA),
    ("ARCHITECTURE.md", "developer/agent", ARCHITECTURE_EXTRA),
    ("docs/dev/plugin-authoring.md", "developer/agent", ""),
    ("docs/dev/protocols-reference.md", "developer/agent", ""),
    ("docs/dev/database-schema.md", "developer/agent", ""),
    ("docs/dev/testing-guide.md", "developer/agent", ""),
    ("docs/dev/search-internals.md", "developer/agent", ""),
    ("docs/user/getting-started.md", "user", ""),
    ("docs/user/configuration.md", "user", ""),
    ("docs/user/cli-reference.md", "user", ""),
    ("docs/user/mcp-server.md", "user", ""),
    ("docs/user/evaluation.md", "user", ""),
    ("docs/user/cloud-database.md", "user", ""),
]


def extract_plan_section(plan_text: str, doc_path: str) -> str:
    """Extract the section of the plan relevant to a specific document.

    Looks for the doc_path (or its filename) in the plan and extracts the
    surrounding content until the next document section starts.
    """
    lines = plan_text.split("\n")
    # Find the line that references this doc path
    start_idx = None
    filename = Path(doc_path).name
    path_variants = [doc_path, filename, f"`{doc_path}`", f"`{filename}`"]

    for i, line in enumerate(lines):
        if any(v in line for v in path_variants):
            # Walk back to find the section heading
            for j in range(i, max(i - 5, -1), -1):
                if lines[j].startswith("#") or lines[j].startswith("- **File"):
                    start_idx = j
                    break
            if start_idx is None:
                start_idx = i
            break

    if start_idx is None:
        # Fallback: return the whole plan
        return plan_text

    # Find the end of this section (next doc section or end of file)
    end_idx = len(lines)
    # Look for the next document section (similar heading level or next file entry)
    heading_level = 0
    for c in lines[start_idx]:
        if c == "#":
            heading_level += 1
        else:
            break

    if heading_level > 0:
        for i in range(start_idx + 1, len(lines)):
            line = lines[i]
            if line.startswith("#"):
                level = 0
                for c in line:
                    if c == "#":
                        level += 1
                    else:
                        break
                if level <= heading_level:
                    end_idx = i
                    break

    return "\n".join(lines[start_idx:end_idx])


async def write_single_doc(
    doc_path: str,
    audience: str,
    extra: str,
    plan_text: str,
    model: str,
    semaphore: asyncio.Semaphore,
) -> bool:
    """Write a single documentation file."""
    async with semaphore:
        plan_section = extract_plan_section(plan_text, doc_path)
        abs_path = PKG_DIR / doc_path

        prompt = WRITE_PROMPT_TEMPLATE.format(
            doc_path=doc_path,
            audience=audience,
            plan_section=plan_section,
            abs_doc_path=abs_path,
            extra_instructions=extra,
        )

        safe_name = doc_path.replace("/", "_").replace(".", "_")
        output = await run_agent(
            prompt,
            name=f"write_{safe_name}",
            model=model,
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
            max_turns=40,
        )

        sentinel = f"DOC_COMPLETE file={doc_path}"
        if sentinel in output:
            log.info("OK: %s written", doc_path)
            return True
        elif abs_path.exists():
            log.warning("Sentinel missing but file exists: %s", doc_path)
            return True
        else:
            log.error("FAILED: %s not written", doc_path)
            return False


async def run_write(model: str) -> None:
    log.info("=" * 60)
    log.info("STAGE 2: WRITE — Generating %d documents", len(DOCS_TO_WRITE))
    log.info("=" * 60)

    if not PLAN_FILE.exists():
        log.error("Plan file not found: %s", PLAN_FILE)
        sys.exit(1)

    plan_text = PLAN_FILE.read_text()
    semaphore = asyncio.Semaphore(MAX_PARALLEL)

    tasks = [
        write_single_doc(doc_path, audience, extra, plan_text, model, semaphore)
        for doc_path, audience, extra in DOCS_TO_WRITE
    ]

    results = await asyncio.gather(*tasks)

    succeeded = sum(1 for r in results if r)
    failed = sum(1 for r in results if not r)
    log.info("Write complete: %d succeeded, %d failed", succeeded, failed)

    if failed > 0:
        log.error("Some documents failed to generate. Check logs.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Documentation generation pipeline for doc-hub",
    )
    parser.add_argument(
        "--skip-plan", action="store_true",
        help="Skip plan stage (docs/_plan.md already exists)",
    )
    parser.add_argument(
        "--skip-write", action="store_true",
        help="Skip write stage (plan only)",
    )
    parser.add_argument(
        "--plan-model", default=DEFAULT_PLAN_MODEL,
        help=f"Model for plan stage (default: {DEFAULT_PLAN_MODEL})",
    )
    parser.add_argument(
        "--write-model", default=DEFAULT_WRITE_MODEL,
        help=f"Model for write stage (default: {DEFAULT_WRITE_MODEL})",
    )
    parser.add_argument(
        "--parallel", type=int, default=MAX_PARALLEL,
        help=f"Max parallel write agents (default: {MAX_PARALLEL})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would run without executing",
    )
    return parser.parse_args(argv)


async def main_async() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_DIR / "doc-pipeline.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logging.getLogger().addHandler(fh)

    log.info("Doc pipeline started at %s", timestamp())
    log.info("Plan model: %s", args.plan_model)
    log.info("Write model: %s", args.write_model)
    log.info("Parallel: %d", args.parallel)

    if args.dry_run:
        log.info("DRY RUN:")
        if not args.skip_plan:
            log.info("  1. Plan: read codebase → docs/_plan.md")
        if not args.skip_write:
            for doc_path, audience, _ in DOCS_TO_WRITE:
                log.info("  2. Write: %s (%s)", doc_path, audience)
        log.info("DRY RUN complete")
        return

    global MAX_PARALLEL
    MAX_PARALLEL = args.parallel

    if not args.skip_plan:
        await run_plan(args.plan_model)

    if not args.skip_write:
        await run_write(args.write_model)

    log.info("=" * 60)
    log.info("DOC PIPELINE COMPLETE at %s", timestamp())
    log.info("=" * 60)

    # Summary: list generated files
    for doc_path, _, _ in DOCS_TO_WRITE:
        full = PKG_DIR / doc_path
        if full.exists():
            size = full.stat().st_size
            log.info("  %s — %d bytes", doc_path, size)
        else:
            log.warning("  %s — MISSING", doc_path)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
