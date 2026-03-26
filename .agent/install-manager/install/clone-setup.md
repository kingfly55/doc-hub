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
