#!/usr/bin/env bash
set -euo pipefail

printf '== DocHub environment check ==\n'
for var in GEMINI_API_KEY DOC_HUB_DATABASE_URL PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD DOC_HUB_VECTOR_DIM DOC_HUB_DATA_DIR DOC_HUB_EVAL_DIR LOGLEVEL; do
  value="${!var-}"
  if [[ -n "$value" ]]; then
    if [[ "$var" == "GEMINI_API_KEY" || "$var" == "PGPASSWORD" || "$var" == "DOC_HUB_DATABASE_URL" ]]; then
      printf '%s=SET\n' "$var"
    else
      printf '%s=%s\n' "$var" "$value"
    fi
  else
    printf '%s=UNSET\n' "$var"
  fi
done

if [[ -n "${DOC_HUB_DATA_DIR-}" ]]; then
  global_env_file="$(realpath -m "$DOC_HUB_DATA_DIR/env")"
elif [[ -n "${XDG_DATA_HOME-}" ]]; then
  global_env_file="$(realpath -m "$XDG_DATA_HOME/doc-hub/env")"
else
  global_env_file="$HOME/.local/share/doc-hub/env"
fi

printf 'GLOBAL_ENV_FILE=%s\n' "$global_env_file"
if [[ -f "$global_env_file" ]]; then
  printf 'GLOBAL_ENV_FILE_STATUS=present\n'
  global_env_keys="$(python3 - <<'PY' "$global_env_file"
from pathlib import Path
import sys
keys = []
for line in Path(sys.argv[1]).read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    keys.append(line.split('=', 1)[0])
print(','.join(sorted(keys)))
PY
)"
  printf 'GLOBAL_ENV_KEYS=%s\n' "$global_env_keys"
else
  printf 'GLOBAL_ENV_FILE_STATUS=missing\n'
fi
