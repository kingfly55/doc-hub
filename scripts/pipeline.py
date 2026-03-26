#!/usr/bin/env python3
"""Adversarial implementation pipeline for doc-hub architecture plan.

Runs: Init → Adversarial Refinement → Implementation Loop.

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
PLAN_DIR = REPO_ROOT / "docs" / "exec-plans" / "completed" / "plugin-architecture"
LOG_DIR = REPO_ROOT / ".pipeline-logs"

DEFAULT_ROUNDS = 4
DEFAULT_ADV_MODEL = "claude-opus-4-6"
DEFAULT_IMPL_MODEL = "claude-sonnet-4-6"
BASE_BRANCH = "main"

# Key source files agents should read for context
SOURCE_FILES_LIST = """- src/doc_hub/models.py
- src/doc_hub/paths.py
- src/doc_hub/db.py
- src/doc_hub/fetchers.py
- src/doc_hub/parse.py
- src/doc_hub/embed.py
- src/doc_hub/index.py
- src/doc_hub/search.py
- src/doc_hub/pipeline.py
- src/doc_hub/mcp_server.py
- src/doc_hub/eval.py
- src/doc_hub/_builtins/fetchers/llms_txt.py
- src/doc_hub/_builtins/parsers/markdown.py
- src/doc_hub/_builtins/embedders/gemini.py
- pyproject.toml
- README.md
- docs/dev/plugin-authoring.md"""


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
    """Write agent output to a log file."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_path = LOG_DIR / f"{ts}_{name}.log"
    log_path.write_text(content)
    log.info("Log written: %s", log_path)
    return log_path


async def run_agent(
    prompt: str,
    *,
    name: str,
    model: str,
    allowed_tools: list[str],
    max_turns: int = 30,
) -> str:
    """Run a Claude Code query via subprocess for robustness.

    Uses the claude CLI directly with --print (text output) instead of the
    Python SDK to avoid MessageParseError on unknown streaming event types.
    The agent's real work is in the file edits it performs; the text output
    is for logging only.
    """
    import shutil

    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError("claude CLI not found in PATH")

    log.info("Running agent: %s (model=%s, max_turns=%d)", name, model, max_turns)
    log.info("Prompt length: %d chars", len(prompt))
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

    # Send prompt via stdin, collect output
    stdout_bytes, stderr_bytes = await proc.communicate(prompt.encode())
    full_output = stdout_bytes.decode(errors="replace")
    stderr_str = stderr_bytes.decode(errors="replace")

    if stderr_str.strip():
        # Log first 1000 chars of stderr (may contain progress info)
        log.info("Agent %s stderr (truncated): %s", name, stderr_str[:1000])

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    log.info("Agent %s completed in %.1fs (%d chars output)", name, elapsed, len(full_output))

    write_log(name, full_output)
    return full_output


# ---------------------------------------------------------------------------
# Stage 1: Init — break spec into milestones
# ---------------------------------------------------------------------------

INIT_PROMPT_TEMPLATE = """You are a senior technical planner designing the architecture for a plugin-based
framework transformation of a Python documentation search engine called doc-hub.

## Your Task

1. First, read the draft plan at: {plan_dir}/plan.md
2. Then read ALL of these source files to understand the current implementation:

{source_files_list}

3. Break the plan into independently verifiable milestones.
4. For each milestone, create a file at: {plan_dir}/N.md (where N is 1, 2, 3, ...)
5. Rewrite {plan_dir}/plan.md — keep the Problem Statement, Architecture Decisions, and
   Constraints sections, but replace the "Milestones" section with milestone reference entries
   in this format:

   ### Milestone N — [Name]
   - **File**: N.md
   - **Status**: incomplete
   - **Summary**: [one-line description]

## Rules for Milestones

- Each milestone MUST be independently verifiable.
- Include concrete Verification Steps and Success Criteria that a future implementation agent
  can check programmatically (e.g., "import doc_hub.protocols and confirm Fetcher, Parser,
  Embedder protocols are defined", "run pytest tests/test_discovery.py").
- Milestones should be sequentially dependent where necessary — note dependencies.
- Each milestone file must have these sections: Scope, Files to Create/Modify, Success Criteria,
  Verification Steps, Dependencies.
- All milestones start with Status: incomplete.
- This is an ARCHITECTURE PLAN, not an implementation. Each milestone describes WHAT to build
  and HOW it should work, with enough detail for an implementation agent to execute. Include
  specific function signatures, class definitions, file paths, SQL statements, and protocol
  definitions where relevant.
- The milestones should cover ALL the problems in the spec: plugin discovery, protocols,
  enum/CHECK removal, home dir storage, DB config cleanup, page browsing, built-in plugins,
  parser pluggability, embedder pluggability, package metadata, and documentation for plugin
  authors.

Do NOT read or reference any file named _original_spec.md.

Start by reading plan.md and all source files, then write all milestone files and the updated plan.md.
Do not ask for confirmation.
"""


async def run_init(model: str) -> None:
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
        max_turns=50,
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
on an implementation plan for transforming doc-hub into a plugin-based framework.

## Your Task

1. First, read ALL plan files at {plan_dir}/ (plan.md and all numbered milestone files: 1.md, 2.md, etc.)
2. If you need to verify something against the actual codebase, read the relevant source file
   from this list (only read what you need, not all of them):

{source_files_list}

3. Find every way this plan could fail, be incomplete, introduce bugs, or create a poor
   developer experience for plugin authors.
4. **Directly edit the plan files on disk** to fix what you find.

