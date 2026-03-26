#!/usr/bin/env python3
"""Adversarial implementation pipeline for doc-hub document hierarchy feature.

Runs: Init → Adversarial Refinement → Automation Audit → Implementation Loop.

Usage:
    # Full run: init + 4 adversarial rounds + implementation
    python scripts/pipeline.py

    # Skip init and adversarial (plan already refined), start implementing
    python scripts/pipeline.py --skip-init --skip-adversarial

    # Start from a specific milestone
    python scripts/pipeline.py --skip-init --skip-adversarial --phase 3

    # Custom models
    python scripts/pipeline.py --impl-model claude-sonnet-4-6

    # Dry run
    python scripts/pipeline.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("pipeline")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_DIR = REPO_ROOT / "docs" / "exec-plans" / "document-hierarchy"
LOG_DIR = REPO_ROOT / ".pipeline-logs"

DEFAULT_ROUNDS = 3
DEFAULT_ADV_MODEL = "gpt-5.4-mini(medium)"
DEFAULT_IMPL_MODEL = "gpt-5.4-mini(medium)"
DEFAULT_MAX_TURNS = 9999
BASE_BRANCH = "main"

# Key source files agents should read for context
SOURCE_FILES_LIST = """- src/doc_hub/db.py
- src/doc_hub/parse.py
- src/doc_hub/index.py
- src/doc_hub/search.py
- src/doc_hub/pipeline.py
- src/doc_hub/mcp_server.py
- src/doc_hub/protocols.py
- src/doc_hub/_builtins/fetchers/llms_txt.py
- src/doc_hub/_builtins/fetchers/local_dir.py
- src/doc_hub/_builtins/parsers/markdown.py
- pyproject.toml
- tests/test_search.py
- tests/test_mcp_server.py
- tests/test_fetchers.py"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_plan_files() -> dict[str, str]:
    """Read plan.md and all numbered milestone files."""
    files = {}
    plan_md = PLAN_DIR / "plan.md"
    if plan_md.exists():
        files["plan.md"] = plan_md.read_text()

    for f in sorted(PLAN_DIR.glob("*.md")):
        if f.name == "plan.md" or f.name == "_original_spec.md":
            continue
        files[f.name] = f.read_text()

    return files


def format_plan_for_prompt(files: dict[str, str]) -> str:
    """Format all plan files into a single prompt block."""
    parts = []
    if "plan.md" in files:
        parts.append(f"### plan.md\n```markdown\n{files['plan.md']}\n```")

    for name, content in sorted(files.items()):
        if name == "plan.md":
            continue
        parts.append(f"### {name}\n```markdown\n{content}\n```")

    return "\n\n".join(parts)


def milestone_files_exist() -> bool:
    """Check if numbered milestone files (1.md, 2.md, ...) exist."""
    return any(PLAN_DIR.glob("[0-9]*.md"))


def write_log(name: str, content: str) -> Path:
    """Write agent output to a timestamped log file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_path = LOG_DIR / f"{ts}_{name}.log"
    log_path.write_text(content)
    log.info("Log written: %s", log_path)
    return log_path


def create_live_log(name: str) -> Path:
    """Create the live log file used while an agent is running."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"current_{name}.log"
    log_path.write_text("")
    return log_path


async def _stream_pipe(
    stream: asyncio.StreamReader,
    *,
    sink,
    chunks: list[str],
    logger: logging.Logger,
    log_level: int,
    prefix: str,
) -> None:
    """Stream subprocess output live to disk and the pipeline logger."""
    while True:
        chunk = await stream.readline()
        if not chunk:
            break
        text = chunk.decode(errors="replace")
        chunks.append(text)
        sink.write(text)
        sink.flush()
        line = text.rstrip()
        if line:
            logger.log(log_level, "%s%s", prefix, line)


