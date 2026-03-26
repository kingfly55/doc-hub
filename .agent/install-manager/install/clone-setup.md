# Clone Setup

## Goal

Set up doc-hub from a repository clone so the unified CLI can be used locally.

## Standard setup

```bash
git clone https://github.com/kingfly55/doc-hub.git && cd doc-hub
uv sync
source .venv/bin/activate
doc-hub --help
```

Expected result:
- `.venv/` exists
- `doc-hub --help` prints the unified CLI tree
- `doc-hub docs man` prints the bundled CLI reference text from the repository checkout

## Recommended global install

If you want `doc-hub` to run from anywhere on the machine, install it as a user tool:

```bash
uv tool install --force /path/to/doc-hub
# or
uv tool install --force git+https://github.com/kingfly55/doc-hub.git
```

Use `~/.local/share/doc-hub/env` for machine-wide DB and Gemini settings in that mode.

## If `uv` is unavailable

Install `uv` first or use your team-approved Python environment workflow. The repository is documented around `uv`.

## Verify Python version

```bash
python --version
```

Expected:
- Python 3.11+

## Common setup issues

### `doc-hub: command not found`
- activate the virtualenv: `source .venv/bin/activate`
- or use `uv run doc-hub --help`

### dependency install failed
- rerun `uv sync`
- inspect network / package index access
- confirm Python version is supported
