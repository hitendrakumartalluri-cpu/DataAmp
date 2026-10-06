#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
if [[ ! -x .venv/bin/python ]]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r services/control-plane/requirements.txt
export PYTHONPATH="$repo_root/services/control-plane"
export AMP_DATA_ROOT="${AMP_DATA_ROOT:-$repo_root/data-hcp-demo}"
export AMP_DATABASE_URL="${AMP_DATABASE_URL:-sqlite:///$AMP_DATA_ROOT/amp.db}"
export AMP_DEMO_MODE=true
simulator_port="${AMP_HCP_SIMULATOR_PORT:-18080}"
export AMP_HCP_DEMO_URL="http://127.0.0.1:$simulator_port"
.venv/bin/python -m uvicorn app.demo.hcp_server:app --host 127.0.0.1 --port "$simulator_port" &
simulator_pid=$!
trap 'kill "$simulator_pid" 2>/dev/null || true' EXIT INT TERM
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${AMP_PORT:-8085}"
