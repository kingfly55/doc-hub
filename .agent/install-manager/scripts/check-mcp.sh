#!/usr/bin/env bash
set -euo pipefail

printf '== DocHub MCP check ==\n'
if command -v doc-hub >/dev/null 2>&1; then
  printf 'doc-hub=FOUND\n'
else
  printf 'doc-hub=MISSING\n'
fi

printf '\n-- help check --\n'
uv run doc-hub serve mcp --help >/dev/null && printf 'serve-mcp-help=OK\n'

if command -v systemctl >/dev/null 2>&1; then
  printf '\n-- systemd unit check --\n'
  systemctl --user status doc-hub-mcp.service --no-pager || true
fi
