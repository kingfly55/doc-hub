# Global Install and MCP Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `doc-hub` work as the canonical global command from anywhere, standardize one durable home-directory env file for global usage, repair the local MCP systemd service to use that install, and update the repository docs and install-manager to match.

**Architecture:** Add one shared CLI bootstrap path that loads a durable global env file under the existing XDG doc-hub home directory after the local `.env` load, preserving existing overrides while enabling a machine-wide fallback. Keep the product-level code change narrow, then update docs and install-manager guidance, and finally repair the real local installation and service wiring using the new documented model.

**Tech Stack:** Python 3.11, uv tool install, python-dotenv, systemd user services, pytest, Markdown docs

---

### Task 1: Add global env bootstrap support

**Files:**
- Modify: `src/doc_hub/cli/shared.py`
- Modify: `tests/test_unified_cli.py`
- Test: `tests/test_unified_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path
from unittest.mock import patch


def test_bootstrap_cli_loads_global_env_file_from_xdg_data_home(tmp_path, monkeypatch):
    from doc_hub.cli.shared import bootstrap_cli

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("DOC_HUB_DATA_DIR", raising=False)
    global_env = tmp_path / "xdg" / "doc-hub" / "env"

    with patch("doc_hub.cli.shared.load_dotenv") as mock_load_dotenv, patch(
        "doc_hub.cli.shared.logging.basicConfig"
    ):
        bootstrap_cli()

    assert mock_load_dotenv.call_args_list[0].args == ()
    assert mock_load_dotenv.call_args_list[1].kwargs == {"dotenv_path": global_env}


def test_bootstrap_cli_prefers_doc_hub_data_dir_for_global_env(tmp_path, monkeypatch):
    from doc_hub.cli.shared import bootstrap_cli

    data_dir = tmp_path / "custom-data"
    monkeypatch.setenv("DOC_HUB_DATA_DIR", str(data_dir))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    global_env = data_dir / "env"

    with patch("doc_hub.cli.shared.load_dotenv") as mock_load_dotenv, patch(
        "doc_hub.cli.shared.logging.basicConfig"
    ):
        bootstrap_cli()

    assert mock_load_dotenv.call_args_list[1].kwargs == {"dotenv_path": global_env}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/joenathan/.config/superpowers/worktrees/doc-hub/global-install-service && uv run pytest tests/test_unified_cli.py -q`
Expected: FAIL because `bootstrap_cli()` only calls `load_dotenv()` once and does not load the global env file.

- [ ] **Step 3: Write the minimal implementation**

```python
from pathlib import Path


def _global_env_path() -> Path:
    env_override = os.environ.get("DOC_HUB_DATA_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve() / "env"

    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data).expanduser().resolve() / "doc-hub" / "env"

    return Path.home() / ".local" / "share" / "doc-hub" / "env"


def bootstrap_cli(*, default_level: int = logging.INFO) -> None:
    load_dotenv()
    load_dotenv(dotenv_path=_global_env_path())
    level = logging.DEBUG if os.environ.get("LOGLEVEL") == "DEBUG" else default_level
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/joenathan/.config/superpowers/worktrees/doc-hub/global-install-service && uv run pytest tests/test_unified_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Run affected regression tests**

Run: `cd /home/joenathan/.config/superpowers/worktrees/doc-hub/global-install-service && uv run pytest tests/test_unified_cli.py tests/test_paths.py tests/test_browse_cli.py -q`
Expected: PASS.

### Task 2: Document the global install and env model

**Files:**
- Modify: `README.md`
- Modify: `docs/user/configuration.md`
- Modify: `docs/user/mcp-server.md`
- Test: documentation spot-check by reading the updated sections

- [ ] **Step 1: Update the install section in `README.md`**

Add the global-install recommendation and replace references to old `doc-hub-*` commands with the unified command only. Include a durable env-file example under `~/.local/share/doc-hub/env`.

```md
# Install globally from a local clone
uv tool install --force /path/to/doc-hub

# Or from GitHub
uv tool install --force git+https://github.com/kingfly55/doc-hub.git

