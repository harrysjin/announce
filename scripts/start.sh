#!/usr/bin/env bash
# Start from any directory. Installation/build remain explicit release steps.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if [[ ! -f dist/index.html ]]; then
  echo 'Frontend build missing. Run npm ci && npm run build from the project root.' >&2
  exit 1
fi
if [[ -x .venv/bin/python ]]; then
  python_bin=.venv/bin/python
else
  python_bin=python3
fi
if [[ -z "${STDICT_API_KEY:-}" ]]; then
  echo 'Dictionary key not configured: manual editing/export remains available.' >&2
fi
# A single worker is required because jobs and temporary state are in-process.
# Do not trust client-supplied proxy headers until a trusted reverse proxy is set.
exec "$python_bin" -m uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --no-proxy-headers --no-access-log
