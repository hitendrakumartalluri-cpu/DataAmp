#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
ENV_FILE="${ENV_FILE:-.env}"
BASE="${AMP_URL:-http://localhost:8080}"

CATALOGUE_GROUPS_JSON="$(curl -fsS "$BASE/api/v1/catalogue-groups?tenant_id=demo")"
GID="$(python3 -c 'import json,sys; x=json.load(sys.stdin); print(next(g["id"] for g in x if g.get("container_name")=="legacy-hcp"))' <<<"$CATALOGUE_GROUPS_JSON")"
KEY="events/outside-amp-smoke.txt"

echo "==> Writing directly to legacy MinIO (bypassing AMP)"
docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T amp python - <<'PY'
import os,boto3
from botocore.config import Config
c=boto3.client('s3', endpoint_url=os.environ['AMP_LAB_LEGACY_ENDPOINT'],
 aws_access_key_id=os.environ['AMP_LAB_LEGACY_ACCESS_KEY'], aws_secret_access_key=os.environ['AMP_LAB_LEGACY_SECRET_KEY'],
 region_name='us-east-1', config=Config(s3={'addressing_style':'path'}))
c.put_object(Bucket=os.environ['AMP_LAB_LEGACY_BUCKET'],Key='events/outside-amp-smoke.txt',Body=b'event path smoke test',ContentType='text/plain')
PY

for i in $(seq 1 30); do
  if curl -fsS "$BASE/api/v1/catalogue-objects?tenant_id=demo&catalogue_group_id=$GID&q=outside-amp-smoke.txt" | python3 -c 'import json,sys; x=json.load(sys.stdin); raise SystemExit(0 if any(r.get("lifecycle_state")=="ACTIVE" for r in x) else 1)' >/dev/null 2>&1; then
    echo "[ok] create event reached AMP catalogue"
    break
  fi
  sleep 1
  if [[ "$i" == "30" ]]; then echo "[error] create event did not reach catalogue"; exit 1; fi
done

echo "==> Deleting directly from legacy MinIO"
docker compose --env-file "$ENV_FILE" -f docker-compose.yml exec -T amp python - <<'PY'
import os,boto3
from botocore.config import Config
c=boto3.client('s3', endpoint_url=os.environ['AMP_LAB_LEGACY_ENDPOINT'],
 aws_access_key_id=os.environ['AMP_LAB_LEGACY_ACCESS_KEY'], aws_secret_access_key=os.environ['AMP_LAB_LEGACY_SECRET_KEY'],
 region_name='us-east-1', config=Config(s3={'addressing_style':'path'}))
c.delete_object(Bucket=os.environ['AMP_LAB_LEGACY_BUCKET'],Key='events/outside-amp-smoke.txt')
PY

for i in $(seq 1 30); do
  if curl -fsS "$BASE/api/v1/catalogue-objects?tenant_id=demo&catalogue_group_id=$GID&q=outside-amp-smoke.txt" | python3 -c 'import json,sys; x=json.load(sys.stdin); raise SystemExit(0 if any(r.get("lifecycle_state")=="TOMBSTONED" for r in x) else 1)' >/dev/null 2>&1; then
    echo "[ok] delete event tombstoned AMP catalogue object"
    break
  fi
  sleep 1
  if [[ "$i" == "30" ]]; then echo "[error] delete event did not reach catalogue"; exit 1; fi
done

echo "==> Latest storage events"
curl -fsS "$BASE/api/v1/storage-events?tenant_id=demo&catalogue_group_id=$GID&limit=6" | python3 -m json.tool

echo "[ok] MinIO -> Kafka -> AMP normalized event -> targeted catalogue refresh passed"
