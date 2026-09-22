#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$REPO_ROOT/lab/scripts/lab-cumulative-regression.sh" "$@"
