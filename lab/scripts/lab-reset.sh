#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
ENV_FILE="${ENV_FILE:-.env}"
if [[ ! -f "$ENV_FILE" ]]; then cp .env.example "$ENV_FILE"; fi
echo "This removes all AMP lab Postgres, S3 and Solr data volumes."
docker compose --env-file "$ENV_FILE" -f docker-compose.yml down -v --remove-orphans
"$ROOT/scripts/lab-up.sh"