# Global env file used from anywhere
mkdir -p ~/.local/share/doc-hub
cat > ~/.local/share/doc-hub/env <<'EOF'
PGHOST=localhost
PGPORT=5433
PGUSER=postgres
PGPASSWORD=your-password
PGDATABASE=postgres
GEMINI_API_KEY=your-key
EOF
```

- [ ] **Step 2: Update `docs/user/configuration.md`**

Document the new env load order explicitly:

```md
Environment loading for CLI commands:
1. Existing process environment
2. `.env` in the current working directory / repo root
3. `~/.local/share/doc-hub/env` (or `{DOC_HUB_DATA_DIR}/env` if overridden)
```

Also add a concrete example showing how to configure the global env file for machine-wide use.

- [ ] **Step 3: Update `docs/user/mcp-server.md`**

Replace `uv run --package doc-hub ...` examples for Claude Desktop / Claude Code stdio with the canonical `doc-hub serve mcp` command where appropriate for a global install, and update the systemd unit example to use `EnvironmentFile=%h/.local/share/doc-hub/env`.

```ini
[Service]
Type=simple
ExecStart=/home/joenathan/.local/bin/doc-hub serve mcp --transport sse --port 8340
Restart=always
RestartSec=10
Environment=HOME=%h
EnvironmentFile=%h/.local/share/doc-hub/env
```

- [ ] **Step 4: Read the updated docs to verify accuracy**

Run: `cd /home/joenathan/.config/superpowers/worktrees/doc-hub/global-install-service && sed -n '1,220p' README.md && printf '\n---\n' && sed -n '1,260p' docs/user/configuration.md && printf '\n---\n' && sed -n '1,240p' docs/user/mcp-server.md`
Expected: the rendered snippets show the unified `doc-hub` command, the global env file path, and the updated service configuration consistently.

### Task 3: Refresh install-manager operational guidance

**Files:**
- Modify: `.agent/install-manager/install/environment.md`
- Modify: `.agent/install-manager/install/services.md`
- Modify: `.agent/install-manager/install/clone-setup.md`
- Modify: `.agent/install-manager/memory/installation-state.md`
- Modify: `.agent/install-manager/memory/resolved-incidents.md`
- Test: `.agent/install-manager/scripts/check-env.sh`, `.agent/install-manager/scripts/check-mcp.sh`

- [ ] **Step 1: Update install-manager environment docs**

Add the durable env-file pattern to `.agent/install-manager/install/environment.md`.

```md
## Recommended machine-wide env file

```bash
mkdir -p ~/.local/share/doc-hub
cat > ~/.local/share/doc-hub/env <<'EOF'
PGHOST=localhost
PGPORT=5433
PGUSER=postgres
PGPASSWORD=your-password
PGDATABASE=postgres
GEMINI_API_KEY=your-key-here
EOF
```