Do NOT output a list of findings — incorporate your improvements directly into the files.

Specifically look for and fix:
- **Ambiguities**: Vague descriptions that an implementation agent would struggle with.
  Replace with specific function signatures, SQL statements, file paths, etc.
- **Gaps**: Missing steps, unhandled edge cases, incomplete error handling.
- **Incorrect assumptions**: Things that won't work given the actual codebase.
- **Weak success criteria**: Criteria that could pass even if the implementation is wrong.
- **Verification gaps**: Steps that won't actually catch failures.
- **Ordering issues**: Milestones that depend on things not yet built.
- **Open questions**: If you can resolve them with a well-reasoned decision, do so.
- **Plugin author experience**: Would someone unfamiliar with doc-hub be able to write a
  plugin using only the documentation and protocols described in the plan?
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
- Are all 10 problems from the spec fully addressed across the milestones?
- Do the Protocol definitions actually match what the existing code needs?
- Is the plugin discovery mechanism fully specified (entry points group names,
  fallback directory paths, name collision resolution)?
- Are the SQL schema changes complete and correct?
- Do milestones have the right dependencies?""",

    2: """## Round 2 Focus: Developer Experience and Plugin Author Journey
Focus especially on:
- Walk through the complete journey of someone writing a new fetcher plugin
  from scratch. Is every step documented in the plan?
- Walk through installing doc-hub standalone for the first time. Is the setup
  experience complete (pip install, first corpus, first search)?
- Are error messages specified for common mistakes (wrong config, missing plugin,
  bad dimensions)?
- Is the parser boundary clearly defined — what does a parser produce vs what
  the core pipeline handles?""",

    3: """## Round 3 Focus: Edge Cases and Failure Modes
Focus especially on:
- What happens when a plugin is installed but broken (import error, wrong signature)?
- What happens when two plugins register the same name?
- What happens when a corpus references a fetcher/parser/embedder that isn't installed?
- What happens during concurrent sync operations with the plugin system?
- What happens when vector dimensions don't match between corpus config and table schema?
- Is backward compatibility handled correctly (existing data, existing env vars)?""",

    4: """## Round 4 Focus: Verification and Implementation Readiness
Focus especially on:
- Could an implementation agent (Claude Sonnet) execute each milestone using only
  the information in the milestone file + plan.md? Or would it need to make guesses?
- Are verification steps concrete enough to be run as commands?
- Are success criteria binary (pass/fail) or ambiguous?
- Is the final state of the codebase fully described — if you listed every file that
  should exist after all milestones, could you do it from the plan?
- Are there any circular dependencies between milestones?
- Final consistency pass: do all milestones agree with each other and with plan.md?""",
}


async def run_adversarial_round(round_num: int, total_rounds: int, model: str) -> None:
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
        max_turns=50,
    )

    log.info("Adversarial round %d complete", round_num)


async def run_adversarial(rounds: int, model: str) -> None:
    """Stage 2: Run N adversarial refinement rounds."""
    log.info("=" * 60)
    log.info("STAGE 2: ADVERSARIAL REFINEMENT — %d rounds", rounds)
    log.info("=" * 60)

    for i in range(1, rounds + 1):
        await run_adversarial_round(i, rounds, model)

    log.info("All %d adversarial rounds complete", rounds)


# ---------------------------------------------------------------------------
# Stage 3: Implementation loop
# ---------------------------------------------------------------------------

IMPL_PROMPT_TEMPLATE = """You are implementing Milestone {milestone_num} of the doc-hub plugin architecture transformation.

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


async def run_implementation_milestone(milestone_num: int, model: str) -> bool:
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
        max_turns=80,
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


async def run_implementation(start_phase: int, model: str) -> None:
    """Stage 3: Implementation loop."""
    log.info("=" * 60)
    log.info("STAGE 3: IMPLEMENTATION LOOP (starting from milestone %d)", start_phase)
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

        success = await run_implementation_milestone(next_milestone, model)
        if not success:
            log.error("Milestone %d failed. Stopping pipeline.", next_milestone)
            sys.exit(1)

        log.info("Milestone %d done. Detecting next...", next_milestone)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adversarial implementation pipeline for doc-hub architecture plan",
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
    log.info("Adv model: %s", args.adv_model)
    log.info("Impl model: %s", args.impl_model)
    log.info("Rounds: %d", args.rounds)
    log.info("Phase: %d", args.phase)

    if args.dry_run:
        log.info("DRY RUN — would execute:")
        if not args.skip_init:
            log.info("  1. Init: break spec into milestones")
        if not args.skip_adversarial:
            for i in range(1, args.rounds + 1):
                focus = ROUND_FOCUSES.get(i, "General improvement")
                focus_title = focus.split("\n")[0] if focus else "General"
                log.info("  2.%d. Adversarial round %d: %s", i, i, focus_title)
        if not args.skip_implementation:
            log.info("  3. Implementation loop starting from milestone %d", args.phase)
        log.info("DRY RUN complete — no agents were run")
        return

    # Stage 1: Init
    if args.force_init:
        for f in PLAN_DIR.glob("[0-9]*.md"):
            f.unlink()
            log.info("Removed %s", f.name)

    if not args.skip_init:
        await run_init(args.adv_model)

    # Stage 2: Adversarial refinement
    if not args.skip_adversarial:
        await run_adversarial(args.rounds, args.adv_model)

    # Stage 3: Implementation
    if not args.skip_implementation:
        await run_implementation(args.phase, args.impl_model)

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
