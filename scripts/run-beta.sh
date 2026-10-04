#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r services/control-plane/requirements.txt
export AMP_DATA_ROOT="${AMP_DATA_ROOT:-$repo_root/data}"
export AMP_DATABASE_URL="${AMP_DATABASE_URL:-sqlite:///$repo_root/data/amp-beta.db}"
export AMP_DEMO_MODE="${AMP_DEMO_MODE:-true}"
export PYTHONPATH="$repo_root/services/control-plane"
exec .venv/bin/python -m uvicorn app.main:app --host "${AMP_BIND_HOST:-127.0.0.1}" --port "${AMP_PORT:-8080}"