Use this when `doc-hub` is installed on PATH and may be run from outside the repository.
```

- [ ] **Step 2: Update service guidance**

Revise `.agent/install-manager/install/services.md` so the persistent service example uses the global command and `EnvironmentFile=%h/.local/share/doc-hub/env`.

- [ ] **Step 3: Update clone/setup guidance**

Revise `.agent/install-manager/install/clone-setup.md` to distinguish local clone usage (`uv run doc-hub`) from the recommended global tool install (`uv tool install --force ...`).

- [ ] **Step 4: Update memory with the repaired install shape**

Add a new dated entry to `.agent/install-manager/memory/installation-state.md` capturing:
- global PATH install via `uv tool install`
- durable env file at `~/.local/share/doc-hub/env`
- user systemd service using the canonical global command

Add a new dated entry to `.agent/install-manager/memory/resolved-incidents.md` capturing the old service/path drift incident and the final repair.

- [ ] **Step 5: Run the install-manager diagnostics**

Run: `cd /home/joenathan/.config/superpowers/worktrees/doc-hub/global-install-service && ./.agent/install-manager/scripts/check-env.sh && ./.agent/install-manager/scripts/check-mcp.sh`
Expected: command output is still structurally correct; after the local machine repair in Task 4, the service check should point at the canonical command and env source.

### Task 4: Repair the actual local installation and user service

**Files:**
- Modify: `~/.config/systemd/user/doc-hub-mcp.service`
- Create or modify: `~/.local/share/doc-hub/env`
- External state: `~/.local/bin/*`, uv tool environment, systemd user daemon
- Test: PATH commands and systemd status commands

- [ ] **Step 1: Back up and inspect the current service file**

Run:
`cp ~/.config/systemd/user/doc-hub-mcp.service ~/.config/systemd/user/doc-hub-mcp.service.bak.$(date +%Y%m%d%H%M%S)`

Expected: timestamped backup exists before changes.

- [ ] **Step 2: Install the canonical global command**

Run:
`uv tool install --force /home/joenathan/.config/superpowers/worktrees/doc-hub/global-install-service`

Expected: `~/.local/bin/doc-hub` exists and `doc-hub --help` works.

- [ ] **Step 3: Remove stale legacy wrappers from PATH**

Run:
`rm -f ~/.local/bin/doc-hub-search ~/.local/bin/doc-hub-pipeline ~/.local/bin/doc-hub-eval ~/.local/bin/doc-hub-sync-all ~/.local/bin/doc-hub-mcp`

Expected: only the unified `doc-hub` command remains exposed for doc-hub usage.

- [ ] **Step 4: Create or update the durable env file**

Write `~/.local/share/doc-hub/env` with the real machine credentials and keys.

```dotenv
PGHOST=localhost
PGPORT=5433
PGUSER=postgres
PGPASSWORD=pydantic-docs
PGDATABASE=postgres
GEMINI_API_KEY=replace-with-real-key
```

If the real Gemini key is not available in this session, stop and ask the user to populate it manually.

- [ ] **Step 5: Rewrite the user service to the canonical model**

Set the service content to:

```ini
[Unit]
Description=doc-hub MCP Server (SSE on :8340)
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=/home/joenathan
ExecStart=/home/joenathan/.local/bin/doc-hub serve mcp --transport sse --port 8340
Restart=always
RestartSec=10
Environment=HOME=/home/joenathan
EnvironmentFile=/home/joenathan/.local/share/doc-hub/env

[Install]
WantedBy=default.target
```

- [ ] **Step 6: Reload and restart the service**

Run:
`systemctl --user daemon-reload && systemctl --user restart doc-hub-mcp.service && systemctl --user enable doc-hub-mcp.service`

Expected: exit code 0.

- [ ] **Step 7: Verify the repaired machine state**

Run:
`command -v doc-hub && doc-hub --help >/dev/null && ! command -v doc-hub-search && ! command -v doc-hub-pipeline && ! command -v doc-hub-eval && ! command -v doc-hub-sync-all && ! command -v doc-hub-mcp && systemctl --user status doc-hub-mcp.service --no-pager`

Expected: `doc-hub` is on PATH, old wrappers are gone, and the service is active with the new command path.

### Task 5: Final verification and repository assessment

**Files:**
- Verify all modified files from Tasks 1–4
- Test: focused pytest run, CLI checks, install-manager checks, systemd status, optional journal tail

- [ ] **Step 1: Run focused repo verification**

Run: `cd /home/joenathan/.config/superpowers/worktrees/doc-hub/global-install-service && uv run pytest tests/test_unified_cli.py tests/test_paths.py tests/test_browse_cli.py -q`
Expected: PASS.

- [ ] **Step 2: Run CLI and install-manager verification**

Run: `cd /home/joenathan/.config/superpowers/worktrees/doc-hub/global-install-service && uv run doc-hub --help >/dev/null && ./.agent/install-manager/scripts/check-env.sh && ./.agent/install-manager/scripts/check-mcp.sh`
Expected: the CLI works; env and MCP diagnostics reflect the repaired model.

- [ ] **Step 3: Verify the live global command outside the repo**

Run: `cd /tmp && doc-hub --help >/dev/null && printf 'global-doc-hub=OK\n'`
Expected: `global-doc-hub=OK`.

- [ ] **Step 4: Assess whether the repo needed changes**

Review the final diff and explicitly answer:
- Did code changes become necessary to support the approved install strategy?
- Did docs need updates to make the strategy usable?
- Did install-manager operational memory need refresh?

Expected: a short final report grounded in the diff and verification output.
