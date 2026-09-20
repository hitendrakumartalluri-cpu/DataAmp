#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../services/control-plane"
export PYTHONPATH=.
export AMP_DEMO_MODE="${AMP_DEMO_MODE:-true}"
export AMP_DATABASE_URL="${AMP_DATABASE_URL:-sqlite:///$(pwd)/amp.db}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}" --reload