async def run_agent(
    prompt: str,
    *,
    name: str,
    model: str,
    allowed_tools: list[str],
    max_turns: int | None = DEFAULT_MAX_TURNS,
) -> str:
    """Run a Claude Code query and stream agent output live into logs.

    Uses the claude CLI directly with --print (text output) instead of the
    Python SDK to avoid MessageParseError on unknown streaming event types.
    Stdout and stderr are streamed live to both a per-agent log file and
    the main pipeline log so `tail -f` is useful while agents are running.
    """
    import shutil

    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError("claude CLI not found in PATH")

    live_log_path = create_live_log(name)
    max_turns_label = "unlimited" if max_turns is None else str(max_turns)
    log.info("Running agent: %s (model=%s, max_turns=%s)", name, model, max_turns_label)
    log.info("Prompt length: %d chars", len(prompt))
    log.info("Live agent log: %s", live_log_path)
    start = datetime.now(timezone.utc)

    cmd = [
        claude_path,
        "--print",
        "--model", model,
        "--dangerously-skip-permissions",
        "--verbose",
    ]
    if max_turns is not None:
        cmd.extend(["--max-turns", str(max_turns)])
    for tool in allowed_tools:
        cmd.extend(["--allowedTools", tool])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(REPO_ROOT),
    )

    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    proc.stdin.write(prompt.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    with live_log_path.open("a", encoding="utf-8") as sink:
        sink.write(f"# Agent: {name}\n")
        sink.write(f"# Model: {model}\n")
        sink.write(f"# Started: {timestamp()}\n\n")
        sink.flush()

        await asyncio.gather(
            _stream_pipe(
                proc.stdout,
                sink=sink,
                chunks=stdout_chunks,
                logger=log,
                log_level=logging.INFO,
                prefix=f"[{name} stdout] ",
            ),
            _stream_pipe(
                proc.stderr,
                sink=sink,
                chunks=stderr_chunks,
                logger=log,
                log_level=logging.INFO,
                prefix=f"[{name} stderr] ",
            ),
        )
        returncode = await proc.wait()
        sink.write(f"\n# Exit code: {returncode}\n")
        sink.write(f"# Finished: {timestamp()}\n")
        sink.flush()

    full_output = "".join(stdout_chunks)
    stderr_str = "".join(stderr_chunks)

    if returncode != 0:
        raise RuntimeError(
            f"Agent {name} exited with code {returncode}. See {live_log_path}"
        )

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    log.info("Agent %s completed in %.1fs (%d chars output)", name, elapsed, len(full_output))

    if stderr_str.strip():
        log.info("Agent %s emitted %d stderr chars", name, len(stderr_str))

    return full_output


# ---------------------------------------------------------------------------
# Stage 1: Init — break spec into milestones
# ---------------------------------------------------------------------------

INIT_PROMPT_TEMPLATE = """You are a senior technical planner designing the implementation of inter-document
hierarchy and document browsing for a Python documentation search engine called doc-hub.

## Your Task

1. First, read the draft plan at: {plan_dir}/plan.md
2. Then read ALL of these source files to understand the current implementation:

{source_files_list}

3. Break the plan into independently verifiable milestones.
4. For each milestone, create a file at: {{plan_dir}}/N.md (where N is 1, 2, 3, ...)
5. Rewrite {plan_dir}/plan.md — keep the Context and Goal sections, but replace the
   phase-by-phase content with milestone reference entries in this format:

   ### Milestone N — [Name]
   - **File**: N.md
   - **Status**: incomplete
   - **Summary**: [one-line description]

## Rules for Milestones

- Each milestone MUST be independently verifiable.
- Include concrete Verification Steps and Success Criteria that a future implementation agent
  can check programmatically (e.g., "run `uv run pytest tests/test_documents.py -x` and confirm
  all pass", "run `uv run python -c 'from doc_hub.documents import DocumentNode'`").
- Milestones should be sequentially dependent where necessary — note dependencies.
- Each milestone file must have these sections: Scope, Files to Create/Modify, Success Criteria,
  Verification Steps, Dependencies.
- All milestones start with Status: incomplete.
- Each milestone describes WHAT to build and HOW it should work, with enough detail for an
  implementation agent to execute. Include specific function signatures, class definitions,
  file paths, SQL statements, and dataclass definitions where relevant.
- The milestones should cover ALL aspects of the plan: DB schema (doc_documents table +
  document_id FK on doc_chunks), tree building core (documents.py), manifest enhancement
  (llms_txt section parsing), pipeline integration (run_build_tree), MCP tools (browse_corpus_tool,
  get_document_tool), CLI commands (doc-hub-browse, doc-hub-read with large-doc threshold),
  and tests.
- Verification steps must be automatable — no live database, no API keys, no Docker required.
  Use unit tests with mocks/fixtures. Mark anything requiring a live DB as conditional
  (e.g., gated behind `if os.environ.get("PGPASSWORD")`).
- Use `uv run` to execute commands (not bare `python` or `pytest`).

Do NOT read or reference any file named _original_spec.md.

Start by reading plan.md and all source files, then write all milestone files and the updated plan.md.
Do not ask for confirmation.
"""


async def run_init(model: str, max_turns: int | None) -> None:
    """Stage 1: Break the spec into milestones."""
    log.info("=" * 60)
    log.info("STAGE 1: INIT — Breaking spec into milestones")
    log.info("=" * 60)

    if milestone_files_exist():
        log.info("Milestone files already exist — skipping init. Use --force-init to override.")
        return

    # Archive original spec
    plan_md = PLAN_DIR / "plan.md"
    original = PLAN_DIR / "_original_spec.md"
    if plan_md.exists() and not original.exists():
        original.write_text(plan_md.read_text())
        log.info("Archived original spec to _original_spec.md")

    prompt = INIT_PROMPT_TEMPLATE.format(
        plan_dir=PLAN_DIR,
        source_files_list=SOURCE_FILES_LIST,
    )

    await run_agent(
        prompt,
        name="init",
        model=model,
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        max_turns=max_turns,
    )

    # Verify milestones were created
    if not milestone_files_exist():
        log.error("INIT FAILED: No milestone files created!")
        sys.exit(1)

    milestone_count = len(list(PLAN_DIR.glob("[0-9]*.md")))
    log.info("Init complete: %d milestone files created", milestone_count)


# ---------------------------------------------------------------------------
# Stage 2: Adversarial refinement rounds
# ---------------------------------------------------------------------------

ADVERSARIAL_PROMPT_TEMPLATE = """You are a senior adversarial reviewer performing refinement round {round_num}/{total_rounds}
on an implementation plan for adding inter-document hierarchy and document browsing to doc-hub.

## Your Task

1. First, read ALL plan files at {plan_dir}/ (plan.md and all numbered milestone files: 1.md, 2.md, etc.)
2. If you need to verify something against the actual codebase, read the relevant source file
   from this list (only read what you need, not all of them):

{source_files_list}

3. Find every way this plan could fail, be incomplete, or introduce bugs.
4. **Directly edit the plan files on disk** to fix what you find.

Do NOT output a list of findings — incorporate your improvements directly into the files.

Specifically look for and fix:
- **Ambiguities**: Vague descriptions that an implementation agent would struggle with.
  Replace with specific function signatures, SQL statements, file paths, etc.
- **Gaps**: Missing steps, unhandled edge cases, incomplete error handling.
- **Incorrect assumptions**: Things that won't work given the actual codebase (e.g., wrong
  function signatures, missing imports, incorrect column types).
- **Weak success criteria**: Criteria that could pass even if the implementation is wrong.
- **Verification gaps**: Steps that won't actually catch failures, or that require a live
  database / external service that won't be available.
- **Ordering issues**: Milestones that depend on things not yet built.
- **Backward compatibility**: Will existing search, MCP tools, and pipeline still work?
- **Fetcher generality**: Does the tree-building work for ALL fetcher types (llms_txt,
  local_dir, future fetchers), not just llms_txt?
- **Redundancy or contradiction**: Remove duplicated content, resolve conflicting statements.

{round_specific_focus}

Do NOT read or reference any file named _original_spec.md.
Do NOT change the file structure format (milestone entry format in plan.md,
section headers in milestone files). Only improve the content within the structure.
Do NOT change any milestone's Status field.

IMPORTANT: Read all plan files first, then edit them. {edit_order}
"""

# Each round has a specific focus area in addition to general review
ROUND_FOCUSES = {
    1: """## Round 1 Focus: Completeness and Correctness
Focus especially on:
- Are all aspects of the plan covered: DB schema, tree building, manifest enhancement,
  pipeline integration, MCP tools, CLI commands, tests?
- Does the doc_documents DDL actually work? Are FKs, constraints, and indexes correct?
- Is the tree-building algorithm fully specified for BOTH cases: with manifest sections
  (llms_txt) AND without (local_dir, URL-path inference)?
- Does the document_id FK on doc_chunks work correctly with nullable + ON DELETE SET NULL?
- Are the upsert_documents two-pass approach and parent_id resolution fully specified?
- Do milestones have the right dependencies?
- Is the backward compatibility fallback (synthetic tree from doc_chunks) well-specified?""",

    2: """## Round 2 Focus: Edge Cases and Fetcher Generality
Focus especially on:
- Walk through the tree building for a local_dir corpus with NO manifest.json and NO
  source_urls. Does the algorithm handle this gracefully?
- What happens when a corpus has a single document? When it has 500 documents?
- What happens when source_file uses `__` encoding but there are no intermediate
  directory nodes? (e.g., `agents__tools.md` but no `agents.md`)
- What happens when llms.txt has no section headers — just a flat list of URLs?
- Is the large-document threshold behavior fully specified for both MCP and CLI?
- Does get_document_tool handle the case where doc_path doesn't match but source_url does?
- Are section_path prefix filters in get_document_chunks correctly handling partial matches?""",

    3: """## Round 3 Focus: Verification and Implementation Readiness
Focus especially on:
- Could an implementation agent (Claude Sonnet) execute each milestone using only
  the information in the milestone file + plan.md? Or would it need to make guesses?
- Are verification steps concrete enough to be run as `uv run` commands?
- Are success criteria binary (pass/fail) or ambiguous?
- Do verification steps avoid requiring a live PostgreSQL database? (Use unit tests
  with mocks/fixtures instead. Mark integration tests as conditional.)
- Is the final state of the codebase fully described — every new file, every modified
  file, every new function signature?
- Are there any circular dependencies between milestones?
- Final consistency pass: do all milestones agree with each other and with plan.md?""",
}


async def run_adversarial_round(
    round_num: int,
    total_rounds: int,
    model: str,
    max_turns: int | None,
) -> None:
    """Run a single adversarial refinement round."""
    log.info("-" * 60)
    log.info("ADVERSARIAL ROUND %d/%d", round_num, total_rounds)
    log.info("-" * 60)

    round_focus = ROUND_FOCUSES.get(round_num, f"""## Round {round_num} Focus: General Improvement
Perform a general quality pass. Fix anything that could be improved.""")

    # Alternate edit order so later milestones get coverage too
    if round_num % 2 == 0:
        edit_order = (
            "Edit milestone files in REVERSE order (highest number first, "
            "then work down to 1.md, then plan.md last). This ensures later "
            "milestones get thorough review."
        )
    else:
        edit_order = (
            "Edit plan.md first, then milestone files in order (1.md, 2.md, etc.)."
        )

    prompt = ADVERSARIAL_PROMPT_TEMPLATE.format(
        round_num=round_num,
        total_rounds=total_rounds,
        plan_dir=PLAN_DIR,
        source_files_list=SOURCE_FILES_LIST,
        round_specific_focus=round_focus,
        edit_order=edit_order,
    )

    await run_agent(
        prompt,
        name=f"adversarial_round_{round_num}",
        model=model,
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        max_turns=max_turns,
    )

    log.info("Adversarial round %d complete", round_num)


async def run_adversarial(rounds: int, model: str, max_turns: int | None) -> None:
    """Stage 2: Run N adversarial refinement rounds."""
    log.info("=" * 60)
    log.info("STAGE 2: ADVERSARIAL REFINEMENT — %d rounds", rounds)
    log.info("=" * 60)

    for i in range(1, rounds + 1):
        await run_adversarial_round(i, rounds, model, max_turns)

    log.info("All %d adversarial rounds complete", rounds)


# ---------------------------------------------------------------------------
# Stage 3: Automation audit
# ---------------------------------------------------------------------------

AUDIT_PROMPT_TEMPLATE = """You are an automation auditor. Your job is to ensure every milestone in this plan
can be executed and verified by a non-interactive agent with NO human intervention.

## Your Task
Read every milestone file. For each milestone, inspect:
1. **Verification Steps** — every step must be runnable non-interactively
2. **Success Criteria** — every criterion must be checkable programmatically
3. **Scope descriptions** — flag anything that says "confirm with owner", "ask the user", etc.

## What Counts as Human Intervention (fix all of these)
- Commands that require a package manager sync/reinstall before entry points are
  discoverable (e.g., `pip install -e .`, `uv sync`). Fix: use `uv run` prefix or
  mock `importlib.metadata.entry_points()` in verification scripts.
- Steps requiring a live database, network service, or external API that may not be
  available. Fix: make these steps conditional (`|| echo "SKIPPED: requires DB"`) or use
  mocks/fixtures in unit tests.
- Steps that say "confirm with owner", "check with user", "manually verify", or
  require interactive input. Fix: replace with concrete, automatable checks.
- Steps requiring Docker, cloud services, or infrastructure not guaranteed to exist.
  Fix: make conditional or mock.
- Steps requiring API keys or secrets that may not be set. Fix: gate behind env var
  checks.
- Bare `python` or `pytest` commands. Fix: use `uv run python` or `uv run pytest`.

## Rules
- **Directly edit** the milestone files to fix issues. Do NOT just list findings.
- Do NOT change milestone Status fields.
- Do NOT change the file structure format.
- Do NOT read or reference any file named _original_spec.md.
- After editing all files, output a summary of what you changed and why.

## File Locations
- Master plan: {plan_dir}/plan.md
- Milestone files: {plan_dir}/1.md, 2.md, etc.

Read each file from disk, audit it, fix issues, and write it back.
"""


async def run_audit(model: str, max_turns: int | None) -> None:
    """Stage 3: Automation audit."""
    log.info("=" * 60)
    log.info("STAGE 3: AUTOMATION AUDIT")
    log.info("=" * 60)

    prompt = AUDIT_PROMPT_TEMPLATE.format(plan_dir=PLAN_DIR)

    await run_agent(
        prompt,
        name="automation_audit",
        model=model,
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
        max_turns=max_turns,
    )

    log.info("Automation audit complete")


# ---------------------------------------------------------------------------
# Stage 4: Implementation loop
# ---------------------------------------------------------------------------

IMPL_PROMPT_TEMPLATE = """You are implementing Milestone {milestone_num} of the doc-hub document hierarchy and browsing feature.

## Milestone Spec
{milestone_content}

## Master Plan (for context)
{plan_content}

## Working Directory
You are working in: {repo_root}

## Source Files You May Need
These are the key source files in the project. Read what you need:

{source_files_list}

## Instructions

1. Implement everything described in the milestone scope.
2. Work through ALL verification steps from the milestone spec and ensure each passes.
3. After all verification steps pass, do the following bookkeeping:

   a. Append a "## Completion Report" section to {plan_dir}/{milestone_num}.md with:
      - What was implemented (brief summary)
      - Verification results (output or confirmation for each step)
      - Any deviations from the original spec and why
      - Timestamp

   b. Edit {plan_dir}/{milestone_num}.md — change the status line from:
      `**Status**: incomplete`
      to:
      `**Status**: complete`

4. Only after ALL of the above is done, emit this exact sentinel as the very last
   line of your output:
   MILESTONE_COMPLETE milestone={milestone_num}

Do NOT read or reference any file named _original_spec.md.
Do NOT modify files outside this milestone's scope unless the milestone spec says to.
Do NOT emit the sentinel until everything is done and verified.
"""


def detect_next_milestone() -> int | None:
    """Detect the next incomplete milestone by scanning milestone files."""
    milestone_files = sorted(PLAN_DIR.glob("[0-9]*.md"), key=lambda f: int(f.stem))
    for mf in milestone_files:
        content = mf.read_text()
        # Check if status is incomplete (line 3 in our milestone files)
        if "**Status**: incomplete" in content:
            return int(mf.stem)
    return None


async def git_command(*args: str) -> str:
    """Run a git command and return output."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(REPO_ROOT),
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace")
        raise RuntimeError(f"git {' '.join(args)} failed: {err}")
    return stdout.decode(errors="replace").strip()


async def run_implementation_milestone(
    milestone_num: int,
    model: str,
    max_turns: int | None,
) -> bool:
    """Implement a single milestone. Returns True on success."""
    log.info("-" * 60)
    log.info("IMPLEMENTING MILESTONE %d", milestone_num)
    log.info("-" * 60)

    milestone_file = PLAN_DIR / f"{milestone_num}.md"
    if not milestone_file.exists():
        log.error("Milestone file not found: %s", milestone_file)
        return False

    milestone_content = milestone_file.read_text()
    plan_content = (PLAN_DIR / "plan.md").read_text()

    # Create git branch
    if milestone_num == 1:
        base = BASE_BRANCH
    else:
        base = f"milestone/{milestone_num - 1}"

    branch_name = f"milestone/{milestone_num}"

    try:
        # Check if branch already exists
        existing = await git_command("branch", "--list", branch_name)
        if existing.strip():
            log.info("Branch %s already exists — checking out", branch_name)
            await git_command("checkout", branch_name)
        else:
            log.info("Creating branch %s from %s", branch_name, base)
            await git_command("checkout", base)
            await git_command("checkout", "-b", branch_name)
    except RuntimeError as e:
        log.error("Git branch setup failed: %s", e)
        return False

    prompt = IMPL_PROMPT_TEMPLATE.format(
        milestone_num=milestone_num,
        milestone_content=milestone_content,
        plan_content=plan_content,
        repo_root=REPO_ROOT,
        source_files_list=SOURCE_FILES_LIST,
        plan_dir=PLAN_DIR,
    )

    output = await run_agent(
        prompt,
        name=f"implement_milestone_{milestone_num}",
        model=model,
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        max_turns=max_turns,
    )

    # Check for sentinel
    sentinel = f"MILESTONE_COMPLETE milestone={milestone_num}"
    if sentinel in output:
        log.info("Sentinel found — Milestone %d complete", milestone_num)

        # Git commit
        try:
            await git_command("add", "-A")
            await git_command(
                "commit", "-m",
                f"feat: complete milestone {milestone_num}\n\n"
                f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>",
            )
            log.info("Committed milestone %d on branch %s", milestone_num, branch_name)
        except RuntimeError as e:
            log.warning("Git commit failed (may be no changes): %s", e)

        return True
    else:
        log.error("SENTINEL MISSING — Milestone %d may not be complete!", milestone_num)
        log.error("Check log for details. Last 500 chars of output:")
        log.error(output[-500:])
        return False


async def run_implementation(
    start_phase: int,
    model: str,
    max_turns: int | None,
) -> None:
    """Stage 4: Implementation loop."""
    log.info("=" * 60)
    log.info("STAGE 4: IMPLEMENTATION LOOP (starting from milestone %d)", start_phase)
    log.info("=" * 60)

    while True:
        next_milestone = detect_next_milestone()
        if next_milestone is None:
            log.info("All milestones complete!")
            break

        if next_milestone < start_phase:
            log.error(
                "Milestone %d is incomplete but --phase %d was specified. "
                "Earlier milestones must be completed first.",
                next_milestone, start_phase,
            )
            sys.exit(1)

        success = await run_implementation_milestone(next_milestone, model, max_turns)
        if not success:
            log.error("Milestone %d failed. Stopping pipeline.", next_milestone)
            sys.exit(1)

        log.info("Milestone %d done. Detecting next...", next_milestone)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adversarial implementation pipeline with live agent log streaming",
    )
    parser.add_argument(
        "--skip-init",
        action="store_true",
        help="Skip init stage (milestone files already exist)",
    )
    parser.add_argument(
        "--force-init",
        action="store_true",
        help="Force re-run init even if milestone files exist (deletes existing)",
    )
    parser.add_argument(
        "--skip-adversarial",
        action="store_true",
        help="Skip adversarial refinement rounds",
    )
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Skip automation audit stage",
    )
    parser.add_argument(
        "--skip-implementation",
        action="store_true",
        help="Skip implementation loop (plan refinement only)",
    )
    parser.add_argument(
        "--phase",
        type=int,
        default=1,
        help="Start implementation from milestone N (default: 1)",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=DEFAULT_ROUNDS,
        help=f"Number of adversarial refinement rounds (default: {DEFAULT_ROUNDS})",
    )
    parser.add_argument(
        "--adv-model",
        default=DEFAULT_ADV_MODEL,
        help=f"Model for adversarial refinement and init (default: {DEFAULT_ADV_MODEL})",
    )
    parser.add_argument(
        "--impl-model",
        default=DEFAULT_IMPL_MODEL,
        help=f"Model for implementation agents (default: {DEFAULT_IMPL_MODEL})",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
        help=f"Max turns per agent run (default: {DEFAULT_MAX_TURNS})",
    )
    parser.add_argument(
        "--no-max-turns",
        action="store_true",
        help="Do not pass --max-turns to claude at all",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run without executing",
    )
    return parser.parse_args(argv)


async def main_async() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
        ],
    )

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Also log to file
    file_handler = logging.FileHandler(LOG_DIR / "pipeline.log")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logging.getLogger().addHandler(file_handler)

    log.info("Pipeline started at %s", timestamp())
    log.info("Plan dir: %s", PLAN_DIR)
    log.info("Log dir: %s", LOG_DIR)
    effective_max_turns = None if args.no_max_turns else args.max_turns

    log.info("Adv model: %s", args.adv_model)
    log.info("Impl model: %s", args.impl_model)
    log.info("Rounds: %d", args.rounds)
    log.info("Phase: %d", args.phase)
    log.info(
        "Max turns: %s",
        "unlimited (flag omitted)" if effective_max_turns is None else effective_max_turns,
    )

    if args.dry_run:
        log.info("DRY RUN — would execute:")
        if not args.skip_init:
            log.info("  1. Init: break spec into milestones")
        if not args.skip_adversarial:
            for i in range(1, args.rounds + 1):
                focus = ROUND_FOCUSES.get(i, "General improvement")
                focus_title = focus.split("\n")[0] if focus else "General"
                log.info("  2.%d. Adversarial round %d: %s", i, i, focus_title)
        if not args.skip_audit:
            log.info("  3. Automation audit")
        if not args.skip_implementation:
            log.info("  4. Implementation loop starting from milestone %d", args.phase)
        log.info(
            "  Agent turns: %s",
            "unlimited (flag omitted)" if effective_max_turns is None else effective_max_turns,
        )
        log.info("DRY RUN complete — no agents were run")
        return

    # Stage 1: Init
    if args.force_init:
        for f in PLAN_DIR.glob("[0-9]*.md"):
            f.unlink()
            log.info("Removed %s", f.name)

    if not args.skip_init:
        await run_init(args.adv_model, effective_max_turns)

    # Stage 2: Adversarial refinement
    if not args.skip_adversarial:
        await run_adversarial(args.rounds, args.adv_model, effective_max_turns)

    # Stage 3: Automation audit
    if not args.skip_audit:
        await run_audit(args.adv_model, effective_max_turns)

    # Stage 4: Implementation
    if not args.skip_implementation:
        await run_implementation(args.phase, args.impl_model, effective_max_turns)

    log.info("=" * 60)
    log.info("PIPELINE COMPLETE at %s", timestamp())
    log.info("=" * 60)

    # Summary
    plan_files = read_plan_files()
    milestone_count = sum(1 for name in plan_files if name != "plan.md")
    log.info("Final state: %d milestone files", milestone_count)
    for name in sorted(plan_files):
        word_count = len(plan_files[name].split())
        log.info("  %s — %d words", name, word_count)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
