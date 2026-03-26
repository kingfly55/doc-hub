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
