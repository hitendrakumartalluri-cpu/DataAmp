#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
ENV_FILE="${ENV_FILE:-.env}"
docker compose --env-file "$ENV_FILE" -f docker-compose.yml down "$@"
