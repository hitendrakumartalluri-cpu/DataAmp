#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
python_bin="${AMP_PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then python_bin="$repo_root/.venv/bin/python"; fi
PYTHONPATH=services/control-plane "$python_bin" -m pytest -q services/control-plane/tests
"$python_bin" -m compileall -q services/control-plane/app
node --check services/control-plane/app/static/app.js
node --check services/control-plane/app/static/beta.js
while IFS= read -r -d '' script; do bash -n "$script"; done < <(find scripts lab/scripts -name '*.sh' -print0)
