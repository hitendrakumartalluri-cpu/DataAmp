#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENV_FILE="${ENV_FILE:-.env}"
if [[ ! -f "$ENV_FILE" ]]; then cp .env.example "$ENV_FILE"; fi

echo "==> Verifying/pulling AMP lab dependency images"
./scripts/lab-pull-images.sh

echo "==> Building and starting AMP full lab"
docker compose --env-file "$ENV_FILE" -f docker-compose.yml up -d --build

echo "==> Waiting for AMP container"
for _ in $(seq 1 90); do
  if docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T amp python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=2)' >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "==> Bootstrapping primary + legacy S3 and AMP catalogue"
docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T amp python -m app.lab_bootstrap

echo
echo "AMP lab is ready:"
echo "  AMP UI / API     http://localhost:8080"
echo "  Primary S3       http://localhost:9000   console http://localhost:9001"
echo "  Legacy S3        http://localhost:9100   console http://localhost:9101"
echo "  Solr              http://localhost:8983"
echo "  Tika              http://localhost:9998"
echo "  Hop Server        http://localhost:8182"
echo "  Kafka (host)      localhost:29092"
echo "  Kafka topics      amp.raw.minio.<storage> -> amp.storage.changes -> catalogue consumer"
echo "  Event smoke test  ./scripts/lab-event-smoke-test.sh"
